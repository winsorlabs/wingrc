"""SQLAlchemy 2.0 models.

The scope layer:
  A single `scope_entity` table holds the scope graph. Common, query-driven
  fields (type, category, status, provenance) are real columns; the variable
  per-type payload lives in a JSONB `attributes` column. Every CMMC list is a
  filter over this one table — "lists are views, not documents."

The assessment layer (added in migration 0002):
  Framework → Control → AssessmentObjective model the NIST 800-171 catalog.
  Product → BaselineControl → BaselineEvidenceSpec model the baseline library.
  OrgProduct links tenants to their tool stack.
  Assessment → ControlState tracks per-objective evidence state.
  Evidence + EvidenceStateLink implement evidence minimization: one artifact
  satisfies many objectives.
  Finding + PoamItem model gaps and remediation.
  ImplementationStatement holds the per-control SSP narrative (intentionally
  per-control, not per-objective — see assessment.py design note 3).

Per-tenant isolation is enforced by `org_id` plus Postgres Row-Level Security
(enabled in migrations), so one client's data can never leak into another's.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# Existing: Organization + scope graph
# ---------------------------------------------------------------------------


class Organization(Base):
    __tablename__ = "organization"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(200), unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ScopeEntity(Base):
    __tablename__ = "scope_entity"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)

    entity_type: Mapped[str] = mapped_column(String(40), index=True)
    natural_key: Mapped[str] = mapped_column(String(400), index=True)
    scope_category: Mapped[str | None] = mapped_column(String(60), index=True)
    status: Mapped[str] = mapped_column(String(30), default="active", index=True)
    in_boundary: Mapped[bool] = mapped_column(default=True)

    # Provenance — makes generated lists defensible.
    source: Mapped[str] = mapped_column(String(40), default="manual")
    source_ref: Mapped[str | None] = mapped_column(String(400))
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    attributes: Mapped[dict] = mapped_column(
        JSONB, server_default=text("'{}'::jsonb")
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


# ---------------------------------------------------------------------------
# Assessment layer: NIST 800-171 / CMMC L2 catalog
# ---------------------------------------------------------------------------


class Framework(Base):
    __tablename__ = "framework"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    key: Mapped[str] = mapped_column(String(60), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    version: Mapped[str] = mapped_column(String(20))
    published_at: Mapped[date | None] = mapped_column(Date)
    deprecated_at: Mapped[date | None] = mapped_column(Date)
    # self-referential: a deprecated framework points to its successor
    successor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("framework.id"), nullable=True
    )


class Control(Base):
    """One NIST 800-171 / CMMC practice (e.g. AC.L2-3.1.1)."""

    __tablename__ = "control"
    __table_args__ = (
        UniqueConstraint("framework_id", "control_id", name="uq_control_identity"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    framework_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("framework.id"), index=True
    )
    control_id: Mapped[str] = mapped_column(String(40), index=True)
    family: Mapped[str] = mapped_column(String(10), index=True)
    title: Mapped[str] = mapped_column(String(400))
    requirement_text: Mapped[str] = mapped_column(Text)
    discussion: Mapped[str | None] = mapped_column(Text)
    # SPRS deduction weight per CMMC scoring: 1 (moderate), 3 (high), 5 (critical)
    sprs_weight: Mapped[int] = mapped_column(SmallInteger, default=1)
    sequence_order: Mapped[int] = mapped_column(Integer, default=0)


class AssessmentObjective(Base):
    """Sub-part of a control (e.g. AC.L2-3.1.1[a]).

    NIST 800-171A defines assessment objectives as granular items evaluators
    check. SPRS scoring and gap tracking operate at this level — each objective
    must be met for its parent control to count as satisfied.

    satisfaction_type classifies HOW the objective is satisfied:
      product            — enforced by a configured security tool
      document_list      — a scope-graph-generated list (e.g. authorized users)
      scheduled_operation— a recurring human activity (see cadence fields)
      narrative          — policy/procedure documentation

    is_draft = True on every seed row until a C3PAO reviews the classification.
    """

    __tablename__ = "assessment_objective"
    __table_args__ = (
        UniqueConstraint(
            "control_id", "objective_key", name="uq_objective_identity"
        ),
        CheckConstraint(
            "satisfaction_type IN ('product','document_list','scheduled_operation','narrative')",
            name="ck_assessment_objective_sat_type",
        ),
        CheckConstraint(
            "cadence IN ('annual','quarterly','monthly') OR cadence IS NULL",
            name="ck_assessment_objective_cadence",
        ),
        CheckConstraint(
            "cadence_responsibility IN ('msp','customer','shared') OR cadence_responsibility IS NULL",
            name="ck_assessment_objective_cadence_resp",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    control_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("control.id"), index=True
    )
    # "a", "b", "c" ... matches objective keys in baseline_control.objectives JSONB
    objective_key: Mapped[str] = mapped_column(String(5))
    text: Mapped[str] = mapped_column(Text)

    # Satisfaction-type tagging (REVIEWABLE DRAFT — requires C3PAO sign-off)
    satisfaction_type: Mapped[str] = mapped_column(
        String(25), nullable=False, server_default=text("'narrative'")
    )
    # Set only for scheduled_operation objectives
    cadence: Mapped[str | None] = mapped_column(String(20), nullable=True)
    cadence_responsibility: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # True until C3PAO has reviewed the type/cadence/responsibility assignment
    is_draft: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))


# ---------------------------------------------------------------------------
# Assessment layer: baseline library
# ---------------------------------------------------------------------------


class Product(Base):
    """A tool in the baseline library (e.g. RocketCyber Managed SOC)."""

    __tablename__ = "product"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    framework_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("framework.id"), index=True
    )
    key: Mapped[str] = mapped_column(String(60), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    provider: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(40))
    asset_type: Mapped[str] = mapped_column(String(40))
    role: Mapped[str] = mapped_column(Text)
    assumed_config: Mapped[list] = mapped_column(
        JSONB, server_default=text("'[]'::jsonb")
    )
    is_published: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class BaselineControl(Base):
    """Per-control entry from the baseline library for a specific product.

    Normalized from the YAML loader's ControlEntry: batch entries (one YAML
    row covering a whole control family) become multiple rows here — one per
    control_id — linked by the same batch_group_id for UI grouping.
    """

    __tablename__ = "baseline_control"
    __table_args__ = (
        UniqueConstraint(
            "product_id", "control_id", name="uq_baseline_control_identity"
        ),
        CheckConstraint(
            "classification IN ('provider_satisfies', 'shared', 'customer_owns')",
            name="ck_baseline_control_classification",
        ),
        CheckConstraint(
            "candidate_state IN ('pending_evidence', 'not_satisfied_by_product')",
            name="ck_baseline_control_candidate_state",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("product.id"), index=True
    )
    control_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("control.id"), index=True
    )
    # List of objective_key strings this entry covers, e.g. ["a", "b", "c"]
    objectives: Mapped[list] = mapped_column(
        JSONB, server_default=text("'[]'::jsonb")
    )
    classification: Mapped[str] = mapped_column(String(30))
    candidate_state: Mapped[str] = mapped_column(String(40))
    provider_contribution: Mapped[str | None] = mapped_column(Text)
    customer_action: Mapped[str | None] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(Text)
    scope_note: Mapped[str | None] = mapped_column(Text)
    # Groups rows that originated from one batch YAML entry
    batch_group_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )


class BaselineEvidenceSpec(Base):
    """Evidence collection spec attached to a baseline_control entry.

    These rows drive the evidence tasks the magic loop creates. Only
    provider_satisfies and shared baseline_control rows have evidence specs;
    customer_owns rows have none — enforced by the minimization invariant.
    """

    __tablename__ = "baseline_evidence_spec"
    __table_args__ = (
        CheckConstraint(
            "evidence_type IN ('screenshot', 'export', 'document', 'link', 'policy')",
            name="ck_baseline_evidence_spec_type",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    baseline_control_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("baseline_control.id"), index=True
    )
    artifact_description: Mapped[str] = mapped_column(Text)
    evidence_type: Mapped[str] = mapped_column(String(20))
    kb_reference: Mapped[str | None] = mapped_column(Text)


# ---------------------------------------------------------------------------
# Assessment layer: tenant tool stack
# ---------------------------------------------------------------------------


class OrgProduct(Base):
    """Link between a tenant org and a product in the baseline library.

    status=candidate      — org is evaluating this product
    status=active         — magic loop has fired; control_state rows updated
    status=decommissioned — product removed; states remain for audit history
    """

    __tablename__ = "org_product"
    __table_args__ = (
        UniqueConstraint("org_id", "product_id", name="uq_org_product_identity"),
        CheckConstraint(
            "status IN ('candidate', 'active', 'decommissioned')",
            name="ck_org_product_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id"), index=True
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("product.id"), index=True
    )
    status: Mapped[str] = mapped_column(String(20), default="candidate")
    configured: Mapped[bool] = mapped_column(Boolean, default=False)
    configuration_notes: Mapped[str | None] = mapped_column(Text)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


# ---------------------------------------------------------------------------
# Assessment layer: assessment core
# ---------------------------------------------------------------------------


class Assessment(Base):
    """One CMMC L2 assessment run for a tenant against a specific framework."""

    __tablename__ = "assessment"
    __table_args__ = (
        CheckConstraint(
            "assessment_type IN ('self', 'third_party', 'c3pao')",
            name="ck_assessment_type",
        ),
        CheckConstraint(
            "status IN ('in_progress', 'submitted', 'closed')",
            name="ck_assessment_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id"), index=True
    )
    framework_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("framework.id"), index=True
    )
    name: Mapped[str] = mapped_column(String(200))
    assessment_type: Mapped[str] = mapped_column(String(20), default="self")
    status: Mapped[str] = mapped_column(String(20), default="in_progress")
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Cached SPRS score; recomputed on demand, stored here for reporting
    sprs_score: Mapped[int | None] = mapped_column(SmallInteger)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ControlState(Base):
    """Evidence state for one assessment_objective within one assessment.

    This is the finest-grained compliance tracking unit. SPRS scoring and
    gap reporting aggregate from rows here.

    On assessment creation, one row per objective is inserted (status=not_met,
    responsibility=customer_owns). The magic loop updates product-covered rows
    to pending_evidence. Engineers confirm met states by attaching evidence.

    See assessment.py for full status/responsibility vocabulary (design notes
    1, 2, 4).
    """

    __tablename__ = "control_state"
    __table_args__ = (
        UniqueConstraint(
            "assessment_id", "objective_id", name="uq_control_state_identity"
        ),
        CheckConstraint(
            "status IN ('not_met', 'pending_evidence', 'partial', 'met',"
            " 'not_applicable', 'inherited')",
            name="ck_control_state_status",
        ),
        CheckConstraint(
            "responsibility IN ('provider_satisfies', 'shared',"
            " 'customer_owns', 'external_system')",
            name="ck_control_state_responsibility",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    assessment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assessment.id"), index=True
    )
    # Denormalized for RLS policy — always equals assessment.org_id
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id"), index=True
    )
    objective_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assessment_objective.id"), index=True
    )
    status: Mapped[str] = mapped_column(String(20), default="not_met")
    responsibility: Mapped[str] = mapped_column(String(25), default="customer_owns")
    # Audit trail back to the product whose magic loop set this state
    sourced_from_product_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("product.id"), nullable=True
    )
    implementation_notes: Mapped[str | None] = mapped_column(Text)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ControlStateHistory(Base):
    """Append-only audit log of every status/responsibility change."""

    __tablename__ = "control_state_history"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    control_state_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("control_state.id"), index=True
    )
    previous_status: Mapped[str | None] = mapped_column(String(20))
    new_status: Mapped[str] = mapped_column(String(20))
    previous_responsibility: Mapped[str | None] = mapped_column(String(25))
    new_responsibility: Mapped[str] = mapped_column(String(25))
    change_reason: Mapped[str | None] = mapped_column(Text)
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


# ---------------------------------------------------------------------------
# Assessment layer: evidence
# ---------------------------------------------------------------------------


class Evidence(Base):
    """One collected evidence artifact (S3 reference).

    An artifact is stored once and can satisfy multiple control objectives via
    EvidenceStateLink — this is evidence minimization at the data layer.
    customer_owns objectives never receive evidence tasks, so there is no path
    for a customer_owns objective to gain evidence here.
    """

    __tablename__ = "evidence"
    __table_args__ = (
        CheckConstraint(
            "artifact_type IN ('screenshot', 'export', 'document', 'link', 'policy')",
            name="ck_evidence_artifact_type",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id"), index=True
    )
    title: Mapped[str] = mapped_column(String(400))
    description: Mapped[str | None] = mapped_column(Text)
    artifact_type: Mapped[str] = mapped_column(String(20))
    storage_key: Mapped[str | None] = mapped_column(Text)
    storage_url: Mapped[str | None] = mapped_column(Text)
    mime_type: Mapped[str | None] = mapped_column(String(100))
    file_size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_product_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("product.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class EvidenceStateLink(Base):
    """Many-to-many join: one artifact can satisfy many objectives."""

    __tablename__ = "evidence_state_link"
    __table_args__ = (
        UniqueConstraint(
            "evidence_id", "control_state_id", name="uq_evidence_state_link"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    evidence_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("evidence.id"), index=True
    )
    control_state_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("control_state.id"), index=True
    )
    linked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class EvidenceTask(Base):
    """Queued evidence collection task created by the magic loop.

    One task per baseline_evidence_spec per active product's provider_satisfies
    and shared controls. customer_owns controls get no tasks — evidence
    minimization enforced at the magic-loop layer.
    """

    __tablename__ = "evidence_task"
    __table_args__ = (
        CheckConstraint(
            "artifact_type IN ('screenshot', 'export', 'document', 'link', 'policy')",
            name="ck_evidence_task_artifact_type",
        ),
        CheckConstraint(
            "status IN ('pending', 'in_progress', 'completed', 'skipped', 'waived')",
            name="ck_evidence_task_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id"), index=True
    )
    assessment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assessment.id"), nullable=True, index=True
    )
    control_state_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("control_state.id"), nullable=True, index=True
    )
    baseline_spec_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("baseline_evidence_spec.id"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(400))
    description: Mapped[str | None] = mapped_column(Text)
    artifact_type: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), default="pending")
    collection_session: Mapped[str | None] = mapped_column(String(200))
    due_date: Mapped[date | None] = mapped_column(Date)
    # Staleness deadline for recurring (scheduled_operation) tasks: when the
    # linked evidence expires and the task must be re-executed.
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_evidence_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("evidence.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


# ---------------------------------------------------------------------------
# Assessment layer: findings and remediation
# ---------------------------------------------------------------------------


class Finding(Base):
    """A documented gap or deficiency identified during the assessment."""

    __tablename__ = "finding"
    __table_args__ = (
        CheckConstraint(
            "severity IN ('critical', 'high', 'medium', 'low', 'informational')",
            name="ck_finding_severity",
        ),
        CheckConstraint(
            "finding_type IN ('gap', 'deficiency', 'weakness', 'observation')",
            name="ck_finding_type",
        ),
        CheckConstraint(
            "status IN ('open', 'in_remediation', 'closed', 'accepted_risk')",
            name="ck_finding_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    assessment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assessment.id"), index=True
    )
    # Denormalized for RLS — always equals assessment.org_id
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id"), index=True
    )
    control_state_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("control_state.id"), index=True
    )
    title: Mapped[str] = mapped_column(String(400))
    description: Mapped[str] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(String(20))
    finding_type: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), default="open")
    identified_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class PoamItem(Base):
    """Plan of Action and Milestones item for remediating a finding."""

    __tablename__ = "poa_m_item"
    __table_args__ = (
        CheckConstraint(
            "status IN ('open', 'on_track', 'delayed', 'completed', 'cancelled')",
            name="ck_poa_m_item_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id"), index=True
    )
    finding_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("finding.id"), nullable=True, index=True
    )
    # Direct link for pre-assessment POA&Ms not tied to a specific finding
    control_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("control.id"), nullable=True, index=True
    )
    title: Mapped[str] = mapped_column(String(400))
    description: Mapped[str] = mapped_column(Text)
    scheduled_completion_date: Mapped[date | None] = mapped_column(Date)
    planned_milestones: Mapped[list] = mapped_column(
        JSONB, server_default=text("'[]'::jsonb")
    )
    responsible_party: Mapped[str | None] = mapped_column(String(200))
    resources_required: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="open")
    risk_acceptance_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# ---------------------------------------------------------------------------
# Assessment layer: SSP generation
# ---------------------------------------------------------------------------


class ImplementationStatement(Base):
    """SSP narrative paragraph for one control within one assessment.

    Intentionally per-control, not per-objective: SSP authors write one
    coherent paragraph per NIST 800-171 practice. Evidence compliance state
    is tracked per-objective in control_state; this record holds the human- or
    AI-generated narrative that explains HOW the org satisfies the practice.

    See assessment.py design note 3 for the full rationale.
    """

    __tablename__ = "implementation_statement"
    __table_args__ = (
        UniqueConstraint(
            "assessment_id", "control_id", name="uq_implementation_statement_identity"
        ),
        CheckConstraint(
            "status IN ('draft', 'reviewed', 'approved')",
            name="ck_implementation_statement_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id"), index=True
    )
    control_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("control.id"), index=True
    )
    assessment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assessment.id"), nullable=True, index=True
    )
    body: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="draft")
    generation_model: Mapped[str | None] = mapped_column(String(100))
    generation_prompt_version: Mapped[str | None] = mapped_column(String(50))
    # Structured grounding data the AI used: {evidence_ids, product_keys, ...}
    grounded_in: Mapped[dict] = mapped_column(
        JSONB, server_default=text("'{}'::jsonb")
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
