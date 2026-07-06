"""Add contact and raci_assignment tables.

Revision ID: 0004_contacts_raci
Revises: 0003_catalog_objective_tags
Create Date: 2026-07-06

contact: tenant-scoped people who appear in RACI matrices and CRM documents.
  Unique per (org_id, email) — same person at different orgs is a different row.

raci_assignment: per-objective RACI assignment linking a contact to a
  control_state row.  Storage is deliberately per-objective (not per-control)
  because the magic loop sets responsibility at the objective level — e.g.
  AC.L2-3.1.1[a]/[b] may be customer-owned while [c] is MSP-owned.

  Bulk-assign UX (future RACI slice) writes multiple rows at once but never
  bypasses this per-objective table; it is a write-path convenience only.
  UNIQUE(control_state_id, contact_id, raci_letter) allows one person to hold
  both R and A on the same objective (valid in RACI theory) while preventing
  exact duplicates.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0004_contacts_raci"
down_revision: str | None = "0003_catalog_objective_tags"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "contact",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("org_id", UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("phone", sa.String(50), nullable=True),
        sa.Column("affiliation", sa.String(20), nullable=False),
        sa.Column("role_title", sa.String(200), nullable=True),
        sa.Column("contract_ref", sa.String(200), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["org_id"], ["organization.id"], ondelete="CASCADE"),
        sa.CheckConstraint(
            "affiliation IN ('msp','customer','mssp','government','other')",
            name="ck_contact_affiliation",
        ),
        sa.UniqueConstraint("org_id", "email", name="uq_contact_org_email"),
    )
    op.create_index("ix_contact_org_id", "contact", ["org_id"])

    op.create_table(
        "raci_assignment",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("control_state_id", UUID(as_uuid=True), nullable=False),
        sa.Column("contact_id", UUID(as_uuid=True), nullable=False),
        sa.Column("raci_letter", sa.String(1), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["control_state_id"], ["control_state.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["contact_id"], ["contact.id"], ondelete="CASCADE"
        ),
        sa.CheckConstraint(
            "raci_letter IN ('A','R','C','I')",
            name="ck_raci_letter",
        ),
        sa.UniqueConstraint(
            "control_state_id", "contact_id", "raci_letter",
            name="uq_raci_assignment",
        ),
    )
    op.create_index("ix_raci_assignment_control_state_id", "raci_assignment", ["control_state_id"])
    op.create_index("ix_raci_assignment_contact_id", "raci_assignment", ["contact_id"])


def downgrade() -> None:
    op.drop_table("raci_assignment")
    op.drop_table("contact")
