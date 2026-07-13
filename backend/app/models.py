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
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Profile fields (added migration 0011) — all nullable; incomplete profile is valid
    cage_code: Mapped[str | None] = mapped_column(String(10))
    uei: Mapped[str | None] = mapped_column(String(20))
    year_established: Mapped[int | None] = mapped_column(SmallInteger)
    industry: Mapped[str | None] = mapped_column(String(100))
    address_line1: Mapped[str | None] = mapped_column(String(200))
    address_line2: Mapped[str | None] = mapped_column(String(200))
    city: Mapped[str | None] = mapped_column(String(100))
    state_or_province: Mapped[str | None] = mapped_column(String(100))
    postal_code: Mapped[str | None] = mapped_column(String(20))
    country: Mapped[str | None] = mapped_column(String(60), server_default=text("'US'"))
    phone_primary: Mapped[str | None] = mapped_column(String(50))
    phone_secondary: Mapped[str | None] = mapped_column(String(50))
    website: Mapped[str | None] = mapped_column(String(400))
    logo_storage_key: Mapped[str | None] = mapped_column(Text)


class SystemDescription(Base):
    """SSP Section 1 narrative for an org's information system.

    One row per org (enforced by UNIQUE org_id). The record is persistent and
    mutable. Bundle export snapshots current state at generation time so dated
    bundles remain accurate after subsequent edits.

    Structured fields rather than one blob so individual sections render into
    discrete SSP subsections without re-parsing.
    """

    __tablename__ = "system_description"
    __table_args__ = (
        UniqueConstraint("org_id", name="uq_system_description_org"),
        CheckConstraint(
            "system_type IN ('major_application','general_support_system','minor_application')",
            name="ck_system_description_type",
        ),
        CheckConstraint(
            "operational_status IN ('operational','under_development',"
            "'undergoing_major_modification')",
            name="ck_system_description_op_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id", ondelete="CASCADE"), index=True
    )
    system_name: Mapped[str] = mapped_column(String(400))
    system_type: Mapped[str] = mapped_column(String(40))
    operational_status: Mapped[str] = mapped_column(String(40))
    system_description: Mapped[str | None] = mapped_column(Text)
    cui_categories: Mapped[list] = mapped_column(
        JSONB, server_default=text("'[]'::jsonb")
    )
    cui_storage_locations: Mapped[list] = mapped_column(
        JSONB, server_default=text("'[]'::jsonb")
    )
    authorization_boundary_description: Mapped[str | None] = mapped_column(Text)
    external_connections: Mapped[list] = mapped_column(
        JSONB, server_default=text("'[]'::jsonb")
    )
    cui_flow_description: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
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
    # True if this practice is also a CMMC Level 1 (FAR 52.204-21) requirement.
    # Source: CMMC Model v2.0 / 32 CFR Part 170 Final Rule (Oct 2024). Reviewable.
    is_level_1: Mapped[bool] = mapped_column(Boolean, default=False)
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
            "cadence_responsibility IN ('msp','customer','shared')"
            " OR cadence_responsibility IS NULL",
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
        String(25), nullable=False, server_default="'narrative'"
    )
    # Set only for scheduled_operation objectives
    cadence: Mapped[str | None] = mapped_column(String(20), nullable=True)
    cadence_responsibility: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # True until C3PAO has reviewed the type/cadence/responsibility assignment
    is_draft: Mapped[bool] = mapped_column(Boolean, server_default="true")
    guidance: Mapped[str | None] = mapped_column(Text, nullable=True)


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
        CheckConstraint(
            "coverage_basis IN ('customer_system', 'platform_only', 'assists')",
            name="ck_baseline_control_coverage_basis",
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
    # WHERE the vendor's coverage applies: on the customer's CUI systems
    # (customer_system), only on the vendor's own platform (platform_only),
    # or partial/capability-only (assists). platform_only entries are excluded
    # from magic-loop activation — they are vendor self-attestation, not
    # customer-system coverage.
    coverage_basis: Mapped[str] = mapped_column(
        String(20), nullable=False, default="customer_system",
        server_default=text("'customer_system'")
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
    deactivated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
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
            " 'not_applicable', 'inherited', 'needs_review')",
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
    """One collected evidence artifact — either a stored file or a location reference.

    kind='file'      — bytes stored in S3; storage_key is set.
    kind='reference' — a URL or filesystem path; reference_location is set,
                       storage_key is NULL (nothing uploaded).

    An artifact is stored once and can satisfy multiple control objectives via
    EvidenceStateLink (evidence minimization). Any control_state may have
    evidence attached regardless of responsibility value.
    """

    __tablename__ = "evidence"
    __table_args__ = (
        CheckConstraint(
            "artifact_type IN ('screenshot', 'export', 'document', 'link', 'policy')",
            name="ck_evidence_artifact_type",
        ),
        CheckConstraint(
            "kind IN ('file', 'reference')",
            name="ck_evidence_kind",
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
    kind: Mapped[str] = mapped_column(
        String(10), nullable=False, server_default=text("'file'")
    )
    artifact_type: Mapped[str] = mapped_column(String(20))
    storage_key: Mapped[str | None] = mapped_column(Text)
    storage_url: Mapped[str | None] = mapped_column(Text)
    mime_type: Mapped[str | None] = mapped_column(String(100))
    file_size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    reference_location: Mapped[str | None] = mapped_column(Text, nullable=True)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_product_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("product.id"), nullable=True
    )
    sha256_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
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
    is_archived: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    archived_by_product: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("product.id"), nullable=True
    )


class EvidenceTask(Base):
    """Queued evidence collection task created by the magic loop.

    One task per unique artifact (keyed on title+artifact_type) per assessment.
    Links to one or more control_states via EvidenceTaskStateLink — one artifact
    can satisfy multiple objectives (evidence minimization). customer_owns and
    platform_only controls never generate tasks.

    status vocabulary: open → collected → na (not applicable / waived)
    """

    __tablename__ = "evidence_task"
    __table_args__ = (
        CheckConstraint(
            "artifact_type IN ('screenshot', 'export', 'document', 'link', 'policy')",
            name="ck_evidence_task_artifact_type",
        ),
        CheckConstraint(
            "status IN ('open', 'collected', 'na')",
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
    baseline_spec_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("baseline_evidence_spec.id"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(400))
    description: Mapped[str | None] = mapped_column(Text)
    artifact_type: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), default="open")
    collection_session: Mapped[str | None] = mapped_column(String(200))
    due_date: Mapped[date | None] = mapped_column(Date)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_evidence_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("evidence.id"), nullable=True
    )
    is_archived: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class EvidenceTaskStateLink(Base):
    """Many-to-many join: one evidence task can satisfy multiple control objectives.

    Mirrors EvidenceStateLink (evidence artifact → many control_states) but at the
    task level. Created by the magic loop alongside the task; never mutated.
    """

    __tablename__ = "evidence_task_state_link"
    __table_args__ = (
        UniqueConstraint("task_id", "control_state_id", name="uq_evidence_task_state_link"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("evidence_task.id"), index=True
    )
    control_state_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("control_state.id"), index=True
    )
    linked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
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


# ---------------------------------------------------------------------------
# RACI layer: contacts and per-objective responsibility assignments
# ---------------------------------------------------------------------------


class Contact(Base):
    """A named person appearing in RACI matrices and CRM documents.

    Scoped per tenant (org_id).  The same individual at two different orgs is
    two rows — no cross-tenant identity.  Unique on (org_id, email).

    affiliation records the party they represent: the MSP, the customer, a
    sub-MSP (MSSP), a government body, or other.  This drives the smart-default
    logic in the RACI UI: magic-loop-set responsibility ('provider_satisfies' →
    MSP contact, 'customer_owns' → customer contact) pre-suggests a contact
    without auto-assigning one.
    """

    __tablename__ = "contact"
    __table_args__ = (
        UniqueConstraint("org_id", "email", name="uq_contact_org_email"),
        CheckConstraint(
            "affiliation IN ('msp','customer','mssp','government','other')",
            name="ck_contact_affiliation",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(200))
    email: Mapped[str] = mapped_column(String(320))
    phone: Mapped[str | None] = mapped_column(String(50))
    affiliation: Mapped[str] = mapped_column(String(20))
    role_title: Mapped[str | None] = mapped_column(String(200))
    contract_ref: Mapped[str | None] = mapped_column(String(200))
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ContactDocumentationRole(Base):
    """Documentation role tags for a contact.

    Many-to-many: one person can hold multiple roles (e.g. President +
    authorizing_official, IT admin + CUI user).

    These are DOCUMENTATION roles that answer "who appears in which SSP
    section / CRM row?" — not platform-access roles. The link to future
    authenticated user accounts runs auth→contact, not contact→auth:
    the user table will carry a nullable contact_id FK; this table never
    references user.

    Vocabulary matches ck_contact_doc_role CHECK in migration 0013.
    """

    __tablename__ = "contact_documentation_role"
    __table_args__ = (
        UniqueConstraint(
            "contact_id", "role", name="uq_contact_documentation_role"
        ),
        CheckConstraint(
            "role IN ('it_admin','security_officer','system_owner','authorizing_official',"
            "'president','cui_user','assessor','mssp','consultant','other')",
            name="ck_contact_doc_role",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    contact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("contact.id", ondelete="CASCADE"),
        index=True,
    )
    role: Mapped[str] = mapped_column(String(40))
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class RaciAssignment(Base):
    """Per-objective RACI assignment: one contact holds one letter on one control_state.

    Storage is per-objective rather than per-control because the magic loop sets
    responsibility at the objective level (e.g. AC.L2-3.1.1[a]/[b] customer-owned,
    [c] MSP-owned).  The bulk-assign UX (future slice) writes multiple rows at once
    as a convenience; it does not change this table's grain.

    UNIQUE(control_state_id, contact_id, raci_letter) prevents exact duplicates
    while allowing one person to hold both R and A on the same objective.
    """

    __tablename__ = "raci_assignment"
    __table_args__ = (
        UniqueConstraint(
            "control_state_id", "contact_id", "raci_letter",
            name="uq_raci_assignment",
        ),
        CheckConstraint("raci_letter IN ('A','R','C','I')", name="ck_raci_letter"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    control_state_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("control_state.id", ondelete="CASCADE"),
        index=True,
    )
    contact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("contact.id", ondelete="CASCADE"),
        index=True,
    )
    raci_letter: Mapped[str] = mapped_column(String(1))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ImplementationStatement(Base):
    """SSP narrative paragraph for one assessment objective within one assessment.

    Keyed per-objective so each [a]/[b]/[c] sub-requirement can have its own
    paragraph. At SSP-publish time, paragraphs combine per control preserving
    the [a]/[b] labels. Evidence compliance state is tracked separately in
    control_state; this record holds only the human- or AI-generated narrative.
    """

    __tablename__ = "implementation_statement"
    __table_args__ = (
        UniqueConstraint(
            "assessment_id", "objective_id", name="uq_implementation_statement_identity"
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
    objective_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("assessment_objective.id", ondelete="CASCADE"),
        index=True,
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


# ---------------------------------------------------------------------------
# Audit log: append-only compliance event record
# ---------------------------------------------------------------------------


class AuditLog(Base):
    """Append-only record of every meaningful compliance mutation.

    Rows are NEVER updated or deleted by application code. DB-level hardening
    (REVOKE UPDATE, DELETE ON audit_log FROM <app_role>) is a pending
    deployment step — see migration 0010 comments.

    actor = "system" until authentication lands (roadmap item I). The field
    is wired now so real user identity drops in with no schema change.

    Scoped mutations logged here (signal, not firehose):
      control_state.update        — status change (mark-met, needs_review, etc.)
      evidence_state_link.archive — link archived during deactivation
      evidence_task.update        — task status change
      evidence_task.archive       — task archived during deactivation
      org_product.activate        — product activated (magic loop fired)
      org_product.deactivate      — product decommissioned
      implementation_statement.upsert — statement created or updated

    NOT logged: _seed_control_states() bulk insert, internal flush/sync.
    """

    __tablename__ = "audit_log"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    org_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id"), nullable=True, index=True
    )
    actor: Mapped[str] = mapped_column(
        String(200), nullable=False, server_default=text("'system'")
    )
    actor_type: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'system'")
    )
    action: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(60), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    before_value: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    after_value: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    context: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
