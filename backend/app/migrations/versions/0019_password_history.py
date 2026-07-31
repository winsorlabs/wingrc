"""Password lifecycle: password_history table + reset-token redemption fix.

Adds `password_history(id, user_id FK, password_hash, created_at)` with an
index on `(user_id, created_at DESC)`, RLS-gated the same way
`mfa_backup_code` is (no direct org_id column, scoped via a subquery on
`user.org_id`). Used by `auth.check_password_reuse`/`auth.record_password`
(backend/app/auth.py) to reject reuse of the last N passwords on
`/auth/set-password`.

Also widens `auth.find_user_for_invite` (from 0015) to drop its
`is_active = FALSE` predicate. That predicate was correct when the function
only served invite redemption (a newly-invited user is always inactive until
MFA enrollment). I.5 (password lifecycle) reuses the same token mechanism and
the same `/set-password` endpoint for *password reset* of an existing,
already-active user — under the old predicate `find_user_for_invite` would
silently return zero rows for any active user, making every reset token
"invalid" no matter how correctly it was minted. The token hash + expiry
check is the real authorization; `is_active` was never load-bearing for that
and is dropped rather than reinterpreted per-caller.

Revision ID: 0019_password_history
Revises: 0018_session_idle
Create Date: 2026-07-30
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0019_password_history"
down_revision: str | None = "0018_session_idle"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "password_history",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", UUID(as_uuid=True),
                  sa.ForeignKey("user.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )
    op.create_index(
        "idx_password_history_user_created",
        "password_history", ["user_id", "created_at"],
    )
    op.execute("ALTER TABLE password_history ENABLE ROW LEVEL SECURITY")
    op.execute(
        """CREATE POLICY password_history_org ON password_history
           USING (user_id IN (
               SELECT id FROM "user"
               WHERE org_id = current_setting('app.current_org', true)::uuid
           ))"""
    )

    op.execute("DROP FUNCTION IF EXISTS auth.find_user_for_invite(VARCHAR)")
    op.execute("""
        CREATE FUNCTION auth.find_user_for_invite(p_token_hash VARCHAR)
        RETURNS SETOF public."user"
        SECURITY DEFINER
        SET search_path = public, pg_catalog
        LANGUAGE sql STABLE AS $$
            SELECT * FROM public."user"
            WHERE invite_token_hash = p_token_hash
              AND invite_expires_at > now()
            LIMIT 1;
        $$
    """)


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS auth.find_user_for_invite(VARCHAR)")
    op.execute("""
        CREATE FUNCTION auth.find_user_for_invite(p_token_hash VARCHAR)
        RETURNS SETOF public."user"
        SECURITY DEFINER
        SET search_path = public, pg_catalog
        LANGUAGE sql STABLE AS $$
            SELECT * FROM public."user"
            WHERE invite_token_hash = p_token_hash
              AND invite_expires_at > now()
              AND is_active = FALSE
            LIMIT 1;
        $$
    """)
    op.drop_index("idx_password_history_user_created", table_name="password_history")
    op.drop_table("password_history")
