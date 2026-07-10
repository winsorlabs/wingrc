"""Deactivation support: needs_review status, archive columns, audit log.

Revision ID: 0010_deactivation_and_audit
Revises: 0009_evidence_task_many_to_many
Create Date: 2026-07-10

Changes:
  1. control_state.status CHECK — add 'needs_review' value.
  2. evidence_state_link — add is_archived, archived_at, archived_by_product.
  3. evidence_task — add is_archived, archived_at.
  4. org_product — add deactivated_at.
  5. Create audit_log table (append-only; see hardening note below).

DB-level append-only hardening (pending production step — not automated here
because the migration runs as the owner role that created the tables):
  REVOKE UPDATE, DELETE ON audit_log FROM wingrc;
  CREATE RULE no_update_audit_log AS ON UPDATE TO audit_log DO INSTEAD NOTHING;
  CREATE RULE no_delete_audit_log AS ON DELETE TO audit_log DO INSTEAD NOTHING;
Run these manually after initial migration on each environment.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0010_deactivation_and_audit"
down_revision = "0009_evidence_task_many_to_many"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. control_state.status: add 'needs_review'
    op.drop_constraint("ck_control_state_status", "control_state")
    op.create_check_constraint(
        "ck_control_state_status",
        "control_state",
        "status IN ('not_met', 'pending_evidence', 'partial', 'met',"
        " 'not_applicable', 'inherited', 'needs_review')",
    )

    # 2. evidence_state_link: archive columns
    op.add_column(
        "evidence_state_link",
        sa.Column("is_archived", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column(
        "evidence_state_link",
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "evidence_state_link",
        sa.Column("archived_by_product", sa.UUID(), nullable=True),
    )
    op.create_foreign_key(
        "fk_evidence_state_link_archived_by_product",
        "evidence_state_link",
        "product",
        ["archived_by_product"],
        ["id"],
    )

    # 3. evidence_task: archive columns
    op.add_column(
        "evidence_task",
        sa.Column("is_archived", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column(
        "evidence_task",
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
    )

    # 4. org_product: deactivated_at
    op.add_column(
        "org_product",
        sa.Column("deactivated_at", sa.DateTime(timezone=True), nullable=True),
    )

    # 5. audit_log table (append-only; see module-level hardening note)
    op.create_table(
        "audit_log",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("org_id", sa.UUID(), nullable=True),
        sa.Column("actor", sa.String(200), nullable=False, server_default=sa.text("'system'")),
        sa.Column("actor_type", sa.String(20), nullable=False, server_default=sa.text("'system'")),
        sa.Column("action", sa.String(80), nullable=False),
        sa.Column("entity_type", sa.String(60), nullable=False),
        sa.Column("entity_id", sa.UUID(), nullable=False),
        sa.Column("before_value", postgresql.JSONB(), nullable=True),
        sa.Column("after_value", postgresql.JSONB(), nullable=True),
        sa.Column("context", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["org_id"], ["organization.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_log_org_id", "audit_log", ["org_id"])
    op.create_index("ix_audit_log_action", "audit_log", ["action"])
    op.create_index("ix_audit_log_entity", "audit_log", ["entity_type", "entity_id"])
    op.create_index("ix_audit_log_created_at", "audit_log", ["created_at"])


def downgrade() -> None:
    op.drop_table("audit_log")

    op.drop_column("org_product", "deactivated_at")

    op.drop_column("evidence_task", "archived_at")
    op.drop_column("evidence_task", "is_archived")

    op.drop_constraint(
        "fk_evidence_state_link_archived_by_product", "evidence_state_link", type_="foreignkey"
    )
    op.drop_column("evidence_state_link", "archived_by_product")
    op.drop_column("evidence_state_link", "archived_at")
    op.drop_column("evidence_state_link", "is_archived")

    op.drop_constraint("ck_control_state_status", "control_state")
    op.create_check_constraint(
        "ck_control_state_status",
        "control_state",
        "status IN ('not_met', 'pending_evidence', 'partial', 'met',"
        " 'not_applicable', 'inherited')",
    )
