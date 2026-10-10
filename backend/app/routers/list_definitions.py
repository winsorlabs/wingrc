"""List library API -- roadmap L.1.

A list is a definition plus its own versioned rows (docs/PLAN-list-library.md
§2). This is not `/orgs/{org_id}/lists`: that path belongs to the four
scope-graph `ListView`s (routers/scope.py), which keep working unchanged.

Endpoints:
  GET  /orgs/{org_id}/list-definitions                       ?control_key= filter
  GET  /orgs/{org_id}/list-definitions/{list_id}             current version
  GET  /orgs/{org_id}/list-definitions/{list_id}/versions    every version
  GET  /orgs/{org_id}/list-definitions/{list_id}/versions/{n}
  POST /orgs/{org_id}/list-definitions/{list_id}/versions    edit = new version

The only write is creating a version. It names the version it was based on
and is refused if that is no longer current -- the same optimistic
concurrency the document library uses, so two editors cannot silently
overwrite each other. L.1's scope ends at proving the model; the editing
screen is L.2.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import list_library
from ..audit import log_event
from ..auth import require_org_access, require_write
from ..db import get_session
from ..models import ListControlTag, ListDefinition, ListVersion

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
    projection_view_id: str | None
    control_keys: list[str]
    version_number: int
    column_count: int
    row_count: int


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


class ListDetailOut(BaseModel):
    id: uuid.UUID
    list_key: str
    is_template: bool
    source_ref: str | None
    projection_view_id: str | None
    control_keys: list[str]
    current: ListVersionOut


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


def _version_out(v: ListVersion) -> ListVersionOut:
    return ListVersionOut(
        id=v.id, version_number=v.version_number, title=v.title, description=v.description,
        responsible=v.responsible, review_cadence=v.review_cadence, columns=list(v.columns),
        rows=[list(r) for r in v.rows], provenance=v.provenance or {},
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
                projection_view_id=d.projection_view_id,
                control_keys=list(list_library.tags_of(session, d.id)),
                version_number=v.version_number, column_count=len(v.columns),
                row_count=len(v.rows),
            )
        )
    return out


@router.get("/{org_id}/list-definitions/{list_id}", response_model=ListDetailOut)
def get_list_definition(
    org_id: uuid.UUID, list_id: uuid.UUID, session: Session = Depends(get_session)
) -> ListDetailOut:
    d = _definition(session, org_id, list_id)
    return ListDetailOut(
        id=d.id, list_key=d.list_key, is_template=d.is_template, source_ref=d.source_ref,
        projection_view_id=d.projection_view_id,
        control_keys=list(list_library.tags_of(session, d.id)),
        current=_version_out(session.get(ListVersion, d.current_version_id)),
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
) -> ListVersionOut:
    d = _definition(session, org_id, list_id)
    if body.base_version_id != d.current_version_id:
        raise HTTPException(
            status_code=409,
            detail="This list changed since that version was read; reload and re-apply the edit.",
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
        {"via": "api", "based_on_version": base.version_number, "note": body.note or ""},
    )
    log_event(
        session, org_id=org_id, action="list.version_create", entity_type="list_definition",
        entity_id=d.id,
        before_value={"version_number": base.version_number, "row_count": len(base.rows)},
        after_value={"version_number": v.version_number, "row_count": len(v.rows)},
        context={"via": "api"},
    )
    session.commit()
    return _version_out(v)
