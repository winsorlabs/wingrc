"""Add permanent AI-generation provenance columns to product.

Supports the document-ingestion-to-baseline-library slice: a baseline
produced by the AI pipeline from uploaded vendor documents must carry a
permanent record of when and by which model it was generated, mirroring
AssessmentObjective.practitioner_notes_generated_at/_model's own
"provenance never gets cleared by a later edit" philosophy. NULL means
hand-authored -- true for every existing row.

Purely additive, both columns nullable, no backfill needed.

Revision ID: 0048_product_ai_provenance
Revises: 0047_review_cycle_notify
Create Date: 2026-09-13
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0048_product_ai_provenance"
down_revision: str | None = "0047_review_cycle_notify"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "product",
        sa.Column("ai_generated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "product",
        sa.Column("ai_generated_model", sa.String(80), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("product", "ai_generated_model")
    op.drop_column("product", "ai_generated_at")
