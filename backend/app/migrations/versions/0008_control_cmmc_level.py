"""Add is_level_1 boolean to control.

Revision ID: 0008_control_cmmc_level
Revises: 0007_evidence_kind_reference
Create Date: 2026-07-09

Adds is_level_1 BOOLEAN NOT NULL DEFAULT FALSE to the control table.

The 17 CMMC Level 1 practices (per CMMC Model v2.0 / 32 CFR Part 170 Final
Rule, Oct 2024) are a subset of the 110 NIST SP 800-171 Rev 2 requirements.
The schema column is set here; values are populated by the catalog seed
(seed_catalog / wingrc seed-catalog), not in this migration, so the seed
remains the single authoritative source for catalog data.
"""
import sqlalchemy as sa
from alembic import op

revision = "0008_control_cmmc_level"
down_revision = "0007_evidence_kind_reference"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "control",
        sa.Column(
            "is_level_1",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("control", "is_level_1")
