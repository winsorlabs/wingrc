"""list_version.reference_columns: columns a list's editors cannot change.

The ODP list (docs/roadmap.md, "The ODP list") carries three columns that
are not the organization's to write: the DoD memo's identifier and value,
verbatim, and an authored gloss. Only the fourth is filled in. Until now
every cell of every list was editable by anyone who could edit rows, so
"reference only" could be a style and nothing more.

A version names its reference columns. The edit route refuses a version
that changes any cell in them, or adds, removes or reorders rows, or
changes the columns. Only the command that writes the template from its
source, and a copy taking a newer template version, write those cells.

Part of a version's content like its columns: a new version records the
list as it then was, and an existing version is never updated (migration
0064's trigger). Adding a column with a default writes no row, so the
trigger does not fire; every existing version reads as having none.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0073_list_reference_columns"
down_revision: str | None = "0072_drop_candidate_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "list_version",
        sa.Column(
            "reference_columns", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
    )


def downgrade() -> None:
    op.drop_column("list_version", "reference_columns")
