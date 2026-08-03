"""User deletion (ADR 0006): user.deleted_at marks permanent anonymization.

Adds a nullable `deleted_at TIMESTAMPTZ` to `user`, distinct from
`is_active`. `is_active=False` is the reversible, day-to-day deactivation
state (an admin can flip it back). `deleted_at` marks the row as
permanently anonymized — PII scrubbed, never reactivatable — per ADR 0006's
"Decision" section. No `audit_log` change: the ADR's whole point is that
the append-only table is never touched by this feature; only `user` gains
a column.

Revision ID: 0021_user_deleted_at
Revises: 0020_password_history_seq
Create Date: 2026-08-03
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021_user_deleted_at"
down_revision: str | None = "0020_password_history_seq"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "user",
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("user", "deleted_at")
