"""G.9 -- auth.product_deployment_footprint(): cross-org read for the
Tools library's "which orgs have this product" view.

org_product carries RLS (migration 0002's _enable_rls, standard
org_id = current_setting('app.current_org')::uuid pattern) -- correct for
every ordinary per-org read, and exactly wrong for a query whose whole
point is seeing every org's org_product rows for one product in a single
result set. Matches the ADR 0009 "System-level cross-org operations need a
different mechanism than per-request RLS" pattern established by
migration 0025 (auth.msp_role_users/auth.grant_org_membership) and
migration 0027 (auth.my_org_memberships) exactly: a narrow, read-only
SECURITY DEFINER SQL function, EXECUTE restricted to wingrc_app rather than
left at Postgres's PUBLIC default, since this discloses which orgs run
which security tools -- deployment-wide identity/posture information, not
something to leave callable by any login role.

organization carries no RLS policy of its own (it's the tenant boundary
itself, not something scoped by one -- same reasoning routers/orgs.py's
list_orgs already relies on for its own follow-up Organization read), so
joining it here needs no separate bypass.

Revision ID: 0037_product_footprint
Revises: 0036_product_document
Create Date: 2026-09-12
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0037_product_footprint"
down_revision: str | None = "0036_product_document"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_ROLE = "wingrc_app"


def upgrade() -> None:
    op.execute("""
        CREATE FUNCTION auth.product_deployment_footprint(p_product_id UUID)
        RETURNS TABLE (org_id UUID, org_name VARCHAR, status VARCHAR)
        SECURITY DEFINER
        SET search_path = public, pg_catalog
        LANGUAGE sql STABLE AS $$
            -- Deliberately unscoped by app.current_org: the entire point is
            -- to see every org's org_product row for one product, which no
            -- single per-request RLS value can express. Read-only, but
            -- still bypasses RLS -- EXECUTE is restricted below.
            SELECT op.org_id, o.name, op.status
            FROM public.org_product op
            JOIN public.organization o ON o.id = op.org_id
            WHERE op.product_id = p_product_id
            ORDER BY o.name;
        $$;
    """)
    op.execute(
        "REVOKE EXECUTE ON FUNCTION auth.product_deployment_footprint(UUID) FROM PUBLIC"
    )
    op.execute(
        f"GRANT EXECUTE ON FUNCTION auth.product_deployment_footprint(UUID) TO {_APP_ROLE}"
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS auth.product_deployment_footprint(UUID)")
