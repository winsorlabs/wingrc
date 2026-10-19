"""An archived evidence link says why; a deactivation says what replaced it.

The objective's evidence ✕ deleted the link and, on the artifact's last
link, the Evidence row and its stored file, unaudited. Now it archives the
link, like every other path that stops a link counting, and nothing deletes
one (evidence_links.py). An archived link is shown on its objective, greyed,
with its reason, so the reason is stored where it is decided:

- `evidence_state_link.archive_cause`: deactivation, detached (a person),
  moved_forward (a list or document link moved to a newer version) or
  attestation_detach (`review-cycles-detach-unattested`). Set exactly when
  the link is archived, and only a deactivation's names a product, because
  that is what its reactivation restores.
- `detached_by` / `detached_at`: who detached it and when.
- `org_product.deactivated_by` / `replaced_by_product_id`: who deactivated
  a product and, if they named one, what replaces it. Never inferred.

**Backfilled from the rows.** An archived link naming a product is a
deactivation's. One whose evidence is a review cycle's record was
detached by the CLI (2026-10-19; its audit row gives the actor). One whose
evidence is a list or document version was moved forward: their unlink
deleted the link until now, so no other archive of them exists. Any other
archived link refuses the migration rather than be given a guessed cause.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0071_evidence_archive_cause"
down_revision: str | None = "0070_evidence_review_cycle"
branch_labels = None
depends_on = None


# In order; each takes only rows the one before left without a cause.
BACKFILL = (
    "UPDATE evidence_state_link SET archive_cause = 'deactivation' "
    "WHERE is_archived AND archived_by_product IS NOT NULL",
    """
    UPDATE evidence_state_link l
    SET archive_cause = 'attestation_detach', detached_at = l.archived_at,
        detached_by = (
            SELECT a.actor FROM audit_log a
            WHERE a.action = 'evidence.attestation_detach'
              AND a.entity_id = l.evidence_id
              AND a.before_value->>'control_state_id' = l.control_state_id::text
            ORDER BY a.created_at DESC, a.id LIMIT 1
        )
    FROM evidence e
    WHERE e.id = l.evidence_id AND l.is_archived AND l.archive_cause IS NULL
      AND e.source_review_cycle_id IS NOT NULL
    """,
    """
    UPDATE evidence_state_link l SET archive_cause = 'moved_forward'
    FROM evidence e
    WHERE e.id = l.evidence_id AND l.is_archived AND l.archive_cause IS NULL
      AND (e.source_list_version_id IS NOT NULL OR e.source_document_version_id IS NOT NULL)
    """,
)
UNKNOWN = (
    "SELECT id FROM evidence_state_link WHERE is_archived AND archive_cause IS NULL ORDER BY id"
)


def upgrade() -> None:
    op.add_column("evidence_state_link", sa.Column("archive_cause", sa.String(24), nullable=True))
    op.add_column("evidence_state_link", sa.Column("detached_by", sa.String(255), nullable=True))
    op.add_column(
        "evidence_state_link",
        sa.Column("detached_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("org_product", sa.Column("deactivated_by", sa.String(255), nullable=True))
    op.add_column(
        "org_product",
        sa.Column(
            "replaced_by_product_id", UUID(as_uuid=True),
            sa.ForeignKey("product.id"), nullable=True,
        ),
    )

    bind = op.get_bind()
    for statement in BACKFILL:
        bind.execute(sa.text(statement))
    unknown = bind.execute(sa.text(UNKNOWN)).scalars().all()
    if unknown:
        raise RuntimeError(f"archived evidence links with no derivable cause: {unknown}")

    op.create_check_constraint(
        "ck_evidence_state_link_archive_cause", "evidence_state_link",
        "archive_cause IN ('deactivation', 'detached', 'moved_forward', 'attestation_detach')",
    )
    op.create_check_constraint(
        "ck_evidence_state_link_archived_has_cause", "evidence_state_link",
        "is_archived = (archive_cause IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_evidence_state_link_product_is_deactivation", "evidence_state_link",
        "archived_by_product IS NULL OR archive_cause = 'deactivation'",
    )


def downgrade() -> None:
    for name in (
        "ck_evidence_state_link_product_is_deactivation",
        "ck_evidence_state_link_archived_has_cause",
        "ck_evidence_state_link_archive_cause",
    ):
        op.drop_constraint(name, "evidence_state_link", type_="check")
    op.drop_column("org_product", "replaced_by_product_id")
    op.drop_column("org_product", "deactivated_by")
    op.drop_column("evidence_state_link", "detached_at")
    op.drop_column("evidence_state_link", "detached_by")
    op.drop_column("evidence_state_link", "archive_cause")
