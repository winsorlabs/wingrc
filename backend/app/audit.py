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
  user.invite                — user invited (routers/users.py)
  user.role_change            — role changed via PATCH /users/{id}
  user.activation_change      — is_active changed via PATCH /users/{id}
  user.deactivate             — DELETE /users/{id}
  user.mfa_reset              — admin-forced MFA reset
  user.delete                 — permanent hard-delete of a zero-history user
                                 (ADR 0006). before/after never carry PII.
  user.anonymize              — PII scrubbed, row kept, audit_log untouched
                                 (ADR 0006). after_value is {"anonymized": true}
                                 only — never the pre-scrub email/name.
  api_user.create             — API user (service account) + first token minted
  api_token.create            — token minted (name/role/expiry only — never
                                 the raw token or its hash)
  api_token.revoke            — token revoked

NOT logged (noise):
  _seed_control_states() bulk insert on assessment creation
  Internal flushes, SPRS recompute, read-only queries

Large text fields (implementation_statement.body): stored in full; bodies
exceeding _MAX_BODY_LEN chars are truncated and after_value["body_truncated"]
is set to True so consumers know the value is partial.

DB-level append-only hardening (pending production step):
  REVOKE UPDATE, DELETE ON audit_log FROM <app_role>;

Actor field: log_event() stamps actor/actor_type from _current_actor, a
ContextVar set once per request by auth.py's get_current_user() (every
protected route depends on that function, directly or transitively via
require_org_access/require_write/require_role) — same mechanism as the IP
address below, and for the identical reason: most call sites (engine.py's
assessment-lifecycle functions in particular, but also several router
internals) have no CurrentUser in scope and threading one through every
function on those call chains would be a far larger, riskier change than
one ContextVar. routers/users.py and routers/auth.py pass actor/actor_type
explicitly instead, since current_user is already a local variable at
every one of their call sites — explicit takes precedence when a caller
supplies it; the ContextVar is only the default. Falls back to "system"
when the ContextVar is unset (a call from outside any request — CLI
scripts, migrations, or a test calling log_event() directly): that default
is correct there, not a bug, since there's no authenticated actor to
attribute to. A caller that means a real system-triggered action (nothing
under a human's control) should keep passing actor="system" explicitly so
that's a deliberate choice, not an artifact of forgetting to pass one.

IP address (out-of-band scope, audit log viewer): log_event() stamps
ip_address from _current_ip, a ContextVar set once per request by
main.py's middleware (`get_client_ip(request)` — the same X-Real-IP-aware
resolver auth.py's login rate limiter already uses; not a second, divergent
extraction path). This avoids threading a Request/IP argument through
every one of this module's ~40 call sites, most of which have no Request in
scope at all (engine.py, several router internals). ContextVars set in
Starlette's `@app.middleware("http")` are visible to code invoked via
`call_next()` even for the sync `def` endpoints this codebase uses
throughout — anyio's threadpool wrapper explicitly copies the current
context into the worker thread — verified empirically in
tests/test_audit_log.py rather than assumed. Falls back to None (NULL) when
log_event() is called outside a request (direct test calls, scripts): a
missing value there is correct, not a bug, since there's no client to
attribute.
"""
from __future__ import annotations

import uuid
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from .models import AuditLog

_MAX_BODY_LEN = 4000
_TEXT_KEYS = ("body", "description", "requirement_text", "change_reason")

_current_ip: ContextVar[str | None] = ContextVar("_current_ip", default=None)


def set_current_ip(ip: str | None) -> None:
    """Called once per request by main.py's middleware. Not for use elsewhere
    — log_event() is the only reader.
    """
    _current_ip.set(ip)


_current_actor: ContextVar[str | None] = ContextVar("_current_actor", default=None)
_current_actor_type: ContextVar[str | None] = ContextVar("_current_actor_type", default=None)


def set_current_actor(actor: str, actor_type: str) -> None:
    """Called once per request by auth.py's get_current_user(), right after
    it resolves the session/API-token identity. Not for use elsewhere —
    log_event() is the only reader. See this module's docstring for why a
    ContextVar rather than threading CurrentUser through every call site.
    """
    _current_actor.set(actor)
    _current_actor_type.set(actor_type)


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
    actor: str | None = None,
    actor_type: str | None = None,
) -> AuditLog:
    """Insert one audit log entry. Never updates or deletes existing rows.

    actor/actor_type default to the current request's authenticated
    identity (see _current_actor above) when the caller doesn't pass them
    explicitly, falling back to "system" outside any request context.
    """
    entry = AuditLog(
        org_id=org_id,
        actor=actor if actor is not None else (_current_actor.get() or "system"),
        actor_type=(
            actor_type if actor_type is not None else (_current_actor_type.get() or "system")
        ),
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        before_value=_sanitise(before_value),
        after_value=_sanitise(after_value),
        context=context,
        ip_address=_current_ip.get(),
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
