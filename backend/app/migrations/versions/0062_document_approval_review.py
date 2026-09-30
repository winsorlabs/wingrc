"""Document approval records and review-cadence notifications -- roadmap N.3.

Two tables, both append-only in practice:

`document_approval` is the authoritative record of every approval decision
about a `DocumentVersion` -- the initial one publishing grants, and every
later "reviewed and still current" reaffirmation. See the model's docstring
in `models.py` for the full design argument; the short version is that
re-approving an unchanged policy must not create a new `DocumentVersion`,
because a byte-identical version fakes an edit and pollutes the diff history
N.2 built, while re-approval is still a real event a periodic-review control
wants to see.

`document_review_notification` tracks digest delivery, with the same
`notified_at`/`notification_error` shape `ReviewCycleReviewer` and
`LiongardSyncNotification` already use -- a failed or never-attempted send
leaving no trace is a bug that was found live once and is not being
reintroduced.

**The backfill matters and is not optional.** Cadence is derived from
`document_approval`, so without backfilling, every document approved before
this migration would read as *never approved* -- which, given "overdue" is
computed from the newest approval, would make already-compliant documents
show as having no review at all rather than as due. One `initial` row is
created per already-approved `document_version`, including `superseded`
ones: they were genuinely approved once, that is history worth keeping, and
the newest row wins for cadence so older ones cannot distort it.

`approver_name` is denormalized on the table, so the backfill resolves it
from `contact` and falls back to an explicit marker where the contact is
gone or was never set -- a NOT NULL column with no honest value gets a
stated placeholder rather than an empty string, so a reader can tell "we
don't know" from "nobody".

**`WITH CHECK` is spelled out on both policies even though it matches
`USING`.** Migration 0059 created the document tables with `USING` only,
relying on `FOR ALL`'s implicit reuse of it; 0061 recorded why that is worth
being explicit about (`audit_log`'s nullable `org_id` was a latent
production break precisely because the implicit check was invisible). Both
new tables carry `org_id` NOT NULL, so the rule is genuinely symmetric --
readable iff in-org, writable iff in-org -- and saying so makes it a
decision rather than an omission.

Revision ID: 0062_document_approval_review
Revises: 0061_rls_transitive_policies
Create Date: 2026-09-30
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0062_document_approval_review"
down_revision: str | None = "0061_rls_transitive_policies"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _rls(table: str) -> None:
    predicate = "org_id = NULLIF(current_setting('app.current_org', true), '')::uuid"
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {table}_tenant_isolation ON {table} "
        f"USING ({predicate}) WITH CHECK ({predicate})"
    )


def upgrade() -> None:
    op.create_table(
        "document_approval",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "document_id",
            UUID(as_uuid=True),
            sa.ForeignKey("document.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "document_version_id",
            UUID(as_uuid=True),
            sa.ForeignKey("document_version.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "org_id",
            UUID(as_uuid=True),
            sa.ForeignKey("organization.id"),
            nullable=False,
        ),
        sa.Column("approval_type", sa.String(20), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "approved_by_contact_id",
            UUID(as_uuid=True),
            sa.ForeignKey("contact.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("approver_name", sa.String(200), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "approval_type IN ('initial', 'reaffirmation')",
            name="ck_document_approval_type",
        ),
    )
    op.create_index(
        "ix_document_approval_document_id", "document_approval", ["document_id"]
    )
    op.create_index(
        "ix_document_approval_document_version_id",
        "document_approval",
        ["document_version_id"],
    )
    op.create_index("ix_document_approval_org_id", "document_approval", ["org_id"])
    op.create_index(
        "ix_document_approval_approved_by_contact_id",
        "document_approval",
        ["approved_by_contact_id"],
    )
    # Exactly one 'initial' approval per version; reaffirmations unconstrained.
    op.create_index(
        "uq_document_approval_initial",
        "document_approval",
        ["document_version_id"],
        unique=True,
        postgresql_where=sa.text("approval_type = 'initial'"),
    )
    op.create_index(
        "ix_document_approval_document_id_approved_at",
        "document_approval",
        ["document_id", "approved_at"],
    )

    op.create_table(
        "document_review_notification",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "org_id",
            UUID(as_uuid=True),
            sa.ForeignKey("organization.id"),
            nullable=False,
        ),
        sa.Column(
            "contact_id",
            UUID(as_uuid=True),
            sa.ForeignKey("contact.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("recipient_name", sa.String(200), nullable=False),
        sa.Column("recipient_email", sa.String(320), nullable=False),
        sa.Column("document_count", sa.Integer(), nullable=False),
        sa.Column("notified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notification_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_document_review_notification_org_id",
        "document_review_notification",
        ["org_id"],
    )
    op.create_index(
        "ix_document_review_notification_contact_id",
        "document_review_notification",
        ["contact_id"],
    )

    # Pre-existing defect, fixed here because it is the same subject: N.1
    # declared document_version.approved_by_contact_id with no ON DELETE
    # clause, making it the only nullable contact FK in this schema without
    # one (ReviewCycleReviewer.user_id,
    # SprsSubmission.submitted_by_contact_id,
    # LiongardSyncNotification.contact_id and AssetApproval all use SET
    # NULL). The effect was that a contact who had ever approved a document
    # could not be deleted: the DELETE raised a ForeignKeyViolation, which
    # breaks ADR 0006's anonymize/hard-delete path outright.
    #
    # Found by a test in this slice deleting an approver, not by reading the
    # schema. Nulling the column loses nothing now that document_approval is
    # the authoritative record and denormalizes approver_name.
    op.drop_constraint(
        "document_version_approved_by_contact_id_fkey",
        "document_version",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "document_version_approved_by_contact_id_fkey",
        "document_version",
        "contact",
        ["approved_by_contact_id"],
        ["id"],
        ondelete="SET NULL",
    )

    _rls("document_approval")
    _rls("document_review_notification")

    # Cross-org discovery for the scheduled digest
    # (scheduler.py:_document_review_digest). `document` is RLS-protected
    # (0059), so finding every org that has any document -- across every
    # org, in one query -- needs a SECURITY DEFINER function, exactly the
    # mechanism auth.orgs_with_liongard_mapping() (0056) and
    # auth.orgs_due_for_review_cycle_open() (0046) already use. Not a
    # blanket RLS bypass: it returns org ids and nothing else, and the job
    # still calls set_current_org() per org before reading that org's rows,
    # so every document read is policy-checked normally.
    #
    # Deliberately does NOT compute which documents are due. "Overdue" is
    # derived from document_approval plus Document.cadence_months in
    # Python (document_reviews.py:review_state), and duplicating that
    # arithmetic in SQL would create a second implementation that could
    # disagree with the one the API reports.
    op.execute(
        """
        CREATE FUNCTION auth.orgs_with_documents()
        RETURNS TABLE (org_id UUID)
        SECURITY DEFINER
        SET search_path = public, pg_catalog
        LANGUAGE sql STABLE AS $$
            SELECT DISTINCT d.org_id FROM public.document d;
        $$;
        """
    )
    op.execute("REVOKE EXECUTE ON FUNCTION auth.orgs_with_documents() FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION auth.orgs_with_documents() TO wingrc_app")

    # Backfill: one 'initial' approval per already-approved version. Runs as
    # the migration role (the owner), which bypasses RLS, so this sees every
    # org's rows -- correct here and the only way a cross-org backfill can
    # work at all.
    op.execute(
        """
        INSERT INTO document_approval (
            id, document_id, document_version_id, org_id, approval_type,
            approved_at, approved_by_contact_id, approver_name, note
        )
        SELECT
            gen_random_uuid(),
            dv.document_id,
            dv.id,
            dv.org_id,
            'initial',
            dv.approved_at,
            dv.approved_by_contact_id,
            COALESCE(c.name, 'Unrecorded (approved before N.3)'),
            'Backfilled by migration 0062 from document_version.approved_at.'
        FROM document_version dv
        LEFT JOIN contact c ON c.id = dv.approved_by_contact_id
        WHERE dv.approved_at IS NOT NULL
        """
    )


def downgrade() -> None:
    op.drop_constraint(
        "document_version_approved_by_contact_id_fkey",
        "document_version",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "document_version_approved_by_contact_id_fkey",
        "document_version",
        "contact",
        ["approved_by_contact_id"],
        ["id"],
    )
    op.execute("DROP FUNCTION IF EXISTS auth.orgs_with_documents()")
    op.execute(
        "DROP POLICY IF EXISTS document_review_notification_tenant_isolation "
        "ON document_review_notification"
    )
    op.execute(
        "DROP POLICY IF EXISTS document_approval_tenant_isolation ON document_approval"
    )
    op.drop_table("document_review_notification")
    op.drop_table("document_approval")
