"""Add satisfaction_type / cadence tags to assessment_objective; expires_at to evidence_task.

Revision ID: 0003_catalog_objective_tags
Revises: 0002_assessment_engine
Create Date: 2026-06-30

satisfaction_type classifies HOW an objective is satisfied:
  product            — enforced by a configured security tool
  document_list      — satisfied by a scope-graph-generated list
  scheduled_operation— a recurring human activity (has cadence + cadence_responsibility)
  narrative          — policy/procedure documentation

For scheduled_operation objectives, cadence (annual/quarterly/monthly) and
cadence_responsibility (msp/customer/shared) drive when evidence_task rows are
due and when they expire.

expires_at on evidence_task records the staleness deadline for evidence attached
to a completed task, reusing the existing due_date + completed_evidence_id flow.

is_draft marks every seed row as a REVIEWABLE DRAFT pending C3PAO sign-off.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_catalog_objective_tags"
down_revision: str | None = "0002_assessment_engine"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── assessment_objective: satisfaction-type tagging ───────────────────────
    op.add_column(
        "assessment_objective",
        sa.Column(
            "satisfaction_type",
            sa.String(25),
            nullable=False,
            server_default="narrative",
        ),
    )
    op.add_column(
        "assessment_objective",
        sa.Column("cadence", sa.String(20), nullable=True),
    )
    op.add_column(
        "assessment_objective",
        sa.Column("cadence_responsibility", sa.String(20), nullable=True),
    )
    op.add_column(
        "assessment_objective",
        sa.Column(
            "is_draft",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )
    op.create_check_constraint(
        "ck_assessment_objective_sat_type",
        "assessment_objective",
        "satisfaction_type IN ('product','document_list','scheduled_operation','narrative')",
    )
    op.create_check_constraint(
        "ck_assessment_objective_cadence",
        "assessment_objective",
        "cadence IN ('annual','quarterly','monthly') OR cadence IS NULL",
    )
    op.create_check_constraint(
        "ck_assessment_objective_cadence_resp",
        "assessment_objective",
        "cadence_responsibility IN ('msp','customer','shared') OR cadence_responsibility IS NULL",
    )

    # ── evidence_task: staleness deadline ────────────────────────────────────
    op.add_column(
        "evidence_task",
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("evidence_task", "expires_at")
    op.drop_constraint(
        "ck_assessment_objective_cadence_resp", "assessment_objective", type_="check"
    )
    op.drop_constraint(
        "ck_assessment_objective_cadence", "assessment_objective", type_="check"
    )
    op.drop_constraint(
        "ck_assessment_objective_sat_type", "assessment_objective", type_="check"
    )
    op.drop_column("assessment_objective", "is_draft")
    op.drop_column("assessment_objective", "cadence_responsibility")
    op.drop_column("assessment_objective", "cadence")
    op.drop_column("assessment_objective", "satisfaction_type")
