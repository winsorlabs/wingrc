"""Baseline versioning (roadmap item P).

Problem this closes: seed_baselines/_seed_product upserted product and
baseline_control rows IN PLACE by (product_id, control_id). Editing
baselines/rocketcyber.yaml and re-seeding (CLI or the G.9 admin import
screen) retroactively changed the compliance claims backing every
tenant's already-activated control_state, with no record anything
changed -- the same class of problem this codebase already refuses
elsewhere (sprs_snapshot is append-only, audit_log is append-only, bundle
exports are point-in-time). See docs/roadmap.md item P for the full
writeup and the three options considered.

Option 1 chosen (immutable baseline versions), not option 2 (implicit
pinning by timestamp/hash) or option 3 (warn-only, already shipped as
G.9's affected-org-count and insufficient on its own): every import from
here on creates a NEW product_baseline_version with entirely fresh
baseline_control/baseline_evidence_spec rows (new UUIDs) rather than
mutating the previous version's rows. A version, once created, is never
edited or deleted -- CLAUDE.md's "candidates, never auto-met" discipline
extended to "imports, never silent rewrites." Chosen specifically because
ControlStateContributor.baseline_control_id (migration 0052) and
bundle_service.py already resolve baseline text through that FK, never
through bare product_id -- so point-in-time bundle correctness falls out
for free once baseline_control rows stop being mutated in place; no
bundle_service change needed.

org_product.baseline_version_id is the pin: which version's claims this
tenant's activation rests on. Set at activation time (engine.py), not at
candidate creation -- nothing is claimed yet at the candidate stage, so
there is nothing to pin. Moving an already-active tenant to a newer
version is a deliberate, separate action (engine.py:move_org_product_version,
this same session's follow-up) that lands affected controls in
needs_review, the same precedent deactivate_org_product already
established for "a coverage claim just changed, a human must re-confirm."

product.current_version_id is the "latest, potentially publishable"
pointer new activations resolve against. Product.is_published is kept
exactly as it already was (unchanged column, unchanged semantics) rather
than moved onto product_baseline_version -- publish is a per-product
decision in this codebase (there is no workflow for "version 1 stays
published while version 2 is independently published later"; a reimport
already always targets the current version and already forces
is_published back to False -- G.9's existing behavior, unchanged here).
Historical versions are never re-published; a tenant already pinned to
one keeps using it regardless of the product's current publish state
(engine.py's activate_org_product: the publish gate only applies to a
FIRST activation, never to reactivating an already-pinned OrgProduct).

Backfill (non-negotiable per the task: existing assessments must not
change): every existing product gets exactly one product_baseline_version
(version_number=1); every existing baseline_control row for that product
is stamped with that version's id; product.current_version_id is set to
it; every existing org_product row for that product (any status) is
pinned to it. Counts are verified before/after -- see BACKFILL_SQL block
below and the raise-on-mismatch pattern from migration 0052
(control_state_contributor), same reasoning: a partial, silently-
incomplete backfill on the assessment layer is worse than a loud failure.

product_baseline_version has no RLS -- deployment-wide like product and
baseline_control themselves (seeds/baselines.py's own module docstring:
"the baseline library is deployment-wide, never org-scoped").

Revision ID: 0054_baseline_versioning
Revises: 0053_product_doc_web_research
Create Date: 2026-09-17
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0054_baseline_versioning"
down_revision: str | None = "0053_product_doc_web_research"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_UUID = sa.dialects.postgresql.UUID(as_uuid=True)

# One version-1 row per existing product, in product creation order so
# version numbering is at least deterministic even though there's only
# ever one version per product at this point in history.
_BACKFILL_VERSIONS_SQL = """
    INSERT INTO product_baseline_version (id, product_id, version_number, created_at)
    SELECT gen_random_uuid(), p.id, 1, p.created_at
    FROM product p
"""

_BACKFILL_BASELINE_CONTROL_SQL = """
    UPDATE baseline_control bc
    SET baseline_version_id = pbv.id
    FROM product_baseline_version pbv
    WHERE pbv.product_id = bc.product_id AND pbv.version_number = 1
"""

_BACKFILL_PRODUCT_CURRENT_VERSION_SQL = """
    UPDATE product p
    SET current_version_id = pbv.id
    FROM product_baseline_version pbv
    WHERE pbv.product_id = p.id AND pbv.version_number = 1
"""

_BACKFILL_ORG_PRODUCT_SQL = """
    UPDATE org_product op
    SET baseline_version_id = pbv.id
    FROM product_baseline_version pbv
    WHERE pbv.product_id = op.product_id AND pbv.version_number = 1
"""

_COUNT_PRODUCTS_SQL = "SELECT count(*) FROM product"
_COUNT_VERSIONS_SQL = "SELECT count(*) FROM product_baseline_version"
_COUNT_BC_TOTAL_SQL = "SELECT count(*) FROM baseline_control"
_COUNT_BC_VERSIONED_SQL = (
    "SELECT count(*) FROM baseline_control WHERE baseline_version_id IS NOT NULL"
)
_COUNT_OP_TOTAL_SQL = "SELECT count(*) FROM org_product"
_COUNT_OP_VERSIONED_SQL = (
    "SELECT count(*) FROM org_product WHERE baseline_version_id IS NOT NULL"
)


def upgrade() -> None:
    op.create_table(
        "product_baseline_version",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column(
            "product_id", _UUID, sa.ForeignKey("product.id"), nullable=False, index=True
        ),
        sa.Column("version_number", sa.Integer, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")
        ),
        sa.UniqueConstraint(
            "product_id", "version_number", name="uq_product_baseline_version_identity"
        ),
    )

    op.add_column(
        "product",
        sa.Column(
            "current_version_id",
            _UUID,
            sa.ForeignKey("product_baseline_version.id"),
            nullable=True,
        ),
    )
    op.add_column(
        "baseline_control",
        sa.Column(
            "baseline_version_id",
            _UUID,
            sa.ForeignKey("product_baseline_version.id"),
            nullable=True,
        ),
    )
    op.add_column(
        "org_product",
        sa.Column(
            "baseline_version_id",
            _UUID,
            sa.ForeignKey("product_baseline_version.id"),
            nullable=True,
        ),
    )

    conn = op.get_bind()
    expected_products = conn.execute(sa.text(_COUNT_PRODUCTS_SQL)).scalar_one()
    expected_bc = conn.execute(sa.text(_COUNT_BC_TOTAL_SQL)).scalar_one()
    expected_op = conn.execute(sa.text(_COUNT_OP_TOTAL_SQL)).scalar_one()

    op.execute(_BACKFILL_VERSIONS_SQL)
    op.execute(_BACKFILL_BASELINE_CONTROL_SQL)
    op.execute(_BACKFILL_PRODUCT_CURRENT_VERSION_SQL)
    op.execute(_BACKFILL_ORG_PRODUCT_SQL)

    actual_versions = conn.execute(sa.text(_COUNT_VERSIONS_SQL)).scalar_one()
    actual_bc = conn.execute(sa.text(_COUNT_BC_VERSIONED_SQL)).scalar_one()
    actual_op = conn.execute(sa.text(_COUNT_OP_VERSIONED_SQL)).scalar_one()
    if actual_versions != expected_products:
        raise RuntimeError(
            f"baseline versioning backfill mismatch: {expected_products} product row(s) "
            f"but {actual_versions} product_baseline_version row(s) created -- expected "
            "exactly one version-1 row per product."
        )
    if actual_bc != expected_bc:
        raise RuntimeError(
            f"baseline versioning backfill mismatch: {expected_bc} baseline_control "
            f"row(s) total, but only {actual_bc} got a baseline_version_id -- at least "
            "one baseline_control row's product has no matching version-1 row."
        )
    if actual_op != expected_op:
        raise RuntimeError(
            f"baseline versioning backfill mismatch: {expected_op} org_product row(s) "
            f"total, but only {actual_op} got a baseline_version_id."
        )

    op.alter_column("baseline_control", "baseline_version_id", nullable=False)

    op.drop_constraint("uq_baseline_control_identity", "baseline_control", type_="unique")
    op.create_unique_constraint(
        "uq_baseline_control_version_identity",
        "baseline_control",
        ["baseline_version_id", "control_id"],
    )

    op.create_index(
        "ix_baseline_control_baseline_version_id",
        "baseline_control",
        ["baseline_version_id"],
    )
    op.create_index(
        "ix_org_product_baseline_version_id", "org_product", ["baseline_version_id"]
    )
    op.create_index(
        "ix_product_current_version_id", "product", ["current_version_id"]
    )


def downgrade() -> None:
    # Lossy if more than one version now exists per product (real usage
    # after this migration ships) -- collapses back to the highest
    # version_number's baseline_control rows per (product_id, control_id),
    # deleting the rest, same "keep the newest, drop the history" tradeoff
    # 0052's downgrade makes for control_state_contributor -> sourced_from_
    # product_id. Safe and lossless immediately after this migration, before
    # any second version has been created anywhere.
    op.execute("""
        DELETE FROM baseline_control bc
        USING product_baseline_version pbv
        WHERE bc.baseline_version_id = pbv.id
          AND pbv.version_number <> (
              SELECT max(pbv2.version_number)
              FROM product_baseline_version pbv2
              WHERE pbv2.product_id = pbv.product_id
          )
    """)

    op.drop_index("ix_product_current_version_id", table_name="product")
    op.drop_index("ix_org_product_baseline_version_id", table_name="org_product")
    op.drop_index("ix_baseline_control_baseline_version_id", table_name="baseline_control")

    op.drop_constraint(
        "uq_baseline_control_version_identity", "baseline_control", type_="unique"
    )
    op.create_unique_constraint(
        "uq_baseline_control_identity", "baseline_control", ["product_id", "control_id"]
    )

    op.drop_column("org_product", "baseline_version_id")
    op.drop_column("baseline_control", "baseline_version_id")
    op.drop_column("product", "current_version_id")

    op.drop_table("product_baseline_version")
