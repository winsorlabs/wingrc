"""Network Diagram / Data Flow Diagram evidence slots on SystemDescription.

docs/pdf_ssp_template_spec.md's Addendum ("Network Diagram & Data Flow
Diagram", not Addendum 2). Two dedicated, pinned attachment slots on
SystemDescription -- not generic anonymous evidence -- so they always
surface in the SSP regardless of objective tagging, matching how the org
logo has a dedicated slot today. Unlike the logo (a bare
`logo_storage_key` column with no Evidence row), the spec explicitly wants
these to go through the existing Evidence pipeline (MinIO storage,
SHA-256 hashing, optional many-to-many linking to objectives) -- so each
slot is a nullable FK to evidence.id, not a bare storage key.

FK-on-parent pointing at evidence.id already has precedent in this schema:
EvidenceTask.completed_evidence_id (migration 0002) is the exact same
shape -- "which Evidence row currently satisfies this slot." Replacing a
diagram follows that same convention: a new Evidence row is created and
the FK is repointed at it; the prior Evidence row (and its file in
storage) is left alone -- "retain prior version, don't overwrite/lose
history" per the spec, same as how re-collecting an EvidenceTask never
deletes the Evidence row a prior collection created.

'network_diagram' and 'data_flow_diagram' are added to evidence's
artifact_type CHECK constraint rather than reusing an existing value
(e.g. 'document') -- these are genuinely distinct, spec-named categories
a C3PAO would want labeled distinctly if a diagram is ever also linked to
an objective (CM.L2-3.4.1, SC.L2-3.13.x per the spec), and the two new
upload endpoints set this server-side (never client-supplied), so no
existing Pydantic artifact_type validator needs to learn about them.

No RLS changes: system_description has never carried its own RLS policy
(access is gated by require_org_access() at the router level, same as
organization itself -- confirmed live, relrowsecurity=false on both).
Only nullable columns are added to it, so no backfill is needed either.

Revision ID: 0029_diagram_evidence_slots
Revises: 0028_sprs_snapshot
Create Date: 2026-09-05
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0029_diagram_evidence_slots"
down_revision: str | None = "0028_sprs_snapshot"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "system_description",
        sa.Column(
            "network_diagram_evidence_id",
            UUID(as_uuid=True),
            sa.ForeignKey("evidence.id"),
            nullable=True,
        ),
    )
    op.add_column(
        "system_description",
        sa.Column(
            "data_flow_diagram_evidence_id",
            UUID(as_uuid=True),
            sa.ForeignKey("evidence.id"),
            nullable=True,
        ),
    )

    op.drop_constraint("ck_evidence_artifact_type", "evidence", type_="check")
    op.create_check_constraint(
        "ck_evidence_artifact_type",
        "evidence",
        "artifact_type IN ('screenshot', 'export', 'document', 'link', 'policy', "
        "'network_diagram', 'data_flow_diagram')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_evidence_artifact_type", "evidence", type_="check")
    op.create_check_constraint(
        "ck_evidence_artifact_type",
        "evidence",
        "artifact_type IN ('screenshot', 'export', 'document', 'link', 'policy')",
    )
    op.drop_column("system_description", "data_flow_diagram_evidence_id")
    op.drop_column("system_description", "network_diagram_evidence_id")
