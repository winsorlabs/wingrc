"""Multiple products can satisfy the same control (multi-tool coverage):
control_state_contributor, backfilled from every existing
control_state.sourced_from_product_id, which is then dropped.

Real motivating case (docs/roadmap.md's multi-tool-coverage writeup): an
MSP's Kaseya suite (Datto RMM, IT Glue, RocketCyber, SaaS Alerts) overlaps
by design on access control, logging, and system integrity -- more than
one tool legitimately covers the same objective. Before this migration,
control_state.sourced_from_product_id could only ever name one product;
activating a second overlapping product silently overwrote it (confirmed
live on a bench stack before this was designed -- see that writeup for the
exact before/after).

sourced_from_product_id is retired, not kept as a denormalized "primary"
pointer, alongside the new join table: every real consumer (the UI badge,
the CRM/bundle export, deactivation's provenance check) was rewritten to
want either "all current contributors" or "is product X a contributor,"
and neither needs a single "primary" -- keeping the column would have
meant inventing an arbitrary tiebreak rule with no actual use, exactly the
"populated but meaningless" trap worth avoiding.

BACKFILL_SQL is a module-level constant, not inlined, for the same reason
0024_msp_membership_backfill.py's BACKFILL_PASS_2_SQL is: by the time
integration tests run, migrations have already applied to an empty
database, so there is no realistic way to exercise this against
pre-existing data through a real `alembic upgrade` run. Kept loadable via
alembic.script.ScriptDirectory (test_migrations.py's own mechanism) so
tests/test_control_state_contributor_backfill.py can seed representative
rows and run this exact SQL directly, matching 0024's precedent.

Matches every existing control_state.sourced_from_product_id row to its
justifying baseline_control by (product_id, control_id via the objective,
objectives @> objective_key) -- the same join
routers/assessments.py:list_control_states already does to resolve a
sourced product's key for display, run here as a JSONB containment check
instead. Verified directly against wl-util-1's real data before writing
this (28 sourced control_state rows, all Acme MSP / RocketCyber + Datto
RMM, every one matched a baseline_control cleanly -- zero orphans). Because
zero orphans exist in the one real deployment this has ever run against,
an orphan here (a sourced_from_product_id whose baseline_control no longer
matches -- e.g. edited out from under it since) is treated as a genuine
data inconsistency worth surfacing loudly rather than silently dropping:
the INSERT's row count is compared against the source count and the
migration raises if they don't match, rolling back the whole transaction
(Alembic runs each migration in one transaction against Postgres) rather
than leaving a partial, silently-incomplete backfill in place.

Revision ID: 0052_control_state_contributor
Revises: 0051_null_task_spec_ref_secdef
Create Date: 2026-09-16
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0052_control_state_contributor"
down_revision: str | None = "0051_null_task_spec_ref_secdef"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


BACKFILL_SQL = """
    INSERT INTO control_state_contributor
        (id, control_state_id, product_id, baseline_control_id, created_at)
    SELECT
        gen_random_uuid(),
        cs.id,
        cs.sourced_from_product_id,
        bc.id,
        now()
    FROM control_state cs
    JOIN assessment_objective ao ON ao.id = cs.objective_id
    JOIN baseline_control bc
        ON bc.product_id = cs.sourced_from_product_id
       AND bc.control_id = ao.control_id
       AND bc.objectives @> to_jsonb(ao.objective_key)
    WHERE cs.sourced_from_product_id IS NOT NULL
"""

_COUNT_SOURCED_SQL = (
    "SELECT count(*) FROM control_state WHERE sourced_from_product_id IS NOT NULL"
)
_COUNT_CONTRIBUTORS_SQL = "SELECT count(*) FROM control_state_contributor"


def upgrade() -> None:
    op.create_table(
        "control_state_contributor",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "control_state_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("control_state.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "product_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("product.id"),
            nullable=False,
        ),
        sa.Column(
            "baseline_control_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("baseline_control.id"),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")
        ),
        sa.UniqueConstraint(
            "control_state_id", "product_id", name="uq_control_state_contributor_identity"
        ),
    )
    op.create_index(
        "ix_control_state_contributor_control_state_id",
        "control_state_contributor",
        ["control_state_id"],
    )
    op.create_index(
        "ix_control_state_contributor_product_id",
        "control_state_contributor",
        ["product_id"],
    )
    op.create_index(
        "ix_control_state_contributor_baseline_control_id",
        "control_state_contributor",
        ["baseline_control_id"],
    )

    conn = op.get_bind()
    expected = conn.execute(sa.text(_COUNT_SOURCED_SQL)).scalar_one()
    op.execute(BACKFILL_SQL)
    actual = conn.execute(sa.text(_COUNT_CONTRIBUTORS_SQL)).scalar_one()
    if actual != expected:
        raise RuntimeError(
            f"control_state_contributor backfill mismatch: {expected} control_state "
            f"row(s) had sourced_from_product_id set, but only {actual} matched a "
            "baseline_control and were backfilled. This means at least one "
            "sourced_from_product_id no longer has a justifying baseline_control row "
            "-- investigate before dropping the column (see this migration's own "
            "docstring for why this raises instead of silently dropping the orphan)."
        )

    op.drop_column("control_state", "sourced_from_product_id")


def downgrade() -> None:
    op.add_column(
        "control_state",
        sa.Column(
            "sourced_from_product_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("product.id"),
            nullable=True,
        ),
    )
    # Lossy by nature: a control_state can now have multiple contributors,
    # and sourced_from_product_id only ever held one. Picks the earliest
    # contributor (created_at) as a reasonable single value rather than
    # leaving every downgraded row NULL -- matches this migration's own
    # forward direction most closely for the common case (a state with
    # exactly one contributor, which is every real row on wl-util-1 today).
    op.execute("""
        UPDATE control_state cs
        SET sourced_from_product_id = earliest.product_id
        FROM (
            SELECT DISTINCT ON (control_state_id) control_state_id, product_id
            FROM control_state_contributor
            ORDER BY control_state_id, created_at ASC
        ) earliest
        WHERE earliest.control_state_id = cs.id
    """)
    op.drop_table("control_state_contributor")
