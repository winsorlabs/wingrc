"""Add provenance columns to contact; normalize existing emails.

Supports the Liongard-identities-to-contacts import feature: a contact
created by that import and one typed by hand must be distinguishable, and
a later re-sync needs to know which fields it may offer to refresh.
Mirrors scope_entity's existing source/source_ref pattern (migration 0001)
rather than inventing a parallel vocabulary -- domain.py:Source already
has LIONGARD alongside MANUAL. NULL source_ref / source="manual" is
correct for every existing row (hand-typed, no external origin).

Also fixes a pre-existing bug surfaced while designing that import:
routers/contacts.py never normalized email for comparison (case-sensitive
dupe/clash checks, case-sensitive DB constraint), so "Alice@x.com" and
"alice@x.com" could already become two separate contacts today. The
application-layer fix (routers/contacts.py, this same change set) lowercases
and strips on write going forward; this migration backfills existing rows
to match, so old data isn't left inconsistent with the new invariant.

The backfill is deliberately conservative: it only rewrites a row's email
to its normalized form when nothing else in the same org would then
collide with it. A row whose normalized form WOULD collide with another
contact in the same org is left untouched (pre-existing near-duplicate,
e.g. "Alice@x.com" vs "alice@x.com" both already present) -- merging two
contact records is a real data decision with RACI-assignment consequences,
not something a migration should do silently. `uq_contact_org_email`
itself stays case-sensitive; this migration does not add a functional
unique index on lower(email), since going forward the write path is the
enforcement point and every new/patched row is normalized before it ever
reaches the constraint.

Revision ID: 0049_contact_provenance
Revises: 0048_product_ai_provenance
Create Date: 2026-09-14
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0049_contact_provenance"
down_revision: str | None = "0048_product_ai_provenance"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "contact",
        sa.Column("source", sa.String(40), nullable=False, server_default="manual"),
    )
    op.add_column(
        "contact",
        sa.Column("source_ref", sa.String(400), nullable=True),
    )

    op.execute(
        """
        UPDATE contact c
        SET email = lower(trim(c.email))
        WHERE c.email <> lower(trim(c.email))
          AND NOT EXISTS (
              SELECT 1 FROM contact c2
              WHERE c2.org_id = c.org_id
                AND c2.id <> c.id
                AND lower(trim(c2.email)) = lower(trim(c.email))
          )
        """
    )


def downgrade() -> None:
    op.drop_column("contact", "source_ref")
    op.drop_column("contact", "source")
