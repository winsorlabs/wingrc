"""D.2 — org_liongard_environment: WinGRC org <-> Liongard Environment mapping.

Liongard's own tenancy model (confirmed against Liongard's docs/Postman
collection before this table was designed -- see ROADMAP.md's D.1/D.2
sections): one Access Key ID/Secret pair per MSP instance
(`integration_connection`, deployment-wide, migration 0030), with many
per-client "Environments" underneath it. D.1 explicitly left "mapping a
WinGRC org to a Liongard Environment id" as D.2's concern, "a separate,
org-scoped table, not a column" on integration_connection. This is that
table.

1:1 today (unique org_id) -- an org syncing from more than one Liongard
Environment isn't a case the connector needs to support yet; extend to
many-to-one if that changes rather than guessing now.

liongard_environment_id is an Integer, not a UUID/string -- Liongard's own
Environment ids are small integers (confirmed from real example payloads
in Liongard's Postman collection, e.g. `"EnvironmentID": 8815`), unlike
every other identifier in this schema.

RLS enabled matching sprs_snapshot (migration 0028) and every other table
a router queries directly by org_id -- this one is read/written directly
by routers/scope.py's Liongard endpoints.

Revision ID: 0035_org_liongard_environment
Revises: 0034_consultant_admin_role
Create Date: 2026-09-11
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0035_org_liongard_environment"
down_revision: str | None = "0034_consultant_admin_role"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "org_liongard_environment",
        sa.Column(
            "id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column(
            "org_id",
            UUID(as_uuid=True),
            sa.ForeignKey("organization.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("liongard_environment_id", sa.Integer(), nullable=False),
        # Display cache only -- refreshed whenever the mapping is (re)set
        # from connectors/liongard.py:list_environments(). Never the source
        # of truth for identity; that's liongard_environment_id + Liongard
        # itself.
        sa.Column("liongard_environment_name", sa.String(200), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_org_liongard_environment_org_id", "org_liongard_environment", ["org_id"]
    )
    op.execute("ALTER TABLE org_liongard_environment ENABLE ROW LEVEL SECURITY")
    op.execute(
        """CREATE POLICY org_liongard_environment_tenant_isolation ON org_liongard_environment
           USING (org_id = NULLIF(current_setting('app.current_org', true), '')::uuid)"""
    )


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS org_liongard_environment_tenant_isolation "
        "ON org_liongard_environment"
    )
    op.drop_table("org_liongard_environment")
