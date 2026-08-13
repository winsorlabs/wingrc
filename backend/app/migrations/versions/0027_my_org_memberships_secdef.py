"""ADR 0009 M.5: SECURITY DEFINER function backing GET /orgs's reshape.

GET /orgs changes from "every Organization row, gated by
require_role('msp_admin', 'msp_engineer')" to "every org the caller has
an org_membership row for, any authenticated role" (routers/orgs.py's
list_orgs). That read is cross-org against org_membership by
construction -- the whole point is seeing every membership row for one
user, not just whichever single org happens to be app.current_org at
that point in the request. It cannot be expressed as any single value
of RLS's per-request GUC, the same shape of problem M.2's
provision_new_org_memberships() hit (see migration 0025 and ADR 0009's
"System-level cross-org operations" subsection) -- and the ADR flagged
this exact spot by name so M.5 wouldn't repeat that mistake: "This read
is cross-org against org_membership ... it needs the same SECURITY
DEFINER treatment as M.2's auto-provisioning ... not a plain ORM
query."

One function, matching migration 0025's precedent exactly: EXECUTE
restricted to wingrc_app (not PUBLIC) -- it discloses every org a given
user_id can access, which is exactly what the endpoint is supposed to
return to that user and only that user; the application layer (not this
function) is responsible for calling it with the authenticated caller's
own id, never an arbitrary one.

Revision ID: 0027_my_org_memberships_secdef
Revises: 0026_user_home_org_id_rename
Create Date: 2026-08-13
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0027_my_org_memberships_secdef"
down_revision: str | None = "0026_user_home_org_id_rename"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_ROLE = "wingrc_app"


def upgrade() -> None:
    op.execute("""
        CREATE FUNCTION auth.my_org_memberships(p_user_id UUID)
        RETURNS TABLE (org_id UUID, role VARCHAR)
        SECURITY DEFINER
        SET search_path = public, pg_catalog
        LANGUAGE sql STABLE AS $$
            -- Deliberately unscoped by app.current_org: the point is to
            -- see every org this one user has a membership row for,
            -- which no single per-request RLS value can express. Bypasses
            -- RLS -- EXECUTE is restricted below, not left at Postgres's
            -- PUBLIC default. Callers must pass the authenticated
            -- caller's own id; this function performs no check of its
            -- own that p_user_id is who's asking.
            SELECT org_id, role FROM public.org_membership WHERE user_id = p_user_id;
        $$;
    """)
    op.execute("REVOKE EXECUTE ON FUNCTION auth.my_org_memberships(UUID) FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION auth.my_org_memberships(UUID) TO {_APP_ROLE}")


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS auth.my_org_memberships(UUID)")
