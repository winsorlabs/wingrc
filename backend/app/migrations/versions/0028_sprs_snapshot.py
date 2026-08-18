"""G.2: sprs_snapshot — historical SPRS score series.

assessment.sprs_score only ever holds the current value, overwritten on
every recompute (engine.py:recompute_sprs, per CLAUDE.md). No historical
series exists today. Replaying ControlStateHistory to reconstruct past
scores would mean re-running compute_sprs at every historical point in
time -- expensive and fragile, since control weights, framework
membership, and the rollup logic could all change over the replay
window. A snapshot table avoids that entirely: one row inserted at the
same point recompute_sprs already writes assessment.sprs_score.

org_id is denormalized from assessment.org_id and RLS-enabled, matching
control_state/evidence_task/finding -- every table a router queries
directly gets this treatment (control_state_history is the one
exception, and only because nothing reads it directly today). G.3's
planned dashboard endpoint will read this table directly, so it belongs
in the RLS group, not the write-only-child group.

No ondelete beyond assessment's own existing FK convention (no CASCADE
specified on assessment's other org-scoped children, per the audit done
for the wl-util-1 cleanup script -- match that, don't diverge).

`seq` (BIGINT GENERATED ALWAYS AS IDENTITY), not `computed_at`, is the
ordering key -- same fix as password_history.seq (migration 0020).
Postgres's now()/CURRENT_TIMESTAMP returns transaction-start time, not
statement-execution time, so two recomputes inside one transaction get
an identical computed_at and an undefined tie order; this table's own
test suite exercises exactly that shape (two recompute_sprs() calls
under the savepoint-per-test db_session fixture). The index this slice
was originally scoped to add on (assessment_id, computed_at) is added
on (assessment_id, seq) instead so chronological reads are correct from
the start, rather than shipping the same bug 0020 already paid down
once and fixing it in a follow-up migration.

Unbounded retention for now -- see G.2 in docs/PLAN-gui-restructure.md.

Revision ID: 0028_sprs_snapshot
Revises: 0027_my_org_memberships_secdef
Create Date: 2026-08-18
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0028_sprs_snapshot"
down_revision: str | None = "0027_my_org_memberships_secdef"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sprs_snapshot",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "assessment_id",
            UUID(as_uuid=True),
            sa.ForeignKey("assessment.id"),
            nullable=False,
        ),
        sa.Column(
            "org_id",
            UUID(as_uuid=True),
            sa.ForeignKey("organization.id"),
            nullable=False,
        ),
        sa.Column("score", sa.SmallInteger, nullable=False),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("seq", sa.BigInteger(), sa.Identity(always=True), nullable=False),
    )
    op.create_index("ix_sprs_snapshot_assessment_id", "sprs_snapshot", ["assessment_id"])
    op.create_index("ix_sprs_snapshot_org_id", "sprs_snapshot", ["org_id"])
    op.create_index(
        "ix_sprs_snapshot_assessment_id_seq",
        "sprs_snapshot",
        ["assessment_id", "seq"],
    )
    op.execute("ALTER TABLE sprs_snapshot ENABLE ROW LEVEL SECURITY")
    op.execute(
        """CREATE POLICY sprs_snapshot_tenant_isolation ON sprs_snapshot
           USING (org_id = NULLIF(current_setting('app.current_org', true), '')::uuid)"""
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS sprs_snapshot_tenant_isolation ON sprs_snapshot")
    op.drop_table("sprs_snapshot")
