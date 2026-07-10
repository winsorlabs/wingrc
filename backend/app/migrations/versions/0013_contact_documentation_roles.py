"""Add documentation roles to contacts.

Extends contact with a notes field (free-form GRC context).
Adds contact_documentation_role: a many-to-many of contacts to named
documentation roles (security_officer, it_admin, etc.). One person can hold
multiple roles (President + senior official, IT + CUI User).

These are DOCUMENTATION roles — who appears on which SSP signature page / CRM
row. NOT platform-access roles. The association to future authenticated user
accounts will be a nullable contact_id FK on the user table; contact never
references user.

Revision ID: 0013_contact_documentation_roles
Revises: 0012_system_description
Create Date: 2026-07-10
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0013_contact_documentation_roles"
down_revision: str | None = "0012_system_description"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ROLE_CHECK = (
    "role IN ('it_admin','security_officer','system_owner','authorizing_official',"
    "'president','cui_user','assessor','mssp','consultant','other')"
)


def upgrade() -> None:
    op.add_column("contact", sa.Column("notes", sa.Text(), nullable=True))

    op.create_table(
        "contact_documentation_role",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("contact_id", UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.String(40), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["contact_id"], ["contact.id"], ondelete="CASCADE"),
        sa.CheckConstraint(_ROLE_CHECK, name="ck_contact_doc_role"),
        sa.UniqueConstraint(
            "contact_id", "role", name="uq_contact_documentation_role"
        ),
    )
    op.create_index(
        "ix_contact_documentation_role_contact_id",
        "contact_documentation_role",
        ["contact_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_contact_documentation_role_contact_id",
        table_name="contact_documentation_role",
    )
    op.drop_table("contact_documentation_role")
    op.drop_column("contact", "notes")
