"""Document library (roadmap item N, slice N.1) -- the core, versioned
record shape. N.2 (browser editing/diffs), N.3 (approval workflow/review
cadence), N.4 (MSP templates) and N.5 (suggested documentation) all build
on what this router and models.py:Document/DocumentVersion/
DocumentObjectiveTag establish here -- see docs/PLAN-document-library.md.

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
  POST   /orgs/{org_id}/documents/{document_id}/objective-tags       Tag an objective
  DELETE /orgs/{org_id}/documents/{document_id}/objective-tags/{id}  Untag
  POST   /orgs/{org_id}/documents/{document_id}/publish              Approve current version

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

from ..audit import log_event
from ..auth import require_org_access, require_write
from ..db import get_session
from ..models import (
    Assessment,
    AssessmentObjective,
    Contact,
    ControlState,
    Document,
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

_DOC_TYPES = frozenset({"policy", "procedure", "plan", "list", "sop", "form", "other"})
_MANUAL_VERSION_STATUSES = frozenset({"draft", "under_review"})

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


class DocumentOut(BaseModel):
    id: uuid.UUID
    doc_id: str
    doc_type: str
    title: str
    cadence_months: int
    is_template_derived: bool
    template_ref: str | None
    current_version: DocumentVersionOut | None
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


def _document_out(session: Session, doc: Document) -> DocumentOut:
    current = (
        session.get(DocumentVersion, doc.current_version_id) if doc.current_version_id else None
    )
    return DocumentOut(
        id=doc.id, doc_id=doc.doc_id, doc_type=doc.doc_type, title=doc.title,
        cadence_months=doc.cadence_months, is_template_derived=doc.is_template_derived,
        template_ref=doc.template_ref,
        current_version=_version_out(current) if current is not None else None,
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
def list_documents(org_id: uuid.UUID, session: Session = Depends(get_session)) -> list[DocumentOut]:
    docs = session.scalars(
        select(Document).where(Document.org_id == org_id).order_by(Document.doc_id)
    ).all()
    return [_document_out(session, d) for d in docs]


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
    """
    doc = _get_document(session, org_id, document_id)
    max_version = session.scalar(
        select(func.max(DocumentVersion.version_number)).where(
            DocumentVersion.document_id == doc.id
        )
    ) or 0

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
