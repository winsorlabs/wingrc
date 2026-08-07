"""Audit log viewer (read-only).

GET /orgs/{org_id}/audit-log — paginated, filterable, msp_admin only.

The log is append-only by design (see audit.py) — this router has no
mutating endpoints and never will.

Identity resolution: `actor` and `entity_id` (when `entity_type == "user"`)
are durable GUIDs stored verbatim in audit_log — never overwritten, never
joined into the stored row. Resolving them to a human-readable name/email
happens here, at read time, per request, from the current state of `user`.
This is a display-layer convenience only; the GUID is always returned
alongside it and is the thing that's actually durable (a user's display
name/email can change or be scrubbed — the GUID doesn't).
"""
from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..auth import CurrentUser, require_org_access
from ..db import get_session
from ..models import AuditLog, User

router = APIRouter(prefix="/orgs/{org_id}", tags=["audit-log"])

_MAX_LIMIT = 200


@router.get("/audit-log")
def list_audit_log(
    org_id: uuid.UUID,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=_MAX_LIMIT),
    action: str | None = Query(default=None),
    actor: str | None = Query(default=None),
    ip_address: str | None = Query(default=None),
    start: datetime | None = Query(default=None, description="Inclusive lower bound on created_at"),
    end: datetime | None = Query(default=None, description="Inclusive upper bound on created_at"),
    db: Session = Depends(get_session),
    _current_user: CurrentUser = Depends(require_org_access("msp_admin")),
):
    """Newest first. Filters combine with AND.

    ip_address is a substring match (ILIKE) against the stored value. Rows
    with ip_address IS NULL (everything predating migration 0022, and
    anything logged outside an HTTP request) never match a non-empty
    ip_address filter — this is plain SQL NULL-comparison semantics, not a
    special case, and it is the deliberate behavior: showing a NULL row as
    if it matched a specific address the admin searched for would be a
    false positive, worse than omitting it.

    actor still filters against the raw stored string (a GUID or "system"),
    not the resolved name/email — the filter operates on the durable
    record, matching what before/after already do.
    """
    query = select(AuditLog).where(AuditLog.org_id == org_id)
    count_query = select(func.count()).select_from(AuditLog).where(AuditLog.org_id == org_id)

    if action:
        query = query.where(AuditLog.action == action)
        count_query = count_query.where(AuditLog.action == action)
    if actor:
        query = query.where(AuditLog.actor.ilike(f"%{actor}%"))
        count_query = count_query.where(AuditLog.actor.ilike(f"%{actor}%"))
    if ip_address:
        query = query.where(AuditLog.ip_address.ilike(f"%{ip_address}%"))
        count_query = count_query.where(AuditLog.ip_address.ilike(f"%{ip_address}%"))
    if start:
        query = query.where(AuditLog.created_at >= start)
        count_query = count_query.where(AuditLog.created_at >= start)
    if end:
        query = query.where(AuditLog.created_at <= end)
        count_query = count_query.where(AuditLog.created_at <= end)

    total = db.execute(count_query).scalar_one()
    # id DESC is a pagination-stability tiebreaker only, not a claim that id
    # reflects real chronological order — created_at ties are real (Postgres
    # server_default=func.now() returns transaction start time, so multiple
    # rows written in one transaction share a timestamp; see the
    # password_history seq-column history for the same root cause). Without
    # a deterministic secondary key, offset-based pagination could skip or
    # repeat rows across pages within a tied cluster.
    rows = db.execute(
        query.order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        .offset(offset)
        .limit(limit)
    ).scalars().all()

    users_by_id = _resolve_identities(db, org_id, rows)

    return {
        "items": [_row_out(r, users_by_id) for r in rows],
        "total": total,
        "offset": offset,
        "limit": limit,
    }


def _resolve_identities(
    db: Session, org_id: uuid.UUID, rows: list[AuditLog]
) -> dict[uuid.UUID, User]:
    """One batch query for every GUID referenced on this page — actor (when
    it parses as a UUID; "system" and similar literals don't) and entity_id
    (when entity_type == "user") — rather than a query per row.
    """
    ids: set[uuid.UUID] = set()
    for r in rows:
        actor_id = _parse_uuid(r.actor)
        if actor_id is not None:
            ids.add(actor_id)
        if r.entity_type == "user":
            ids.add(r.entity_id)

    if not ids:
        return {}

    users = db.execute(
        select(User).where(User.home_org_id == org_id, User.id.in_(ids))
    ).scalars().all()
    return {u.id: u for u in users}


def _parse_uuid(value: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(value)
    except (ValueError, AttributeError, TypeError):
        return None


def _identity_out(user_id: uuid.UUID, users_by_id: dict[uuid.UUID, User]) -> dict:
    """Fallback chain (ADR 0006 shapes this — anonymize keeps the row,
    delete removes it):
      1. Row exists, not anonymized -> display_name + email.
      2. Row exists, deleted_at set (anonymized) -> "anonymized" status,
         no display_name/email — never surface the scrubbed placeholder
         values as if they were real PII.
      3. Row absent entirely (hard-deleted) -> "deleted" status. This is
         the documented, expected outcome of ADR 0006's zero-history path,
         not a data-integrity bug, so it's labeled distinctly from
         "anonymized" rather than rendered as a bare orphan GUID.
    The GUID itself is always included — it's the durable record.
    """
    user = users_by_id.get(user_id)
    if user is None:
        return {"id": str(user_id), "status": "deleted", "display_name": None, "email": None}
    if user.deleted_at is not None:
        return {"id": str(user_id), "status": "anonymized", "display_name": None, "email": None}
    return {
        "id": str(user_id),
        "status": "active",
        "display_name": user.display_name,
        "email": user.email,
    }


def _row_out(r: AuditLog, users_by_id: dict[uuid.UUID, User]) -> dict:
    actor_id = _parse_uuid(r.actor)
    actor_user = _identity_out(actor_id, users_by_id) if actor_id is not None else None
    entity_user = _identity_out(r.entity_id, users_by_id) if r.entity_type == "user" else None

    return {
        "id": str(r.id),
        "created_at": r.created_at.isoformat(),
        "actor": r.actor,
        "actor_type": r.actor_type,
        # None when actor isn't a resolvable GUID (e.g. the literal "system").
        "actor_user": actor_user,
        "action": r.action,
        "entity_type": r.entity_type,
        "entity_id": str(r.entity_id),
        # None unless entity_type == "user".
        "entity_user": entity_user,
        "before_value": r.before_value,
        "after_value": r.after_value,
        "context": r.context,
        # NULL means "predates IP capture (migration 0022) or was logged
        # outside an HTTP request" — never "unknown due to a filter".
        "ip_address": r.ip_address,
    }
