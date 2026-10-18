"""A published document version records the objectives it was approved
for -- roadmap item X.

Until X, publish linked the new version's Evidence to every tagged
objective, and "what did this version cover" was read back from those
links. Publish no longer links anything (a person attaches a document, as
L.4 does for lists), so that record has to be kept where it is made: the
document's tag set, copied onto the version at publish.

**Backfilled from the record that existed.** A version published before
this has no tag set saved, but publish linked it to every tagged objective
then, so those links (archived ones too: a republish archived them) are
that version's record. The objectives they name are copied in. This
invents nothing. It moves the existing record into the column, and after
this no link is ever read as coverage again. That matters, because a
link a person makes from now on is evidence, not coverage. A version
published while no assessment was in_progress got no links and so gets
`[]`, which is what its record says. Draft and under_review versions stay
NULL: they have not been published.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0069_document_published_tags"
down_revision: str | None = "0068_list_link_event"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "document_version", sa.Column("published_objective_ids", JSONB, nullable=True)
    )
    op.execute("""
        UPDATE document_version dv
        SET published_objective_ids = COALESCE((
            SELECT jsonb_agg(DISTINCT cs.objective_id::text ORDER BY cs.objective_id::text)
            FROM evidence e
            JOIN evidence_state_link l ON l.evidence_id = e.id
            JOIN control_state cs ON cs.id = l.control_state_id
            WHERE e.source_document_version_id = dv.id
        ), '[]'::jsonb)
        WHERE dv.status IN ('approved', 'superseded')
    """)


def downgrade() -> None:
    op.drop_column("document_version", "published_objective_ids")
