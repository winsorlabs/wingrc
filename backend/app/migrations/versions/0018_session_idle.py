"""Session inactivity timeout (3.1.11): last_activity_at + idle window.

Adds user_session.last_activity_at, defaulting to now() so no pre-existing
session is retroactively treated as stale on deploy. Extends
auth.resolve_session (from 0015) with a second parameter, p_idle_seconds,
enforced in the same SECURITY DEFINER lookup that already checks
revoked_at/expires_at. The actual policy value (session_idle_minutes)
lives in config.py, not here — this function only enforces whatever
window the caller passes in.

The old single-argument auth.resolve_session(VARCHAR) is dropped rather
than left alongside a new two-argument overload: CREATE OR REPLACE only
adds an overload when the parameter list changes, and an old, callable
signature with no idle check has no business staying around.

Revision ID: 0018_session_idle
Revises: 0017_api_login_method
Create Date: 2026-07-24
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018_session_idle"
down_revision: str | None = "0017_api_login_method"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "user_session",
        sa.Column(
            "last_activity_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    op.execute("DROP FUNCTION IF EXISTS auth.resolve_session(VARCHAR)")
    op.execute("""
        CREATE FUNCTION auth.resolve_session(p_hash VARCHAR, p_idle_seconds INT)
        RETURNS TABLE (user_id UUID, org_id UUID, expires_at TIMESTAMPTZ)
        SECURITY DEFINER
        SET search_path = public, pg_catalog
        LANGUAGE sql STABLE AS $$
            SELECT s.user_id, s.org_id, s.expires_at
            FROM public.user_session s
            WHERE s.token_hash = p_hash
              AND s.revoked_at IS NULL
              AND s.expires_at > now()
              AND s.last_activity_at > now() - make_interval(secs => p_idle_seconds)
            LIMIT 1;
        $$
    """)


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS auth.resolve_session(VARCHAR, INT)")
    op.execute("""
        CREATE FUNCTION auth.resolve_session(p_hash VARCHAR)
        RETURNS TABLE (user_id UUID, org_id UUID, expires_at TIMESTAMPTZ)
        SECURITY DEFINER
        SET search_path = public, pg_catalog
        LANGUAGE sql STABLE AS $$
            SELECT s.user_id, s.org_id, s.expires_at
            FROM public.user_session s
            WHERE s.token_hash = p_hash
              AND s.revoked_at IS NULL
              AND s.expires_at > now()
            LIMIT 1;
        $$
    """)
    op.drop_column("user_session", "last_activity_at")
