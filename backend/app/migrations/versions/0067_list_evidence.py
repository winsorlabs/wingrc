"""A populated list version becomes evidence -- roadmap L.4.

The link names a `list_version`, never the list: a list moves on, a
version does not (0064's trigger refuses an UPDATE), and an export of one
is the same bytes every time (render.render_list_version_bytes). So the
evidence row is a reference to that version, rendered on demand -- the
shape N.1 chose for documents (`source_document_version_id`), and RESTRICT
for the same reason.

- `evidence.source_list_version_id`, nullable, RESTRICT.
- `list` as an artifact type. Not `export`, which already means a file
  someone uploaded from a tool.
- `ck_evidence_list_reference`: a list artifact is exactly an evidence row
  naming a list version, and it is a reference -- there are no bytes in
  storage to fetch, delete or hash. Not a third `kind`: sixteen places
  branch on `kind` today, and each would have had to learn a new value.

The artifact-type list starts from 0045's, which added `attestation`.
models.py had not followed it; it does now.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0067_list_evidence"
down_revision: str | None = "0066_list_adoption"
branch_labels = None
depends_on = None

_TYPES = (
    "'screenshot', 'export', 'document', 'link', 'policy', "
    "'network_diagram', 'data_flow_diagram', 'attestation'"
)


def upgrade() -> None:
    op.add_column(
        "evidence",
        sa.Column(
            "source_list_version_id", UUID(as_uuid=True),
            sa.ForeignKey("list_version.id", ondelete="RESTRICT"), nullable=True,
        ),
    )
    op.create_index("ix_evidence_source_list_version_id", "evidence", ["source_list_version_id"])
    op.drop_constraint("ck_evidence_artifact_type", "evidence", type_="check")
    op.create_check_constraint(
        "ck_evidence_artifact_type", "evidence", f"artifact_type IN ({_TYPES}, 'list')"
    )
    op.create_check_constraint(
        "ck_evidence_list_reference",
        "evidence",
        "(artifact_type = 'list') = (source_list_version_id IS NOT NULL) "
        "AND (source_list_version_id IS NULL OR kind = 'reference')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_evidence_list_reference", "evidence", type_="check")
    op.drop_constraint("ck_evidence_artifact_type", "evidence", type_="check")
    op.create_check_constraint(
        "ck_evidence_artifact_type", "evidence", f"artifact_type IN ({_TYPES})"
    )
    op.drop_index("ix_evidence_source_list_version_id", table_name="evidence")
    op.drop_column("evidence", "source_list_version_id")
