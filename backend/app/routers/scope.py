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

  GET    /orgs/{org_id}/integrations/liongard/environments   Available Liongard Environments
  GET    /orgs/{org_id}/integrations/liongard/environment    This org's Environment mapping
  PUT    /orgs/{org_id}/integrations/liongard/environment    Set the mapping
  DELETE /orgs/{org_id}/integrations/liongard/environment    Unmap (2026-09-16)
  POST   /orgs/{org_id}/integrations/liongard/sync/dry-run   Pull + reconcile, no writes

One Liongard Environment maps to at most one org (2026-09-16 -- migration
0050, PUT's own duplicate check below): a Liongard Environment already
represents one client's infrastructure, so two orgs sharing one would
silently pull the same devices/identities into two separate scope graphs.
See OrgLiongardEnvironment's own docstring in models.py and migration
0050's for the full reasoning, including why that migration adds the DB
constraint defensively rather than unconditionally.

D.2: the Liongard device/user pull reuses this exact dry-run -> apply shape
(see the section at the bottom of this file) -- its dry-run endpoint builds
the identical DryRunOut/ScopeChangeOut this module already returns for
workbook imports, and its apply step is the *same*
POST /imports/workbook/apply endpoint above, unmodified. Nothing about that
endpoint's body actually depends on the source being a workbook -- see its
own docstring.

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

import json
import tempfile
import uuid
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from .. import liongard_sync, repo
from ..audit import log_event
from ..auth import require_org_access, require_write
from ..catalog import VIEWS_BY_ID
from ..connectors import liongard as liongard_connector
from ..crypto import CredentialCipherError, decrypt_credential
from ..db import get_session
from ..domain import (
    CanonicalEntity,
    DeviceSubtype,
    EntityStatus,
    EntityType,
    ScopeCategory,
    Source,
    normalize_mac_address,
)
from ..importers.workbook import parse_workbook, resolve_canonical_device_attributes
from ..models import IntegrationConnection, OrgLiongardEnvironment, ScopeEntity
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
    """Known attribute keys for DEVICE/SOFTWARE entities -- the field list
    from docs/pdf_ssp_template_spec.md's "Component/asset inventory" gap
    section (NIST CUI SSP template section 2.1/2.2) plus the device_subtype/
    asset_tag/mac_addresses fields added for the future Liongard connector
    (roadmap D.2), which will write into this same canonical vocabulary.
    Validated here only -- `attributes` stays a free-form JSONB column, no
    schema migration. Unknown keys in an entity's `attributes` dict pass
    through untouched; only fields listed here are type-checked.

    This field list must match `domain.py:DEVICE_SOFTWARE_CANONICAL_
    ATTRIBUTES` exactly -- that constant is reconcile.py's own source for
    which attributes get compared for CHANGED detection (minus
    last_login_user, deliberately -- see reconcile.py's module docstring),
    so the two must never drift into two different descriptions of "the
    canonical vocabulary." Fields stay hand-declared here (not generated
    from the frozenset) since Pydantic needs each field's own type and
    validator, not just its name -- test_domain_attribute_vocabulary.py
    asserts the two stay in sync instead.
    """

    make_oem: str | None = None
    model: str | None = None
    version: str | None = None
    responsible_contact_id: uuid.UUID | None = None
    device_subtype: DeviceSubtype | None = None
    # Free-text fallback so an unrecognized subtype (manual entry, or a
    # Liongard device class this vocabulary doesn't cover yet) is captured
    # rather than silently dropped when device_subtype == OTHER.
    device_subtype_other: str | None = None
    asset_tag: str | None = None
    # A NIC list, not a single value -- a laptop has wifi + ethernet + a
    # dock/USB adapter, and Liongard reports per-NIC data. MAC randomization
    # on modern mobile OSes also means a MAC is not a stable *identity* for
    # phones/tablets -- it's an attribute here, never used as a natural_key.
    mac_addresses: list[str] | None = None
    # Human-readable label, deliberately never the reconcile identity --
    # see importers/liongard.py's module docstring ("Display name vs.
    # natural key") for the full reasoning. A canonical attribute, not a
    # scope_entity column, for the same "consistent with every other
    # display-ish field" reasoning as make_oem/model/asset_tag above; the
    # frontend falls back to natural_key when this is unset, so workbook
    # and manually-entered assets (which don't set it) render unchanged.
    display_name: str | None = None
    # Telemetry, not an ownership signal -- must never be read as, or
    # written to, responsible_contact_id. See
    # importers/liongard.py:device_profile_to_canonical()'s own docstring;
    # this field exists purely so the asset drawer can show it, clearly
    # labeled as an observed last login, not an owner.
    last_login_user: str | None = None

    @field_validator("asset_tag", "display_name", "last_login_user")
    @classmethod
    def _strip_optional_text(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        return v or None

    @field_validator("mac_addresses")
    @classmethod
    def _normalize_macs(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return None
        try:
            return [normalize_mac_address(m) for m in v if m and m.strip()] or None
        except ValueError as exc:
            raise ValueError(str(exc)) from exc


def _validate_device_software_attributes(entity_type: str, attributes: dict[str, Any]) -> None:
    """Validate the known DEVICE/SOFTWARE keys in `attributes` and write
    back any normalized form (MAC canonicalization, enum -> str) so the
    persisted dict matches what was validated, not the raw caller input.
    """
    if entity_type not in (EntityType.DEVICE.value, EntityType.SOFTWARE.value):
        return
    known = {k: v for k, v in attributes.items() if k in DeviceSoftwareAttributes.model_fields}
    try:
        validated = DeviceSoftwareAttributes.model_validate(known)
    except ValidationError as exc:
        raise ValueError(f"Invalid device/software attributes: {exc}") from exc
    for k in known:
        value = getattr(validated, k)
        if value is None:
            attributes[k] = None
        elif isinstance(value, uuid.UUID):
            attributes[k] = str(value)
        elif isinstance(value, DeviceSubtype):
            attributes[k] = value.value
        else:
            attributes[k] = value


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
    warnings: list[str] = Field(default_factory=list)


class LiongardPullStatus(BaseModel):
    """One line of "what did the pull actually return" per entity type --
    always devices + identities for the Liongard path, always empty for
    the workbook path. Exists because an empty `changes` list is
    ambiguous on its own: it means either "nothing to compare" (Liongard
    returned records, all already match scope) or "nothing to find"
    (every record pulled is still in Discovery state, filtered out before
    reconcile ever saw it) -- these read identically to a user unless the
    pre-filter count is surfaced. `message` is the same fact rendered as a
    plain, actionable sentence so the wizard doesn't have to re-derive it
    from the three numbers.
    """

    entity_label: str
    total_found: int
    inventory_count: int
    message: str


class DryRunOut(BaseModel):
    summary: dict[str, int]
    changes: list[ScopeChangeOut]
    # Pull-level warnings that aren't tied to any single change row -- e.g.
    # a Liongard record with no usable natural key, so it was never turned
    # into a CanonicalEntity at all and can't appear in `changes`. Always
    # empty for the workbook path today; added for D.2's Liongard sync
    # rather than as a workbook-specific field, since "a warning too broad
    # to attach to one row" isn't source-specific.
    warnings: list[str] = Field(default_factory=list)
    # Always empty for the workbook path -- see LiongardPullStatus above.
    pull_status: list[LiongardPullStatus] = Field(default_factory=list)


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

    attr_warnings = resolve_canonical_device_attributes(session, org_id, incoming)
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
                warnings=attr_warnings.get(
                    (c.entity_type.value, c.natural_key.strip().lower()), []
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
            # Read from the change's own incoming.source rather than
            # hardcoding "workbook": this endpoint is intentionally
            # source-agnostic -- D.2's Liongard sync dry-run (below) reuses
            # it unmodified to apply connector-sourced changes too, per
            # ROADMAP.md D.2's "reuse the existing apply path, don't build
            # a parallel one." A hardcoded label here would mislabel every
            # Liongard-sourced apply in the audit log.
            context={"via": "api", "source": entity.source.value},
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


# ---------------------------------------------------------------------------
# Liongard connector (D.2): org <-> Environment mapping, sync dry-run.
# Apply reuses POST /imports/workbook/apply above unmodified -- see this
# file's module docstring.
# ---------------------------------------------------------------------------


class LiongardEnvironmentOption(BaseModel):
    id: int
    name: str


class LiongardEnvironmentMappingIn(BaseModel):
    liongard_environment_id: int


class LiongardEnvironmentMappingOut(BaseModel):
    liongard_environment_id: int
    liongard_environment_name: str | None
    updated_at: datetime | None
    # Count of this org's scope_entity rows with source="liongard" -- shown
    # by the frontend as part of the unmap confirmation (never as part of
    # setting/changing a mapping, where it isn't relevant). Computed fresh
    # on every read rather than cached, since it's cheap and the whole
    # point is an accurate number at the moment someone is deciding whether
    # to unmap.
    liongard_sourced_scope_count: int = 0


class LiongardUnmapOut(BaseModel):
    liongard_environment_id: int
    liongard_environment_name: str | None
    orphaned_scope_entity_count: int


def get_liongard_credential(session: Session) -> tuple[dict[str, Any], dict[str, Any]]:
    """Load and decrypt the deployment-wide Liongard credential
    (IntegrationConnection, migration 0030 -- D.1). Raises a specific
    HTTPException rather than a generic 500 for either failure mode, same
    discipline as routers/integrations.py's own test_connection.

    Not underscore-prefixed: routers/contacts.py's Liongard-identities import
    imports this directly rather than duplicating credential-decryption
    logic. This module stays the one place that owns the deployment-wide
    Liongard credential and the org<->Environment mapping.
    """
    row = session.scalars(
        select(IntegrationConnection).where(IntegrationConnection.connector_key == "liongard")
    ).first()
    if row is None or row.encrypted_credential is None:
        raise HTTPException(
            status_code=400,
            detail=(
                "Liongard isn't configured yet -- add a credential in "
                "Administration → Integrations first."
            ),
        )
    try:
        credential = json.loads(decrypt_credential(row.encrypted_credential))
    except CredentialCipherError as e:
        raise HTTPException(
            status_code=500,
            detail=(
                "Could not decrypt the stored Liongard credential -- check the deployment's "
                "WINGRC_CREDENTIAL_ENCRYPTION_KEYS configuration."
            ),
        ) from e
    return (row.config or {}), credential


def get_liongard_mapping(session: Session, org_id: uuid.UUID) -> OrgLiongardEnvironment | None:
    return session.scalars(
        select(OrgLiongardEnvironment).where(OrgLiongardEnvironment.org_id == org_id)
    ).first()


def _count_liongard_sourced_scope(session: Session, org_id: uuid.UUID) -> int:
    return (
        session.scalars(
            select(func.count())
            .select_from(ScopeEntity)
            .where(ScopeEntity.org_id == org_id, ScopeEntity.source == Source.LIONGARD.value)
        ).one()
    )


def _other_org_holding_environment(
    session: Session, environment_id: int, org_id: uuid.UUID
) -> str | None:
    """Name of an org other than `org_id` currently mapped to
    `environment_id`, or None -- migration 0050's own
    auth.liongard_environment_holder(), the only way to see this across
    RLS (org_liongard_environment is scoped to app.current_org, which is
    this request's own org, not the one that might already hold it).
    """
    rows = session.execute(
        text("SELECT org_id, org_name FROM auth.liongard_environment_holder(:env_id)"),
        {"env_id": environment_id},
    ).all()
    for other_org_id, other_org_name in rows:
        if other_org_id != org_id:
            return other_org_name
    return None


@router.get(
    "/{org_id}/integrations/liongard/environments",
    response_model=list[LiongardEnvironmentOption],
)
def list_liongard_environments(
    org_id: uuid.UUID, session: Session = Depends(get_session)
) -> list[LiongardEnvironmentOption]:
    """Environments visible to this deployment's Liongard key -- for the
    org-mapping picker below. org_id is accepted (and required by the
    org-access/write dependencies this router applies to every route) for
    consistent auth scoping and URL shape even though the underlying call
    doesn't use it -- environments come from the one deployment-wide
    credential, not from anything org-specific.
    """
    config, credential = get_liongard_credential(session)
    try:
        environments = liongard_connector.list_environments(config, credential)
    except liongard_connector.LiongardAPIError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    return [LiongardEnvironmentOption(id=e.id, name=e.name) for e in environments]


@router.get(
    "/{org_id}/integrations/liongard/environment",
    response_model=LiongardEnvironmentMappingOut | None,
)
def get_liongard_environment_mapping(
    org_id: uuid.UUID, session: Session = Depends(get_session)
) -> LiongardEnvironmentMappingOut | None:
    row = get_liongard_mapping(session, org_id)
    if row is None:
        return None
    return LiongardEnvironmentMappingOut(
        liongard_environment_id=row.liongard_environment_id,
        liongard_environment_name=row.liongard_environment_name,
        updated_at=row.updated_at,
        liongard_sourced_scope_count=_count_liongard_sourced_scope(session, org_id),
    )


@router.put(
    "/{org_id}/integrations/liongard/environment",
    response_model=LiongardEnvironmentMappingOut,
)
def set_liongard_environment_mapping(
    org_id: uuid.UUID,
    body: LiongardEnvironmentMappingIn,
    session: Session = Depends(get_session),
) -> LiongardEnvironmentMappingOut:
    """Validated against a live environment list, not just stored blind --
    a typo'd Environment id would otherwise silently point this org's sync
    at nothing (or, worse, if Liongard ever reused ids across MSP
    instances, at the wrong tenant's data) with no feedback until the
    first sync attempt failed confusingly.
    """
    config, credential = get_liongard_credential(session)
    try:
        environments = liongard_connector.list_environments(config, credential)
    except liongard_connector.LiongardAPIError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e

    match = next((e for e in environments if e.id == body.liongard_environment_id), None)
    if match is None:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Environment id {body.liongard_environment_id} was not found for this "
                "Liongard account."
            ),
        )

    # One environment maps to at most one org (2026-09-16 decision -- see
    # models.py's OrgLiongardEnvironment docstring and migration 0050):
    # a Liongard Environment already represents one client's
    # infrastructure, so two orgs sharing one would silently pull the same
    # devices/identities into two separate scope graphs -- a compliance-
    # boundary leak, not just an inconvenience. Checked here, before the
    # write, for a real error naming the other org; the DB constraint
    # (when present -- see that migration's own defensive design) is the
    # backstop, not the primary signal.
    holder = _other_org_holding_environment(session, body.liongard_environment_id, org_id)
    if holder is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Environment {match.name!r} (id {match.id}) is already mapped to "
                f"{holder!r} -- unmap it there first."
            ),
        )

    row = get_liongard_mapping(session, org_id)
    is_new = row is None
    before = (
        None
        if is_new
        else {
            "liongard_environment_id": row.liongard_environment_id,
            "liongard_environment_name": row.liongard_environment_name,
        }
    )
    if row is None:
        row = OrgLiongardEnvironment(org_id=org_id)
        session.add(row)
    row.liongard_environment_id = match.id
    row.liongard_environment_name = match.name
    # Set explicitly rather than relying on the column's server-side
    # onupdate=func.now() (models.py): on an UPDATE (re-mapping), the ORM
    # marks a server-onupdate column expired after flush rather than
    # populating it via RETURNING, so reading row.updated_at below would
    # trigger a lazy-reload SELECT that runs after this request's commit
    # has already cleared SET LOCAL app.current_org -- RLS then matches
    # zero rows and raises ObjectDeletedError. Found via the integration
    # test suite exercising a second PUT (re-map) on the bench stack, not
    # assumed -- this failure mode is real in production too, since a real
    # COMMIT clears SET LOCAL the same way. IntegrationConnection's own
    # router (routers/integrations.py) never hit this because it never
    # returns updated_at in a response at all.
    row.updated_at = datetime.now(UTC)
    session.flush()

    log_event(
        session,
        org_id=org_id,
        action="liongard_environment.set",
        entity_type="org_liongard_environment",
        entity_id=row.id,
        before_value=before,
        after_value={"liongard_environment_id": match.id, "liongard_environment_name": match.name},
        context={"via": "api", "created": is_new},
    )
    # Computed before commit, not after -- app.current_org (this request's
    # RLS context) is a SET LOCAL value cleared the moment commit() returns,
    # so a query issued afterward would see zero rows under RLS regardless
    # of actual data, same failure mode this function's own surrounding
    # comment already documents for a post-commit refresh().
    scope_count = _count_liongard_sourced_scope(session, org_id)
    session.commit()
    # No session.refresh() here, deliberately -- OrgLiongardEnvironment is
    # RLS-protected (unlike IntegrationConnection, whose own set/test
    # endpoints in routers/integrations.py do refresh after commit): by the
    # time commit() returns, this request's app.current_org setting has
    # already been reset, so a post-commit refresh's SELECT matches zero
    # rows under RLS and raises InvalidRequestError -- found by the
    # integration test suite, not assumed. expire_on_commit=False (see
    # conftest.py's db_session docstring; matches db.py's production
    # SessionLocal) means row's already-set attributes stay valid without
    # one anyway, same as create_scope_entity/patch_scope_entity above.
    return LiongardEnvironmentMappingOut(
        liongard_environment_id=row.liongard_environment_id,
        liongard_environment_name=row.liongard_environment_name,
        updated_at=row.updated_at,
        liongard_sourced_scope_count=scope_count,
    )


@router.delete(
    "/{org_id}/integrations/liongard/environment",
    response_model=LiongardUnmapOut,
)
def delete_liongard_environment_mapping(
    org_id: uuid.UUID, session: Session = Depends(get_session)
) -> LiongardUnmapOut:
    """Unmap this org from its Liongard Environment -- the gap this was
    added to close: an org could be re-pointed at a different Environment
    (PUT) but never fully unmapped. Same role gate as PUT/GET (router-level
    require_org_access() + require_write()), audit-logged like every other
    mapping change.

    Never deletes scope_entity rows sourced from this mapping (checked
    before building this: every Liongard sync run so far has been dry-run
    only, so there are none live today, but the design doesn't assume that
    stays true). A Liongard-sourced scope_entity row has no FK to
    org_liongard_environment at all -- unmapping is purely a row delete
    here, and any prior scope_entity rows simply become manually-owned
    entities from this point on (source stays "liongard" as a provenance
    record of where they originally came from; nothing about them changes).
    That count is returned so the caller can report it, not silently drop
    it.
    """
    row = get_liongard_mapping(session, org_id)
    if row is None:
        raise HTTPException(
            status_code=404,
            detail="This org has no Liongard Environment mapping to remove.",
        )

    scope_count = _count_liongard_sourced_scope(session, org_id)
    before = {
        "liongard_environment_id": row.liongard_environment_id,
        "liongard_environment_name": row.liongard_environment_name,
    }
    unmapped_id = row.liongard_environment_id
    unmapped_name = row.liongard_environment_name

    session.delete(row)
    log_event(
        session,
        org_id=org_id,
        action="liongard_environment.unmap",
        entity_type="org_liongard_environment",
        entity_id=row.id,
        before_value=before,
        context={"via": "api", "orphaned_scope_entity_count": scope_count},
    )
    session.commit()

    return LiongardUnmapOut(
        liongard_environment_id=unmapped_id,
        liongard_environment_name=unmapped_name,
        orphaned_scope_entity_count=scope_count,
    )


def _describe_liongard_pull(
    entity_label: str, total_found: int, inventory_count: int, changed_count: int
) -> str:
    """Plain-language status for one entity type's pull -- see
    LiongardPullStatus's own docstring for why this exists. Three
    genuinely different situations, all of which otherwise present as
    "an empty (or unremarkable) dry-run":
      1. Liongard returned nothing at all for this environment.
      2. Liongard returned records, but none are confirmed (Inventory
         state) yet -- the actionable case, so it names the remedy.
      3. Records are confirmed and compared, and scope already matches --
         genuinely nothing to do, worded differently from case 2 on
         purpose so the two are never mistaken for each other.
    """
    if total_found == 0:
        return f"Liongard returned no {entity_label} for this Environment."
    if inventory_count == 0:
        return (
            f"Liongard returned {total_found} {entity_label}, but none are in Inventory "
            "state yet -- promote them from Discovery to Inventory in Liongard before "
            "WinGRC will include them."
        )
    if changed_count == 0:
        return f"{inventory_count} {entity_label} compared against scope -- no changes."
    return f"{inventory_count} {entity_label} in Inventory, {changed_count} new or changed."


@router.post("/{org_id}/integrations/liongard/sync/dry-run", response_model=DryRunOut)
def liongard_sync_dry_run(
    org_id: uuid.UUID, session: Session = Depends(get_session)
) -> DryRunOut:
    """Pull devices + identities from this org's mapped Liongard Environment
    and return the reconcile diff. No writes -- same "confirmed diff before
    mutation" contract as /imports/workbook/dry-run above; apply is the
    identical POST /imports/workbook/apply endpoint, since its body doesn't
    actually depend on the source being a workbook.

    Both device-profiles and identities are pulled in one call (matching
    ROADMAP.md item D's own framing of Liongard as "populates scope lists
    (users, hardware, software)") rather than as two separate dry-runs --
    the resulting diff mixes DEVICE and PERSON rows, exactly like a
    workbook dry-run already mixes entity types from its own multiple
    sheets.

    `pull_status` (see LiongardPullStatus) always carries one entry per
    entity type, even when there's nothing to report -- an empty `changes`
    list is ambiguous (nothing found vs. nothing changed vs. everything
    still Discovery-state and filtered out), and this is what resolves it
    for the caller instead of leaving that read on the raw numbers.

    D.3 second half: the actual pull-and-reconcile computation now lives
    in liongard_sync.py:pull_and_reconcile, shared with the scheduled
    daily sync job -- this endpoint is a thin wrapper that formats the
    same result for the interactive dry-run response. A brand-new
    entity's `incoming.status` already comes back as `pending_approval`
    from that shared function -- see its own docstring for why that
    applies here too, not just the scheduled path.
    """
    try:
        pull = liongard_sync.pull_and_reconcile(session, org_id)
    except liongard_sync.LiongardSyncError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except liongard_connector.LiongardAPIError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e

    result = pull.reconcile_result
    row_warnings = pull.row_warnings
    pull_level_warnings = pull.pull_level_warnings

    pull_status = [
        LiongardPullStatus(
            entity_label=ps.entity_label,
            total_found=ps.total_found,
            inventory_count=ps.inventory_count,
            message=_describe_liongard_pull(
                ps.entity_label, ps.total_found, ps.inventory_count, ps.changed_count
            ),
        )
        for ps in pull.pull_statuses
    ]

    return DryRunOut(
        summary=result.summary(),
        warnings=pull_level_warnings,
        pull_status=pull_status,
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
                warnings=row_warnings.get((c.entity_type.value, c.natural_key.strip().lower()), []),
            )
            for c in result.changes
            if c.change_type.value != "unchanged"
        ],
    )
