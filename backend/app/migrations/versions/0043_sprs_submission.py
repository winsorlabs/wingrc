"""sprs_submission / sprs_reminder_log -- the record of what was actually
filed with SPRS (never to be confused with sprs_snapshot, which records
what WinGRC computed), and the idempotency log the annual-reminder
scheduler job checks before sending.

Org-scoped, RLS-enabled, matching sprs_snapshot's exact policy shape
(0028) -- both tables get read directly by routers (sprs_submissions.py),
so they belong in the RLS group.

submitted_by_contact_id is ON DELETE SET NULL, not CASCADE -- matching
User.contact_id's precedent (a convenience link to a person's *current*
record), not RaciAssignment.contact_id's (a live "who's responsible now"
fact that should vanish with the person). See models.py:SprsSubmission's
own docstring for the full reasoning: a submitter's name/email are
denormalized into the row so the record of who filed survives the
contact being hard-deleted (contact has no soft-delete in this codebase).

Revision ID: 0043_sprs_submission
Revises: 0042_expire_invites_secdef
Create Date: 2026-09-12
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0043_sprs_submission"
down_revision: str | None = "0042_expire_invites_secdef"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sprs_submission",
        sa.Column(
            "id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column("org_id", UUID(as_uuid=True), sa.ForeignKey("organization.id"), nullable=False),
        sa.Column(
            "assessment_id", UUID(as_uuid=True), sa.ForeignKey("assessment.id"), nullable=True
        ),
        sa.Column("score", sa.SmallInteger(), nullable=False),
        sa.Column("submitted_date", sa.Date(), nullable=False),
        sa.Column(
            "submitted_by_contact_id",
            UUID(as_uuid=True),
            sa.ForeignKey("contact.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("submitted_by_name", sa.String(200), nullable=False),
        sa.Column("submitted_by_email", sa.String(320), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(100), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("voided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("voided_reason", sa.Text(), nullable=True),
    )
    op.create_index("ix_sprs_submission_org_id", "sprs_submission", ["org_id"])
    op.create_index("ix_sprs_submission_assessment_id", "sprs_submission", ["assessment_id"])
    # The "current submission for this org" lookup (routers/
    # sprs_submissions.py, the annual-reminder SECURITY DEFINER function
    # below) is always "most recent non-voided row for org_id" -- index
    # for that access pattern directly rather than a plain org_id scan.
    op.create_index(
        "ix_sprs_submission_org_current",
        "sprs_submission",
        ["org_id", sa.text("submitted_date DESC"), sa.text("created_at DESC")],
        postgresql_where=sa.text("voided_at IS NULL"),
    )
    op.execute("ALTER TABLE sprs_submission ENABLE ROW LEVEL SECURITY")
    op.execute(
        """CREATE POLICY sprs_submission_tenant_isolation ON sprs_submission
           USING (org_id = NULLIF(current_setting('app.current_org', true), '')::uuid)"""
    )

    op.create_table(
        "sprs_reminder_log",
        sa.Column(
            "id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column("org_id", UUID(as_uuid=True), sa.ForeignKey("organization.id"), nullable=False),
        sa.Column(
            "submission_id",
            UUID(as_uuid=True),
            sa.ForeignKey("sprs_submission.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "sent_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_sprs_reminder_log_org_id", "sprs_reminder_log", ["org_id"])
    op.execute("ALTER TABLE sprs_reminder_log ENABLE ROW LEVEL SECURITY")
    op.execute(
        """CREATE POLICY sprs_reminder_log_tenant_isolation ON sprs_reminder_log
           USING (org_id = NULLIF(current_setting('app.current_org', true), '')::uuid)"""
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS sprs_reminder_log_tenant_isolation ON sprs_reminder_log")
    op.drop_table("sprs_reminder_log")
    op.execute("DROP POLICY IF EXISTS sprs_submission_tenant_isolation ON sprs_submission")
    op.drop_table("sprs_submission")
