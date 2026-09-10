"""Replace practitioner_notes draft/reviewed status with edit provenance.

Removes assessment_objective.practitioner_notes_is_draft. Jarrod's call:
"reviewed" implied "now authoritative," but a human-edited practitioner
note is still one practitioner's opinion, never official CMMC guidance --
marking it reviewed would launder it into something it isn't. Nothing
about a practitioner note should ever "graduate." The AI-authorship
caveat in the UI is now unconditional and permanent, regardless of edit
state -- see models.py's AssessmentObjective docstring for the full
rationale and ControlDrawer.tsx for the UI side.

Adds three columns:
  practitioner_notes_original    -- the AI-generated text, frozen, for revert.
  practitioner_notes_edited_by   -- FK user.id, ON DELETE SET NULL, NULL = untouched.
  practitioner_notes_edited_at   -- NULL = untouched; the actual "has this
                                     been edited" signal everywhere (reseed
                                     protection, UI provenance) -- unlike
                                     edited_by, never nulled by a cascade.

No content is lost: practitioner_notes_original is backfilled from the
current practitioner_notes value for every row that has one, so revert
works immediately for every already-seeded note. The one thing that IS
lost, deliberately: any row already marked reviewed
(practitioner_notes_is_draft = false) loses that status -- there is no
replacement value for it, because the concept itself is what's being
removed. Checked before writing this: no row on any known deployment
(bench or wl-util-1) had actually been marked reviewed yet -- the feature
this undoes only shipped 2026-09-09, and no review UI was ever built for
it -- so this is not expected to discard any real signal in practice, but
the migration would do the same thing even if it had.

Revision ID: 0032_practitioner_notes_edit
Revises: 0031_objective_guidance_split
Create Date: 2026-09-10
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0032_practitioner_notes_edit"
down_revision: str | None = "0031_objective_guidance_split"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "assessment_objective", sa.Column("practitioner_notes_original", sa.Text(), nullable=True)
    )
    op.add_column(
        "assessment_objective",
        sa.Column("practitioner_notes_edited_by", UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_assessment_objective_practitioner_notes_edited_by",
        "assessment_objective",
        "user",
        ["practitioner_notes_edited_by"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column(
        "assessment_objective",
        sa.Column("practitioner_notes_edited_at", sa.DateTime(timezone=True), nullable=True),
    )

    # Backfill: original = current text, for every row that has one. No
    # note's content is lost by this migration -- only the draft/reviewed
    # status field, which has no replacement (see module docstring).
    op.execute(
        "UPDATE assessment_objective SET practitioner_notes_original = practitioner_notes "
        "WHERE practitioner_notes IS NOT NULL"
    )

    op.drop_column("assessment_objective", "practitioner_notes_is_draft")


def downgrade() -> None:
    op.add_column(
        "assessment_objective",
        sa.Column(
            "practitioner_notes_is_draft",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )
    op.drop_column("assessment_objective", "practitioner_notes_edited_at")
    op.drop_constraint(
        "fk_assessment_objective_practitioner_notes_edited_by",
        "assessment_objective",
        type_="foreignkey",
    )
    op.drop_column("assessment_objective", "practitioner_notes_edited_by")
    op.drop_column("assessment_objective", "practitioner_notes_original")
