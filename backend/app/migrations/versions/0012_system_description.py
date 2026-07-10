"""System description table for SSP Section 1.

One row per org (uq_system_description_org). The system is persistent;
bundle export takes a point-in-time snapshot at generation time so dated
bundles stay accurate even after the description is later edited.

Structured fields (not one blob) so individual sections render into
different SSP subsections without re-parsing.

Revision ID: 0012_system_description
Revises: 0011_org_profile
Create Date: 2026-07-10
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0012_system_description"
down_revision: str | None = "0011_org_profile"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "system_description",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("org_id", UUID(as_uuid=True), nullable=False),
        sa.Column("system_name", sa.String(400), nullable=False),
        sa.Column("system_type", sa.String(40), nullable=False),
        sa.Column("operational_status", sa.String(40), nullable=False),
        sa.Column("system_description", sa.Text(), nullable=True),
        sa.Column(
            "cui_categories",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "cui_storage_locations",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("authorization_boundary_description", sa.Text(), nullable=True),
        sa.Column(
            "external_connections",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("cui_flow_description", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["org_id"], ["organization.id"], ondelete="CASCADE"),
        sa.CheckConstraint(
            "system_type IN ('major_application','general_support_system','minor_application')",
            name="ck_system_description_type",
        ),
        sa.CheckConstraint(
            "operational_status IN ('operational','under_development',"
            "'undergoing_major_modification')",
            name="ck_system_description_op_status",
        ),
        sa.UniqueConstraint("org_id", name="uq_system_description_org"),
    )
    op.create_index("ix_system_description_org_id", "system_description", ["org_id"])


def downgrade() -> None:
    op.drop_index("ix_system_description_org_id", table_name="system_description")
    op.drop_table("system_description")
