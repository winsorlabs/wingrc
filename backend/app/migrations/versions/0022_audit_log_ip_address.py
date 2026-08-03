"""Audit log viewer: audit_log.ip_address, populated going forward only.

Adds a nullable `ip_address VARCHAR(45)` (enough for an IPv4-mapped IPv6
literal) to audit_log. Populated at write time by app/audit.py's
log_event() reading a per-request ContextVar set by main.py's new
middleware (app.auth.get_client_ip — the same X-Real-IP-aware resolver the
I.6 login rate limiter already uses; see that function's own docstring for
the nginx trust assumption this reuses rather than duplicates).

Rows written before this migration have ip_address = NULL and cannot be
backfilled — the address was never captured, so there is nothing to derive
it from. The audit log viewer must show this as "unknown", not blank-as-if-
filtered, and the IP filter must not treat NULL as a match.

Two supporting indexes:
  - (org_id, created_at) — every list query is org-scoped and sorts newest
    first; this is the base-case index regardless of which filters are set.
  - (org_id, ip_address) — the new filterable column, same org-scoped shape.

Revision ID: 0022_audit_log_ip_address
Revises: 0021_user_deleted_at
Create Date: 2026-08-03
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022_audit_log_ip_address"
down_revision: str | None = "0021_user_deleted_at"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "audit_log",
        sa.Column("ip_address", sa.String(45), nullable=True),
    )
    op.create_index("idx_audit_log_org_created", "audit_log", ["org_id", "created_at"])
    op.create_index("idx_audit_log_org_ip", "audit_log", ["org_id", "ip_address"])


def downgrade() -> None:
    op.drop_index("idx_audit_log_org_ip", table_name="audit_log")
    op.drop_index("idx_audit_log_org_created", table_name="audit_log")
    op.drop_column("audit_log", "ip_address")
