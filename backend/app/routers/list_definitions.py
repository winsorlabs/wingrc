"""List library API -- roadmap L.1.

A list is a definition plus its own versioned rows (docs/PLAN-list-library.md
§2). This is not `/orgs/{org_id}/lists`: that path belongs to the four
scope-graph `ListView`s (routers/scope.py), which keep working unchanged.

Endpoints:
  GET  /orgs/{org_id}/list-definitions                       ?control_key= filter
  GET  /orgs/{org_id}/list-definitions/{list_id}             current version
  GET  /orgs/{org_id}/list-definitions/{list_id}/versions    every version
  GET  /orgs/{org_id}/list-definitions/{list_id}/versions/{n}
  GET  /orgs/{org_id}/list-definitions/{list_id}/export        .xlsx, ?version=n
  POST /orgs/{org_id}/list-definitions/{list_id}/versions    edit = new version
  GET  /orgs/{org_id}/list-library/import-report             what was never imported

The only write is creating a version. It names the version it was based on
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

from .. import list_library
from ..audit import log_event
from ..auth import CurrentUser, get_current_user, require_org_access, require_write
from ..db import get_session
from ..models import AuditLog, ListControlTag, ListDefinition, ListVersion
from ..render import render_list_version_bytes

router = APIRouter(
    prefix="/orgs",
    tags=["list-library"],
    dependencies=[Depends(require_org_access()), Depends(require_write())],
)


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
    merged_from: list[str]
    has_changelog: bool


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


class NotImportedOut(BaseModel):
    source_ref: str
    status: str
    reason: str


class ImportReportOut(BaseModel):
    recorded_at: datetime | None
    not_imported: list[NotImportedOut]
    not_ingested: list[str]


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


def _version_out(v: ListVersion) -> ListVersionOut:
    return ListVersionOut(
        id=v.id, version_number=v.version_number, title=v.title, description=v.description,
        responsible=v.responsible, review_cadence=v.review_cadence, columns=list(v.columns),
        rows=[list(r) for r in v.rows], provenance=v.provenance or {},
        created_at=v.created_at, saved_by=_saved_by(v),
    )


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
    for d in session.scalars(stmt.order_by(ListDefinition.list_key, ListDefinition.id)):
        v = session.get(ListVersion, d.current_version_id)
        out.append(
            ListSummaryOut(
                id=d.id, list_key=d.list_key, title=v.title, is_template=d.is_template,
                source_ref=d.source_ref, projection_view_id=d.projection_view_id,
                control_keys=list(list_library.tags_of(session, d.id)),
                version_number=v.version_number, column_count=len(v.columns),
                row_count=len(v.rows), updated_at=v.created_at, saved_by=_saved_by(v),
                merged_from=list((v.provenance or {}).get("merged_from") or []),
                has_changelog=bool((v.provenance or {}).get("changelogs")),
            )
        )
    return out


@router.get("/{org_id}/list-definitions/{list_id}", response_model=ListDetailOut)
def get_list_definition(
    org_id: uuid.UUID, list_id: uuid.UUID, session: Session = Depends(get_session)
) -> ListDetailOut:
    d = _definition(session, org_id, list_id)
    v = session.get(ListVersion, d.current_version_id)
    return ListDetailOut(
        id=d.id, list_key=d.list_key, is_template=d.is_template, source_ref=d.source_ref,
        projection_view_id=d.projection_view_id,
        control_keys=list(list_library.tags_of(session, d.id)),
        merge=_merge_out(d, v),
        current=_version_out(v),
    )


@router.get("/{org_id}/list-definitions/{list_id}/versions", response_model=list[ListVersionOut])
def list_versions(
    org_id: uuid.UUID, list_id: uuid.UUID, session: Session = Depends(get_session)
) -> list[ListVersionOut]:
    d = _definition(session, org_id, list_id)
    return [
        _version_out(v)
        for v in session.scalars(
            select(ListVersion)
            .where(ListVersion.list_id == d.id)
            .order_by(ListVersion.version_number)
        )
    ]


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
    return _version_out(v)


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
    current_user: CurrentUser = Depends(get_current_user),
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
        },
        context={"via": "api", "list_key": d.list_key, "note": body.note or ""},
    )
    session.commit()
    return _version_out(v)


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
