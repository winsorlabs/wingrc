"""Job scheduler infrastructure -- D.3's second prerequisite (after
outbound email): job_run, the append-only run-history table scheduler.py
writes to.

Deployment-wide, like integration_connection/deployment_settings -- no
org_id, no RLS. See models.py:JobRun's own docstring for the full
compliance-record reasoning (why this follows audit_log/sprs_snapshot's
append-only discipline, and why it's a separate table from audit_log
rather than a new action type in it).

Revision ID: 0041_job_run
Revises: 0040_admin_users_secdef
Create Date: 2026-09-12
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0041_job_run"
down_revision: str | None = "0040_admin_users_secdef"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "job_run",
        sa.Column(
            "id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column("job_name", sa.String(100), nullable=False),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("result", JSONB, nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("worker_id", sa.String(200), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "status IN ('running', 'succeeded', 'failed')", name="ck_job_run_status"
        ),
    )
    # The admin panel's "last run" lookup and the scheduler's own
    # due-check/orphan-reconciliation query are both "most recent row for
    # this job_name" -- index the pair, descending on created_at so that
    # lookup is an index-only scan rather than a sort.
    op.create_index(
        "ix_job_run_job_name_created_at",
        "job_run",
        ["job_name", sa.text("created_at DESC")],
    )


def downgrade() -> None:
    op.drop_index("ix_job_run_job_name_created_at", table_name="job_run")
    op.drop_table("job_run")
