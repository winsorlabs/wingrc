"""Multi-org access (M.1): org_membership + deployment_settings.

See docs/adr/0009-multi-org-user-access.md. Schema-only slice — nothing in
the application reads or writes either table yet. `User.org_id`/`User.role`
remain fully authoritative for access until the M.4 enforcement cutover;
this migration exists purely to get the data (and the ADR 0005 dependency
anchor) in place ahead of that, so M.2's auto-provisioning and M.4's
cutover aren't also carrying a schema migration in their own diffs.

`org_membership` — one row per (user, org) grant, role scoped to the grant
rather than global to the user. Backfilled 1:1 from every existing `User`
row here (pass 1: preserves today's access exactly, zero behavior change).
Pass 2 — granting every existing msp_admin/msp_engineer membership into
every *other* existing org, the step that actually fixes the ADR 0009
defect — is deliberately NOT done here; it's application-level
auto-provisioning logic (M.2), not a one-time backfill, since it has to
keep firing on every future org creation and MSP-role invite, not just
run once against today's data.

`deployment_settings` — singleton (`CHECK (id = 1)`) naming which org is
this deployment's own MSP org, per ADR 0009's Boundary section. Backfilled
from the earliest-created msp_admin's org if one already exists (an
upgrading deployment); left empty on a fresh database, where
`manage.py bootstrap-admin` populates it at first run instead (nothing to
backfill yet — no users exist before that command runs).

Revision ID: 0023_org_membership
Revises: 0022_audit_log_ip_address
Create Date: 2026-08-07
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0023_org_membership"
down_revision: str | None = "0022_audit_log_ip_address"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- org_membership ---
    op.create_table(
        "org_membership",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", UUID(as_uuid=True),
                  sa.ForeignKey("user.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("org_id", UUID(as_uuid=True),
                  sa.ForeignKey("organization.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("role", sa.String(40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "org_id", name="uq_org_membership_user_org"),
        sa.CheckConstraint(
            "role IN ('msp_admin','msp_engineer','customer_poc','c3pao_assessor')",
            name="ck_org_membership_role",
        ),
    )
    op.execute("ALTER TABLE org_membership ENABLE ROW LEVEL SECURITY")
    op.execute(
        """CREATE POLICY org_membership_tenant_isolation ON org_membership
           USING (org_id = NULLIF(current_setting('app.current_org', true), '')::uuid)"""
    )

    # Backfill pass 1: mirror today's single-org access exactly.
    op.execute(
        """
        INSERT INTO org_membership (id, user_id, org_id, role, created_at)
        SELECT gen_random_uuid(), id, org_id, role, now()
        FROM "user"
        """
    )

    # --- deployment_settings ---
    op.create_table(
        "deployment_settings",
        sa.Column("id", sa.SmallInteger, primary_key=True, server_default="1"),
        sa.Column("msp_org_id", UUID(as_uuid=True),
                  sa.ForeignKey("organization.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.CheckConstraint("id = 1", name="ck_deployment_settings_singleton"),
    )

    # Backfill: anchor to the earliest-created msp_admin's org, if one
    # already exists (an upgrading deployment). Inserts zero rows on a
    # fresh database — bootstrap-admin populates it there instead.
    op.execute(
        """INSERT INTO deployment_settings (id, msp_org_id, created_at)
           SELECT 1, org_id, now()
           FROM "user"
           WHERE role = 'msp_admin'
           ORDER BY created_at ASC
           LIMIT 1"""
    )


def downgrade() -> None:
    op.drop_table("deployment_settings")
    op.execute("DROP POLICY IF EXISTS org_membership_tenant_isolation ON org_membership")
    op.drop_table("org_membership")
