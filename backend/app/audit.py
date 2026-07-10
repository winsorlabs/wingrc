"""Append-only audit log service.

Every mutating compliance operation flows through log_event(). This module
intentionally has no UPDATE or DELETE paths — entries are insert-only.

Scoped to meaningful compliance mutations (signal, not firehose):
  org_product.activate        — product activated, magic loop fired
  org_product.deactivate      — product decommissioned
  control_state.update        — status change (mark-met, needs_review, etc.)
  evidence_state_link.archive — evidence link archived on deactivation
  evidence_task.update        — task status changed (open/collected/na)
  evidence_task.archive       — task archived on deactivation
  implementation_statement.upsert — statement created or updated

NOT logged (noise):
  _seed_control_states() bulk insert on assessment creation
  Internal flushes, SPRS recompute, read-only queries

Large text fields (implementation_statement.body): stored in full; bodies
exceeding _MAX_BODY_LEN chars are truncated and after_value["body_truncated"]
is set to True so consumers know the value is partial.

DB-level append-only hardening (pending production step):
  REVOKE UPDATE, DELETE ON audit_log FROM <app_role>;

Actor field: wired as actor="system", actor_type="system" until authentication
lands (roadmap item I). No schema change required when real user identity arrives.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from .models import AuditLog

_MAX_BODY_LEN = 4000
_TEXT_KEYS = ("body", "description", "requirement_text", "change_reason")


def log_event(
    session: Session,
    *,
    org_id: uuid.UUID | None,
    action: str,
    entity_type: str,
    entity_id: uuid.UUID,
    before_value: dict[str, Any] | None = None,
    after_value: dict[str, Any] | None = None,
    context: dict[str, Any] | None = None,
    actor: str = "system",
    actor_type: str = "system",
) -> AuditLog:
    """Insert one audit log entry. Never updates or deletes existing rows."""
    entry = AuditLog(
        org_id=org_id,
        actor=actor,
        actor_type=actor_type,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        before_value=_sanitise(before_value),
        after_value=_sanitise(after_value),
        context=context,
        created_at=datetime.now(UTC),
    )
    session.add(entry)
    return entry


def _sanitise(value: dict[str, Any] | None) -> dict[str, Any] | None:
    """Truncate oversized text fields; leave everything else verbatim."""
    if value is None:
        return None
    out = dict(value)
    for key in _TEXT_KEYS:
        if key in out and isinstance(out[key], str) and len(out[key]) > _MAX_BODY_LEN:
            out[key] = out[key][:_MAX_BODY_LEN]
            out["body_truncated"] = True
    return out
