"""initial scope graph

Revision ID: 0001_initial
Revises:
Create Date: 2026-06-26

Creates the organization and scope_entity tables. scope_entity is the single
source of truth from which every CMMC list is projected. Row-Level Security is
enabled so per-tenant isolation is enforced in the database, not just the app.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "organization",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("name", sa.String(200), nullable=False, unique=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    op.create_table(
        "scope_entity",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("entity_type", sa.String(40), nullable=False),
        sa.Column("natural_key", sa.String(400), nullable=False),
        sa.Column("scope_category", sa.String(60), nullable=True),
        sa.Column(
            "status", sa.String(30), nullable=False, server_default="active"
        ),
        sa.Column(
            "in_boundary", sa.Boolean, nullable=False, server_default=sa.text("true")
        ),
        sa.Column("source", sa.String(40), nullable=False, server_default="manual"),
        sa.Column("source_ref", sa.String(400), nullable=True),
        sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "attributes",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_scope_entity_org_id", "scope_entity", ["org_id"])
    op.create_index("ix_scope_entity_entity_type", "scope_entity", ["entity_type"])
    op.create_index("ix_scope_entity_natural_key", "scope_entity", ["natural_key"])
    op.create_index(
        "ix_scope_entity_scope_category", "scope_entity", ["scope_category"]
    )
    op.create_index("ix_scope_entity_status", "scope_entity", ["status"])
    op.create_unique_constraint(
        "uq_scope_entity_identity",
        "scope_entity",
        ["org_id", "entity_type", "natural_key"],
    )

    # Defense-in-depth tenant isolation. The application sets
    # `SET app.current_org = '<uuid>'` per request/session; the policy then
    # restricts every row to the active tenant.
    op.execute("ALTER TABLE scope_entity ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY scope_entity_tenant_isolation ON scope_entity
        USING (org_id = NULLIF(current_setting('app.current_org', true), '')::uuid)
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS scope_entity_tenant_isolation ON scope_entity")
    op.drop_table("scope_entity")
    op.drop_table("organization")
