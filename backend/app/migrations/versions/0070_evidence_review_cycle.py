"""A review cycle's record names its cycle -- review cycles attach only
what attests.

A cycle closed at its deadline, or one that reached nobody, still writes
its record: who was asked, who did not answer. It is no longer attached
to AC.L2-3.1.1[a]/[c], because it says nobody reviewed them. Unattached,
nothing pointed at it: evidence is listed per objective, and a cycle had
no link to its record. `evidence.source_review_cycle_id` is that link, so
the cycle's page shows its record and the generic unlink never deletes
it.

**Backfilled from what close_cycle wrote.** It stamps the cycle's
`closed_at` and the record's `collected_at` with one value, so a record
is the attestation in the cycle's org collected at the moment the cycle
closed. A record two cycles would claim refuses the migration rather
than guess.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0070_evidence_review_cycle"
down_revision: str | None = "0069_document_published_tags"
branch_labels = None
depends_on = None

_MATCH = """
    FROM review_cycle c
    WHERE e.org_id = c.org_id
      AND e.artifact_type = 'attestation'
      AND c.closed_at IS NOT NULL
      AND e.collected_at = c.closed_at
"""


def upgrade() -> None:
    op.add_column(
        "evidence",
        sa.Column(
            "source_review_cycle_id", UUID(as_uuid=True),
            sa.ForeignKey("review_cycle.id", ondelete="RESTRICT"), nullable=True,
        ),
    )
    op.create_index("ix_evidence_source_review_cycle_id", "evidence", ["source_review_cycle_id"])
    bind = op.get_bind()
    ambiguous = bind.execute(sa.text(
        "SELECT e.id FROM evidence e WHERE (SELECT count(*) " + _MATCH + ") > 1"
    )).scalars().all()
    if ambiguous:
        raise RuntimeError(f"attestation evidence matches more than one review cycle: {ambiguous}")
    bind.execute(sa.text("UPDATE evidence e SET source_review_cycle_id = c.id " + _MATCH))


def downgrade() -> None:
    op.drop_index("ix_evidence_source_review_cycle_id", table_name="evidence")
    op.drop_column("evidence", "source_review_cycle_id")
