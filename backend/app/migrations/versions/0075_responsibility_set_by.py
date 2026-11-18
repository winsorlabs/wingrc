"""Responsibility set by a person: who and when.

Until now `control_state.responsibility` had no human write path. It was
seeded `customer_owns` and moved only when a tool activated, deactivated
or changed version (docs/roadmap.md, "Responsibility had no human write
path"). It prints in the bundle's customer responsibility matrix and the
SSP, so it is an assessor-facing claim, and a person must be able to set it.

These columns mark a value a person set. A tool never overwrites one
without asking: activation asks, and the paths that do not ask keep the
person's value and record what the tool would have set
(engine._tool_responsibility).

No backfill: before this migration no person could set it, so every
existing value is a default or a tool's.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0075_responsibility_set_by"
down_revision: str | None = "0074_ssp_import_session"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("control_state", sa.Column("responsibility_set_by", sa.Text(), nullable=True))
    op.add_column("control_state", sa.Column(
        "responsibility_set_at", sa.DateTime(timezone=True), nullable=True))
    op.create_check_constraint(
        "ck_control_state_responsibility_set",
        "control_state",
        "(responsibility_set_by IS NULL) = (responsibility_set_at IS NULL)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_control_state_responsibility_set", "control_state", type_="check")
    op.drop_column("control_state", "responsibility_set_at")
    op.drop_column("control_state", "responsibility_set_by")
