"""Fix password_history retention ordering: add a real monotonic seq column.

Root cause (found via test_reuse_allows_password_beyond_generation_window
failing deterministically against real Postgres, never against the
DB-free unit suite): auth.check_password_reuse and auth.record_password
(0019) both ordered by `created_at DESC` with no tiebreaker. Postgres's
now()/CURRENT_TIMESTAMP — what `created_at`'s server_default (func.now())
calls — returns the *start time of the enclosing transaction*, not the time
a given statement executes. Multiple password_history rows inserted inside
one transaction (the exact pattern the failing test exercises, and a
pattern this codebase's savepoint-per-call test fixture doesn't protect
against) get a byte-identical created_at, and "ORDER BY created_at DESC"
over tied rows has no defined order. Postgres's actual (undocumented,
implementation-specific) tie resolution for that query shape evidently
preserves original insertion order rather than reversing it, so
record_password's retention-trim step deleted the newest row instead of
the oldest, letting an old password stay inside the "last N" reuse window
indefinitely.

This is the first table in this codebase where sort order is load-bearing
for a security control (password reuse) rather than just display — no
other table has ever needed a tiebreaker because nothing else's correctness
depended on strict recency order. `id` (a client-generated uuid.uuid4(),
per PasswordHistory in models.py) can't serve as a tiebreaker either — a
random UUID carries no correlation with insertion order, so pairing it with
created_at would make the query deterministic without making it correct.

Fix: add `seq BIGINT GENERATED ALWAYS AS IDENTITY` — a real, unconditionally
monotonic ordering key — and repoint both call sites in auth.py at
`seq.desc()` instead of `created_at.desc()`. `id` stays the UUID primary key
(consistent with every other table in this codebase); `seq` is purely an
ordering column. `created_at` is kept on the model for potential future
display/audit use but is no longer load-bearing for retention correctness.
Index swaps from (user_id, created_at) to (user_id, seq) accordingly —
nothing else in the codebase queries password_history by created_at.

Pre-existing rows: password_history was created by 0019, deployed and
migrated on wl-util-1 immediately before this fix, with no application
code path yet calling record_password outside of test runs (which roll
back their own inserts — see tests/conftest.py's db_session fixture).
`SELECT COUNT(*) FROM password_history` against wl-util-1's live wingrc DB
was checked (not assumed) before writing this migration, confirming the
table was still empty in that environment — see PLAN-auth-rbac-completion.md,
I.5 deviation 5, for the recorded count. If this migration ever runs
against an environment where the table is *not* empty, Postgres still
backfills `seq` for existing rows (GENERATED ALWAYS AS IDENTITY supports
ADD COLUMN on a populated table), but the order it assigns to pre-existing
rows is Postgres's internal physical/ctid row order — likely, but not
guaranteed, to match true insertion order for a table that's never been
updated/vacuumed. That's a one-time bootstrapping caveat for rows that
predate this migration only; every row inserted after this migration gets
a correctly monotonic seq at insert time, with no possible collision.

Revision ID: 0020_password_history_seq
Revises: 0019_password_history
Create Date: 2026-07-31
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0020_password_history_seq"
down_revision: str | None = "0019_password_history"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "password_history",
        sa.Column("seq", sa.BigInteger(), sa.Identity(always=True), nullable=False),
    )
    op.drop_index("idx_password_history_user_created", table_name="password_history")
    op.create_index(
        "idx_password_history_user_seq",
        "password_history", ["user_id", "seq"],
    )


def downgrade() -> None:
    op.drop_index("idx_password_history_user_seq", table_name="password_history")
    op.create_index(
        "idx_password_history_user_created",
        "password_history", ["user_id", "created_at"],
    )
    op.drop_column("password_history", "seq")
