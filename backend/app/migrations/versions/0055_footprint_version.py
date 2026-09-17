"""Extend auth.product_deployment_footprint() with baseline_version_id/
version_number (roadmap item P follow-up): "which tenants are on which
version" has to be answerable from the UI, per that item's own §6
verification list.

org_product still carries RLS -- this SECURITY DEFINER function is the
one place that already reads across every org for one product (migration
0037); it now also joins product_baseline_version (no RLS, deployment-
wide, same as product/baseline_control) to surface which version each
org's activation is pinned to, in the same query rather than a second
round trip.

Return type changes, so this is DROP + CREATE, not CREATE OR REPLACE
(Postgres does not allow CREATE OR REPLACE to change a function's output
columns).

Revision ID: 0055_footprint_version
Revises: 0054_baseline_versioning
Create Date: 2026-09-17
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0055_footprint_version"
down_revision: str | None = "0054_baseline_versioning"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_ROLE = "wingrc_app"


def upgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS auth.product_deployment_footprint(UUID)")
    op.execute("""
        CREATE FUNCTION auth.product_deployment_footprint(p_product_id UUID)
        RETURNS TABLE (
            org_id UUID,
            org_name VARCHAR,
            status VARCHAR,
            baseline_version_id UUID,
            version_number INTEGER
        )
        SECURITY DEFINER
        SET search_path = public, pg_catalog
        LANGUAGE sql STABLE AS $$
            SELECT op.org_id, o.name, op.status, op.baseline_version_id, pbv.version_number
            FROM public.org_product op
            JOIN public.organization o ON o.id = op.org_id
            LEFT JOIN public.product_baseline_version pbv ON pbv.id = op.baseline_version_id
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
    op.execute("""
        CREATE FUNCTION auth.product_deployment_footprint(p_product_id UUID)
        RETURNS TABLE (org_id UUID, org_name VARCHAR, status VARCHAR)
        SECURITY DEFINER
        SET search_path = public, pg_catalog
        LANGUAGE sql STABLE AS $$
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
