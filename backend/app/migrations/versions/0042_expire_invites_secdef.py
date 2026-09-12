"""auth.expire_stale_invites(): SECURITY DEFINER cross-org sweep for the
job scheduler's proof job.

Matches auth.msp_role_users()/auth.all_users_directory()'s existing
precedent (0025, 0040) exactly -- the established mechanism in this
codebase for "a system-level operation that must see/touch every org at
once, which no single app.current_org value can express." A background
worker must never be handed a blanket RLS bypass to get this effect; it
gets exactly one purpose-built, narrowly-scoped function instead.

What it does: null out invite_token_hash/invite_expires_at on any user
row whose invite/reset token has already expired. This is data hygiene,
not a security fix -- auth.find_user_for_invite() (0015) already checks
invite_expires_at > now() at redemption time, so an expired token hash is
already unusable. Nothing currently sweeps it, though, so a token hash
past its 48h TTL sits in the row indefinitely. Read-only in effect on
anything that matters for auth (an expired hash can't be redeemed either
way); still restricted to wingrc_app below, same as every other function
this pattern has produced, on the same "don't rely on the PUBLIC default
going forward" reasoning 0025 gave for its own two functions.

Revision ID: 0042_expire_invites_secdef
Revises: 0041_job_run
Create Date: 2026-09-12
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0042_expire_invites_secdef"
down_revision: str | None = "0041_job_run"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_ROLE = "wingrc_app"


def upgrade() -> None:
    op.execute("""
        CREATE FUNCTION auth.expire_stale_invites()
        RETURNS INTEGER
        SECURITY DEFINER
        SET search_path = public, pg_catalog
        LANGUAGE sql AS $$
            WITH cleared AS (
                UPDATE public."user"
                SET invite_token_hash = NULL, invite_expires_at = NULL
                WHERE invite_token_hash IS NOT NULL
                  AND invite_expires_at IS NOT NULL
                  AND invite_expires_at < now()
                RETURNING 1
            )
            SELECT count(*)::INTEGER FROM cleared;
        $$;
    """)
    op.execute("REVOKE EXECUTE ON FUNCTION auth.expire_stale_invites() FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION auth.expire_stale_invites() TO {_APP_ROLE}")


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS auth.expire_stale_invites()")
