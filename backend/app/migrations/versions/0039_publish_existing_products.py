"""G.9 -- backfill: mark every product seeded before this migration
published.

Product.is_published (migration 0002) has been set False by the seeder
since the column existed, and was read nowhere in the codebase until this
slice's enforcement lands (routers/assessments.py's tenant Tools-panel
query, engine.py:activate_org_product). Every product in the database
today therefore has is_published = False.

Landing the enforcement filter without this backfill would silently empty
the Tools panel -- and block re-activation -- for every tenant that
already has a product candidate or active, RocketCyber included on any
deployment that ran D-item work before this. This migration exists so
that never happens: everything seeded before "publish" was a real,
enforced concept is treated as already reviewed and exposed, matching
the state tenants already depend on. Anything imported through the new
Tools admin screen *after* this migration starts unpublished, per that
screen's own design -- this is a one-time historical backfill, not a
statement that future imports should default to published.

Revision ID: 0039_publish_existing_products
Revises: 0038_product_source_docs
Create Date: 2026-09-12
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0039_publish_existing_products"
down_revision: str | None = "0038_product_source_docs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("UPDATE product SET is_published = true WHERE is_published = false")


def downgrade() -> None:
    # Deliberately a no-op, not a revert-to-False: by the time anyone runs
    # this downgrade, real admin decisions (via the Tools screen's own
    # publish/unpublish, migration 0036 onward) may have been made on top
    # of this backfill, and blindly flipping every product back to
    # unpublished would erase those, not just this migration's own change.
    pass
