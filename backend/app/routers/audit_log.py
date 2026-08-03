"""Audit log viewer (read-only).

GET /orgs/{org_id}/audit-log — paginated, filterable, msp_admin only.

The log is append-only by design (see audit.py) — this router has no
mutating endpoints and never will.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..auth import CurrentUser, require_org_access
from ..db import get_session
from ..models import AuditLog

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

    return {
        "items": [_row_out(r) for r in rows],
        "total": total,
        "offset": offset,
        "limit": limit,
    }


def _row_out(r: AuditLog) -> dict:
    return {
        "id": str(r.id),
        "created_at": r.created_at.isoformat(),
        "actor": r.actor,
        "actor_type": r.actor_type,
        "action": r.action,
        "entity_type": r.entity_type,
        "entity_id": str(r.entity_id),
        "before_value": r.before_value,
        "after_value": r.after_value,
        "context": r.context,
        # NULL means "predates IP capture (migration 0022) or was logged
        # outside an HTTP request" — never "unknown due to a filter".
        "ip_address": r.ip_address,
    }
