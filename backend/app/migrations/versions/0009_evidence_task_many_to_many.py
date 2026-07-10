"""Refactor evidence_task: many-to-many state links, open/collected/na status.

Revision ID: 0009_evidence_task_many_to_many
Revises: 0008_control_cmmc_level
Create Date: 2026-07-09

Changes:
  1. Create evidence_task_state_link join table (task ↔ control_state, M:N).
  2. Drop evidence_task.control_state_id (replaced by the join table).
  3. Migrate existing status values to the new vocabulary:
       pending / in_progress → open
       completed             → collected
       skipped / waived      → na
  4. Replace the status CHECK constraint with ('open', 'collected', 'na').
"""
import sqlalchemy as sa
from alembic import op

revision = "0009_evidence_task_many_to_many"
down_revision = "0008_control_cmmc_level"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Create the join table
    op.create_table(
        "evidence_task_state_link",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("task_id", sa.UUID(), nullable=False),
        sa.Column("control_state_id", sa.UUID(), nullable=False),
        sa.Column(
            "linked_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["control_state_id"], ["control_state.id"]),
        sa.ForeignKeyConstraint(["task_id"], ["evidence_task.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_id", "control_state_id", name="uq_evidence_task_state_link"),
    )
    op.create_index("ix_evidence_task_state_link_task_id", "evidence_task_state_link", ["task_id"])
    op.create_index(
        "ix_evidence_task_state_link_control_state_id",
        "evidence_task_state_link",
        ["control_state_id"],
    )

    # 2. Migrate existing rows from the old FK to the join table before dropping
    op.execute("""
        INSERT INTO evidence_task_state_link (id, task_id, control_state_id)
        SELECT gen_random_uuid(), id, control_state_id
        FROM evidence_task
        WHERE control_state_id IS NOT NULL
        ON CONFLICT DO NOTHING
    """)

    # 3. Drop the old singular FK column
    op.drop_constraint(
        "evidence_task_control_state_id_fkey", "evidence_task", type_="foreignkey"
    )
    op.drop_index("ix_evidence_task_control_state_id", table_name="evidence_task")
    op.drop_column("evidence_task", "control_state_id")

    # 4. Migrate status values to new vocabulary
    op.execute("""
        UPDATE evidence_task
        SET status = CASE
            WHEN status IN ('pending', 'in_progress') THEN 'open'
            WHEN status = 'completed'                 THEN 'collected'
            WHEN status IN ('skipped', 'waived')      THEN 'na'
            ELSE 'open'
        END
    """)

    # 5. Replace the status CHECK constraint
    op.drop_constraint("ck_evidence_task_status", "evidence_task")
    op.create_check_constraint(
        "ck_evidence_task_status",
        "evidence_task",
        "status IN ('open', 'collected', 'na')",
    )


def downgrade() -> None:
    # Restore status constraint
    op.drop_constraint("ck_evidence_task_status", "evidence_task")
    op.create_check_constraint(
        "ck_evidence_task_status",
        "evidence_task",
        "status IN ('pending', 'in_progress', 'completed', 'skipped', 'waived')",
    )

    # Migrate status back
    op.execute("""
        UPDATE evidence_task
        SET status = CASE
            WHEN status = 'open'      THEN 'pending'
            WHEN status = 'collected' THEN 'completed'
            WHEN status = 'na'        THEN 'waived'
            ELSE 'pending'
        END
    """)

    # Restore singular FK column
    op.add_column(
        "evidence_task",
        sa.Column("control_state_id", sa.UUID(), nullable=True),
    )
    op.create_index(
        "ix_evidence_task_control_state_id", "evidence_task", ["control_state_id"]
    )
    op.create_foreign_key(
        "evidence_task_control_state_id_fkey",
        "evidence_task",
        "control_state",
        ["control_state_id"],
        ["id"],
    )

    # Restore singular FK data from the join table (first link per task)
    op.execute("""
        UPDATE evidence_task et
        SET control_state_id = (
            SELECT control_state_id FROM evidence_task_state_link
            WHERE task_id = et.id
            ORDER BY linked_at
            LIMIT 1
        )
    """)

    op.drop_table("evidence_task_state_link")
