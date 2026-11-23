"""A new organization stops granting access to deactivated and anonymized
accounts.

`auth.msp_role_users()` (0025) lists every msp_admin and msp_engineer, and
`org_membership.provision_new_org_memberships` grants each one the new
org. It never read `deactivated_at` (0076) or `deleted_at`, so creating an
org gave a deactivated MSP admin, and an anonymized one, a membership in
it, with no audit row naming them. That is the grant
`grant_membership` refuses since 2026-11-23, made by another path
(docs/roadmap.md, "A deactivated account could still be granted
access").

An account awaiting enrolment (is_active false, deactivated_at null) is
still listed: it is an account being set up, not one being offboarded,
and an MSP engineer invited yesterday should see the org made today.

Same signature and return type, so CREATE OR REPLACE keeps the grants.

Revision ID: 0079_msp_role_users_active
Revises: 0078_directory_account_status
Create Date: 2026-11-23
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0079_msp_role_users_active"
down_revision: str | None = "0078_directory_account_status"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _replace(where: str) -> None:
    op.execute(f"""
        CREATE OR REPLACE FUNCTION auth.msp_role_users()
        RETURNS TABLE (id UUID, role VARCHAR)
        SECURITY DEFINER
        SET search_path = public, pg_catalog
        LANGUAGE sql STABLE AS $$
            -- Deliberately unscoped by app.current_org: the entire point
            -- is to see every msp_admin/msp_engineer across every org in
            -- the deployment, which no per-request RLS value can express.
            SELECT id, role FROM public."user" WHERE {where};
        $$;
    """)


def upgrade() -> None:
    _replace(
        "role IN ('msp_admin', 'msp_engineer') "
        "AND deactivated_at IS NULL AND deleted_at IS NULL"
    )


def downgrade() -> None:
    _replace("role IN ('msp_admin', 'msp_engineer')")
