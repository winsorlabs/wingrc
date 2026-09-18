"""D.3 second half: daily Liongard sync + asset/user onboarding approval.

Five new tables: liongard_sync_result, liongard_sync_result_change,
liongard_sync_notification, asset_approval, asset_approval_checklist_item.
All org-scoped and RLS-enabled, matching review_cycle*'s exact policy
shape (0045) -- every one carries its own org_id, even the child tables,
per that migration's own precedent (no RLS-enabled table in this codebase
relies on a join for its policy).

Plus one SECURITY DEFINER function, auth.orgs_with_liongard_mapping(), for
the scheduled job's own discovery step (scheduler.py:_liongard_daily_sync):
org_liongard_environment is RLS-protected (0035), so finding every org
with a live mapping -- across every org, in one query -- needs the same
cross-org-read mechanism as auth.orgs_due_for_review_cycle_open() (0046),
not a blanket RLS bypass.

scope_entity.status gains no migration of its own: verified against
models.py:ScopeEntity and every migration touching it (0001_initial) --
there is no CHECK constraint on that column, only Pydantic-level
validation (routers/scope.py:_ENTITY_STATUSES, derived from
domain.EntityStatus). Adding EntityStatus.PENDING_APPROVAL is a pure
Python enum change with nothing to migrate here.

Revision ID: 0056_liongard_sync_approval
Revises: 0055_footprint_version
Create Date: 2026-09-18
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0056_liongard_sync_approval"
down_revision: str | None = "0055_footprint_version"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_ROLE = "wingrc_app"


def _rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(
        f"""CREATE POLICY {table}_tenant_isolation ON {table}
           USING (org_id = NULLIF(current_setting('app.current_org', true), '')::uuid)"""
    )


def upgrade() -> None:
    op.create_table(
        "liongard_sync_result",
        sa.Column(
            "id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column("org_id", UUID(as_uuid=True), sa.ForeignKey("organization.id"), nullable=False),
        sa.Column(
            "job_run_id", UUID(as_uuid=True), sa.ForeignKey("job_run.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("liongard_environment_id", sa.Integer(), nullable=False),
        sa.Column("liongard_environment_name", sa.String(200), nullable=True),
        sa.Column("pulled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "status", sa.String(20), nullable=False, server_default=sa.text("'pending_review'")
        ),
        sa.Column("summary", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("warnings", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "status IN ('pending_review', 'reviewed', 'superseded', 'no_changes')",
            name="ck_liongard_sync_result_status",
        ),
    )
    op.create_index("ix_liongard_sync_result_org_id", "liongard_sync_result", ["org_id"])
    _rls("liongard_sync_result")

    op.create_table(
        "liongard_sync_result_change",
        sa.Column(
            "id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column(
            "sync_result_id", UUID(as_uuid=True),
            sa.ForeignKey("liongard_sync_result.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("org_id", UUID(as_uuid=True), sa.ForeignKey("organization.id"), nullable=False),
        sa.Column("change_type", sa.String(10), nullable=False),
        sa.Column("entity_type", sa.String(40), nullable=False),
        sa.Column("natural_key", sa.String(400), nullable=False),
        sa.Column("field_diffs", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("incoming", JSONB, nullable=True),
        sa.Column("warnings", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("resolution", sa.String(10), nullable=True, server_default=sa.text("'pending'")),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_by", sa.String(100), nullable=True),
        sa.CheckConstraint(
            "change_type IN ('new', 'changed', 'missing')",
            name="ck_liongard_sync_result_change_type",
        ),
        sa.CheckConstraint(
            "resolution IN ('pending', 'approved', 'rejected')",
            name="ck_liongard_sync_result_change_resolution",
        ),
    )
    op.create_index(
        "ix_liongard_sync_result_change_sync_result_id",
        "liongard_sync_result_change", ["sync_result_id"],
    )
    op.create_index(
        "ix_liongard_sync_result_change_org_id", "liongard_sync_result_change", ["org_id"]
    )
    _rls("liongard_sync_result_change")

    op.create_table(
        "liongard_sync_notification",
        sa.Column(
            "id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column(
            "sync_result_id", UUID(as_uuid=True),
            sa.ForeignKey("liongard_sync_result.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("org_id", UUID(as_uuid=True), sa.ForeignKey("organization.id"), nullable=False),
        sa.Column(
            "contact_id", UUID(as_uuid=True), sa.ForeignKey("contact.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("recipient_name", sa.String(200), nullable=False),
        sa.Column("recipient_email", sa.String(320), nullable=False),
        sa.Column("notified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notification_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index(
        "ix_liongard_sync_notification_sync_result_id",
        "liongard_sync_notification", ["sync_result_id"],
    )
    op.create_index(
        "ix_liongard_sync_notification_org_id", "liongard_sync_notification", ["org_id"]
    )
    _rls("liongard_sync_notification")

    op.create_table(
        "asset_approval",
        sa.Column(
            "id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column("org_id", UUID(as_uuid=True), sa.ForeignKey("organization.id"), nullable=False),
        sa.Column(
            "scope_entity_id", UUID(as_uuid=True),
            sa.ForeignKey("scope_entity.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "sync_result_change_id", UUID(as_uuid=True),
            sa.ForeignKey("liongard_sync_result_change.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("decision", sa.String(10), nullable=False),
        sa.Column("decided_by", sa.String(100), nullable=False),
        sa.Column("decided_by_name", sa.String(200), nullable=False),
        sa.Column(
            "decided_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "decision IN ('approved', 'rejected')", name="ck_asset_approval_decision"
        ),
    )
    op.create_index("ix_asset_approval_org_id", "asset_approval", ["org_id"])
    op.create_index("ix_asset_approval_scope_entity_id", "asset_approval", ["scope_entity_id"])
    _rls("asset_approval")

    op.create_table(
        "asset_approval_checklist_item",
        sa.Column(
            "id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column(
            "approval_id", UUID(as_uuid=True),
            sa.ForeignKey("asset_approval.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("org_id", UUID(as_uuid=True), sa.ForeignKey("organization.id"), nullable=False),
        sa.Column("product_key", sa.String(60), nullable=False),
        sa.Column("product_name", sa.String(200), nullable=False),
        sa.Column("confirmed", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.create_index(
        "ix_asset_approval_checklist_item_approval_id",
        "asset_approval_checklist_item", ["approval_id"],
    )
    op.create_index(
        "ix_asset_approval_checklist_item_org_id", "asset_approval_checklist_item", ["org_id"]
    )
    _rls("asset_approval_checklist_item")

    # Scheduler discovery step: which orgs have a live Liongard mapping to
    # sync -- org_liongard_environment is RLS-protected (0035), so a
    # cross-org scan needs a SECURITY DEFINER function, matching
    # auth.orgs_due_for_review_cycle_open()'s exact precedent (0046).
    op.execute("""
        CREATE FUNCTION auth.orgs_with_liongard_mapping()
        RETURNS TABLE (
            org_id UUID, liongard_environment_id INTEGER, liongard_environment_name VARCHAR
        )
        SECURITY DEFINER
        SET search_path = public, pg_catalog
        LANGUAGE sql STABLE AS $$
            SELECT org_id, liongard_environment_id, liongard_environment_name
            FROM public.org_liongard_environment;
        $$;
    """)
    op.execute("REVOKE EXECUTE ON FUNCTION auth.orgs_with_liongard_mapping() FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION auth.orgs_with_liongard_mapping() TO {_APP_ROLE}")


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS auth.orgs_with_liongard_mapping()")
    op.drop_table("asset_approval_checklist_item")
    op.drop_table("asset_approval")
    op.drop_table("liongard_sync_notification")
    op.drop_table("liongard_sync_result_change")
    op.drop_table("liongard_sync_result")
