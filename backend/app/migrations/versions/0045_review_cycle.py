"""Periodic review & attestation (D.3's first half): review_cycle and its
four child tables, Organization.review_cadence_months, and a new
'attestation' evidence artifact_type.

All five review_cycle* tables are org-scoped and RLS-enabled, matching
sprs_submission's exact policy shape (0043) -- routers/review_cycles.py
reads all of them directly.

'attestation' is a genuinely new evidence kind, not squeezed into
'document': an attestation record is system-generated from a structured
sign-off event (who/when/what-was-shown), not a human-uploaded file --
see models.py's own module docstring for review_cycle and
routers/review_cycles.py for how a closed cycle becomes one.

Revision ID: 0045_review_cycle
Revises: 0044_sprs_reminder_secdef
Create Date: 2026-09-13
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0045_review_cycle"
down_revision: str | None = "0044_sprs_reminder_secdef"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(
        f"""CREATE POLICY {table}_tenant_isolation ON {table}
           USING (org_id = NULLIF(current_setting('app.current_org', true), '')::uuid)"""
    )


def upgrade() -> None:
    op.add_column(
        "organization",
        sa.Column(
            "review_cadence_months",
            sa.SmallInteger(),
            nullable=False,
            server_default=sa.text("6"),
        ),
    )

    op.drop_constraint("ck_evidence_artifact_type", "evidence", type_="check")
    op.create_check_constraint(
        "ck_evidence_artifact_type",
        "evidence",
        "artifact_type IN ('screenshot', 'export', 'document', 'link', 'policy', "
        "'network_diagram', 'data_flow_diagram', 'attestation')",
    )

    op.create_table(
        "review_cycle",
        sa.Column(
            "id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column("org_id", UUID(as_uuid=True), sa.ForeignKey("organization.id"), nullable=False),
        sa.Column(
            "opened_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default=sa.text("'open'")),
        sa.Column("cadence_months", sa.SmallInteger(), nullable=False),
        sa.Column("opened_by", sa.String(100), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "status IN ('open', 'completed', 'closed_unattested')",
            name="ck_review_cycle_status",
        ),
    )
    op.create_index("ix_review_cycle_org_id", "review_cycle", ["org_id"])
    _rls("review_cycle")

    op.create_table(
        "review_cycle_item",
        sa.Column(
            "id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column(
            "cycle_id",
            UUID(as_uuid=True),
            sa.ForeignKey("review_cycle.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("org_id", UUID(as_uuid=True), sa.ForeignKey("organization.id"), nullable=False),
        sa.Column("subject_type", sa.String(10), nullable=False),
        sa.Column(
            "scope_entity_id",
            UUID(as_uuid=True),
            sa.ForeignKey("scope_entity.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("natural_key", sa.String(400), nullable=False),
        sa.Column("scope_category", sa.String(60), nullable=True),
        sa.Column("attributes", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "subject_type IN ('user', 'device')", name="ck_review_cycle_item_subject"
        ),
    )
    op.create_index("ix_review_cycle_item_cycle_id", "review_cycle_item", ["cycle_id"])
    op.create_index("ix_review_cycle_item_org_id", "review_cycle_item", ["org_id"])
    _rls("review_cycle_item")

    op.create_table(
        "review_cycle_reviewer",
        sa.Column(
            "id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column(
            "cycle_id",
            UUID(as_uuid=True),
            sa.ForeignKey("review_cycle.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("org_id", UUID(as_uuid=True), sa.ForeignKey("organization.id"), nullable=False),
        sa.Column(
            "user_id", UUID(as_uuid=True), sa.ForeignKey("user.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("reviewer_name", sa.String(200), nullable=False),
        sa.Column("reviewer_email", sa.String(320), nullable=False),
        sa.Column("reviewer_side", sa.String(10), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default=sa.text("'requested'")),
        sa.Column(
            "requested_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("viewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "reviewer_side IN ('msp', 'client')", name="ck_review_cycle_reviewer_side"
        ),
        sa.CheckConstraint(
            "status IN ('requested', 'viewed', 'attested', 'no_response')",
            name="ck_review_cycle_reviewer_status",
        ),
    )
    op.create_index("ix_review_cycle_reviewer_cycle_id", "review_cycle_reviewer", ["cycle_id"])
    op.create_index("ix_review_cycle_reviewer_org_id", "review_cycle_reviewer", ["org_id"])
    _rls("review_cycle_reviewer")

    op.create_table(
        "review_cycle_reminder_log",
        sa.Column(
            "id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column(
            "cycle_id",
            UUID(as_uuid=True),
            sa.ForeignKey("review_cycle.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "reviewer_id",
            UUID(as_uuid=True),
            sa.ForeignKey("review_cycle_reviewer.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("reminder_number", sa.SmallInteger(), nullable=False),
        sa.Column(
            "sent_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("reviewer_id", "reminder_number", name="uq_review_cycle_reminder"),
    )
    op.create_index(
        "ix_review_cycle_reminder_log_cycle_id", "review_cycle_reminder_log", ["cycle_id"]
    )
    # No RLS on this one -- deployment-internal scheduler bookkeeping with
    # no org_id column of its own, same tier as sprs_reminder_log/job_run;
    # nothing reads it through an org-scoped request.

    op.create_table(
        "review_cycle_flag",
        sa.Column(
            "id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column(
            "cycle_item_id",
            UUID(as_uuid=True),
            sa.ForeignKey("review_cycle_item.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("org_id", UUID(as_uuid=True), sa.ForeignKey("organization.id"), nullable=False),
        sa.Column(
            "flagged_by_reviewer_id",
            UUID(as_uuid=True),
            sa.ForeignKey("review_cycle_reviewer.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("flagged_by_name", sa.String(200), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_note", sa.Text(), nullable=True),
    )
    op.create_index("ix_review_cycle_flag_cycle_item_id", "review_cycle_flag", ["cycle_item_id"])
    op.create_index("ix_review_cycle_flag_org_id", "review_cycle_flag", ["org_id"])
    _rls("review_cycle_flag")


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS review_cycle_flag_tenant_isolation ON review_cycle_flag")
    op.drop_table("review_cycle_flag")
    op.drop_table("review_cycle_reminder_log")
    op.execute(
        "DROP POLICY IF EXISTS review_cycle_reviewer_tenant_isolation ON review_cycle_reviewer"
    )
    op.drop_table("review_cycle_reviewer")
    op.execute("DROP POLICY IF EXISTS review_cycle_item_tenant_isolation ON review_cycle_item")
    op.drop_table("review_cycle_item")
    op.execute("DROP POLICY IF EXISTS review_cycle_tenant_isolation ON review_cycle")
    op.drop_table("review_cycle")

    op.drop_constraint("ck_evidence_artifact_type", "evidence", type_="check")
    op.create_check_constraint(
        "ck_evidence_artifact_type",
        "evidence",
        "artifact_type IN ('screenshot', 'export', 'document', 'link', 'policy', "
        "'network_diagram', 'data_flow_diagram')",
    )

    op.drop_column("organization", "review_cadence_months")
