"""Drop baseline_control.candidate_state: nothing read it.

The Tool Import wizard asked for it on every row. Activation filters on
classification and coverage_basis only; the bundle, the SSP, the CLI and
every screen but the wizard's own column never read it. Where code set it,
it was a function of classification, so the wizard alone could make a row
contradict itself, and changing it alone wrote a new baseline version
(docs/roadmap.md, "The slice's first act: delete candidate_state").

Dropping a column on rows that are otherwise immutable (item P) is safe
only because nothing reads it. The downgrade puts it back derived from
classification, which is what every writer but the wizard set.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0072_drop_candidate_state"
down_revision: str | None = "0071_evidence_archive_cause"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_baseline_control_candidate_state", "baseline_control", type_="check")
    op.drop_column("baseline_control", "candidate_state")


def downgrade() -> None:
    op.add_column("baseline_control", sa.Column("candidate_state", sa.String(40), nullable=True))
    op.execute(
        "UPDATE baseline_control SET candidate_state = CASE "
        "WHEN classification = 'customer_owns' THEN 'not_satisfied_by_product' "
        "ELSE 'pending_evidence' END"
    )
    op.alter_column("baseline_control", "candidate_state", nullable=False)
    op.create_check_constraint(
        "ck_baseline_control_candidate_state",
        "baseline_control",
        "candidate_state IN ('pending_evidence', 'not_satisfied_by_product')",
    )
