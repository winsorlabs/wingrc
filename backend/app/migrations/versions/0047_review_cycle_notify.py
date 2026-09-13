"""Track per-reviewer notification delivery on review_cycle_reviewer, and
add an honest terminal status for both a reviewer and a cycle when
notification was never actually delivered.

Fixes a correctness bug found live on wl-util-1 (2026-09-13): a cycle
opened on a deployment with no SMTP credential and no WINGRC_PUBLIC_URL
configured still closed its reviewers as 'no_response' -- asserting two
named people failed to respond to a request that was never sent.
`email_service.send()`'s result was discarded at open time and consulted
only for reminder-log idempotency at sweep time, never persisted. See
models.py:ReviewCycleReviewer's own docstring for the full reasoning.

Purely additive against existing rows: notified_at/notification_error
are nullable, and NULL is the semantically correct value for every
existing review_cycle_reviewer row -- none of them has ever had a
delivery outcome persisted, so "unknown/not yet notified" (NULL) is
already the true state, not a placeholder needing a backfill. No data
migration is required or performed.

Revision ID: 0047_review_cycle_notify
Revises: 0046_review_cycle_secdef
Create Date: 2026-09-14
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0047_review_cycle_notify"
down_revision: str | None = "0046_review_cycle_secdef"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "review_cycle_reviewer",
        sa.Column("notified_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "review_cycle_reviewer",
        sa.Column("notification_error", sa.Text(), nullable=True),
    )

    op.drop_constraint("ck_review_cycle_reviewer_status", "review_cycle_reviewer", type_="check")
    op.create_check_constraint(
        "ck_review_cycle_reviewer_status",
        "review_cycle_reviewer",
        "status IN ('requested', 'viewed', 'attested', 'no_response', 'not_notified')",
    )

    op.drop_constraint("ck_review_cycle_status", "review_cycle", type_="check")
    op.create_check_constraint(
        "ck_review_cycle_status",
        "review_cycle",
        "status IN ('open', 'completed', 'closed_unattested', 'closed_undeliverable')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_review_cycle_status", "review_cycle", type_="check")
    op.create_check_constraint(
        "ck_review_cycle_status",
        "review_cycle",
        "status IN ('open', 'completed', 'closed_unattested')",
    )

    op.drop_constraint("ck_review_cycle_reviewer_status", "review_cycle_reviewer", type_="check")
    op.create_check_constraint(
        "ck_review_cycle_reviewer_status",
        "review_cycle_reviewer",
        "status IN ('requested', 'viewed', 'attested', 'no_response')",
    )

    op.drop_column("review_cycle_reviewer", "notification_error")
    op.drop_column("review_cycle_reviewer", "notified_at")
