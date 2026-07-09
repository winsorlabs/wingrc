"""Add kind and reference_location to evidence.

Revision ID: 0007_evidence_kind_reference
Revises: 0006_coverage_basis
Create Date: 2026-07-09

Changes:
  evidence:
    - Add column kind VARCHAR(10) NOT NULL DEFAULT 'file'
    - Add CHECK constraint: file | reference
    - Add column reference_location TEXT NULL (URL or path for reference-kind rows)

Existing rows are all file-kind (storage_key populated); default is safe.
"""
import sqlalchemy as sa
from alembic import op

revision = "0007_evidence_kind_reference"
down_revision = "0006_coverage_basis"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "evidence",
        sa.Column("kind", sa.String(10), nullable=False, server_default="file"),
    )
    op.add_column(
        "evidence",
        sa.Column("reference_location", sa.Text(), nullable=True),
    )
    op.create_check_constraint(
        "ck_evidence_kind",
        "evidence",
        "kind IN ('file', 'reference')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_evidence_kind", "evidence", type_="check")
    op.drop_column("evidence", "reference_location")
    op.drop_column("evidence", "kind")
