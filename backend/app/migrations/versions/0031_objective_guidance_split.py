"""Split assessment_objective.guidance into official_guidance (government-
sourced) + practitioner_notes (AI-drafted), never blended.

The old `guidance` column held ad hoc, sparsely-populated free text --
unsourced, not attributed to or verified against the real CMMC Assessment
Guide, populated for only 111/316 objectives. That's the exact
"plausible-sounding compliance text with a government attribution on it"
anti-pattern CLAUDE.md warns against. Dropped outright, not migrated
forward: its content was never properly sourced, so there is nothing in it
worth carrying into official_guidance, and carrying it into
practitioner_notes would misrepresent AI-drafted content's provenance
(practitioner_notes_generated_at/model describe real generation metadata,
which old `guidance` rows have none of).

official_guidance / official_guidance_source: populated by
seeds/catalog.py from backend/app/seeds/cmmc_official_guidance.yaml
(mechanically generated from the real Assessment Guide PDF -- see
scripts/cmmc_guidance/README.md). No draft flag -- verbatim source text
doesn't need review sign-off the way an AI draft does.

practitioner_notes / practitioner_notes_is_draft / _generated_at / _model:
AI-drafted, advisory, mirrors AssessmentObjective.is_draft's existing
draft-until-reviewed pattern. Defaults is_draft=true so every freshly
seeded row reads as unreviewed until a qualified human signs off.

Revision ID: 0031_objective_guidance_split
Revises: 0030_integration_connection
Create Date: 2026-09-09
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0031_objective_guidance_split"
down_revision: str | None = "0030_integration_connection"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column("assessment_objective", "guidance")

    op.add_column("assessment_objective", sa.Column("official_guidance", sa.Text(), nullable=True))
    op.add_column(
        "assessment_objective",
        sa.Column("official_guidance_source", sa.String(200), nullable=True),
    )
    op.add_column("assessment_objective", sa.Column("practitioner_notes", sa.Text(), nullable=True))
    op.add_column(
        "assessment_objective",
        sa.Column(
            "practitioner_notes_is_draft",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )
    op.add_column(
        "assessment_objective",
        sa.Column("practitioner_notes_generated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "assessment_objective", sa.Column("practitioner_notes_model", sa.String(60), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("assessment_objective", "practitioner_notes_model")
    op.drop_column("assessment_objective", "practitioner_notes_generated_at")
    op.drop_column("assessment_objective", "practitioner_notes_is_draft")
    op.drop_column("assessment_objective", "practitioner_notes")
    op.drop_column("assessment_objective", "official_guidance_source")
    op.drop_column("assessment_objective", "official_guidance")

    op.add_column("assessment_objective", sa.Column("guidance", sa.Text(), nullable=True))
