"""G.9 -- product.source_docs: store the baseline YAML's `source_docs:`
free-text list, previously parsed and then discarded.

`_seed_product` (seeds/baselines.py) never stored `source_docs` anywhere
-- every product's source-document claims (e.g. "RocketCyber_SIEM_and_
SOC_Baseline.docx (Winsors Labs MSP baseline, v1.0)") existed only in the
YAML file on disk, invisible to the Tools admin screen. Needed so the
screen's documentation-attachment feature can show an admin which claimed
source document a real upload (ProductDocument.source_docs_ref, migration
0036) corresponds to. Same JSONB-list shape and default as
Product.assumed_config, added the same way in migration 0002 -- not a new
convention.

Revision ID: 0038_product_source_docs
Revises: 0037_product_footprint
Create Date: 2026-09-12
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0038_product_source_docs"
down_revision: str | None = "0037_product_footprint"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "product",
        sa.Column("source_docs", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
    )


def downgrade() -> None:
    op.drop_column("product", "source_docs")
