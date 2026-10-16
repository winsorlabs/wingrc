"""List library API -- roadmap L.1.

A list is a definition plus its own versioned rows (docs/PLAN-list-library.md
§2). This is not `/orgs/{org_id}/lists`: that path belongs to the four
scope-graph `ListView`s (routers/scope.py), which keep working unchanged.

Endpoints:
  GET  /orgs/{org_id}/list-definitions                       ?control_key= filter
  POST /orgs/{org_id}/list-definitions                       build a list by hand (L.2.2)
  GET  /orgs/{org_id}/list-definitions/{list_id}             current version
  GET  /orgs/{org_id}/list-definitions/{list_id}/versions    every version
  GET  /orgs/{org_id}/list-definitions/{list_id}/versions/{n}
  GET  /orgs/{org_id}/list-definitions/{list_id}/export        .xlsx, ?version=n
  POST /orgs/{org_id}/list-definitions/{list_id}/versions    edit = new version
  POST   /orgs/{org_id}/list-definitions/{list_id}/tags        add a practice tag
  DELETE /orgs/{org_id}/list-definitions/{list_id}/tags/{key}  remove one
  GET  /orgs/{org_id}/list-definitions/{list_id}/tag-events  who tagged what, when
  POST /orgs/{org_id}/list-definitions/adopt                 copy templates in (L.7)
  POST /orgs/{org_id}/list-definitions/{list_id}/take-template-version
  GET  /orgs/{org_id}/list-library/import-report             what was never imported

Who may write (L.2.2): a customer contact fills lists in; it does not
define them. Defining a list -- building one, its columns, and its tags,
which say what it claims to serve -- is the practitioner's (DEFINE_ROLES:
msp_admin, consultant_admin, msp_engineer). customer_poc edits rows only:
a version it saves must keep the columns it was based on. Tagging is the
more consequential half, since once L.4 links a list as evidence a tag
widens or narrows what an assessment rests on. c3pao_assessor writes
nothing. Tag removal is further guarded by evidence, whatever the role
(list_library.tag_removal_blockers).

Adopting a template (L.7) copies it from the org that holds it -- the
MSP's, or this org itself when the MSP is also its own tenant -- into this
org's lists. Templates stay in their org; the person adopting must define
lists in both, and the router reads the template under its org and writes
the copy under this one (`_in_template_org`). customer_poc adopts nothing,
on the rows-only line: an adopted list is a list defined.

Content changes only by creating a version. It names the version it was based on
and is refused if that is no longer current -- the same optimistic
concurrency the document library uses, so two editors cannot silently
overwrite each other. The 409 says who saved the newer version and when,
because the person who gets it has to decide what to do with their edit
(L.2's screen re-applies it onto the newer version; it is never dropped).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import list_adoption, list_library
from ..audit import log_event
from ..auth import CurrentUser, require_org_access, require_write
from ..db import get_session
from ..list_templates import practice_key
from ..models import AuditLog, ListControlTag, ListDefinition, ListVersion, OrgMembership
from ..render import render_list_version_bytes
from ..rls import set_current_org

DEFINE_ROLES = ("msp_admin", "consultant_admin", "msp_engineer")

router = APIRouter(
    prefix="/orgs",
    tags=["list-library"],
    dependencies=[Depends(require_org_access()), Depends(require_write())],
)


class AdoptedFromOut(BaseModel):
    """Where a copy came from and whether its template has moved on. The
    template's title and key are as they were when the copy took it."""

    template_list_id: uuid.UUID
    template_org_id: uuid.UUID
    same_org: bool
    template_title: str
    template_key: str
    based_on_version_number: int
    head_version_number: int
    update_available: bool


class DistributionOut(BaseModel):
    version_number: int
    copies: int
    this_org: bool


class ListSummaryOut(BaseModel):
    id: uuid.UUID
    list_key: str
    title: str
    is_template: bool
    source_ref: str | None
    projection_view_id: str | None
    control_keys: list[str]
    version_number: int
    column_count: int
    row_count: int
    updated_at: datetime
    saved_by: str | None
    cause: str  # the current version's
    merged_from: list[str]
    has_changelog: bool
    example_rows: int
    adopted_from: AdoptedFromOut | None = None
    adopted_copies: int = 0  # a template's copies, in every org


class ListVersionOut(BaseModel):
    id: uuid.UUID
    version_number: int
    title: str
    description: str | None
    responsible: str | None
    review_cadence: str | None
    columns: list[str]
    rows: list[list[str]]
    provenance: dict
    created_at: datetime
    saved_by: str | None
    # Why this version exists: a list_library.CAUSES key, stored or derived.
    cause: str
    # Rows still a template's [PLACEHOLDER] example (list_library.is_populated).
    example_rows: int


class MergeColumnOut(BaseModel):
    source_column: str
    target_column: str
    added: bool


class MergeOut(BaseModel):
    """The recorded de-duplication that made this one list out of two
    template sheets, so the screen can show it rather than leave it
    implied by two tags."""

    target_ref: str
    absorbed_ref: str
    columns: list[MergeColumnOut]


class ListDetailOut(BaseModel):
    id: uuid.UUID
    list_key: str
    is_template: bool
    source_ref: str | None
    projection_view_id: str | None
    control_keys: list[str]
    merge: MergeOut | None
    current: ListVersionOut
    adopted_from: AdoptedFromOut | None = None
    distribution: list[DistributionOut] = Field(default_factory=list)
    own_copy_id: uuid.UUID | None = None  # this org's copy of this template


class NotImportedOut(BaseModel):
    source_ref: str
    status: str
    reason: str


class ImportReportOut(BaseModel):
    recorded_at: datetime | None
    not_imported: list[NotImportedOut]
    not_ingested: list[str]


class ListCreateIn(BaseModel):
    """The template header block's fields, and only those (L.2.2): one
    definition schema, two ways in. `origin_note` is a person's note on
    where the content came from -- never import lineage."""

    title: str = Field(min_length=1, max_length=400)
    control_keys: list[str] = Field(min_length=1)  # row 2
    description: str = ""  # row 4, What goes here
    responsible: str = ""  # row 5
    review_cadence: str = ""  # row 5
    columns: list[str] = Field(min_length=1)  # row 10
    rows: list[list[str]] = Field(default_factory=list)
    origin_note: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def _check(self) -> ListCreateIn:
        if not self.title.strip():
            raise ValueError("a list needs a title")
        if any(not c.strip() for c in self.columns):
            raise ValueError("column names must not be blank")
        if len({c.strip().lower() for c in self.columns}) != len(self.columns):
            raise ValueError("column names must be distinct")
        self.control_keys = list(dict.fromkeys(practice_key(k) for k in self.control_keys))
        bad = [i for i, r in enumerate(self.rows) if len(r) != len(self.columns)]
        if bad:
            raise ValueError(
                f"rows {bad[:5]} do not have {len(self.columns)} cells, one per column"
            )
        return self


class TagIn(BaseModel):
    control_key: str

    @model_validator(mode="after")
    def _check(self) -> TagIn:
        self.control_key = practice_key(self.control_key)
        return self


class TagEventOut(BaseModel):
    id: uuid.UUID
    control_key: str
    change: str  # added | removed
    via: str  # template_import | api
    saved_by: str | None
    at_version: int | None
    note: str | None
    created_at: datetime


class NewVersionIn(BaseModel):
    base_version_id: uuid.UUID
    columns: list[str] | None = None  # defaults to the base version's
    rows: list[list[str]] = Field(default_factory=list)
    note: str | None = None

    @model_validator(mode="after")
    def _no_blank_columns(self) -> NewVersionIn:
        if self.columns is not None and any(not c.strip() for c in self.columns):
            raise ValueError("column names must not be blank")
        return self


def _definition(session: Session, org_id: uuid.UUID, list_id: uuid.UUID) -> ListDefinition:
    d = session.get(ListDefinition, list_id)
    if d is None or d.org_id != org_id:
        raise HTTPException(status_code=404, detail="List not found")
    return d


def _saved_by(v: ListVersion) -> str | None:
    return (v.provenance or {}).get("saved_by")


def _version_out(v: ListVersion, cause: str) -> ListVersionOut:
    return ListVersionOut(
        id=v.id, version_number=v.version_number, title=v.title, description=v.description,
        responsible=v.responsible, review_cadence=v.review_cadence, columns=list(v.columns),
        rows=[list(r) for r in v.rows], provenance=v.provenance or {},
        created_at=v.created_at, saved_by=_saved_by(v), cause=cause,
        example_rows=list_library.example_rows(v.rows),
    )


def _adopted_from(
    d: ListDefinition,
    history: list[ListVersion],
    heads: dict[uuid.UUID, list_adoption.TemplateHead],
) -> AdoptedFromOut | None:
    head = heads.get(d.id)
    if head is None:
        return None
    p = list_adoption.last_adoption(history).provenance or {}
    return AdoptedFromOut(
        template_list_id=head.template_list_id, template_org_id=head.template_org_id,
        same_org=head.template_org_id == d.org_id,
        template_title=p.get("template_title", ""), template_key=p.get("template_key", ""),
        based_on_version_number=head.based_on_version_number,
        head_version_number=head.head_version_number,
        update_available=head.update_available,
    )


def _causes(session: Session, d: ListDefinition) -> dict[uuid.UUID, str]:
    return list_library.version_causes(list_library.history_of(session, d.id))


def _merge_out(d: ListDefinition, v: ListVersion) -> MergeOut | None:
    rule = list_library.merge_rule_for((v.provenance or {}).get("merged_from") or [])
    if rule is None:
        return None
    return MergeOut(
        target_ref=d.source_ref or f"{rule.target[0]}#{rule.target[1]}",
        absorbed_ref=f"{rule.absorbed[0]}#{rule.absorbed[1]}",
        columns=[
            MergeColumnOut(source_column=src, target_column=dst, added=dst in rule.added_columns)
            for src, dst in rule.column_map.items()
        ],
    )


@router.get("/{org_id}/list-definitions", response_model=list[ListSummaryOut])
def list_definitions(
    org_id: uuid.UUID,
    control_key: str | None = Query(default=None),
    session: Session = Depends(get_session),
) -> list[ListSummaryOut]:
    stmt = select(ListDefinition).where(ListDefinition.org_id == org_id)
    if control_key is not None:
        stmt = stmt.where(
            ListDefinition.id.in_(
                select(ListControlTag.list_id).where(ListControlTag.control_key == control_key)
            )
        )
    out = []
    heads = list_adoption.template_heads(session)
    spread = list_adoption.template_distribution(session)
    for d in session.scalars(stmt.order_by(ListDefinition.list_key, ListDefinition.id)):
        v = session.get(ListVersion, d.current_version_id)
        history = list_library.history_of(session, d.id)
        cause = list_library.version_causes(history)[v.id]
        out.append(
            ListSummaryOut(
                id=d.id, list_key=d.list_key, title=v.title, is_template=d.is_template,
                source_ref=d.source_ref, projection_view_id=d.projection_view_id,
                control_keys=list(list_library.tags_of(session, d.id)),
                version_number=v.version_number, column_count=len(v.columns),
                row_count=len(v.rows), updated_at=v.created_at, saved_by=_saved_by(v), cause=cause,
                merged_from=list((v.provenance or {}).get("merged_from") or []),
                has_changelog=bool((v.provenance or {}).get("changelogs")),
                example_rows=list_library.example_rows(v.rows),
                adopted_from=_adopted_from(d, history, heads),
                adopted_copies=sum(x.copies for x in spread.get(d.id, [])),
            )
        )
    return out


@router.post("/{org_id}/list-definitions", response_model=ListDetailOut, status_code=201)
def create_list_definition(
    org_id: uuid.UUID,
    body: ListCreateIn,
    session: Session = Depends(get_session),
    current_user: CurrentUser = Depends(require_org_access(*DEFINE_ROLES)),
) -> ListDetailOut:
    """Build a list by hand. v1 records `created_by_hand`, never
    `first_import`: there was no source."""
    who = current_user.display_name or current_user.email
    content = {
        "title": body.title.strip(), "description": body.description.strip(),
        "responsible": body.responsible.strip(), "review_cadence": body.review_cadence.strip(),
        "columns": [c.strip() for c in body.columns], "rows": body.rows,
    }
    try:
        d = list_library.create_by_hand(
            session, org_id, content, tuple(body.control_keys), who, body.origin_note
        )
    except list_library.ListKeyTaken as e:
        raise HTTPException(
            status_code=409,
            detail={
                "message": f"This org already has a list named like that ({e.existing.list_key}). "
                "Give the new one a different title.",
                "list_id": str(e.existing.id),
            },
        ) from None
    log_event(
        session, org_id=org_id, action="list.create", entity_type="list_definition",
        entity_id=d.id,
        after_value={
            "version_number": 1, "columns": content["columns"], "row_count": len(body.rows),
            "control_keys": list(body.control_keys),
        },
        context={"via": "api", "list_key": d.list_key, "cause": "created_by_hand"},
    )
    session.commit()
    return get_list_definition(org_id, d.id, session)


@router.get("/{org_id}/list-definitions/{list_id}", response_model=ListDetailOut)
def get_list_definition(
    org_id: uuid.UUID, list_id: uuid.UUID, session: Session = Depends(get_session)
) -> ListDetailOut:
    d = _definition(session, org_id, list_id)
    v = session.get(ListVersion, d.current_version_id)
    history = list_library.history_of(session, d.id)
    own = list_adoption.existing_copy(session, org_id, d.id) if d.is_template else None
    return ListDetailOut(
        id=d.id, list_key=d.list_key, is_template=d.is_template, source_ref=d.source_ref,
        projection_view_id=d.projection_view_id,
        control_keys=list(list_library.tags_of(session, d.id)),
        merge=_merge_out(d, v),
        current=_version_out(v, list_library.version_causes(history)[v.id]),
        adopted_from=_adopted_from(d, history, list_adoption.template_heads(session)),
        distribution=[
            DistributionOut(version_number=x.version_number, copies=x.copies, this_org=x.this_org)
            for x in list_adoption.template_distribution(session).get(d.id, [])
        ],
        own_copy_id=own.id if own else None,
    )


@router.get("/{org_id}/list-definitions/{list_id}/versions", response_model=list[ListVersionOut])
def list_versions(
    org_id: uuid.UUID, list_id: uuid.UUID, session: Session = Depends(get_session)
) -> list[ListVersionOut]:
    d = _definition(session, org_id, list_id)
    history = list_library.history_of(session, d.id)
    causes = list_library.version_causes(history)
    return [_version_out(v, causes[v.id]) for v in history]


@router.get(
    "/{org_id}/list-definitions/{list_id}/versions/{version_number}",
    response_model=ListVersionOut,
)
def get_version(
    org_id: uuid.UUID,
    list_id: uuid.UUID,
    version_number: int,
    session: Session = Depends(get_session),
) -> ListVersionOut:
    d = _definition(session, org_id, list_id)
    v = session.scalar(
        select(ListVersion).where(
            ListVersion.list_id == d.id, ListVersion.version_number == version_number
        )
    )
    if v is None:
        raise HTTPException(status_code=404, detail="Version not found")
    return _version_out(v, _causes(session, d)[v.id])


@router.get("/{org_id}/list-definitions/{list_id}/export")
def export_list(
    org_id: uuid.UUID,
    list_id: uuid.UUID,
    version: int | None = Query(default=None, ge=1),
    session: Session = Depends(get_session),
) -> Response:
    """One list version as .xlsx, for handing to an assessor (roadmap L.3).

    A GET, so a read-only role -- the assessor -- can export: the lists
    slice once built its export as a POST, which put it behind
    require_write() and gave a C3PAO a 403 on the one artifact they most
    want. Rendered in memory, never to a shared path. The same renderer
    the bundle uses, so a list exported here and the same version in a
    bundle are the same bytes. The version id is in the workbook, in the
    X-WinGRC-List-Version-Id header, and in the audit row."""
    d = _definition(session, org_id, list_id)
    if version is None:
        v = session.get(ListVersion, d.current_version_id)
    else:
        v = session.scalar(
            select(ListVersion).where(
                ListVersion.list_id == d.id, ListVersion.version_number == version
            )
        )
        if v is None:
            raise HTTPException(status_code=404, detail="Version not found")
    snap = list_library.snap_version(d, v, list_library.tags_of(session, d.id))
    body = render_list_version_bytes(snap)
    log_event(
        session, org_id=org_id, action="list.export", entity_type="list_definition",
        entity_id=d.id,
        after_value={"version_id": snap.version_id, "version_number": snap.version_number},
        context={"list_key": d.list_key, "via": "api"},
    )
    session.commit()
    return Response(
        content=body,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="{snap.filename}"',
            "X-WinGRC-List-Version-Id": snap.version_id,
        },
    )


@router.post(
    "/{org_id}/list-definitions/{list_id}/versions",
    response_model=ListVersionOut,
    status_code=201,
)
def create_list_version(
    org_id: uuid.UUID,
    list_id: uuid.UUID,
    body: NewVersionIn,
    session: Session = Depends(get_session),
    current_user: CurrentUser = Depends(require_org_access()),
) -> ListVersionOut:
    d = _definition(session, org_id, list_id)
    if body.base_version_id != d.current_version_id:
        current = session.get(ListVersion, d.current_version_id)
        raise HTTPException(
            status_code=409,
            detail={
                "message": (
                    "This list changed while you were editing: someone saved a newer "
                    "version. Your edit has not been saved, and has not been lost."
                ),
                "base_version_id": str(body.base_version_id),
                "current_version_id": str(current.id),
                "current_version_number": current.version_number,
                "saved_by": _saved_by(current),
                "saved_at": current.created_at.isoformat(),
            },
        )
    base = session.get(ListVersion, d.current_version_id)
    columns = body.columns if body.columns is not None else list(base.columns)
    if columns != list(base.columns) and current_user.role not in DEFINE_ROLES:
        raise HTTPException(
            status_code=403,
            detail="You can edit this list's rows; its columns are set by your MSP.",
        )
    bad = [i for i, r in enumerate(body.rows) if len(r) != len(columns)]
    if bad:
        raise HTTPException(
            status_code=422,
            detail=f"rows {bad[:5]} do not have {len(columns)} cells, one per column",
        )
    content = list_library.content_of(base) | {"columns": columns, "rows": body.rows}
    v = list_library.create_version(
        session, d, content,
        {
            "via": "api",
            "cause": "edit",
            "based_on_version": base.version_number,
            "note": body.note or "",
            "saved_by": current_user.display_name or current_user.email,
        },
    )
    log_event(
        session, org_id=org_id, action="list.version_create", entity_type="list_definition",
        entity_id=d.id,
        before_value={"version_number": base.version_number, "row_count": len(base.rows)},
        after_value={
            "version_number": v.version_number,
            "row_count": len(v.rows),
            **list_library.row_changes(base.rows, v.rows),
            # By name: rows are positional, so a renamed column keeps its
            # cells and reads here as one removed and one added.
            "columns_added": [c for c in columns if c not in base.columns],
            "columns_removed": [c for c in base.columns if c not in columns],
        },
        context={"via": "api", "list_key": d.list_key, "note": body.note or ""},
    )
    session.commit()
    return _version_out(v, "edit")


def _event_out(e) -> TagEventOut:
    return TagEventOut(
        id=e.id, control_key=e.control_key, change=e.change, via=e.via, saved_by=e.saved_by,
        at_version=e.at_version, note=e.note, created_at=e.created_at,
    )


@router.get(
    "/{org_id}/list-definitions/{list_id}/tag-events", response_model=list[TagEventOut]
)
def list_tag_events(
    org_id: uuid.UUID, list_id: uuid.UUID, session: Session = Depends(get_session)
) -> list[TagEventOut]:
    d = _definition(session, org_id, list_id)
    return [_event_out(e) for e in list_library.tag_events(session, d.id)]


@router.post(
    "/{org_id}/list-definitions/{list_id}/tags", response_model=TagEventOut, status_code=201
)
def add_list_tag(
    org_id: uuid.UUID,
    list_id: uuid.UUID,
    body: TagIn,
    session: Session = Depends(get_session),
    current_user: CurrentUser = Depends(require_org_access(*DEFINE_ROLES)),
) -> TagEventOut:
    """Tag a list to a further practice. Adding is safe -- it makes the list
    a candidate there, nothing more -- so it needs only the record."""
    d = _definition(session, org_id, list_id)
    event = list_library.add_tag(
        session, d, body.control_key, "api", current_user.display_name or current_user.email
    )
    if event is None:
        raise HTTPException(status_code=409, detail=f"This list already serves {body.control_key}.")
    log_event(
        session, org_id=org_id, action="list.tag_add", entity_type="list_definition",
        entity_id=d.id, after_value={"control_key": body.control_key},
        context={"via": "api", "list_key": d.list_key, "tag_event_id": str(event.id)},
    )
    session.commit()
    return _event_out(event)


@router.delete(
    "/{org_id}/list-definitions/{list_id}/tags/{control_key}", response_model=TagEventOut
)
def remove_list_tag(
    org_id: uuid.UUID,
    list_id: uuid.UUID,
    control_key: str,
    session: Session = Depends(get_session),
    current_user: CurrentUser = Depends(require_org_access(*DEFINE_ROLES)),
) -> TagEventOut:
    """The dangerous direction: it narrows what the list claims to serve.
    Refused while evidence rests on the tag, naming the link; refused for
    the last tag, since a list serves at least one practice (row 2)."""
    d = _definition(session, org_id, list_id)
    origins = list_library.tag_origins(session, d.id)
    if control_key not in origins:
        raise HTTPException(status_code=404, detail=f"This list does not serve {control_key}.")
    if len(origins) == 1:
        raise HTTPException(
            status_code=409,
            detail="A list serves at least one practice; add another before removing this one.",
        )
    try:
        event = list_library.remove_tag(
            session, d, control_key, "api", current_user.display_name or current_user.email
        )
    except list_library.TagRemovalRefused as e:
        raise HTTPException(
            status_code=409, detail={"message": str(e), "blocked_by": e.blockers}
        ) from None
    log_event(
        session, org_id=org_id, action="list.tag_remove", entity_type="list_definition",
        entity_id=d.id,
        before_value={"control_key": control_key, "added_via": origins[control_key]},
        context={"via": "api", "list_key": d.list_key, "tag_event_id": str(event.id)},
    )
    session.commit()
    return _event_out(event)


class AdoptIn(BaseModel):
    source_org_id: uuid.UUID  # the org holding the templates; may be this one
    template_ids: list[uuid.UUID] = Field(min_length=1, max_length=100)
    apply: bool = False  # false: show the set that would be written


class AdoptItemOut(BaseModel):
    template_id: uuid.UUID
    template_title: str
    template_version_number: int
    control_keys: list[str]
    row_count: int
    example_rows: int
    action: str  # create | already_adopted
    list_id: uuid.UUID | None  # the copy: new, or the one this org already keeps
    list_key: str


class AdoptOut(BaseModel):
    source_org_id: uuid.UUID
    same_org: bool
    applied: bool
    items: list[AdoptItemOut]


class TakeIn(BaseModel):
    apply: bool = False
    # "list_key@vN": the edit this update would replace, named exactly.
    overwrite_edit: str | None = None


class TakeOut(BaseModel):
    from_template_version: int
    to_template_version: int
    rows_from: str  # template: still its examples; copy: this org's own rows, carried
    changed_fields: list[str]
    columns_added: list[str]
    columns_removed: list[str]
    dropped_cells: int
    person_changed: list[str]
    edit_ref: str | None  # set when taking it needs that edit named
    tag_add: list[str]
    tag_remove: list[str]
    applied: bool
    version_number: int | None


def _in_template_org(session, current_user, template_org_id, home_org_id, read):
    """Runs `read` scoped to the template's org, then returns to this one.
    The person must define lists there too: membership in both orgs is
    what makes the reach across the boundary legitimate, and nothing else
    does. Flush before calling -- a pending write would land under the
    wrong org."""
    set_current_org(session, template_org_id)
    try:
        role = session.scalar(
            select(OrgMembership.role).where(
                OrgMembership.user_id == current_user.id,
                OrgMembership.org_id == template_org_id,
            )
        )
        if role is None:
            raise HTTPException(
                status_code=403, detail="You are not a member of the org that holds the template."
            )
        if role not in DEFINE_ROLES:
            raise HTTPException(
                status_code=403,
                detail="Adopting a template needs a role that defines lists in the org that "
                "holds it, as well as in this one.",
            )
        try:
            return read()
        except list_adoption.NotATemplate:
            raise HTTPException(
                status_code=404, detail="Template not found in that org. Nothing was written."
            ) from None
    finally:
        set_current_org(session, home_org_id)


@router.post("/{org_id}/list-definitions/adopt", response_model=AdoptOut)
def adopt_templates(
    org_id: uuid.UUID,
    body: AdoptIn,
    session: Session = Depends(get_session),
    current_user: CurrentUser = Depends(require_org_access(*DEFINE_ROLES)),
) -> AdoptOut:
    """Copy templates into this org's lists. Without `apply`, says what
    would be written -- each copy its own record, the set shown first. A
    template this org already keeps a copy of is reported, never copied
    twice. Audited on both sides when the orgs differ."""
    who = current_user.display_name or current_user.email
    ids = list(dict.fromkeys(body.template_ids))
    snaps = _in_template_org(
        session, current_user, body.source_org_id, org_id,
        lambda: [list_adoption.read_template(session, body.source_org_id, t) for t in ids],
    )
    same = body.source_org_id == org_id
    items, created = [], []
    for s in snaps:
        item = {
            "template_id": s.list_id, "template_title": s.content["title"],
            "template_version_number": s.version_number, "control_keys": list(s.control_keys),
            "row_count": len(s.content["rows"]),
            "example_rows": list_library.example_rows(s.content["rows"]),
        }
        existing = list_adoption.existing_copy(session, org_id, s.list_id)
        if existing is not None:
            items.append(AdoptItemOut(
                **item, action="already_adopted", list_id=existing.id, list_key=existing.list_key
            ))
            continue
        d = None
        if body.apply:
            try:
                d = list_adoption.adopt(session, org_id, s, who)
            except list_library.ListKeyTaken as e:
                raise HTTPException(
                    status_code=409,
                    detail=f"This org already has a list keyed {e.existing.list_key}, which is "
                    "not a copy of this template. Nothing was written.",
                ) from None
            created.append((s, d))
            log_event(
                session, org_id=org_id, action="list.adopt", entity_type="list_definition",
                entity_id=d.id,
                after_value={
                    "version_number": 1, "row_count": len(s.content["rows"]),
                    "control_keys": list(s.control_keys),
                },
                context={"via": "api", "list_key": d.list_key, "same_org": same}
                | s.provenance(),
            )
        items.append(AdoptItemOut(
            **item, action="create", list_id=d.id if d else None,
            list_key=d.list_key if d else list_adoption.copy_key(s.list_key),
        ))
    if created:
        session.flush()
        if not same:
            set_current_org(session, body.source_org_id)
            for s, d in created:
                log_event(
                    session, org_id=body.source_org_id, action="list.template_adopted",
                    entity_type="list_definition", entity_id=s.list_id,
                    after_value={
                        "into_org_id": str(org_id), "copy_list_id": str(d.id),
                        "template_version_number": s.version_number,
                    },
                    context={"via": "api", "list_key": s.list_key},
                )
            session.flush()
            set_current_org(session, org_id)
        session.commit()
    return AdoptOut(
        source_org_id=body.source_org_id, same_org=same, applied=body.apply, items=items
    )


@router.post(
    "/{org_id}/list-definitions/{list_id}/take-template-version", response_model=TakeOut
)
def take_template_version(
    org_id: uuid.UUID,
    list_id: uuid.UUID,
    body: TakeIn,
    session: Session = Depends(get_session),
    current_user: CurrentUser = Depends(require_org_access(*DEFINE_ROLES)),
) -> TakeOut:
    """Move a copy onto its template's current version, as a new version of
    the copy. Without `apply`, says what would change and whether it needs
    an edit named. Nothing happens to a copy until someone does this."""
    d = _definition(session, org_id, list_id)
    head = list_adoption.template_heads(session).get(d.id)
    if head is None:
        raise HTTPException(status_code=404, detail="This list is not a copy of a template.")
    snap = _in_template_org(
        session, current_user, head.template_org_id, org_id,
        lambda: list_adoption.read_template(session, head.template_org_id, head.template_list_id),
    )
    if snap.version_id == d.template_list_version_id:
        raise HTTPException(
            status_code=409,
            detail=f"This list is already based on template version {snap.version_number}.",
        )
    version = None
    if body.apply:
        try:
            plan, version = list_adoption.take_update(
                session, d, snap, current_user.display_name or current_user.email,
                body.overwrite_edit,
            )
        except list_adoption.UpdateRefused as e:
            raise HTTPException(status_code=409, detail=str(e)) from None
        except list_library.TagRemovalRefused as e:
            raise HTTPException(status_code=409, detail=str(e)) from None
        change = {
            "from_template_version": plan.based_on, "to_template_version": snap.version_number,
            "version_number": version.version_number, "rows_from": plan.rows_from,
            "replaces_edit_in_version": plan.edit.version_number if plan.edit else None,
        }
        log_event(
            session, org_id=org_id, action="list.template_version_take",
            entity_type="list_definition", entity_id=d.id, after_value=change,
            context={"via": "api", "list_key": d.list_key} | snap.provenance(),
        )
        session.flush()
        if snap.org_id != org_id:
            set_current_org(session, snap.org_id)
            log_event(
                session, org_id=snap.org_id, action="list.template_version_taken",
                entity_type="list_definition", entity_id=snap.list_id,
                after_value=change | {"into_org_id": str(org_id), "copy_list_id": str(d.id)},
                context={"via": "api", "list_key": snap.list_key},
            )
            session.flush()
            set_current_org(session, org_id)
        session.commit()
    else:
        plan = list_adoption.plan_update(session, d, snap)
    return TakeOut(
        from_template_version=plan.based_on, to_template_version=snap.version_number,
        rows_from=plan.rows_from, changed_fields=plan.changed_fields,
        columns_added=plan.columns_added, columns_removed=plan.columns_removed,
        dropped_cells=plan.dropped_cells, person_changed=plan.person_changed,
        edit_ref=plan.edit_ref, tag_add=list(plan.tag_add), tag_remove=list(plan.tag_remove),
        applied=body.apply, version_number=version.version_number if version else None,
    )


@router.get("/{org_id}/list-library/import-report", response_model=ImportReportOut)
def import_report(org_id: uuid.UUID, session: Session = Depends(get_session)) -> ImportReportOut:
    """The latest template import's record of what it did *not* import.
    Read from the `list.import` audit event the CLI writes on every apply,
    not a second store: the import is an audited action, and its audit row
    is the record. Empty when this org has never had an import."""
    row = session.scalar(
        select(AuditLog)
        .where(AuditLog.org_id == org_id, AuditLog.action == "list.import")
        .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        .limit(1)
    )
    if row is None:
        return ImportReportOut(recorded_at=None, not_imported=[], not_ingested=[])
    ctx = row.context or {}
    return ImportReportOut(
        recorded_at=row.created_at,
        not_imported=[NotImportedOut(**n) for n in ctx.get("not_imported", [])],
        not_ingested=list(ctx.get("not_ingested", [])),
    )
