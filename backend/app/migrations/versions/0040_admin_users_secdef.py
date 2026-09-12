"""M.7 -- auth.all_users_directory(): deployment-wide user directory read
for the new Administration -> Users screen (G.11).

See docs/adr/0009-multi-org-user-access.md's M.7 entry and
docs/PLAN-gui-restructure.md's M.7/M.8/G.11 section. Same SECURITY
DEFINER pattern as every prior cross-org read in this codebase
(auth.msp_role_users/auth.my_org_memberships, migrations 0025/0027;
auth.resolve_user_identity, migration 0033; auth.product_deployment_
footprint, migration 0037) -- "every user, and every org_membership row
each one holds" cannot be expressed as any single value of the per-request
app.current_org GUC, so this bypasses org_membership's RLS deliberately
and narrowly, the same way every function in that lineage does. EXECUTE
restricted to wingrc_app, not PUBLIC, for the same reason as all of them:
this discloses identity and cross-org access information, not something
to leave at Postgres's default.

Returns memberships as a JSONB array (org_id/org_name/role per row)
rather than a second function + a client-side join -- one user's full
membership list is exactly the shape the directory UI needs per row, and
it's still one narrow, single-purpose cross-org read, not scope creep
into a second, differently-shaped function.

Deliberately does NOT return User.role. As of M.4
(auth.py:require_org_access), User.role no longer governs access
anywhere in the normal request path -- it is read only as a fallback for
a membership row that should always exist (auth.py:_role_for_membership),
logged loudly when that fallback actually fires. Surfacing it in a
directory next to real per-org org_membership.role values would suggest
it means something it doesn't; the directory shows home_org_id (still
genuinely authoritative -- session-resolution's default app.current_org,
and the audit-log anchor for account-level events with no org in the
URL) and the real per-org membership roles instead.

Revision ID: 0040_admin_users_secdef
Revises: 0039_publish_existing_products
Create Date: 2026-09-12
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0040_admin_users_secdef"
down_revision: str | None = "0039_publish_existing_products"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_ROLE = "wingrc_app"


def upgrade() -> None:
    op.execute("""
        CREATE FUNCTION auth.all_users_directory()
        RETURNS TABLE (
            id UUID,
            email VARCHAR,
            display_name VARCHAR,
            home_org_id UUID,
            deleted_at TIMESTAMPTZ,
            memberships JSONB
        )
        SECURITY DEFINER
        SET search_path = public, pg_catalog
        LANGUAGE sql STABLE AS $$
            -- Deliberately unscoped by app.current_org -- the whole point
            -- is every user and every org_membership row across every
            -- org in the deployment, which no per-request RLS value can
            -- express. Read-only, but still bypasses RLS and discloses
            -- identity + cross-org access information deployment-wide --
            -- EXECUTE is restricted below, not left at Postgres's PUBLIC
            -- default.
            SELECT
                u.id,
                u.email,
                u.display_name,
                u.home_org_id,
                u.deleted_at,
                COALESCE(
                    jsonb_agg(
                        jsonb_build_object('org_id', om.org_id, 'org_name', o.name, 'role', om.role)
                        ORDER BY o.name
                    ) FILTER (WHERE om.id IS NOT NULL),
                    '[]'::jsonb
                ) AS memberships
            FROM public."user" u
            LEFT JOIN public.org_membership om ON om.user_id = u.id
            LEFT JOIN public.organization o ON o.id = om.org_id
            GROUP BY u.id, u.email, u.display_name, u.home_org_id, u.deleted_at;
        $$;
    """)
    op.execute("REVOKE EXECUTE ON FUNCTION auth.all_users_directory() FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION auth.all_users_directory() TO {_APP_ROLE}")


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS auth.all_users_directory()")
