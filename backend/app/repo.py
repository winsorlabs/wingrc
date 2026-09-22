"""Persistence adapter between `ScopeEntity` rows and `CanonicalEntity` domain
objects. The domain core never imports SQLAlchemy; this is the only seam.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from .domain import (
    CanonicalEntity,
    EntityStatus,
    EntityType,
    ScopeCategory,
    Source,
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
    return [to_canonical(r) for r in session.scalars(stmt)]


def upsert(session: Session, org_id: uuid.UUID, entity: CanonicalEntity) -> ScopeEntity:
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

    if row is None:
        row = ScopeEntity(org_id=org_id, entity_type=entity.entity_type.value)
        session.add(row)
    row.natural_key = entity.natural_key
    row.scope_category = entity.scope_category.value if entity.scope_category else None
    row.status = entity.status.value
    row.in_boundary = entity.in_boundary
    row.source = entity.source.value
    row.source_ref = entity.source_ref
    row.attributes = entity.attributes
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
