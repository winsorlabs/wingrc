"""Persistence adapter between `ScopeEntity` rows and `CanonicalEntity` domain
objects. The domain core never imports SQLAlchemy; this is the only seam.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

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
    row.last_verified_at = datetime.now(timezone.utc)
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
