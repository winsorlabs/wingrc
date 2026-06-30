"""assessment engine: framework, controls, objectives, baseline library, assessments

Revision ID: 0002_assessment_engine
Revises: 0001_initial
Create Date: 2026-06-30

Creates the full CMMC L2 assessment core on top of the existing scope layer:
  - NIST 800-171 catalog: framework, control, assessment_objective
  - Baseline library: product, baseline_control, baseline_evidence_spec
  - Tenant tool stack: org_product
  - Assessment core: assessment, control_state, control_state_history
  - Evidence layer: evidence, evidence_state_link, evidence_task
  - Gap tracking: finding, poa_m_item
  - SSP generation: implementation_statement

All status/classification columns are backed by Postgres CHECK constraints that
mirror the Python StrEnum values in assessment.py. RLS is enabled on every
table with a direct org_id column, using the same app.current_org pattern as
the scope_entity table.

Design decisions recorded in backend/app/assessment.py (five design notes).
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_assessment_engine"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Reusable RLS policy DDL template
_RLS_ENABLE = "ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"
_RLS_POLICY = """
CREATE POLICY {table}_tenant_isolation ON {table}
USING (org_id = NULLIF(current_setting('app.current_org', true), '')::uuid)
"""


def _enable_rls(table: str) -> None:
    op.execute(_RLS_ENABLE.format(table=table))
    op.execute(_RLS_POLICY.format(table=table))


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. framework
    # ------------------------------------------------------------------
    op.create_table(
        "framework",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("key", sa.String(60), nullable=False, unique=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("version", sa.String(20), nullable=False),
        sa.Column("published_at", sa.Date, nullable=True),
        sa.Column("deprecated_at", sa.Date, nullable=True),
        sa.Column(
            "successor_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("framework.id"),
            nullable=True,
        ),
    )

    # ------------------------------------------------------------------
    # 2. control
    # ------------------------------------------------------------------
    op.create_table(
        "control",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "framework_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("framework.id"),
            nullable=False,
        ),
        sa.Column("control_id", sa.String(40), nullable=False),
        sa.Column("family", sa.String(10), nullable=False),
        sa.Column("title", sa.String(400), nullable=False),
        sa.Column("requirement_text", sa.Text, nullable=False),
        sa.Column("discussion", sa.Text, nullable=True),
        sa.Column(
            "sprs_weight", sa.SmallInteger, nullable=False, server_default="1"
        ),
        sa.Column(
            "sequence_order", sa.Integer, nullable=False, server_default="0"
        ),
        sa.UniqueConstraint("framework_id", "control_id", name="uq_control_identity"),
    )
    op.create_index("ix_control_framework_id", "control", ["framework_id"])
    op.create_index("ix_control_control_id", "control", ["control_id"])
    op.create_index("ix_control_family", "control", ["family"])

    # ------------------------------------------------------------------
    # 3. assessment_objective
    # ------------------------------------------------------------------
    op.create_table(
        "assessment_objective",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "control_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("control.id"),
            nullable=False,
        ),
        sa.Column("objective_key", sa.String(5), nullable=False),
        sa.Column("text", sa.Text, nullable=False),
        sa.UniqueConstraint(
            "control_id", "objective_key", name="uq_objective_identity"
        ),
    )
    op.create_index(
        "ix_assessment_objective_control_id", "assessment_objective", ["control_id"]
    )

    # ------------------------------------------------------------------
    # 4. product
    # ------------------------------------------------------------------
    op.create_table(
        "product",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "framework_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("framework.id"),
            nullable=False,
        ),
        sa.Column("key", sa.String(60), nullable=False, unique=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("provider", sa.String(200), nullable=False),
        sa.Column("category", sa.String(40), nullable=False),
        sa.Column("asset_type", sa.String(40), nullable=False),
        sa.Column("role", sa.Text, nullable=False),
        sa.Column(
            "assumed_config",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "is_published",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_product_framework_id", "product", ["framework_id"])

    # ------------------------------------------------------------------
    # 5. baseline_control
    # ------------------------------------------------------------------
    op.create_table(
        "baseline_control",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "product_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("product.id"),
            nullable=False,
        ),
        sa.Column(
            "control_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("control.id"),
            nullable=False,
        ),
        sa.Column(
            "objectives",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("classification", sa.String(30), nullable=False),
        sa.Column("candidate_state", sa.String(40), nullable=False),
        sa.Column("provider_contribution", sa.Text, nullable=True),
        sa.Column("customer_action", sa.Text, nullable=True),
        sa.Column("note", sa.Text, nullable=True),
        sa.Column("scope_note", sa.Text, nullable=True),
        sa.Column(
            "batch_group_id", postgresql.UUID(as_uuid=True), nullable=True
        ),
        sa.UniqueConstraint(
            "product_id", "control_id", name="uq_baseline_control_identity"
        ),
        sa.CheckConstraint(
            "classification IN ('provider_satisfies', 'shared', 'customer_owns')",
            name="ck_baseline_control_classification",
        ),
        sa.CheckConstraint(
            "candidate_state IN ('pending_evidence', 'not_satisfied_by_product')",
            name="ck_baseline_control_candidate_state",
        ),
    )
    op.create_index(
        "ix_baseline_control_product_id", "baseline_control", ["product_id"]
    )
    op.create_index(
        "ix_baseline_control_control_id", "baseline_control", ["control_id"]
    )
    op.create_index(
        "ix_baseline_control_batch_group_id",
        "baseline_control",
        ["batch_group_id"],
    )

    # ------------------------------------------------------------------
    # 6. baseline_evidence_spec
    # ------------------------------------------------------------------
    op.create_table(
        "baseline_evidence_spec",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "baseline_control_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("baseline_control.id"),
            nullable=False,
        ),
        sa.Column("artifact_description", sa.Text, nullable=False),
        sa.Column("evidence_type", sa.String(20), nullable=False),
        sa.Column("kb_reference", sa.Text, nullable=True),
        sa.CheckConstraint(
            "evidence_type IN"
            " ('screenshot', 'export', 'document', 'link', 'policy')",
            name="ck_baseline_evidence_spec_type",
        ),
    )
    op.create_index(
        "ix_baseline_evidence_spec_baseline_control_id",
        "baseline_evidence_spec",
        ["baseline_control_id"],
    )

    # ------------------------------------------------------------------
    # 7. org_product  (tenant table — gets RLS)
    # ------------------------------------------------------------------
    op.create_table(
        "org_product",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "org_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organization.id"),
            nullable=False,
        ),
        sa.Column(
            "product_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("product.id"),
            nullable=False,
        ),
        sa.Column(
            "status", sa.String(20), nullable=False, server_default="candidate"
        ),
        sa.Column(
            "configured",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("configuration_notes", sa.Text, nullable=True),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("org_id", "product_id", name="uq_org_product_identity"),
        sa.CheckConstraint(
            "status IN ('candidate', 'active', 'decommissioned')",
            name="ck_org_product_status",
        ),
    )
    op.create_index("ix_org_product_org_id", "org_product", ["org_id"])
    op.create_index("ix_org_product_product_id", "org_product", ["product_id"])
    _enable_rls("org_product")

    # ------------------------------------------------------------------
    # 8. assessment  (tenant table — gets RLS)
    # ------------------------------------------------------------------
    op.create_table(
        "assessment",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "org_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organization.id"),
            nullable=False,
        ),
        sa.Column(
            "framework_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("framework.id"),
            nullable=False,
        ),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column(
            "assessment_type", sa.String(20), nullable=False, server_default="self"
        ),
        sa.Column(
            "status", sa.String(20), nullable=False, server_default="in_progress"
        ),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sprs_score", sa.SmallInteger, nullable=True),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "assessment_type IN ('self', 'third_party', 'c3pao')",
            name="ck_assessment_type",
        ),
        sa.CheckConstraint(
            "status IN ('in_progress', 'submitted', 'closed')",
            name="ck_assessment_status",
        ),
    )
    op.create_index("ix_assessment_org_id", "assessment", ["org_id"])
    op.create_index("ix_assessment_framework_id", "assessment", ["framework_id"])
    _enable_rls("assessment")

    # ------------------------------------------------------------------
    # 9. control_state  (tenant table — gets RLS)
    # ------------------------------------------------------------------
    op.create_table(
        "control_state",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "assessment_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("assessment.id"),
            nullable=False,
        ),
        sa.Column(
            "org_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organization.id"),
            nullable=False,
        ),
        sa.Column(
            "objective_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("assessment_objective.id"),
            nullable=False,
        ),
        sa.Column(
            "status", sa.String(20), nullable=False, server_default="not_met"
        ),
        sa.Column(
            "responsibility",
            sa.String(25),
            nullable=False,
            server_default="customer_owns",
        ),
        sa.Column(
            "sourced_from_product_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("product.id"),
            nullable=True,
        ),
        sa.Column("implementation_notes", sa.Text, nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "assessment_id", "objective_id", name="uq_control_state_identity"
        ),
        sa.CheckConstraint(
            "status IN ('not_met', 'pending_evidence', 'partial', 'met',"
            " 'not_applicable', 'inherited')",
            name="ck_control_state_status",
        ),
        sa.CheckConstraint(
            "responsibility IN ('provider_satisfies', 'shared',"
            " 'customer_owns', 'external_system')",
            name="ck_control_state_responsibility",
        ),
    )
    op.create_index(
        "ix_control_state_assessment_id", "control_state", ["assessment_id"]
    )
    op.create_index("ix_control_state_org_id", "control_state", ["org_id"])
    op.create_index(
        "ix_control_state_objective_id", "control_state", ["objective_id"]
    )
    _enable_rls("control_state")

    # ------------------------------------------------------------------
    # 10. control_state_history  (accessed via RLS'd control_state)
    # ------------------------------------------------------------------
    op.create_table(
        "control_state_history",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "control_state_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("control_state.id"),
            nullable=False,
        ),
        sa.Column("previous_status", sa.String(20), nullable=True),
        sa.Column("new_status", sa.String(20), nullable=False),
        sa.Column("previous_responsibility", sa.String(25), nullable=True),
        sa.Column("new_responsibility", sa.String(25), nullable=False),
        sa.Column("change_reason", sa.Text, nullable=True),
        sa.Column(
            "changed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_control_state_history_control_state_id",
        "control_state_history",
        ["control_state_id"],
    )

    # ------------------------------------------------------------------
    # 11. evidence  (tenant table — gets RLS)
    # ------------------------------------------------------------------
    op.create_table(
        "evidence",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "org_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organization.id"),
            nullable=False,
        ),
        sa.Column("title", sa.String(400), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("artifact_type", sa.String(20), nullable=False),
        sa.Column("storage_key", sa.Text, nullable=True),
        sa.Column("storage_url", sa.Text, nullable=True),
        sa.Column("mime_type", sa.String(100), nullable=True),
        sa.Column("file_size_bytes", sa.BigInteger, nullable=True),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "source_product_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("product.id"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "artifact_type IN"
            " ('screenshot', 'export', 'document', 'link', 'policy')",
            name="ck_evidence_artifact_type",
        ),
    )
    op.create_index("ix_evidence_org_id", "evidence", ["org_id"])
    _enable_rls("evidence")

    # ------------------------------------------------------------------
    # 12. evidence_state_link
    # ------------------------------------------------------------------
    op.create_table(
        "evidence_state_link",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "evidence_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("evidence.id"),
            nullable=False,
        ),
        sa.Column(
            "control_state_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("control_state.id"),
            nullable=False,
        ),
        sa.Column(
            "linked_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "evidence_id", "control_state_id", name="uq_evidence_state_link"
        ),
    )
    op.create_index(
        "ix_evidence_state_link_evidence_id",
        "evidence_state_link",
        ["evidence_id"],
    )
    op.create_index(
        "ix_evidence_state_link_control_state_id",
        "evidence_state_link",
        ["control_state_id"],
    )

    # ------------------------------------------------------------------
    # 13. evidence_task  (tenant table — gets RLS)
    # ------------------------------------------------------------------
    op.create_table(
        "evidence_task",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "org_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organization.id"),
            nullable=False,
        ),
        sa.Column(
            "assessment_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("assessment.id"),
            nullable=True,
        ),
        sa.Column(
            "control_state_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("control_state.id"),
            nullable=True,
        ),
        sa.Column(
            "baseline_spec_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("baseline_evidence_spec.id"),
            nullable=True,
        ),
        sa.Column("title", sa.String(400), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("artifact_type", sa.String(20), nullable=False),
        sa.Column(
            "status", sa.String(20), nullable=False, server_default="pending"
        ),
        sa.Column("collection_session", sa.String(200), nullable=True),
        sa.Column("due_date", sa.Date, nullable=True),
        sa.Column(
            "completed_evidence_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("evidence.id"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "artifact_type IN"
            " ('screenshot', 'export', 'document', 'link', 'policy')",
            name="ck_evidence_task_artifact_type",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'in_progress', 'completed', 'skipped', 'waived')",
            name="ck_evidence_task_status",
        ),
    )
    op.create_index("ix_evidence_task_org_id", "evidence_task", ["org_id"])
    op.create_index(
        "ix_evidence_task_assessment_id", "evidence_task", ["assessment_id"]
    )
    op.create_index(
        "ix_evidence_task_control_state_id", "evidence_task", ["control_state_id"]
    )
    _enable_rls("evidence_task")

    # ------------------------------------------------------------------
    # 14. finding  (tenant table — gets RLS)
    # ------------------------------------------------------------------
    op.create_table(
        "finding",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "assessment_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("assessment.id"),
            nullable=False,
        ),
        sa.Column(
            "org_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organization.id"),
            nullable=False,
        ),
        sa.Column(
            "control_state_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("control_state.id"),
            nullable=False,
        ),
        sa.Column("title", sa.String(400), nullable=False),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("finding_type", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="open"),
        sa.Column(
            "identified_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "severity IN ('critical', 'high', 'medium', 'low', 'informational')",
            name="ck_finding_severity",
        ),
        sa.CheckConstraint(
            "finding_type IN ('gap', 'deficiency', 'weakness', 'observation')",
            name="ck_finding_type",
        ),
        sa.CheckConstraint(
            "status IN ('open', 'in_remediation', 'closed', 'accepted_risk')",
            name="ck_finding_status",
        ),
    )
    op.create_index("ix_finding_assessment_id", "finding", ["assessment_id"])
    op.create_index("ix_finding_org_id", "finding", ["org_id"])
    op.create_index(
        "ix_finding_control_state_id", "finding", ["control_state_id"]
    )
    _enable_rls("finding")

    # ------------------------------------------------------------------
    # 15. poa_m_item  (tenant table — gets RLS)
    # ------------------------------------------------------------------
    op.create_table(
        "poa_m_item",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "org_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organization.id"),
            nullable=False,
        ),
        sa.Column(
            "finding_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("finding.id"),
            nullable=True,
        ),
        sa.Column(
            "control_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("control.id"),
            nullable=True,
        ),
        sa.Column("title", sa.String(400), nullable=False),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("scheduled_completion_date", sa.Date, nullable=True),
        sa.Column(
            "planned_milestones",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("responsible_party", sa.String(200), nullable=True),
        sa.Column("resources_required", sa.Text, nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="open"),
        sa.Column("risk_acceptance_reason", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('open', 'on_track', 'delayed', 'completed', 'cancelled')",
            name="ck_poa_m_item_status",
        ),
    )
    op.create_index("ix_poa_m_item_org_id", "poa_m_item", ["org_id"])
    op.create_index("ix_poa_m_item_finding_id", "poa_m_item", ["finding_id"])
    op.create_index("ix_poa_m_item_control_id", "poa_m_item", ["control_id"])
    _enable_rls("poa_m_item")

    # ------------------------------------------------------------------
    # 16. implementation_statement  (tenant table — gets RLS)
    # ------------------------------------------------------------------
    op.create_table(
        "implementation_statement",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "org_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organization.id"),
            nullable=False,
        ),
        sa.Column(
            "control_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("control.id"),
            nullable=False,
        ),
        sa.Column(
            "assessment_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("assessment.id"),
            nullable=True,
        ),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column(
            "status", sa.String(20), nullable=False, server_default="draft"
        ),
        sa.Column("generation_model", sa.String(100), nullable=True),
        sa.Column("generation_prompt_version", sa.String(50), nullable=True),
        sa.Column(
            "grounded_in",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "assessment_id",
            "control_id",
            name="uq_implementation_statement_identity",
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'reviewed', 'approved')",
            name="ck_implementation_statement_status",
        ),
    )
    op.create_index(
        "ix_implementation_statement_org_id",
        "implementation_statement",
        ["org_id"],
    )
    op.create_index(
        "ix_implementation_statement_control_id",
        "implementation_statement",
        ["control_id"],
    )
    op.create_index(
        "ix_implementation_statement_assessment_id",
        "implementation_statement",
        ["assessment_id"],
    )
    _enable_rls("implementation_statement")


def downgrade() -> None:
    # Drop in reverse FK dependency order
    for table in [
        "implementation_statement",
        "poa_m_item",
        "finding",
        "evidence_task",
        "evidence_state_link",
        "evidence",
        "control_state_history",
        "control_state",
        "assessment",
        "org_product",
        "baseline_evidence_spec",
        "baseline_control",
        "product",
        "assessment_objective",
        "control",
        "framework",
    ]:
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")
        op.drop_table(table)
