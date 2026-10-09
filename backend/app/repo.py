"""Persistence adapter between `ScopeEntity` rows and `CanonicalEntity` domain
objects. The domain core never imports SQLAlchemy; this is the only seam.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from .domain import (
    CONNECTOR_SOURCES,
    OPERATOR_OVERLAY_ATTRIBUTES,
    CanonicalEntity,
    EntityStatus,
    EntityType,
    ScopeCategory,
    Source,
    connector_supplied_fields,
)
from .models import Organization, ScopeEntity


class PendingApprovalWriteError(Exception):
    """A scope_entity row must never be written with status=pending_approval,
    and once a row already holds that status, nothing may change it except
    the Liongard asset-approval workflow (liongard_sync.py:approve_change/
    reject_change).

    This is the one choke point for the invariant, not a per-endpoint
    check: repo.upsert() is the only function that ever writes a
    ScopeEntity row (create_scope_entity, patch_scope_entity, import_apply,
    cli.py's `seed --apply`, and approve_change/reject_change all funnel
    through it), so enforcing it here closes every writer at once,
    including ones not yet written. approve_change/reject_change never
    trip this in normal operation -- they always resolve a change whose
    scope_entity row doesn't exist yet (pull_and_reconcile forces NEW
    entities to pending_approval only in the LiongardSyncResultChange
    JSONB, never in scope_entity -- see that function's own docstring) and
    always write status=active themselves. A row already stuck at
    pending_approval with no resolvable change (a stranded row -- see
    docs/roadmap.md's Liongard sync entry) has no sanctioned edit path:
    delete it and re-sync so it reconciles as NEW again.
    """


def to_canonical(row: ScopeEntity) -> CanonicalEntity:
    return CanonicalEntity(
        entity_type=EntityType(row.entity_type),
        natural_key=row.natural_key,
        attributes=dict(row.attributes or {}),
        scope_category=(
            ScopeCategory(row.scope_category) if row.scope_category else None
        ),
        status=EntityStatus(row.status),
        in_boundary=row.in_boundary,
        source=Source(row.source),
        source_ref=row.source_ref,
    )


def list_entities(
    session: Session, org_id: uuid.UUID, entity_type: EntityType | None = None
) -> list[CanonicalEntity]:
    stmt = select(ScopeEntity).where(ScopeEntity.org_id == org_id)
    if entity_type is not None:
        stmt = stmt.where(ScopeEntity.entity_type == entity_type.value)
    # Ordered because this feeds render.py's .xlsx list exports -- an
    # assessor-facing deliverable, so two exports of unchanged scope data
    # must not order rows differently. (org_id, entity_type, natural_key) is
    # unique (uq_scope_entity_identity) and org_id is already filtered, so
    # this is a total order rather than one that merely usually holds.
    stmt = stmt.order_by(ScopeEntity.entity_type, ScopeEntity.natural_key)
    return [to_canonical(r) for r in session.scalars(stmt)]


def upsert(
    session: Session,
    org_id: uuid.UUID,
    entity: CanonicalEntity,
    *,
    operator_edit: bool = False,
) -> ScopeEntity:
    """The only writer of a ScopeEntity row.

    `operator_edit=True` is for a person editing the entity directly
    (POST/PATCH /scope): what they send is what is stored. Every other
    caller is an import or a sync, and the default is the safe one, so a
    new writer that forgets the flag cannot erase anything:

    - An OPERATOR_OVERLAY_ATTRIBUTES key the incoming entity does not
      carry is kept from the existing row. A Liongard write never sets one
      at all -- Liongard is never the source of where a device sits.
    - `scope_category` is kept when the incoming entity has none.
    - No import or sync changes an existing row's `in_boundary`, and none
      moves a row out of `decommissioned`. Whether an entity is inside the
      CUI boundary, and whether it has come back into service, are human
      decisions no feed has a source for; before this, applying a CHANGED
      row for a device a reviewer had rejected (in_boundary=False) quietly
      put it back in scope, because every fresh pull and every workbook row
      carries in_boundary=True by default. Liongard was fixed 2026-10-07 and
      the workbook path the same day after the asymmetry was noticed.
    - A workbook may still *decommission* an entity -- its Decommissioned
      Date column is a real source for that. Liongard keeps the row's
      status outright: it has no source for decommissioning either.

    Before 2026-10-07 this assigned every field wholesale, so a sync
    applied after an operator filled in Location erased it.
    """
    stmt = select(ScopeEntity).where(
        ScopeEntity.org_id == org_id,
        ScopeEntity.entity_type == entity.entity_type.value,
        ScopeEntity.natural_key == entity.natural_key,
    )
    row = session.scalars(stmt).first()

    if entity.status == EntityStatus.PENDING_APPROVAL:
        raise PendingApprovalWriteError(
            f"Refusing to write status=pending_approval for {entity.entity_type.value} "
            f"{entity.natural_key!r} directly -- a pending Liongard entity is queued in "
            "liongard_sync_result_change and only reaches scope_entity once approved or "
            "rejected."
        )
    if row is not None and row.status == EntityStatus.PENDING_APPROVAL.value:
        raise PendingApprovalWriteError(
            f"{entity.entity_type.value} {entity.natural_key!r} is pending Liongard asset "
            "approval -- resolve it via the approve/reject workflow, not a direct edit."
        )

    attributes = dict(entity.attributes)
    source, source_ref = entity.source, entity.source_ref
    scope_category = entity.scope_category.value if entity.scope_category else None
    status = entity.status.value
    in_boundary = entity.in_boundary
    if not operator_edit:
        from_sync = entity.source == Source.LIONGARD
        existing = dict(row.attributes or {}) if row is not None else {}
        for key in OPERATOR_OVERLAY_ATTRIBUTES:
            if from_sync:
                attributes.pop(key, None)
            if key not in attributes and key in existing:
                attributes[key] = existing[key]
        if (
            row is not None
            and Source(row.source) in CONNECTOR_SOURCES
            and entity.source not in CONNECTOR_SOURCES
        ):
            # A transcription never overwrites what a connector observed,
            # and never takes over the row's provenance (2026-10-09). The
            # dry-run reports each such field as a conflict.
            for key in connector_supplied_fields(entity.entity_type, existing):
                attributes[key] = existing[key]
            source, source_ref = Source(row.source), row.source_ref
        if row is not None:
            if scope_category is None:
                scope_category = row.scope_category
            in_boundary = row.in_boundary
            if from_sync or row.status == EntityStatus.DECOMMISSIONED.value:
                status = row.status

    if row is None:
        row = ScopeEntity(org_id=org_id, entity_type=entity.entity_type.value)
        session.add(row)
    row.natural_key = entity.natural_key
    row.scope_category = scope_category
    row.status = status
    row.in_boundary = in_boundary
    row.source = source.value
    row.source_ref = source_ref
    row.attributes = attributes
    row.last_verified_at = datetime.now(UTC)
    return row


def get_or_create_org(session: Session, name: str) -> Organization:
    org = session.scalars(
        select(Organization).where(Organization.name == name)
    ).first()
    if org is None:
        org = Organization(name=name)
        session.add(org)
        session.flush()
    return org
