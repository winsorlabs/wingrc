"""Add coverage_basis to baseline_control.

Revision ID: 0006_coverage_basis
Revises: 0005_objective_guidance
Create Date: 2026-07-08

Changes:
  baseline_control:
    - Add column coverage_basis VARCHAR(20) NOT NULL DEFAULT 'customer_system'
    - Add CHECK constraint: customer_system | platform_only | assists

  Existing rows default to 'customer_system' (safe: they activate normally).
  Re-run 'wingrc seed-baselines' after migrating to apply per-product YAML values.
"""
import sqlalchemy as sa
from alembic import op

revision = "0006_coverage_basis"
down_revision = "0005_objective_guidance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "baseline_control",
        sa.Column(
            "coverage_basis",
            sa.String(20),
            nullable=False,
            server_default="customer_system",
        ),
    )
    op.create_check_constraint(
        "ck_baseline_control_coverage_basis",
        "baseline_control",
        "coverage_basis IN ('customer_system', 'platform_only', 'assists')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_baseline_control_coverage_basis", "baseline_control", type_="check"
    )
    op.drop_column("baseline_control", "coverage_basis")
