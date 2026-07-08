"""Re-key implementation_statement to per-objective; add guidance to assessment_objective.

Revision ID: 0005_statement_per_objective_and_guidance
Revises: 0004_contacts_raci
Create Date: 2026-07-07

Changes:
  implementation_statement:
    - Drop unique constraint uq_implementation_statement_identity (assessment_id + control_id)
    - Drop index ix_implementation_statement_control_id
    - Drop column control_id
    - Add column objective_id UUID NOT NULL FK -> assessment_objective(id) ON DELETE CASCADE
    - Create index ix_implementation_statement_objective_id
    - Create unique constraint uq_implementation_statement_identity (assessment_id, objective_id)

  assessment_objective:
    - Add column guidance TEXT NULL

Data note: existing implementation_statement rows are deleted before the schema change.
This is correct for development; no production data exists yet.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0005_statement_per_objective_and_guidance"
down_revision: str | None = "0004_contacts_raci"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── assessment_objective: add guidance column ──────────────────────────
    op.add_column(
        "assessment_objective",
        sa.Column("guidance", sa.Text, nullable=True),
    )

    # ── implementation_statement: re-key from control to objective ─────────
    # Clear existing rows before the schema change (no prod data to preserve).
    op.execute("DELETE FROM implementation_statement")

    op.drop_constraint(
        "uq_implementation_statement_identity",
        "implementation_statement",
        type_="unique",
    )
    op.drop_index(
        "ix_implementation_statement_control_id",
        table_name="implementation_statement",
    )
    op.drop_column("implementation_statement", "control_id")

    op.add_column(
        "implementation_statement",
        sa.Column(
            "objective_id",
            UUID(as_uuid=True),
            sa.ForeignKey("assessment_objective.id", ondelete="CASCADE"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_implementation_statement_objective_id",
        "implementation_statement",
        ["objective_id"],
    )
    op.create_unique_constraint(
        "uq_implementation_statement_identity",
        "implementation_statement",
        ["assessment_id", "objective_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_implementation_statement_identity",
        "implementation_statement",
        type_="unique",
    )
    op.drop_index(
        "ix_implementation_statement_objective_id",
        table_name="implementation_statement",
    )
    op.drop_column("implementation_statement", "objective_id")

    op.add_column(
        "implementation_statement",
        sa.Column(
            "control_id",
            UUID(as_uuid=True),
            sa.ForeignKey("control.id"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_implementation_statement_control_id",
        "implementation_statement",
        ["control_id"],
    )
    op.create_unique_constraint(
        "uq_implementation_statement_identity",
        "implementation_statement",
        ["assessment_id", "control_id"],
    )

    op.drop_column("assessment_objective", "guidance")
