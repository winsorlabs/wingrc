"""Fix M.2's real-Postgres RLS crash: SECURITY DEFINER cross-org functions.

See docs/adr/0009-multi-org-user-access.md's "System-level cross-org
operations need a different mechanism than per-request RLS" subsection
for the full incident writeup. Summary: org_membership.py's
provision_new_org_memberships() read every existing msp_admin/
msp_engineer user via a plain ORM query, which runs under user's RLS
policy (0015) -- org_id = current_setting('app.current_org')::uuid. That
policy is correct for an ordinary one-org-per-request read; it cannot be
correct for a query whose whole point is seeing across every org. On real
Postgres this either crashed (''::uuid cast, when app.current_org hadn't
been touched on the current pooled connection since its last rollback --
Postgres reverts a never-declared custom GUC's placeholder to boot_val
'' on ROLLBACK, not to "not yet registered", once that connection has
touched it at all) or silently under-scoped the read to one org instead
of every org (when app.current_org held a valid value).

Two SECURITY DEFINER functions, matching auth.resolve_session/
auth.find_user_for_login/auth.resolve_api_token's existing precedent
(0015) exactly -- the same tool this codebase already uses for "must
read/write before this request's own org context applies":

  auth.msp_role_users()             -- every existing msp_admin/
                                        msp_engineer user, id + role.
  auth.grant_org_membership(...)    -- insert one org_membership row,
                                        idempotent (ON CONFLICT DO
                                        NOTHING), bypassing RLS.

Unlike 0016's four functions (which rely on Postgres's default PUBLIC
EXECUTE grant, deliberately -- see that migration's own docstring for why
that was fine there), EXECUTE here is explicitly restricted to
wingrc_app. grant_org_membership performs no authorization of its own
(see its body) and mints real access grants -- leaving it PUBLIC-callable
would let any login role hand out org_membership rows directly via SQL,
bypassing every application-layer check entirely. msp_role_users() is
read-only but still discloses every MSP user's identity deployment-wide;
restricted for the same reason, not because either function is uniquely
dangerous compared to 0016's, but because "rely on the implicit default"
is worth stopping doing going forward rather than only in hindsight.

Revision ID: 0025_org_membership_security_definer
Revises: 0024_msp_membership_backfill
Create Date: 2026-08-08
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0025_org_membership_security_definer"
down_revision: str | None = "0024_msp_membership_backfill"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_ROLE = "wingrc_app"


def upgrade() -> None:
    op.execute("""
        CREATE FUNCTION auth.msp_role_users()
        RETURNS TABLE (id UUID, role VARCHAR)
        SECURITY DEFINER
        SET search_path = public, pg_catalog
        LANGUAGE sql STABLE AS $$
            -- Deliberately unscoped by app.current_org: the entire point
            -- is to see every msp_admin/msp_engineer across every org in
            -- the deployment, which no per-request RLS value can express.
            -- Read-only, but still bypasses RLS -- EXECUTE is restricted
            -- below, not left at Postgres's PUBLIC default.
            SELECT id, role FROM public."user" WHERE role IN ('msp_admin', 'msp_engineer');
        $$;
    """)
    op.execute("REVOKE EXECUTE ON FUNCTION auth.msp_role_users() FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION auth.msp_role_users() TO {_APP_ROLE}")

    op.execute("""
        CREATE FUNCTION auth.grant_org_membership(p_user_id UUID, p_org_id UUID, p_role VARCHAR)
        RETURNS UUID
        SECURITY DEFINER
        SET search_path = public, pg_catalog
        LANGUAGE sql AS $$
            -- No authorization check of any kind: this grants whatever
            -- (user_id, org_id, role) it is called with, unconditionally.
            -- It exists ONLY to bypass org_membership's RLS for the
            -- narrow, already-authorized cross-org writes
            -- org_membership.py's auto-provisioning performs -- the
            -- caller (application code, already behind require_role/
            -- require_org_access) is entirely responsible for deciding
            -- whether a grant should happen. Do not call this from
            -- anywhere that hasn't already made that decision, and do
            -- not treat it as a safe, generally-callable primitive just
            -- because it's SECURITY DEFINER -- it is exactly as
            -- dangerous as a raw INSERT into org_membership would be if
            -- RLS weren't in the way at all.
            INSERT INTO public.org_membership (id, user_id, org_id, role)
            VALUES (gen_random_uuid(), p_user_id, p_org_id, p_role)
            ON CONFLICT (user_id, org_id) DO NOTHING
            RETURNING id;
        $$;
    """)
    op.execute(
        "REVOKE EXECUTE ON FUNCTION "
        "auth.grant_org_membership(UUID, UUID, VARCHAR) FROM PUBLIC"
    )
    op.execute(
        f"GRANT EXECUTE ON FUNCTION "
        f"auth.grant_org_membership(UUID, UUID, VARCHAR) TO {_APP_ROLE}"
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS auth.grant_org_membership(UUID, UUID, VARCHAR)")
    op.execute("DROP FUNCTION IF EXISTS auth.msp_role_users()")
