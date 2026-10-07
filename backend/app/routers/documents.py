"""Document library (roadmap item N, slices N.1, N.2 and N.3).

N.1 established the core versioned record shape; N.2 added the read
surface a browser editor needs -- single-version detail, a structured
diff between any two versions, and the versions-plus-audit history view --
plus optimistic-concurrency conflict detection on save. N.3 added the
approval record and the review cadence: every `DocumentOut` now carries a
derived `review` verdict, approval history is its own endpoint, and
re-approval of an unchanged document appends an approval without creating a
version. N.4 (MSP templates) and N.5 (suggested documentation) build on
this and models.py:Document/DocumentVersion/DocumentObjectiveTag -- see
docs/PLAN-document-library.md.

N.3's cadence logic lives in document_reviews.py, not here: whether a
document is overdue is derived arithmetic over document_approval, and it is
deliberately never stored. Nothing in this router reads
`DocumentVersion.approved_at` to answer a cadence question -- that column
is a snapshot of the initial approval only and knows nothing about
reaffirmations.

Document bodies are GFM-subset Markdown; markdown_doc.py owns the format
decision and is the only thing that renders one to HTML. Nothing in this
router returns rendered HTML -- see the N.2 section below for why.

Not to be confused with importers/document.py -- that module is the AI
vendor-CRM/baseline-document extractor, a completely different feature
(roadmap item N.1's own grounding note flags this exact confusion risk).

Endpoints:
  POST   /orgs/{org_id}/documents                                    Create (+ version 1)
  GET    /orgs/{org_id}/documents                                    List
  GET    /orgs/{org_id}/documents/{document_id}                      Detail + version history
  PATCH  /orgs/{org_id}/documents/{document_id}                      Identity fields
  DELETE /orgs/{org_id}/documents/{document_id}                      Refused if any Evidence
                                                                       still references a version
  POST   /orgs/{org_id}/documents/{document_id}/versions             New version (edit)
  PATCH  /orgs/{org_id}/documents/{document_id}/versions/{id}        draft <-> under_review only
  GET    /orgs/{org_id}/documents/{document_id}/versions/{id}        One version + objective set
  GET    /orgs/{org_id}/documents/{document_id}/diff                 Diff any two versions
  GET    /orgs/{org_id}/documents/{document_id}/history              Versions + audit timeline
  POST   /orgs/{org_id}/documents/{document_id}/objective-tags       Tag an objective
  DELETE /orgs/{org_id}/documents/{document_id}/objective-tags/{id}  Untag
  POST   /orgs/{org_id}/documents/{document_id}/publish              Approve current version
  GET    /orgs/{org_id}/documents/{document_id}/approvals            Approval history (N.3)
  POST   /orgs/{org_id}/documents/{document_id}/reaffirm             Re-approve, unchanged (N.3)

`GET /documents` takes `review_status` alongside `doc_type` -- the cadence
verdict is derived, so that filter is applied after the query, not as a
WHERE clause.

Uses {document_id} (the internal UUID), not roadmap N's illustrative
{doc_id} path notation -- every other resource in this API keys its URL on
the internal id, not a natural key (see routers/scope.py's own natural-key-
is-never-the-URL-key precedent), and doc_id is operator-supplied,
human-facing text, not a routing identifier. doc_id itself is still exactly
roadmap N's stable, human-readable, MSP-assigned field -- just not the URL.

storage_key (DocumentVersion) is carried in the schema, per roadmap N's own
field list, but no endpoint here writes or reads it -- there is no upload
path in this slice, matching how is_template_derived/template_ref are also
carried-but-unexercised placeholders N.4 fills in. Every version in N.1 is
plain text (body); a real file-backed version, and the id-based-storage-
path convention models.py:Document Version's own docstring already
documents for whenever that lands, is N.4's concern (that's also where
.docx->text conversion is decided).
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..audit import identity_out, log_event, parse_actor_uuid, resolve_identities
from ..auth import require_org_access, require_write
from ..db import get_session
from ..document_diff import BodyDiff, diff_bodies, diff_sets
from ..document_reviews import (
    DocumentReviewError,
    approvals_for_document,
    latest_approval,
    reaffirm,
    record_approval,
    review_state,
)
from ..models import (
    Assessment,
    AssessmentObjective,
    AuditLog,
    Contact,
    ControlState,
    Document,
    DocumentApproval,
    DocumentObjectiveTag,
    DocumentVersion,
    Evidence,
    EvidenceStateLink,
)

router = APIRouter(
    prefix="/orgs",
    tags=["documents"],
    dependencies=[Depends(require_org_access()), Depends(require_write())],
)

_DOC_TYPES = frozenset(
    {"policy", "procedure", "plan", "baseline", "list", "sop", "form", "other"}
)
_MANUAL_VERSION_STATUSES = frozenset({"draft", "under_review"})

# N.3 review-cadence filter vocabulary. The four are document_reviews.py's
# own status values; `needs_attention` is the union an operator actually
# wants, kept as a named filter rather than making callers pass three.
_NEEDS_ATTENTION = frozenset({"never_approved", "due_soon", "overdue"})
_REVIEW_FILTERS = frozenset({*_NEEDS_ATTENTION, "current", "needs_attention"})

# doc_id lands in Evidence.reference_location once published and is meant
# to be usable as a storage-path component later (N.4) -- treated as the
# same class of input as a filename: no path separators, no leading/
# trailing separator, no ".." anywhere (defensive; nothing in this slice
# actually builds a path from it yet, but a later slice that does must not
# inherit an unvalidated value).
_DOC_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,58}[A-Za-z0-9]$")


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class DocumentVersionOut(BaseModel):
    id: uuid.UUID
    version_number: int
    status: str
    body: str | None
    is_template_derived: bool
    template_ref: str | None
    approved_at: datetime | None
    approved_by_contact_id: uuid.UUID | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ReviewStateOut(BaseModel):
    """Where this document stands against its review cadence -- N.3.

    Computed on every read, never stored (document_reviews.py's module
    docstring has the reasoning). `status` is one of never_approved /
    current / due_soon / overdue.

    Carried on every DocumentOut so the library list can show overdue
    without a second round trip -- §4's "make overdue visible" starts
    here, and a field the list already has is harder to forget to render
    than a separate endpoint.
    """

    status: str
    last_approved_at: datetime | None
    next_due_at: datetime | None
    days_until_due: int | None


class ApprovalOut(BaseModel):
    """One approval decision -- initial or reaffirmation.

    `approver_*` is the named Contact whose decision satisfies the cadence.
    The authenticated user who *recorded* it is a different fact and is
    deliberately not here: it lives in audit_log and is surfaced by
    `GET .../history`, the same place N.2 put document history rather than
    duplicating it. See DocumentApproval's docstring -- these are usually
    two different people and the UI must not imply the clicker approved it.
    """

    id: uuid.UUID
    document_version_id: uuid.UUID
    version_number: int | None
    approval_type: str
    approved_at: datetime
    approved_by_contact_id: uuid.UUID | None
    approver_name: str
    note: str | None


class DocumentOut(BaseModel):
    id: uuid.UUID
    doc_id: str
    doc_type: str
    title: str
    cadence_months: int
    is_template_derived: bool
    template_ref: str | None
    current_version: DocumentVersionOut | None
    review: ReviewStateOut
    created_at: datetime
    updated_at: datetime


class DocumentDetailOut(DocumentOut):
    versions: list[DocumentVersionOut]
    tagged_objective_ids: list[uuid.UUID]


class DocumentCreateIn(BaseModel):
    doc_id: str
    doc_type: str
    title: str
    cadence_months: int = 12
    body: str | None = None

    @field_validator("doc_id")
    @classmethod
    def _check_doc_id(cls, v: str) -> str:
        v = v.strip()
        if not _DOC_ID_RE.match(v) or ".." in v:
            raise ValueError(
                "doc_id must be 2-60 characters of letters, digits, '.', '_', '-' only, "
                "starting and ending with a letter or digit -- same class of input as a "
                "filename, since it appears in Evidence.reference_location"
            )
        return v

    @field_validator("doc_type")
    @classmethod
    def _check_doc_type(cls, v: str) -> str:
        if v not in _DOC_TYPES:
            raise ValueError(f"doc_type must be one of: {sorted(_DOC_TYPES)}")
        return v

    @field_validator("cadence_months")
    @classmethod
    def _check_cadence(cls, v: int) -> int:
        if v < 1:
            raise ValueError("cadence_months must be at least 1")
        return v

    @field_validator("title")
    @classmethod
    def _check_title(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("title must not be blank")
        return v


class DocumentPatchIn(BaseModel):
    title: str | None = None
    doc_type: str | None = None
    cadence_months: int | None = None

    @field_validator("doc_type")
    @classmethod
    def _check_doc_type(cls, v: str | None) -> str | None:
        if v is not None and v not in _DOC_TYPES:
            raise ValueError(f"doc_type must be one of: {sorted(_DOC_TYPES)}")
        return v

    @field_validator("cadence_months")
    @classmethod
    def _check_cadence(cls, v: int | None) -> int | None:
        if v is not None and v < 1:
            raise ValueError("cadence_months must be at least 1")
        return v

    @field_validator("title")
    @classmethod
    def _check_title(cls, v: str | None) -> str | None:
        if v is not None:
            v = v.strip()
            if not v:
                raise ValueError("title must not be blank")
        return v


class VersionCreateIn(BaseModel):
    body: str | None = None
    # Optimistic concurrency (roadmap N.2). The version the editor was
    # working from. Required once a document has any version at all --
    # see create_document_version's docstring for why a silent
    # last-write-wins was not an acceptable answer and why this is a
    # conflict check rather than a lock.
    base_version_id: uuid.UUID | None = None


class VersionStatusPatchIn(BaseModel):
    status: str

    @field_validator("status")
    @classmethod
    def _check_status(cls, v: str) -> str:
        if v not in _MANUAL_VERSION_STATUSES:
            raise ValueError(
                "status must be 'draft' or 'under_review' -- 'approved' is set only by "
                "POST .../publish, 'superseded' is never set manually"
            )
        return v


class ObjectiveTagIn(BaseModel):
    objective_id: uuid.UUID


class PublishIn(BaseModel):
    approved_by_contact_id: uuid.UUID


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_document(session: Session, org_id: uuid.UUID, document_id: uuid.UUID) -> Document:
    doc = session.get(Document, document_id)
    if doc is None or doc.org_id != org_id:
        raise HTTPException(status_code=404, detail="Document not found")
    return doc


def _version_out(v: DocumentVersion) -> DocumentVersionOut:
    return DocumentVersionOut.model_validate(v)


def _review_out(session: Session, doc: Document, *, now: datetime | None = None) -> ReviewStateOut:
    """The cadence verdict, from the newest approval of any kind.

    Reads document_approval, never DocumentVersion.approved_at -- that
    column is a denormalized snapshot of the *initial* approval only and
    knows nothing about reaffirmations, so using it here would report a
    document re-approved four times as last reviewed on its original date.
    DocumentApproval's docstring says the same thing from the other side.
    """
    approval = latest_approval(session, document_id=doc.id)
    state = review_state(
        last_approved_at=approval.approved_at if approval else None,
        cadence_months=doc.cadence_months,
        now=now or datetime.now(UTC),
    )
    return ReviewStateOut(
        status=state.status,
        last_approved_at=state.last_approved_at,
        next_due_at=state.next_due_at,
        days_until_due=state.days_until_due,
    )


def _document_out(session: Session, doc: Document) -> DocumentOut:
    current = (
        session.get(DocumentVersion, doc.current_version_id) if doc.current_version_id else None
    )
    return DocumentOut(
        id=doc.id, doc_id=doc.doc_id, doc_type=doc.doc_type, title=doc.title,
        cadence_months=doc.cadence_months, is_template_derived=doc.is_template_derived,
        template_ref=doc.template_ref,
        current_version=_version_out(current) if current is not None else None,
        review=_review_out(session, doc),
        created_at=doc.created_at, updated_at=doc.updated_at,
    )


def _document_detail_out(session: Session, doc: Document) -> DocumentDetailOut:
    base = _document_out(session, doc)
    versions = session.scalars(
        select(DocumentVersion)
        .where(DocumentVersion.document_id == doc.id)
        .order_by(DocumentVersion.version_number.desc())
    ).all()
    tag_ids = session.scalars(
        select(DocumentObjectiveTag.objective_id).where(DocumentObjectiveTag.document_id == doc.id)
    ).all()
    return DocumentDetailOut(
        **base.model_dump(),
        versions=[_version_out(v) for v in versions],
        tagged_objective_ids=list(tag_ids),
    )


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


@router.post("/{org_id}/documents", response_model=DocumentDetailOut, status_code=201)
def create_document(
    org_id: uuid.UUID, body: DocumentCreateIn, session: Session = Depends(get_session)
) -> DocumentDetailOut:
    existing = session.scalars(
        select(Document).where(Document.org_id == org_id, Document.doc_id == body.doc_id)
    ).first()
    if existing is not None:
        raise HTTPException(
            status_code=409, detail=f"doc_id {body.doc_id!r} already exists for this org"
        )

    doc = Document(
        org_id=org_id, doc_id=body.doc_id, doc_type=body.doc_type, title=body.title,
        cadence_months=body.cadence_months,
    )
    session.add(doc)
    session.flush()

    version = DocumentVersion(
        document_id=doc.id, org_id=org_id, version_number=1, status="draft", body=body.body,
    )
    session.add(version)
    session.flush()
    doc.current_version_id = version.id
    session.flush()

    log_event(
        session, org_id=org_id, action="document.create", entity_type="document",
        entity_id=doc.id, after_value={"doc_id": doc.doc_id, "title": doc.title},
        context={"via": "api"},
    )
    # Build the response BEFORE commit, not after: doc.updated_at has
    # onupdate=func.now() (set by the current_version_id update above),
    # and reading a just-flushed server-computed column after commit can
    # trigger a deferred SELECT that runs with app.current_org already
    # reset by _app_session's own commit wrapper -- RLS then matches zero
    # rows and raises ObjectDeletedError on a row that's very much still
    # there (tests/conftest.py:_app_session's own docstring names this
    # exact trap; ScopeEntityOut avoids it by never exposing updated_at at
    # all, which isn't the right fix here since it's a useful field).
    result = _document_detail_out(session, doc)
    session.commit()
    return result


@router.get("/{org_id}/documents", response_model=list[DocumentOut])
def list_documents(
    org_id: uuid.UUID,
    session: Session = Depends(get_session),
    doc_type: str | None = None,
    review_status: str | None = None,
) -> list[DocumentOut]:
    """`doc_type` backs the Library nav's per-type views (Policies,
    Procedures, Plans) -- validated against the same vocabulary the create
    and patch bodies use, so an unknown value is a 422 rather than a
    silently empty list that reads like "you have no policies".

    `review_status` (N.3) filters on the *derived* cadence verdict, so it
    is applied in Python after the query rather than as a WHERE clause --
    "overdue" is not a column and deliberately is not one (see
    document_reviews.py). `needs_attention` is the useful one: everything
    an operator should act on, which is never_approved + due_soon +
    overdue rather than just overdue. Validated the same way doc_type is,
    for the same reason.
    """
    query = select(Document).where(Document.org_id == org_id)
    if doc_type is not None:
        if doc_type not in _DOC_TYPES:
            raise HTTPException(
                status_code=422, detail=f"doc_type must be one of: {sorted(_DOC_TYPES)}"
            )
        query = query.where(Document.doc_type == doc_type)
    if review_status is not None and review_status not in _REVIEW_FILTERS:
        raise HTTPException(
            status_code=422,
            detail=f"review_status must be one of: {sorted(_REVIEW_FILTERS)}",
        )
    docs = session.scalars(query.order_by(Document.doc_id)).all()
    out = [_document_out(session, d) for d in docs]
    if review_status == "needs_attention":
        return [d for d in out if d.review.status in _NEEDS_ATTENTION]
    if review_status is not None:
        return [d for d in out if d.review.status == review_status]
    return out


@router.get("/{org_id}/documents/{document_id}", response_model=DocumentDetailOut)
def get_document(
    org_id: uuid.UUID, document_id: uuid.UUID, session: Session = Depends(get_session)
) -> DocumentDetailOut:
    doc = _get_document(session, org_id, document_id)
    return _document_detail_out(session, doc)


@router.patch("/{org_id}/documents/{document_id}", response_model=DocumentDetailOut)
def patch_document(
    org_id: uuid.UUID, document_id: uuid.UUID, body: DocumentPatchIn,
    session: Session = Depends(get_session),
) -> DocumentDetailOut:
    doc = _get_document(session, org_id, document_id)
    update_data = body.model_dump(exclude_unset=True)
    if not update_data:
        return _document_detail_out(session, doc)

    before = {"title": doc.title, "doc_type": doc.doc_type, "cadence_months": doc.cadence_months}
    for field_name, value in update_data.items():
        setattr(doc, field_name, value)
    session.flush()

    log_event(
        session, org_id=org_id, action="document.update", entity_type="document",
        entity_id=doc.id, before_value=before, after_value=update_data,
        context={"via": "api"},
    )
    # Built before commit -- see create_document's own comment for why
    # (doc.updated_at's onupdate).
    result = _document_detail_out(session, doc)
    session.commit()
    return result


@router.delete("/{org_id}/documents/{document_id}", status_code=204)
def delete_document(
    org_id: uuid.UUID, document_id: uuid.UUID, session: Session = Depends(get_session)
) -> None:
    """Refused with 409 if any Evidence still references one of this
    document's versions -- a published document is a compliance record,
    same "must not disappear silently" reasoning as migration 0057's
    asset_approval RESTRICT. Checked at the application level for a clean
    error; evidence.source_document_version_id's own RESTRICT FK is the
    defense-in-depth backstop if this check is ever bypassed.
    """
    doc = _get_document(session, org_id, document_id)
    version_ids = session.scalars(
        select(DocumentVersion.id).where(DocumentVersion.document_id == doc.id)
    ).all()
    if version_ids:
        has_evidence = session.scalars(
            select(Evidence.id).where(Evidence.source_document_version_id.in_(version_ids))
        ).first()
        if has_evidence is not None:
            raise HTTPException(
                status_code=409,
                detail=(
                    "This document has been published and has evidence attached -- it "
                    "cannot be deleted."
                ),
            )

    log_event(
        session, org_id=org_id, action="document.delete", entity_type="document",
        entity_id=doc.id, before_value={"doc_id": doc.doc_id, "title": doc.title},
        context={"via": "api"},
    )
    try:
        session.delete(doc)
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(
            status_code=409,
            detail="This document cannot be deleted -- something still references it.",
        ) from exc


# ---------------------------------------------------------------------------
# Versions
# ---------------------------------------------------------------------------


@router.post(
    "/{org_id}/documents/{document_id}/versions", response_model=DocumentVersionOut, status_code=201
)
def create_document_version(
    org_id: uuid.UUID, document_id: uuid.UUID, body: VersionCreateIn,
    session: Session = Depends(get_session),
) -> DocumentVersionOut:
    """Editing a document creates a new version; it never mutates an
    existing one (roadmap item P's exact lesson -- see models.py:Document's
    own docstring). The prior version, whatever its status, is untouched
    by this call -- only publish (below) ever changes an existing version's
    status, and only to move it to superseded as the side effect of a
    NEWER version being published, never as a side effect of this one.

    **Concurrent editing (roadmap N.2's own required decision).** Two
    people editing one document is a real MSP scenario, and a silent
    last-write-wins is the wrong answer: the loser's work vanishes with
    no signal. This is optimistic concurrency -- the caller states which
    version it edited from (`base_version_id`), and a save races only if
    the document moved underneath it, which returns 409 carrying the
    version that won so the UI can say "X saved version N while you were
    editing" and offer a diff.

    Chosen over a lock deliberately. A lock needs a lease, an expiry, and
    a steal path, because an MSP engineer who closes a tab must not
    strand a policy document for whoever needs it next -- and every one
    of those mechanisms is state that can itself be wrong. A conflict
    check is stateless, never strands anything, and cannot lose an edit:
    the "loser" still holds their text in the browser and can re-save
    onto the new base. Nothing is destroyed either way, because versions
    are append-only -- the racing save is still recorded, just as a
    version off a different parent.

    `base_version_id` is required once a document has any version, and
    optional only for the no-versions-yet case (which `create_document`
    handles anyway). Omitting it on a document that has versions is a 422,
    not a silent unchecked write -- a client that does not know what it is
    editing from cannot be allowed to overwrite whatever is current.
    """
    doc = _get_document(session, org_id, document_id)
    max_version = session.scalar(
        select(func.max(DocumentVersion.version_number)).where(
            DocumentVersion.document_id == doc.id
        )
    ) or 0

    if max_version and body.base_version_id is None:
        raise HTTPException(
            status_code=422,
            detail=(
                "base_version_id is required -- it names the version this edit started "
                "from, so a concurrent save can be detected rather than silently lost."
            ),
        )
    if body.base_version_id is not None and doc.current_version_id != body.base_version_id:
        current = (
            session.get(DocumentVersion, doc.current_version_id)
            if doc.current_version_id
            else None
        )
        raise HTTPException(
            status_code=409,
            detail={
                "message": (
                    "This document changed while you were editing -- someone else saved a "
                    "newer version. Your text has not been lost; compare and re-save onto "
                    "the current version."
                ),
                "base_version_id": str(body.base_version_id),
                "current_version_id": str(doc.current_version_id)
                if doc.current_version_id
                else None,
                "current_version_number": current.version_number if current else None,
            },
        )

    version = DocumentVersion(
        document_id=doc.id, org_id=org_id, version_number=max_version + 1,
        status="draft", body=body.body,
    )
    session.add(version)
    session.flush()
    doc.current_version_id = version.id
    session.flush()

    log_event(
        session, org_id=org_id, action="document.version.create", entity_type="document_version",
        entity_id=version.id,
        after_value={"doc_id": doc.doc_id, "version_number": version.version_number},
        context={"via": "api"},
    )
    session.commit()
    return _version_out(version)


@router.patch(
    "/{org_id}/documents/{document_id}/versions/{version_id}", response_model=DocumentVersionOut
)
def patch_document_version_status(
    org_id: uuid.UUID, document_id: uuid.UUID, version_id: uuid.UUID, body: VersionStatusPatchIn,
    session: Session = Depends(get_session),
) -> DocumentVersionOut:
    """draft <-> under_review only -- see DocumentVersion's own docstring
    for the full legal-transition table. approved is reachable only via
    publish; superseded is never reachable via PATCH at all. Only the
    document's own current version may be touched this way -- an older,
    already-superseded version is never a legal PATCH target.
    """
    doc = _get_document(session, org_id, document_id)
    version = session.get(DocumentVersion, version_id)
    if version is None or version.document_id != doc.id:
        raise HTTPException(status_code=404, detail="Version not found")
    if doc.current_version_id != version.id:
        raise HTTPException(
            status_code=409, detail="Only the current version's status can be changed directly"
        )
    if version.status not in _MANUAL_VERSION_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot manually change status away from {version.status!r}",
        )

    before_status = version.status
    version.status = body.status
    session.flush()

    log_event(
        session, org_id=org_id, action="document.version.status_change",
        entity_type="document_version", entity_id=version.id,
        before_value={"status": before_status}, after_value={"status": version.status},
        context={"via": "api"},
    )
    session.commit()
    return _version_out(version)


# ---------------------------------------------------------------------------
# Objective tags
# ---------------------------------------------------------------------------


@router.post("/{org_id}/documents/{document_id}/objective-tags", status_code=201)
def add_objective_tag(
    org_id: uuid.UUID, document_id: uuid.UUID, body: ObjectiveTagIn,
    session: Session = Depends(get_session),
) -> dict:
    doc = _get_document(session, org_id, document_id)
    objective = session.get(AssessmentObjective, body.objective_id)
    if objective is None:
        raise HTTPException(status_code=422, detail="Unknown objective_id")

    existing = session.scalars(
        select(DocumentObjectiveTag).where(
            DocumentObjectiveTag.document_id == doc.id,
            DocumentObjectiveTag.objective_id == body.objective_id,
        )
    ).first()
    if existing is not None:
        return {"document_id": doc.id, "objective_id": body.objective_id}

    tag = DocumentObjectiveTag(document_id=doc.id, org_id=org_id, objective_id=body.objective_id)
    session.add(tag)
    session.flush()
    log_event(
        session, org_id=org_id, action="document.objective_tag.add", entity_type="document",
        entity_id=doc.id, after_value={"objective_id": str(body.objective_id)},
        context={"via": "api"},
    )
    session.commit()
    return {"document_id": doc.id, "objective_id": body.objective_id}


@router.delete("/{org_id}/documents/{document_id}/objective-tags/{objective_id}", status_code=204)
def remove_objective_tag(
    org_id: uuid.UUID, document_id: uuid.UUID, objective_id: uuid.UUID,
    session: Session = Depends(get_session),
) -> None:
    doc = _get_document(session, org_id, document_id)
    tag = session.scalars(
        select(DocumentObjectiveTag).where(
            DocumentObjectiveTag.document_id == doc.id,
            DocumentObjectiveTag.objective_id == objective_id,
        )
    ).first()
    if tag is None:
        raise HTTPException(status_code=404, detail="Tag not found")

    session.delete(tag)
    log_event(
        session, org_id=org_id, action="document.objective_tag.remove", entity_type="document",
        entity_id=doc.id, before_value={"objective_id": str(objective_id)},
        context={"via": "api"},
    )
    session.commit()


# ---------------------------------------------------------------------------
# Publish
# ---------------------------------------------------------------------------


@router.post("/{org_id}/documents/{document_id}/publish", response_model=DocumentOut)
def publish_document(
    org_id: uuid.UUID, document_id: uuid.UUID, body: PublishIn,
    session: Session = Depends(get_session),
) -> DocumentOut:
    """Approves the document's current version and attaches evidence --
    roadmap N's own publish action, adapted for versioning:

    1. status -> approved, approved_at -> now, approved_by_contact_id ->
       the named Contact (a real person recorded as granting approval --
       not necessarily the authenticated caller; see DocumentVersion's own
       docstring for why these are two different facts. The authenticated
       caller is captured separately by log_event's actor resolution).
    2. Any OTHER version of this same document that's still 'approved'
       becomes 'superseded' (republish handling: at most one approved
       version per document at a time). Its evidence links are archived
       (EvidenceStateLink.is_archived), not deleted -- the Evidence row
       itself is untouched and permanently resolvable (source_document_
       version_id points at the version that was actually approved, never
       "whatever is current"), so an already-generated bundle keeps
       rendering exactly what it rendered; a fresh export after this call
       correctly shows only the new version's evidence, matching every
       other point-in-time guarantee in this codebase (state changes ARE
       reflected in exports going forward; they don't rewrite exports
       already produced, because those are frozen ZIP bytes already handed
       out).
    3. One new Evidence row (kind='reference', reference_location=doc_id,
       source_document_version_id=this version) -- created even if there
       are no tagged objectives or no in_progress assessment right now
       (an "unlinked" reference is a normal state elsewhere in this
       codebase, e.g. bundle_service.py's own unlinked-evidence section).
    4. For each tagged objective, across every in_progress assessment for
       this org (never assumed to be exactly one -- same precedent
       review_cycles.py's own "active assessment(s)" resolution uses):
       one EvidenceStateLink. control_state.status is NEVER touched here --
       "candidates, never auto-met": evidence is attached, an engineer
       reviews and marks objectives met by hand, same rule tool activation
       and connector output already follow.
    """
    doc = _get_document(session, org_id, document_id)
    if doc.current_version_id is None:
        raise HTTPException(status_code=422, detail="Document has no version to publish")
    version = session.get(DocumentVersion, doc.current_version_id)
    if version.status not in _MANUAL_VERSION_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot publish a version with status {version.status!r}",
        )

    contact = session.get(Contact, body.approved_by_contact_id)
    if contact is None or contact.org_id != org_id:
        raise HTTPException(
            status_code=422, detail="approved_by_contact_id must be a contact in this org"
        )

    now = datetime.now(UTC)
    version.status = "approved"
    version.approved_at = now
    version.approved_by_contact_id = body.approved_by_contact_id
    session.flush()

    # N.3: the authoritative approval record. The two lines above are now a
    # denormalized snapshot of *this* row -- see DocumentApproval's
    # docstring. Written here, inside the one path that can grant approval,
    # so there is no way to become approved without an approval record.
    record_approval(
        session,
        document=doc,
        version=version,
        contact=contact,
        approval_type="initial",
        now=now,
    )

    # Republish: supersede any other version of this document still marked
    # approved, and archive the evidence links its own Evidence row(s)
    # produced -- never the Evidence rows themselves.
    other_approved = session.scalars(
        select(DocumentVersion).where(
            DocumentVersion.document_id == doc.id,
            DocumentVersion.id != version.id,
            DocumentVersion.status == "approved",
        )
    ).all()
    if other_approved:
        superseded_ids = [v.id for v in other_approved]
        for v in other_approved:
            v.status = "superseded"
        prior_evidence_ids = session.scalars(
            select(Evidence.id).where(Evidence.source_document_version_id.in_(superseded_ids))
        ).all()
        if prior_evidence_ids:
            old_links = session.scalars(
                select(EvidenceStateLink).where(
                    EvidenceStateLink.evidence_id.in_(prior_evidence_ids),
                    EvidenceStateLink.is_archived.is_(False),
                )
            ).all()
            for link in old_links:
                link.is_archived = True
                link.archived_at = now
        session.flush()

    ev = Evidence(
        org_id=org_id, kind="reference", title=doc.title, artifact_type="document",
        reference_location=doc.doc_id, collected_at=now, source_document_version_id=version.id,
    )
    session.add(ev)
    session.flush()

    tag_objective_ids = session.scalars(
        select(DocumentObjectiveTag.objective_id).where(DocumentObjectiveTag.document_id == doc.id)
    ).all()
    linked_count = 0
    if tag_objective_ids:
        assessment_ids = session.scalars(
            select(Assessment.id).where(
                Assessment.org_id == org_id, Assessment.status == "in_progress"
            )
        ).all()
        if assessment_ids:
            control_states = session.scalars(
                select(ControlState).where(
                    ControlState.assessment_id.in_(assessment_ids),
                    ControlState.objective_id.in_(tag_objective_ids),
                )
            ).all()
            for cs in control_states:
                session.add(EvidenceStateLink(evidence_id=ev.id, control_state_id=cs.id))
                linked_count += 1
    session.flush()

    log_event(
        session, org_id=org_id, action="document.publish", entity_type="document_version",
        entity_id=version.id,
        after_value={
            "doc_id": doc.doc_id, "version_number": version.version_number,
            "approved_by_contact_id": str(body.approved_by_contact_id),
            "evidence_id": str(ev.id), "linked_control_states": linked_count,
        },
        context={"via": "api"},
    )
    # Built before commit -- see create_document's own comment for why
    # (doc.updated_at's onupdate; here version.status/approved_at/
    # approved_by_contact_id were also just flushed on a row read back
    # into the response, same trap).
    result = _document_out(session, doc)
    session.commit()
    return result


# ---------------------------------------------------------------------------
# Version detail, diff, history (roadmap N.2)
# ---------------------------------------------------------------------------
#
# Note what these endpoints deliberately do NOT return: rendered HTML.
# Bodies go to the browser as Markdown and are rendered there into React
# elements, so the SPA never needs `dangerouslySetInnerHTML` -- an
# invariant the frontend holds everywhere today, and that this slice was
# not willing to be the first to break. markdown_doc.render_html exists
# for the bundle/PDF path, which has no DOM to render into. See
# markdown_doc.py's own docstring for the full two-renderer argument.


class VersionDetailOut(DocumentVersionOut):
    """A single version, with the objective set it actually covers.

    `objective_ids` is resolved differently depending on status, and
    `objective_basis` says which way, because the honest answer differs:

      published  -- for an approved or superseded version, the exact set
                    that version was published against, recovered from
                    the EvidenceStateLink rows its own Evidence row
                    produced (archived ones included -- a republish
                    archives them, and "what did v2 cover" must survive
                    that). Recorded state, not inference.
      current    -- for a draft or under_review version there is no such
                    record yet, so this is the document's tag set as it
                    stands right now.

    DocumentObjectiveTag hangs off `document`, not `document_version`, so
    tags are not versioned state and "the tags as of version 3" is not a
    question the schema can answer for an unpublished version. Naming the
    basis is the honest alternative to quietly presenting one as the
    other; the audit timeline carries the tag add/remove actions that fill
    the gap.
    """

    objective_ids: list[uuid.UUID]
    objective_basis: str


class SpanOut(BaseModel):
    start: int
    end: int


class DiffRowOut(BaseModel):
    op: str
    old_line_no: int | None = None
    new_line_no: int | None = None
    old_text: str | None = None
    new_text: str | None = None
    old_spans: list[SpanOut] = []
    new_spans: list[SpanOut] = []
    skipped: int = 0


class BodyDiffOut(BaseModel):
    rows: list[DiffRowOut]
    added_lines: int
    removed_lines: int
    changed_lines: int
    identical: bool
    is_initial: bool


class SetDiffOut(BaseModel):
    added: list[uuid.UUID]
    removed: list[uuid.UUID]
    unchanged: list[uuid.UUID]
    changed: bool


class DocumentDiffOut(BaseModel):
    document_id: uuid.UUID
    from_version: VersionDetailOut | None
    to_version: VersionDetailOut
    body: BodyDiffOut
    objectives: SetDiffOut
    objective_basis_note: str | None = None
    events: list[dict]


class DocumentHistoryOut(BaseModel):
    document_id: uuid.UUID
    versions: list[DocumentVersionOut]
    events: list[dict]


_DOCUMENT_AUDIT_ACTIONS = (
    "document.create",
    "document.update",
    "document.delete",
    "document.version.create",
    "document.version.status_change",
    "document.objective_tag.add",
    "document.objective_tag.remove",
    "document.publish",
)


def _published_objective_ids(session: Session, version: DocumentVersion) -> list[uuid.UUID]:
    """Objectives a published version actually landed against.

    Archived links are included on purpose: a later republish archives
    them (publish_document's own step 2), and the question this answers
    is "what did THIS version cover when it was approved" -- which that
    archival must not erase.
    """
    return list(
        session.scalars(
            select(ControlState.objective_id)
            .join(EvidenceStateLink, EvidenceStateLink.control_state_id == ControlState.id)
            .join(Evidence, Evidence.id == EvidenceStateLink.evidence_id)
            .where(Evidence.source_document_version_id == version.id)
            .distinct()
        ).all()
    )


def _current_tag_ids(session: Session, document_id: uuid.UUID) -> list[uuid.UUID]:
    return list(
        session.scalars(
            select(DocumentObjectiveTag.objective_id).where(
                DocumentObjectiveTag.document_id == document_id
            )
        ).all()
    )


def _version_detail_out(session: Session, version: DocumentVersion) -> VersionDetailOut:
    if version.status in ("approved", "superseded"):
        ids = _published_objective_ids(session, version)
        basis = "published"
    else:
        ids = _current_tag_ids(session, version.document_id)
        basis = "current"
    return VersionDetailOut(
        **_version_out(version).model_dump(), objective_ids=ids, objective_basis=basis
    )


def _body_diff_out(diff: BodyDiff) -> BodyDiffOut:
    return BodyDiffOut(
        rows=[
            DiffRowOut(
                op=r.op,
                old_line_no=r.old_line_no,
                new_line_no=r.new_line_no,
                old_text=r.old_text,
                new_text=r.new_text,
                old_spans=[SpanOut(start=s.start, end=s.end) for s in r.old_spans],
                new_spans=[SpanOut(start=s.start, end=s.end) for s in r.new_spans],
                skipped=r.skipped,
            )
            for r in diff.rows
        ],
        added_lines=diff.added_lines,
        removed_lines=diff.removed_lines,
        changed_lines=diff.changed_lines,
        identical=diff.identical,
        is_initial=diff.is_initial,
    )


def _document_events(
    session: Session,
    org_id: uuid.UUID,
    doc: Document,
    *,
    since: datetime | None = None,
    until: datetime | None = None,
) -> list[dict]:
    """Audit rows for this document and every one of its versions.

    Roadmap N.2's own instruction: no parallel history table. The versions
    ARE the content record and audit_log IS the action record, so "who
    changed what, when" is those two joined at read time, not a third
    store that has to be kept in sync with both.

    Matched on entity_id, not action name alone -- a document action is
    logged against either the document (create/update/tag) or a specific
    version (version.create/status_change/publish), so both id sets are
    in scope.
    """
    version_ids = list(
        session.scalars(
            select(DocumentVersion.id).where(DocumentVersion.document_id == doc.id)
        ).all()
    )
    entity_ids = [doc.id, *version_ids]
    query = select(AuditLog).where(
        AuditLog.org_id == org_id,
        AuditLog.action.in_(_DOCUMENT_AUDIT_ACTIONS),
        AuditLog.entity_id.in_(entity_ids),
    )
    if since is not None:
        query = query.where(AuditLog.created_at >= since)
    if until is not None:
        query = query.where(AuditLog.created_at <= until)
    rows = list(
        session.scalars(query.order_by(AuditLog.created_at.desc(), AuditLog.id.desc())).all()
    )
    users_by_id = resolve_identities(session, org_id, rows)
    out: list[dict] = []
    for r in rows:
        actor_id = parse_actor_uuid(r.actor)
        out.append(
            {
                "id": str(r.id),
                "created_at": r.created_at.isoformat(),
                "action": r.action,
                "actor": r.actor,
                "actor_type": r.actor_type,
                "actor_user": identity_out(actor_id, users_by_id)
                if actor_id is not None
                else None,
                "entity_type": r.entity_type,
                "entity_id": str(r.entity_id),
                "before_value": r.before_value,
                "after_value": r.after_value,
            }
        )
    return out


@router.get(
    "/{org_id}/documents/{document_id}/versions/{version_id}", response_model=VersionDetailOut
)
def get_document_version(
    org_id: uuid.UUID,
    document_id: uuid.UUID,
    version_id: uuid.UUID,
    session: Session = Depends(get_session),
) -> VersionDetailOut:
    doc = _get_document(session, org_id, document_id)
    version = session.get(DocumentVersion, version_id)
    if version is None or version.document_id != doc.id:
        raise HTTPException(status_code=404, detail="Version not found")
    return _version_detail_out(session, version)


@router.get("/{org_id}/documents/{document_id}/history", response_model=DocumentHistoryOut)
def get_document_history(
    org_id: uuid.UUID, document_id: uuid.UUID, session: Session = Depends(get_session)
) -> DocumentHistoryOut:
    """Versions and audit actions together -- roadmap N.2's section 4."""
    doc = _get_document(session, org_id, document_id)
    versions = session.scalars(
        select(DocumentVersion)
        .where(DocumentVersion.document_id == doc.id)
        .order_by(DocumentVersion.version_number.desc())
    ).all()
    return DocumentHistoryOut(
        document_id=doc.id,
        versions=[_version_out(v) for v in versions],
        events=_document_events(session, org_id, doc),
    )


@router.get("/{org_id}/documents/{document_id}/diff", response_model=DocumentDiffOut)
def get_document_diff(
    org_id: uuid.UUID,
    document_id: uuid.UUID,
    session: Session = Depends(get_session),
    to_version_id: uuid.UUID | None = None,
    from_version_id: uuid.UUID | None = None,
    context_lines: int = 3,
) -> DocumentDiffOut:
    """Diff any two versions of one document.

    Defaults chosen for the common case -- "what changed since we approved
    this": `to_version_id` defaults to the current version and
    `from_version_id` to the version immediately preceding it by
    version_number, so diffing a superseded version against the current
    one is the zero-argument call.

    Version 1 is not a special case for the caller: with no preceding
    version, `from_version` comes back null and the body diff carries
    `is_initial`, so the view renders "initial version" rather than an
    empty or broken panel.
    """
    doc = _get_document(session, org_id, document_id)
    if context_lines < 0 or context_lines > 100:
        raise HTTPException(status_code=422, detail="context_lines must be between 0 and 100")

    if to_version_id is None:
        if doc.current_version_id is None:
            raise HTTPException(status_code=422, detail="Document has no versions to diff")
        to_version = session.get(DocumentVersion, doc.current_version_id)
    else:
        to_version = session.get(DocumentVersion, to_version_id)
    if to_version is None or to_version.document_id != doc.id:
        raise HTTPException(status_code=404, detail="Version not found")

    if from_version_id is None:
        from_version = session.scalars(
            select(DocumentVersion)
            .where(
                DocumentVersion.document_id == doc.id,
                DocumentVersion.version_number < to_version.version_number,
            )
            .order_by(DocumentVersion.version_number.desc())
            .limit(1)
        ).first()
    else:
        from_version = session.get(DocumentVersion, from_version_id)
        if from_version is None or from_version.document_id != doc.id:
            raise HTTPException(status_code=404, detail="Version not found")

    to_detail = _version_detail_out(session, to_version)
    from_detail = _version_detail_out(session, from_version) if from_version else None

    body = diff_bodies(
        from_version.body if from_version else None,
        to_version.body,
        context_lines=context_lines,
    )
    obj = diff_sets(
        [str(i) for i in (from_detail.objective_ids if from_detail else [])],
        [str(i) for i in to_detail.objective_ids],
    )

    # A tag change matters to the SSP even when the body is byte-identical
    # -- the objective set a document answers for is what the
    # implementation statements and the evidence manifest are built from.
    # Say plainly when the two sides were resolved on different bases,
    # rather than presenting an unpublished draft's live tag set as if it
    # were a record of what that version covered.
    note = None
    if from_detail is not None and from_detail.objective_basis != to_detail.objective_basis:
        note = (
            f"Objective sets compared on different bases: version "
            f"{from_detail.version_number} is {from_detail.objective_basis}, version "
            f"{to_detail.version_number} is {to_detail.objective_basis}. A 'current' basis "
            f"reflects the document's tags as they stand now, not as they stood at that "
            f"version -- tags are document-level, not version-level."
        )
    elif to_detail.objective_basis == "current":
        note = (
            "Both versions are unpublished, so the objective set shown is the document's "
            "current tag set for each -- tags are document-level, not version-level. The "
            "timeline carries the individual tag changes."
        )

    events = _document_events(
        session,
        org_id,
        doc,
        since=from_version.created_at if from_version else None,
        until=to_version.created_at,
    )

    return DocumentDiffOut(
        document_id=doc.id,
        from_version=from_detail,
        to_version=to_detail,
        body=_body_diff_out(body),
        objectives=SetDiffOut(
            added=[uuid.UUID(i) for i in obj.added],
            removed=[uuid.UUID(i) for i in obj.removed],
            unchanged=[uuid.UUID(i) for i in obj.unchanged],
            changed=obj.changed,
        ),
        objective_basis_note=note,
        events=events,
    )


# ---------------------------------------------------------------------------
# N.3 -- approval history and re-approval
# ---------------------------------------------------------------------------


class ReaffirmIn(BaseModel):
    """Re-approve the currently approved version, unchanged.

    `approved_by_contact_id` is the named person whose decision this is --
    required, never defaulted to the authenticated caller, because the whole
    point of the Contact/User split is that the approver frequently is not a
    login (DocumentApproval's docstring). Defaulting it would quietly record
    the operator as the approver, which is the misattribution this design
    exists to avoid.
    """

    approved_by_contact_id: uuid.UUID
    note: str | None = None


def _approval_out(session: Session, approval: DocumentApproval) -> ApprovalOut:
    version = session.get(DocumentVersion, approval.document_version_id)
    return ApprovalOut(
        id=approval.id,
        document_version_id=approval.document_version_id,
        version_number=version.version_number if version is not None else None,
        approval_type=approval.approval_type,
        approved_at=approval.approved_at,
        approved_by_contact_id=approval.approved_by_contact_id,
        approver_name=approval.approver_name,
        note=approval.note,
    )


@router.get(
    "/{org_id}/documents/{document_id}/approvals",
    response_model=list[ApprovalOut],
)
def list_document_approvals(
    org_id: uuid.UUID, document_id: uuid.UUID, session: Session = Depends(get_session)
) -> list[ApprovalOut]:
    """Full approval history, newest first -- "reviewed and still current on
    <date> by <person>", which is what a periodic-review control asks for.

    Readable by `c3pao_assessor`: this is exactly the evidence an assessor
    wants, and `require_write` (applied at router level) gates methods, not
    routes, so a GET stays open to read-only roles while the reaffirm POST
    below does not.
    """
    _get_document(session, org_id, document_id)
    return [
        _approval_out(session, a)
        for a in approvals_for_document(session, document_id=document_id)
    ]


@router.post(
    "/{org_id}/documents/{document_id}/reaffirm",
    response_model=DocumentDetailOut,
)
def reaffirm_document(
    org_id: uuid.UUID,
    document_id: uuid.UUID,
    body: ReaffirmIn,
    session: Session = Depends(get_session),
) -> DocumentDetailOut:
    """Record that the approved version was reviewed and is still current.

    **Creates no new version, and mutates nothing.** The approved version's
    body stays byte-identical, its status stays `approved`, its evidence
    links stay exactly as they are, and N.2's diff history gains no entry --
    because nothing changed. A byte-identical version would fake an edit and
    answer "what changed in this policy" with noise for every annual review
    that changed nothing. All that happens is one appended
    `document_approval` row, which moves the derived cadence verdict back to
    `current`.

    Requires an already-approved version. A draft has nothing to reaffirm,
    and treating "reaffirm a draft" as approval would grant approval outside
    `publish_document` -- the only path that attaches evidence.

    Refused for `c3pao_assessor` by the router-level `require_write` gate:
    an assessor reads approvals, never grants one.
    """
    try:
        approval = reaffirm(
            session,
            org_id=org_id,
            document_id=document_id,
            contact_id=body.approved_by_contact_id,
            now=datetime.now(UTC),
            note=body.note,
        )
    except DocumentReviewError as e:
        message = str(e)
        if "not found" in message:
            raise HTTPException(status_code=404, detail=message) from e
        if "must be a contact" in message:
            raise HTTPException(status_code=422, detail=message) from e
        raise HTTPException(status_code=409, detail=message) from e

    doc = _get_document(session, org_id, document_id)
    log_event(
        session,
        org_id=org_id,
        action="document.reaffirm",
        entity_type="document",
        entity_id=doc.id,
        context={
            "doc_id": doc.doc_id,
            "document_version_id": str(approval.document_version_id),
            # The named approver. Who was authenticated is resolved by
            # log_event itself, onto the actor columns -- two different
            # facts, and this is the one the cadence is satisfied by.
            "approver_name": approval.approver_name,
            "approved_by_contact_id": str(approval.approved_by_contact_id),
            "cadence_months": doc.cadence_months,
        },
    )
    session.commit()
    return _document_detail_out(session, doc)
