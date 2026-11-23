"""The user directory reads each account's status, not only its access.

auth.all_users_directory() (migration 0040) returned memberships and
deleted_at, and nothing about whether the account could sign in. So
Administration > User Directory, the screen where someone answers "who
has access to this organization", showed a deactivated user holding MSP
Admin on four organizations exactly as it showed an active one
(Jarrod's offboarding walk, 2026-11-20).

This adds is_active, deactivated_at and login_method, the three fields
the per-org Users tab's status reads (frontend lib/userStatus.ts). A
change of RETURNS TABLE needs DROP and CREATE, so EXECUTE is granted
again.

Revision ID: 0078_directory_account_status
Revises: 0077_rls_unset_org_raises
Create Date: 2026-11-20
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0078_directory_account_status"
down_revision: str | None = "0077_rls_unset_org_raises"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_ROLE = "wingrc_app"

_MEMBERSHIPS = """
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
"""


def _create(extra_out: str, extra_cols: str) -> str:
    return f"""
        CREATE FUNCTION auth.all_users_directory()
        RETURNS TABLE (
            id UUID,
            email VARCHAR,
            display_name VARCHAR,
            home_org_id UUID,
            deleted_at TIMESTAMPTZ,{extra_out}
            memberships JSONB
        )
        SECURITY DEFINER
        SET search_path = public, pg_catalog
        LANGUAGE sql STABLE AS $$
            -- Deliberately unscoped by app.current_org: every user and
            -- every org_membership row across the deployment (0040).
            SELECT u.id, u.email, u.display_name, u.home_org_id, u.deleted_at,{extra_cols}
            {_MEMBERSHIPS}
            GROUP BY u.id;
        $$;
    """


def _replace(sql: str) -> None:
    op.execute("DROP FUNCTION auth.all_users_directory()")
    op.execute(sql)
    op.execute("REVOKE EXECUTE ON FUNCTION auth.all_users_directory() FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION auth.all_users_directory() TO {_APP_ROLE}")


def upgrade() -> None:
    _replace(_create(
        "\n            is_active BOOLEAN,\n            deactivated_at TIMESTAMPTZ,"
        "\n            login_method VARCHAR,",
        " u.is_active, u.deactivated_at, u.login_method,",
    ))


def downgrade() -> None:
    _replace(_create("", ""))
