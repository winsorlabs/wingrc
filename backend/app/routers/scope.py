"""Scope module: read the scope graph, dry-run/apply workbook imports, and
manual single-entity CRUD for ad-hoc asset entry (G.5).

Endpoints:
  GET    /orgs/{org_id}/scope                     List scope entities
  POST   /orgs/{org_id}/scope                      Create/upsert one entity
  PATCH  /orgs/{org_id}/scope/{entity_id}          Partial-update one entity
  DELETE /orgs/{org_id}/scope/{entity_id}          Delete one entity
  POST   /orgs/{org_id}/imports/workbook/dry-run   Parse + reconcile, no writes
  POST   /orgs/{org_id}/imports/workbook/apply     Apply a confirmed diff
  POST   /orgs/{org_id}/exports/{view_id}          Render a CMMC list

Moved here from main.py (G.5) -- scope was the last resource whose endpoints
lived directly in main.py instead of a dedicated router (see
docs/PLAN-gui-restructure.md's G.5 section). The move also closes a real
gap: those endpoints previously ran under bare get_current_user with no
require_org_access()/require_write() check, unlike every other router --
any authenticated user could read or (once write endpoints existed) mutate
any org's scope regardless of membership. They now carry the same
router-level dependency pair as contacts.py/evidence.py.

Side-effecting apply is deliberately a separate, explicit step from dry-run
-- imports never mutate scope without a confirmed diff. This is about
dry-run-before-apply, not CLI-vs-API: nothing blocks exposing apply over
HTTP the same way dry-run already was, since cli.py's own `seed --apply`
calls the identical reconcile() + repo.upsert() functions this router does.
"""

from __future__ import annotations

import tempfile
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import repo
from ..audit import log_event
from ..auth import require_org_access, require_write
from ..catalog import VIEWS_BY_ID
from ..db import get_session
from ..domain import CanonicalEntity, EntityStatus, EntityType, ScopeCategory, Source
from ..importers.workbook import parse_workbook
from ..models import ScopeEntity
from ..reconcile import reconcile
from ..render import render_view

router = APIRouter(
    prefix="/orgs",
    tags=["scope"],
    dependencies=[Depends(require_org_access()), Depends(require_write())],
)

_ENTITY_TYPES = frozenset(t.value for t in EntityType)
_SCOPE_CATEGORIES = frozenset(c.value for c in ScopeCategory)
_ENTITY_STATUSES = frozenset(s.value for s in EntityStatus)

# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class DeviceSoftwareAttributes(BaseModel):
    """Known attribute keys for DEVICE/SOFTWARE entities -- the exact field
    list from docs/pdf_ssp_template_spec.md's "Component/asset inventory"
    gap section (NIST CUI SSP template section 2.1/2.2): make/OEM, model,
    version, and the person/role responsible. Validated here only --
    `attributes` stays a free-form JSONB column, no schema migration.
    Unknown keys in an entity's `attributes` dict pass through untouched;
    only these four are type-checked.
    """

    make_oem: str | None = None
    model: str | None = None
    version: str | None = None
    responsible_contact_id: uuid.UUID | None = None


def _validate_device_software_attributes(entity_type: str, attributes: dict[str, Any]) -> None:
    if entity_type not in (EntityType.DEVICE.value, EntityType.SOFTWARE.value):
        return
    known = {k: v for k, v in attributes.items() if k in DeviceSoftwareAttributes.model_fields}
    try:
        DeviceSoftwareAttributes.model_validate(known)
    except ValidationError as exc:
        raise ValueError(f"Invalid device/software attributes: {exc}") from exc


class ScopeEntityIn(BaseModel):
    entity_type: str
    natural_key: str
    scope_category: str | None = None
    status: str = "active"
    in_boundary: bool = True
    attributes: dict[str, Any] = Field(default_factory=dict)

    @field_validator("entity_type")
    @classmethod
    def _validate_entity_type(cls, v: str) -> str:
        if v not in _ENTITY_TYPES:
            raise ValueError(f"entity_type must be one of: {sorted(_ENTITY_TYPES)}")
        return v

    @field_validator("natural_key")
    @classmethod
    def _validate_natural_key(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("natural_key must not be blank")
        return v

    @field_validator("scope_category")
    @classmethod
    def _validate_scope_category(cls, v: str | None) -> str | None:
        if v is not None and v not in _SCOPE_CATEGORIES:
            raise ValueError(f"scope_category must be one of: {sorted(_SCOPE_CATEGORIES)}")
        return v

    @field_validator("status")
    @classmethod
    def _validate_status(cls, v: str) -> str:
        if v not in _ENTITY_STATUSES:
            raise ValueError(f"status must be one of: {sorted(_ENTITY_STATUSES)}")
        return v

    @model_validator(mode="after")
    def _validate_type_attributes(self) -> ScopeEntityIn:
        _validate_device_software_attributes(self.entity_type, self.attributes)
        return self


class ScopeEntityPatch(BaseModel):
    """Partial update by row id. `entity_type`/`natural_key` are
    deliberately not patchable here -- both are part of repo.upsert()'s
    match key (org_id, entity_type, natural_key), so changing either
    through an id-addressed PATCH would upsert a second, unrelated row
    instead of renaming this one. Delete + recreate to change either.
    `attributes` is shallow-merged into the existing dict, not replaced
    wholesale, so a caller can update one field without resending every
    other attribute.
    """

    scope_category: str | None = None
    status: str | None = None
    in_boundary: bool | None = None
    attributes: dict[str, Any] | None = None

    @field_validator("scope_category")
    @classmethod
    def _validate_scope_category(cls, v: str | None) -> str | None:
        if v is not None and v not in _SCOPE_CATEGORIES:
            raise ValueError(f"scope_category must be one of: {sorted(_SCOPE_CATEGORIES)}")
        return v

    @field_validator("status")
    @classmethod
    def _validate_status(cls, v: str | None) -> str | None:
        if v is not None and v not in _ENTITY_STATUSES:
            raise ValueError(f"status must be one of: {sorted(_ENTITY_STATUSES)}")
        return v


class ScopeEntityOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    entity_type: str
    natural_key: str
    scope_category: str | None
    status: str
    in_boundary: bool
    source: str
    source_ref: str | None
    attributes: dict[str, Any]


class ScopeChangeIncoming(BaseModel):
    """Full incoming-entity fields for one NEW/CHANGED reconcile row --
    what repo.upsert() needs to actually write. Absent for MISSING rows,
    which carry no incoming data at all (see domain.EntityChange).
    """

    scope_category: str | None = None
    status: str = "active"
    in_boundary: bool = True
    source: str = "workbook"
    source_ref: str | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)


class ScopeChangeOut(BaseModel):
    change_type: str
    entity_type: str
    natural_key: str
    field_diffs: dict[str, list[Any]]
    incoming: ScopeChangeIncoming | None = None


class DryRunOut(BaseModel):
    summary: dict[str, int]
    changes: list[ScopeChangeOut]


class ScopeChangeIn(BaseModel):
    """One confirmed row from a prior dry-run response, echoed back to
    /imports/workbook/apply. Same shape as ScopeChangeOut -- the frontend
    shows the dry-run diff, the user confirms (optionally deselecting
    rows they don't want applied), and exactly that shape is sent back.
    """

    change_type: str
    entity_type: str
    natural_key: str
    incoming: ScopeChangeIncoming | None = None


class ApplyIn(BaseModel):
    changes: list[ScopeChangeIn]


class ApplyOut(BaseModel):
    applied: int


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_scope_entity(session: Session, org_id: uuid.UUID, entity_id: uuid.UUID) -> ScopeEntity:
    row = session.scalars(
        select(ScopeEntity).where(ScopeEntity.id == entity_id, ScopeEntity.org_id == org_id)
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Scope entity not found")
    return row


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


@router.get("/{org_id}/scope", response_model=list[ScopeEntityOut])
def get_scope(
    org_id: uuid.UUID,
    entity_type: str | None = Query(default=None),
    session: Session = Depends(get_session),
) -> list[ScopeEntityOut]:
    if entity_type is not None and entity_type not in _ENTITY_TYPES:
        raise HTTPException(
            status_code=422, detail=f"entity_type must be one of: {sorted(_ENTITY_TYPES)}"
        )
    stmt = select(ScopeEntity).where(ScopeEntity.org_id == org_id)
    if entity_type is not None:
        stmt = stmt.where(ScopeEntity.entity_type == entity_type)
    stmt = stmt.order_by(ScopeEntity.natural_key)
    rows = session.scalars(stmt).all()
    return [ScopeEntityOut.model_validate(r) for r in rows]


# ---------------------------------------------------------------------------
# Manual single-entity CRUD
# ---------------------------------------------------------------------------


@router.post("/{org_id}/scope", response_model=ScopeEntityOut, status_code=201)
def create_scope_entity(
    org_id: uuid.UUID,
    body: ScopeEntityIn,
    session: Session = Depends(get_session),
) -> ScopeEntityOut:
    """Thin wrapper around repo.upsert(). Upsert semantics: matching this
    org's existing (entity_type, natural_key) updates that row rather than
    erroring, exactly like `wingrc seed --apply`. The audit log entry
    reflects which actually happened (create vs update).
    """
    entity_type = EntityType(body.entity_type)
    existing = session.scalars(
        select(ScopeEntity).where(
            ScopeEntity.org_id == org_id,
            ScopeEntity.entity_type == entity_type.value,
            ScopeEntity.natural_key == body.natural_key,
        )
    ).first()
    is_new = existing is None

    entity = CanonicalEntity(
        entity_type=entity_type,
        natural_key=body.natural_key,
        attributes=body.attributes,
        scope_category=ScopeCategory(body.scope_category) if body.scope_category else None,
        status=EntityStatus(body.status),
        in_boundary=body.in_boundary,
        source=Source.MANUAL,
        source_ref=None,
    )
    row = repo.upsert(session, org_id, entity)
    session.flush()
    log_event(
        session,
        org_id=org_id,
        action="scope_entity.create" if is_new else "scope_entity.update",
        entity_type="scope_entity",
        entity_id=row.id,
        after_value={
            "entity_type": entity.entity_type.value,
            "natural_key": entity.natural_key,
            "attributes": entity.attributes,
        },
        context={"via": "api"},
    )
    session.commit()
    return ScopeEntityOut.model_validate(row)


@router.patch("/{org_id}/scope/{entity_id}", response_model=ScopeEntityOut)
def patch_scope_entity(
    org_id: uuid.UUID,
    entity_id: uuid.UUID,
    body: ScopeEntityPatch,
    session: Session = Depends(get_session),
) -> ScopeEntityOut:
    row = _get_scope_entity(session, org_id, entity_id)
    current = repo.to_canonical(row)

    update_data = body.model_dump(include=body.model_fields_set)
    if not update_data:
        return ScopeEntityOut.model_validate(row)

    before: dict[str, Any] = {
        "scope_category": current.scope_category.value if current.scope_category else None,
        "status": current.status.value,
        "in_boundary": current.in_boundary,
        "attributes": current.attributes,
    }

    kwargs: dict[str, Any] = {}
    if "scope_category" in update_data:
        kwargs["scope_category"] = (
            ScopeCategory(update_data["scope_category"]) if update_data["scope_category"] else None
        )
    if "status" in update_data:
        kwargs["status"] = EntityStatus(update_data["status"])
    if "in_boundary" in update_data:
        kwargs["in_boundary"] = update_data["in_boundary"]
    if "attributes" in update_data:
        kwargs["attributes"] = {**current.attributes, **update_data["attributes"]}

    entity = replace(current, **kwargs)
    try:
        _validate_device_software_attributes(entity.entity_type.value, entity.attributes)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    updated_row = repo.upsert(session, org_id, entity)
    session.flush()
    log_event(
        session,
        org_id=org_id,
        action="scope_entity.update",
        entity_type="scope_entity",
        entity_id=updated_row.id,
        before_value=before,
        after_value=update_data,
        context={"via": "api"},
    )
    session.commit()
    return ScopeEntityOut.model_validate(updated_row)


@router.delete("/{org_id}/scope/{entity_id}", status_code=204)
def delete_scope_entity(
    org_id: uuid.UUID,
    entity_id: uuid.UUID,
    session: Session = Depends(get_session),
) -> None:
    row = _get_scope_entity(session, org_id, entity_id)
    log_event(
        session,
        org_id=org_id,
        action="scope_entity.delete",
        entity_type="scope_entity",
        entity_id=row.id,
        before_value={"entity_type": row.entity_type, "natural_key": row.natural_key},
        context={"via": "api"},
    )
    session.delete(row)
    session.commit()


# ---------------------------------------------------------------------------
# Workbook import: dry-run (no writes) then apply (confirmed diff only)
# ---------------------------------------------------------------------------


@router.post("/{org_id}/imports/workbook/dry-run", response_model=DryRunOut)
async def import_dry_run(
    org_id: uuid.UUID,
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
) -> DryRunOut:
    """Parse an uploaded workbook and return the reconcile diff. No writes.

    Each NEW/CHANGED row carries its full `incoming` entity data, not just
    the diffed fields -- POST .../imports/workbook/apply needs the whole
    entity to call repo.upsert() with, the same way cli.py's `seed --apply`
    already does from its own in-memory reconcile result; the diffed-fields
    view alone isn't enough to reconstruct what to write. MISSING rows
    carry no `incoming` (see domain.EntityChange) and are informational
    only -- apply never deletes on import, matching cli.py's own behavior.
    """
    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name
    try:
        # Parsing reads from the temp file, but provenance must record the
        # name the user actually uploaded, not the generated temp filename.
        incoming = parse_workbook(tmp_path, source_ref=file.filename)
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    current = repo.list_entities(session, org_id)
    result = reconcile(current, incoming)
    return DryRunOut(
        summary=result.summary(),
        changes=[
            ScopeChangeOut(
                change_type=c.change_type.value,
                entity_type=c.entity_type.value,
                natural_key=c.natural_key,
                field_diffs={k: list(v) for k, v in c.field_diffs.items()},
                incoming=(
                    ScopeChangeIncoming(
                        scope_category=(
                            c.incoming.scope_category.value if c.incoming.scope_category else None
                        ),
                        status=c.incoming.status.value,
                        in_boundary=c.incoming.in_boundary,
                        source=c.incoming.source.value,
                        source_ref=c.incoming.source_ref,
                        attributes=c.incoming.attributes,
                    )
                    if c.incoming is not None
                    else None
                ),
            )
            for c in result.changes
            if c.change_type.value != "unchanged"
        ],
    )


@router.post("/{org_id}/imports/workbook/apply", response_model=ApplyOut)
def import_apply(
    org_id: uuid.UUID,
    body: ApplyIn,
    session: Session = Depends(get_session),
) -> ApplyOut:
    """Apply a confirmed subset of a prior dry-run's diff. Preserves the
    "confirmed diff before mutation" principle -- the frontend must have
    already shown the dry-run result and gotten explicit user confirmation;
    this endpoint performs no reconciliation of its own, it only writes
    exactly what it's handed. Only NEW/CHANGED rows are written -- MISSING
    rows (present in scope but absent from the workbook) are never
    auto-deleted, matching cli.py's own `seed --apply` behavior.
    """
    applied = 0
    for c in body.changes:
        if c.change_type not in ("new", "changed"):
            continue
        if c.incoming is None:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Change for {c.natural_key!r} ({c.change_type}) has no "
                    "incoming data to apply"
                ),
            )
        try:
            entity_type = EntityType(c.entity_type)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        entity = CanonicalEntity(
            entity_type=entity_type,
            natural_key=c.natural_key,
            attributes=c.incoming.attributes,
            scope_category=(
                ScopeCategory(c.incoming.scope_category) if c.incoming.scope_category else None
            ),
            status=EntityStatus(c.incoming.status),
            in_boundary=c.incoming.in_boundary,
            source=Source(c.incoming.source),
            source_ref=c.incoming.source_ref,
        )
        row = repo.upsert(session, org_id, entity)
        session.flush()
        log_event(
            session,
            org_id=org_id,
            action="scope_entity.import_apply",
            entity_type="scope_entity",
            entity_id=row.id,
            after_value={
                "entity_type": entity.entity_type.value,
                "natural_key": entity.natural_key,
                "change_type": c.change_type,
            },
            context={"via": "api", "source": "workbook"},
        )
        applied += 1

    session.commit()
    return ApplyOut(applied=applied)


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


@router.post("/{org_id}/exports/{view_id}")
def export_view(
    org_id: uuid.UUID,
    view_id: str,
    session: Session = Depends(get_session),
) -> FileResponse:
    view = VIEWS_BY_ID.get(view_id)
    if view is None:
        raise HTTPException(status_code=404, detail=f"Unknown view {view_id!r}")
    entities = repo.list_entities(session, org_id, view.entity_type)
    out = Path(tempfile.gettempdir()) / f"{view_id}.xlsx"
    render_view(view, entities, out)
    return FileResponse(out, filename=f"{view_id}.xlsx")
