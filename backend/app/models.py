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
    Identity,
    Index,
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

    # AC.L2-3.1.1[a]/[c]: how often this org's authorized-users/devices
    # review cycle (review_cycle, migration 0045) runs. 800-171 leaves
    # "periodically" org-defined -- same pattern as config.py's
    # session_idle_minutes for 3.1.11: must stay configurable, and the
    # configured value must appear in the SSP (the review_cycle evidence
    # document states it explicitly; see routers/review_cycles.py).
    # Per-org, not a deployment-wide Settings field like
    # session_idle_minutes -- unlike a session timeout (one technical
    # policy for the whole deployment), review cadence is a contractual
    # fact that genuinely differs client to client. 6 months (twice
    # yearly) is the default absent a configured value -- not mandated by
    # 800-171, chosen as a defensible common cadence for access
    # recertification; the org can set any positive value.
    review_cadence_months: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default=text("6")
    )


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
    # Pinned diagram slots (migration 0029) -- which Evidence row currently
    # satisfies each. Same "pointer to current version" shape as
    # EvidenceTask.completed_evidence_id: replacing a diagram creates a new
    # Evidence row and repoints the FK here, leaving the prior row (and its
    # storage file) untouched. Not a bare storage key like Organization.
    # logo_storage_key -- these deliberately go through the Evidence
    # pipeline per docs/pdf_ssp_template_spec.md's Addendum.
    network_diagram_evidence_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("evidence.id"), nullable=True
    )
    data_flow_diagram_evidence_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("evidence.id"), nullable=True
    )
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

    # Two separate guidance fields (migration 0031) -- never blended into
    # one, per CLAUDE.md's compliance-content discipline. An MSP reading
    # this in front of a C3PAO must always know which sentences are
    # authoritative and which are advisory.
    #
    # official_guidance: verbatim-derived from the real CMMC Assessment
    # Guide Level 2 PDF (scripts/cmmc_guidance/, backend/app/seeds/
    # cmmc_official_guidance.yaml) -- never AI-paraphrased, never
    # hand-typed from memory. official_guidance_source is the citation
    # (practice id + guide version), derived at seed time rather than
    # stored 316 times over -- see seeds/catalog.py.
    official_guidance: Mapped[str | None] = mapped_column(Text, nullable=True)
    official_guidance_source: Mapped[str | None] = mapped_column(String(200), nullable=True)

    # practitioner_notes: AI-drafted (backend/app/seeds/
    # cmmc_practitioner_notes.yaml), advisory only -- what assessors
    # typically examine and what evidence typically demonstrates the
    # objective, never a verdict ("candidates, never auto-met" applies to
    # guidance text the same as it does to control state).
    #
    # Migration 0032 removed the earlier draft/reviewed status concept
    # (practitioner_notes_is_draft) -- deliberately, per Jarrod: "reviewed"
    # implied "now authoritative," but a human-edited note is still one
    # practitioner's opinion, never official CMMC guidance, no matter who
    # touched it last. Nothing here "graduates." The AI-authorship caveat
    # (frontend ControlDrawer's practitioner-notes callout) is permanent
    # and renders unconditionally, regardless of edit state.
    #
    # Provenance replaces status: a note is either untouched (edited_at
    # IS NULL -- still exactly what seed_catalog.py wrote) or edited
    # (edited_at IS NOT NULL, edited_by names who). The AI-origin
    # statement in the UI never disappears even when edited; edit
    # provenance is additional, not a replacement. Editing is msp_admin
    # only -- see routers/objectives.py for why (this table is
    # deployment-wide catalog data, not org-scoped, same tier as D.1's
    # Integrations router).
    #
    # practitioner_notes_original: the AI-generated text, frozen at
    # whichever seed wrote it -- never touched by an edit, only by a
    # reseed of an *untouched* row (see seeds/catalog.py's
    # _should_write_practitioner_notes). Lets a mangled edit be reverted
    # without re-running generation. practitioner_notes_generated_at/
    # _model describe that original generation and likewise never change
    # on edit -- "AI-generated on <date> by <model>" stays true forever,
    # even for an edited note.
    #
    # practitioner_notes_edited_by/_edited_at: NULL/NULL means untouched.
    # edited_by is ON DELETE SET NULL (not a hard requirement to keep the
    # editing user's account alive), but _edited_at is the actual
    # "has this been edited" signal used everywhere (reseed protection,
    # UI provenance) precisely because it can't be nulled out by that
    # cascade -- an edited note stays protected/labeled as edited even if
    # its editor's account is later hard-deleted (ADR 0006); it just loses
    # the ability to name them.
    #
    # NOT currently included in any export (SSP bundle, PDF, etc.) -- the
    # bundle_service.py pipeline has no reference to guidance at all
    # today. Whoever adds that must carry the AI-authorship caveat into
    # the export alongside the text; a warning that only exists in the
    # web UI is worthless once the content is exported and handed to
    # someone else.
    practitioner_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    practitioner_notes_original: Mapped[str | None] = mapped_column(Text, nullable=True)
    practitioner_notes_generated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    practitioner_notes_model: Mapped[str | None] = mapped_column(String(60), nullable=True)
    practitioner_notes_edited_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )
    practitioner_notes_edited_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


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
    # The YAML's source_docs: list, verbatim (migration 0038) -- free-text
    # claims of what the mapping was authored from (e.g. "RocketCyber_SIEM_
    # and_SOC_Baseline.docx (Winsors Labs MSP baseline, v1.0)"). Never
    # replaced by a real upload -- see ProductDocument's own docstring for
    # why the claim and the artifact are deliberately two different things.
    source_docs: Mapped[list] = mapped_column(
        JSONB, server_default=text("'[]'::jsonb")
    )
    is_published: Mapped[bool] = mapped_column(Boolean, default=False)
    # Permanent AI-generation provenance. Set once by the document-ingestion
    # import path and never cleared by a later re-import that omits them --
    # mirrors AssessmentObjective.practitioner_notes_generated_at/_model: this
    # is a permanent authorship record, not a dismissible status. NULL means
    # hand-authored.
    ai_generated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    ai_generated_model: Mapped[str | None] = mapped_column(
        String(80), nullable=True
    )
    # Baseline versioning (roadmap item P, migration 0054): the "latest,
    # potentially publishable" version new activations resolve against.
    # is_published above stays exactly what it always was -- a per-product
    # decision, not moved onto ProductBaselineVersion -- because a reimport
    # always targets a brand new current_version_id and already forces
    # is_published back to False (G.9's existing behavior); there is no
    # workflow in this app for two versions to be independently published
    # at once. Nullable only because a Product row is flushed before its
    # first version can exist; always populated once seeding completes.
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("product_baseline_version.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ProductBaselineVersion(Base):
    """One immutable snapshot of a product's baseline mapping (roadmap item P).

    Every import (CLI seed of baselines/*.yaml, or the G.9 admin upload
    screen) that actually changes the mapping creates a NEW row here plus
    entirely fresh BaselineControl/BaselineEvidenceSpec rows (new UUIDs) --
    it never mutates the previous version's rows. A no-op reimport (content
    identical to the current version) reuses the existing version instead
    of minting an empty one; see seeds/baselines.py:_seed_product.

    This is the fix for the bug this same slice found: seed_baselines used
    to upsert BaselineControl in place by (product_id, control_id), so
    editing or removing a control in the source YAML retroactively changed
    -- or for removal, silently never changed at all -- the justification
    behind every tenant's already-activated control_state. Under
    versioning, OrgProduct.baseline_version_id pins a tenant to the exact
    version active when they activated; a newer version never touches
    them until someone explicitly moves them
    (engine.py:move_org_product_version).

    No RLS -- deployment-wide like Product and BaselineControl themselves
    (see seeds/baselines.py's own module docstring: the baseline library is
    never org-scoped).

    What changed between version N and N+1, when, and who applied it is
    answered from audit_log's existing "product.import" action (its
    after_value carries the new version_number and a control-level
    changed/new/removed summary), not a parallel record kept here --
    version_number is purely the identity/ordering anchor.
    """

    __tablename__ = "product_baseline_version"
    __table_args__ = (
        UniqueConstraint(
            "product_id", "version_number", name="uq_product_baseline_version_identity"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("product.id"), index=True
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class BaselineControl(Base):
    """Per-control entry from one immutable version of a product's baseline.

    Normalized from the YAML loader's ControlEntry: batch entries (one YAML
    row covering a whole control family) become multiple rows here — one per
    control_id — linked by the same batch_group_id for UI grouping.

    Belongs to exactly one ProductBaselineVersion (baseline_version_id) and
    is never mutated or deleted once created — see that class's own
    docstring for why. product_id is kept alongside baseline_version_id
    (denormalized, always equal to the version's own product_id) purely so
    the many existing "every baseline_control for this product regardless
    of version" queries don't all need an extra join; the version is what
    actually scopes a live query (engine.py's magic loop, evidence-task
    fan-out) to the one set of claims a specific OrgProduct is pinned to.
    """

    __tablename__ = "baseline_control"
    __table_args__ = (
        UniqueConstraint(
            "baseline_version_id", "control_id", name="uq_baseline_control_version_identity"
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
    baseline_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("product_baseline_version.id"), index=True
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


class ProductDocument(Base):
    """A real file attached to a baseline library product -- the vendor's
    CRM, the MSP's own baseline doc, KB exports.

    Closes a provenance gap: the YAML's `source_docs` field (Product.role's
    sibling, stored as free text on the baseline file itself, not a
    column here) names a source document with no file behind it anywhere
    in this app. `source_docs_ref` is a free-text pointer to which
    source_docs string this upload corresponds to -- not a real FK, since
    source_docs is an unstructured list with no natural key. Keeps
    source_docs itself untouched: that string is the record of what the
    mapping was authored from, this row is the artifact, not a
    replacement for the claim.

    Deployment-wide like Product itself -- no org_id, no RLS. Storage key
    follows Evidence's convention (models.py:Evidence): id-based path so a
    crafted filename can't path-traverse, original name kept in `title`.

    kind='web_research' (2026-09-17, AI research slice): a page fetched by
    importers/research.py's SSRF-safe fetcher (web_fetch.py) rather than
    uploaded by an admin -- distinguishes "this came from an uploaded file"
    from "this came from a fetched web page" at a glance, per that slice's
    own provenance requirement. For these rows, `title` is the fetched
    page's <title> (falling back to the URL itself), `source_docs_ref`
    holds the exact URL fetched, and `uploaded_at` (the column's existing
    name, unchanged) IS the retrieval timestamp -- no new columns needed,
    the existing shape already carries everything a reader needs to
    reproduce or re-check the claim.
    """

    __tablename__ = "product_document"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('crm', 'baseline_doc', 'kb_export', 'other', 'web_research')",
            name="ck_product_document_kind",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("product.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(400))
    kind: Mapped[str] = mapped_column(String(20), default="other")
    source_docs_ref: Mapped[str | None] = mapped_column(Text)
    storage_key: Mapped[str] = mapped_column(Text)
    mime_type: Mapped[str | None] = mapped_column(String(100))
    file_size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    sha256_hash: Mapped[str | None] = mapped_column(String(64))
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


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
    # Which version of the product's baseline this tenant's activation
    # rests on (roadmap item P, migration 0054). NULL until first
    # activation -- a candidate row hasn't claimed anything yet, so there
    # is nothing to pin. Set once at activation (engine.py:activate_org_
    # product) and left untouched by every later reimport; changes only
    # via the deliberate engine.py:move_org_product_version action.
    baseline_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("product_baseline_version.id"), nullable=True
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

    MULTIPLE CONTRIBUTORS (2026-09-16): which product(s) actually cover this
    objective lives in ControlStateContributor (see that class's own
    docstring), a join to product/baseline_control, not a column here. This
    control_state row used to carry a single sourced_from_product_id --
    retired by this same change, not kept alongside the join as a
    denormalized "primary" pointer. There was no real consumer left that
    needed a single "primary" contributor once every consumer (the UI badge,
    the CRM/bundle export, deactivation's provenance check) was rewritten to
    want either "all contributors" or "is product X a contributor" -- neither
    needs a tiebreak-by-something-arbitrary "primary." Keeping the column
    would have meant inventing a tiebreak rule with no real use, which is
    exactly the "populated but meaningless" trap worth avoiding. See
    docs/roadmap.md's multi-tool-coverage writeup for the full reasoning and
    migration 0052's backfill of every existing sourced_from_product_id value
    into a ControlStateContributor row before the column was dropped.
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
    implementation_notes: Mapped[str | None] = mapped_column(Text)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ControlStateContributor(Base):
    """One product actively contributing coverage to one control_state.

    Real motivating case: an MSP's Kaseya suite (Datto RMM, IT Glue,
    RocketCyber, SaaS Alerts) legitimately overlaps by design on access
    control, logging, and system integrity -- more than one tool can and
    does satisfy the same objective for the same tenant, and Jarrod does
    not want that blocked (see docs/roadmap.md's multi-tool-coverage
    writeup). Before this table, control_state.sourced_from_product_id
    could only ever name one product; activating a second overlapping
    product silently overwrote it (see that same writeup for what this
    looked like in practice before the fix).

    baseline_control_id is NOT NULL and is the real answer to "why does
    this product cover this control" -- it is the specific baseline_control
    row (classification, provider_contribution, customer_action, note)
    engine.py's magic loop matched to reach this state.

    CORRECTION (2026-09-17, roadmap item P / baseline versioning,
    migration 0054): this row's text is NOT live anymore, and was never
    meant to be -- the reasoning above predates baseline versioning and
    was wrong about what "the mapping is edited later" actually means.
    BaselineControl rows are now immutable once created: an edit to a
    product's baseline creates a brand new ProductBaselineVersion with
    entirely new BaselineControl rows, it never mutates the row this FK
    already points at. So baseline_control_id resolving through a live
    join was always going to show the text as it was AT THE VERSION this
    row's OrgProduct.baseline_version_id was pinned to -- there was no
    real "later edit" case for it to reflect, because nothing upstream of
    it can change in place anymore. This is consistent with how every
    other join in this codebase works precisely because nothing here
    snapshots a COPY of the text -- it points at an immutable original,
    which is a stronger guarantee than a copy would be, not a looser one.

    Rows are inserted by engine.py's _run_loop when a product's magic loop
    first claims an objective, and DELETED by deactivate_org_product when
    that product deactivates -- this table names the CURRENT, live set of
    contributors, not a history of who ever contributed. The historical
    fact "product X used to cover this, then stopped" lives in
    control_state_history.change_reason (free text), matching the exact
    existing convention deactivate_org_product already used for
    change_reason="Satisfying tool deactivated: ..." before this table
    existed -- not a new convention invented for this table.
    """

    __tablename__ = "control_state_contributor"
    __table_args__ = (
        UniqueConstraint(
            "control_state_id", "product_id", name="uq_control_state_contributor_identity"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    control_state_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("control_state.id", ondelete="CASCADE"), index=True
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("product.id"), index=True
    )
    baseline_control_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("baseline_control.id"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
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


class SprsSnapshot(Base):
    """Point-in-time SPRS score, recorded on every recompute_sprs() call.

    assessment.sprs_score only ever holds the current value — this table is
    the historical series behind it, written once per recompute. Every
    recompute_sprs() call site (as of this writing: start_assessment,
    activate_org_product, deactivate_org_product, bundle_service's
    snapshot_bundle, AND routers/assessments.py's patch_control_state —
    the last one predates this table, added 2026-07-09, and was missed in
    this table's original design writeup) funnels through this one
    function, so this remains the single hook point for every case even
    though the call-site count was undercounted here at first. Append-only,
    unbounded retention (see G.2 in docs/PLAN-gui-restructure.md for the
    reasoning).

    `seq`, not `computed_at`, is the ordering key — same fix as
    PasswordHistory.seq (migration 0020): Postgres's now()/CURRENT_TIMESTAMP
    returns transaction-start time, not statement-execution time, so two
    recomputes in one transaction (e.g. activate-then-deactivate in the same
    request, or this table's own test suite under the savepoint-per-test
    fixture) get an identical computed_at and an undefined tie order. `seq`
    is a real auto-incrementing identity, always strictly monotonic
    regardless of transaction/clock timing. computed_at is kept for
    display; the index for chronological reads uses seq.
    """

    __tablename__ = "sprs_snapshot"
    __table_args__ = (
        Index("ix_sprs_snapshot_assessment_id_seq", "assessment_id", "seq"),
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
    score: Mapped[int] = mapped_column(SmallInteger)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    seq: Mapped[int] = mapped_column(BigInteger, Identity(always=True), nullable=False)


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
            "artifact_type IN ('screenshot', 'export', 'document', 'link', 'policy', "
            "'network_diagram', 'data_flow_diagram')",
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
    # Provenance -- mirrors scope_entity.source/source_ref (migration 0001).
    # Set once at creation, never rewritten by a later sync: a contact is a
    # curated record, not a cache of its source, so a re-sync's offer to
    # refresh specific fields (routers/contacts.py's Liongard import) never
    # touches these two columns even when it updates name/phone.
    source: Mapped[str] = mapped_column(String(40), default="manual")
    source_ref: Mapped[str | None] = mapped_column(String(400))
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
    # Populated at write time by log_event() from a per-request ContextVar
    # (see audit.py) — NULL for every row written before migration 0022 and
    # for any log_event() call outside an HTTP request (tests, scripts).
    # Never backfillable: the address was never captured for those rows.
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


# ---------------------------------------------------------------------------
# Auth layer: users, sessions, MFA, API tokens
# ---------------------------------------------------------------------------


class User(Base):
    """Authenticated user account.

    login_method='sso'   — identity validated via Microsoft Entra ID.
    login_method='local' — password + TOTP MFA managed by WinGRC.
    login_method='api'   — machine/service account; no password or MFA, has
                            no session/cookie login path, authenticates only
                            via its ApiToken(s). email is a generated,
                            non-deliverable placeholder, not a real mailbox.

    contact_id is nullable: a user can exist without a contact entry (e.g. a
    read-only engineer who doesn't appear in SSP/CRM docs), and a contact can
    exist without a user account (customer POCs, CUI users). The FK runs
    user→contact, never contact→user — enforced by test_contacts_raci.
    """

    __tablename__ = "user"
    __table_args__ = (
        UniqueConstraint("home_org_id", "email", name="uq_user_home_org_id_email"),
        CheckConstraint("login_method IN ('sso','local','api')", name="ck_user_login_method"),
        CheckConstraint(
            "role IN ('msp_admin','consultant_admin','msp_engineer',"
            "'customer_poc','c3pao_assessor')",
            name="ck_user_role",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # Renamed from org_id (migration 0026, ADR 0009 M.3): under multi-org
    # membership, a user's accessible orgs come from org_membership, not
    # this column — this is the anchor RLS uses for the account-mechanics
    # tables (user, user_session, mfa_backup_code, api_token,
    # password_history) and the audit-log anchor for account-level events
    # that aren't scoped to any particular business org (login, password
    # change, ...). It is not "the org this user has access to" anymore.
    home_org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id", ondelete="CASCADE"), index=True
    )
    contact_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("contact.id", ondelete="SET NULL"), nullable=True
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    entra_oid: Mapped[str | None] = mapped_column(String(100), unique=True, nullable=True)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    login_method: Mapped[str] = mapped_column(
        String(10), nullable=False, server_default=text("'local'")
    )
    role: Mapped[str] = mapped_column(String(40), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    # Local auth
    password_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    invite_token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    invite_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # MFA
    totp_secret: Mapped[str | None] = mapped_column(Text, nullable=True)
    mfa_enrolled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    # Lockout (local accounts; ignored for SSO)
    failed_login_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    lockout_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    requires_admin_reset: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # ADR 0006: permanent, irreversible anonymization marker — distinct from
    # is_active, which an admin can always flip back. deleted_at is never
    # unset once written; the UI must tell the two states apart (never offer
    # "reactivate" once this is set).
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class UserSession(Base):
    """Server-side session record. Raw token is stored as SHA-256 hash only.

    org_id is denormalized from user.home_org_id so the session-resolution
    function can set app.current_org before any RLS-gated query runs.
    """

    __tablename__ = "user_session"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("user.id", ondelete="CASCADE"), nullable=False
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # 3.1.11 idle timeout heartbeat; throttle-updated (see auth._resolve_session),
    # not written on every request.
    last_activity_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class MfaBackupCode(Base):
    """Single-use TOTP recovery code. Shown once at enrollment; hash stored."""

    __tablename__ = "mfa_backup_code"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("user.id", ondelete="CASCADE"),
        nullable=False, index=True
    )
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PasswordHistory(Base):
    """Prior password hash, kept only to reject reuse on set/reset.

    Never read on the login path — only on /auth/set-password, which is
    already a deliberately expensive PBKDF2-heavy operation (see
    auth.check_password_reuse).

    `seq` (added in migration 0020), not `created_at`, is the ordering key
    both auth.check_password_reuse and auth.record_password sort by.
    `created_at` uses Postgres's now()/CURRENT_TIMESTAMP, which returns
    transaction-start time, not statement-execution time — multiple rows
    inserted in one transaction (or, in principle, two transactions that
    start within the same microsecond) get an identical value and an
    undefined tie order. `seq` is a real auto-incrementing identity, so it
    is always strictly monotonic regardless of transaction/clock timing.
    See migration 0020 for the incident this fixes.
    """

    __tablename__ = "password_history"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("user.id", ondelete="CASCADE"),
        nullable=False, index=True
    )
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    seq: Mapped[int] = mapped_column(BigInteger, Identity(always=True), nullable=False)


class ApiToken(Base):
    """Machine-to-machine Bearer token. Raw value shown once at creation.

    Role is set explicitly at creation — the token may have a lower privilege
    than the issuing user's own role. An msp_admin cannot issue a token with
    a role higher than their own.
    """

    __tablename__ = "api_token"
    __table_args__ = (
        CheckConstraint(
            "role IN ('msp_admin','consultant_admin','msp_engineer',"
            "'customer_poc','c3pao_assessor')",
            name="ck_api_token_role",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id", ondelete="CASCADE"),
        nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("user.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    role: Mapped[str] = mapped_column(String(40), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class OrgMembership(Base):
    """One user's access grant to one org, with a role scoped to that grant.

    See docs/adr/0009-multi-org-user-access.md. **This table is
    authoritative for access as of M.4** (landed 2026-08-17, verified on
    wl-util-1): `auth.py:require_org_access()` reads this table directly,
    403s if no row exists, and takes the effective role from the
    membership it finds. `User.home_org_id` remains authoritative for a
    different, narrower job — session resolution's default
    `app.current_org` before a specific route's `org_id` is known, and the
    audit-log anchor for account-level events with no org in the URL —
    but not for deciding which orgs a user can reach. `User.role` is no
    longer authoritative for anything: `_role_for_membership` (auth.py)
    falls back to it only if a membership row is unexpectedly missing,
    logging a warning when that happens, since every user-creation path
    (bootstrap-admin, invite_user, create_api_user) provisions one.
    (An earlier version of this docstring said the opposite — that
    `require_org_access()` didn't consult this table yet. That was
    correct when M.2 landed and became stale once M.4 shipped without
    this comment being updated to match; corrected here rather than left
    for the next reader to discover by tracing the code themselves.)
    Auto-provisioning (routers/orgs.py's create_org(), routers/users.py's
    invite_user(), via org_membership.py) keeps every existing
    msp_admin/msp_engineer granted into every org. Role travels with the
    membership, not the person: the same user can hold a different role
    on each org they're a member of.

    `consultant_admin` (migration 0034) is deliberately NOT in
    org_membership.py's `_AUTO_PROVISION_ROLES` — it's a per-engagement
    role for an external party, so membership must be granted explicitly
    per org (the same invite_user() path every customer_poc uses), never
    auto-fanned-out to every client the MSP serves the way msp_admin/
    msp_engineer are.

    RLS is enabled (`org_membership_tenant_isolation`, same single-org
    `app.current_org` pattern as every other org-scoped table), and as of
    M.4 an ordinary per-org request (e.g. `require_org_access`'s own
    membership lookup, or M.8's grant/revoke endpoints) genuinely runs
    under it — `app.current_org` is set to the one org the request is
    about, RLS matches the query to it, no bypass involved. The
    cross-org reads/writes auto-provisioning (and M.7's directory) need
    — "every existing MSP user," "grant into every other org," "every
    membership row across every org" — cannot be expressed as any single
    value of
    app.current_org at all; those go through SECURITY DEFINER functions
    (auth.msp_role_users/auth.grant_org_membership, migration 0025),
    matching auth.resolve_session/find_user_for_login's existing
    precedent, not a plain RLS-scoped query. See ADR 0009's "System-level
    cross-org operations" subsection for the incident that established
    this pattern.
    """

    __tablename__ = "org_membership"
    __table_args__ = (
        UniqueConstraint("user_id", "org_id", name="uq_org_membership_user_org"),
        CheckConstraint(
            "role IN ('msp_admin','consultant_admin','msp_engineer',"
            "'customer_poc','c3pao_assessor')",
            name="ck_org_membership_role",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("user.id", ondelete="CASCADE"), index=True
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(40), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class DeploymentSettings(Base):
    """Singleton anchor: which org is this deployment's own MSP org.

    See docs/adr/0009-multi-org-user-access.md's Boundary section. Exists
    purely as an integrity constraint, not an access-control input — no
    request-time authorization check consults this table. Its only job is
    naming, structurally, the ADR 0005 assumption ("one MSP per
    deployment") that M.2's auto-provisioning rule depends on, so a future
    multi-MSP deployment model has to deliberately confront and redesign
    this table rather than silently outgrow an assumption that only ever
    lived in prose.

    Populated once by `manage.py bootstrap-admin` at first run. No API
    endpoint ever writes to it — changing `msp_org_id` after bootstrap is a
    deliberate DBA/migration action, never a runtime one. `id` is pinned to
    `1` by the CHECK constraint, enforcing exactly one row can ever exist.
    """

    __tablename__ = "deployment_settings"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_deployment_settings_singleton"),
    )

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, default=1)
    msp_org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class IntegrationConnection(Base):
    """Deployment-wide connector credential (D.1 — Liongard first).

    Deliberately NOT org-scoped, same tier as DeploymentSettings above (and
    like Product/Framework, shared reference-ish data rather than per-tenant
    data): Liongard's own tenancy model is one API credential per MSP
    instance (a Liongard user account, not a per-client one), with many
    per-client "Environments" underneath — confirmed against Liongard's own
    docs before this table was designed, see ROADMAP.md's D.1 section. So
    there is one row per connector *type* per WinGRC deployment. Mapping a
    WinGRC org to a Liongard Environment id is D.2's concern — org-scoped,
    not built here — and will be a separate table, not a column on this one.

    config holds non-secret, connector-specific settings (e.g. Liongard's
    instance_url) as plain JSONB — safe to return over the API as-is.
    encrypted_credential holds everything secret (e.g. Liongard's access
    key id + secret, bundled together as one encrypted JSON blob) via
    crypto.py's Fernet-based encrypt_credential/decrypt_credential — never
    plaintext at rest, and never returned over the API at all (write-only:
    see routers/integrations.py's response schema, which carries only
    credential_hint). credential_key_version records which configured key
    label encrypted this row, for crypto.py's rotation story.

    last_test_ok/last_test_error/last_tested_at are the connection-test
    result surfaced to the D.1 screen ("connected / never connected / last
    attempt failed", with the real error). No sync history here — that's
    D.2/D.3's concern once there's an actual sync to have history of.
    """

    __tablename__ = "integration_connection"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    connector_key: Mapped[str] = mapped_column(String(60), unique=True)
    config: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    encrypted_credential: Mapped[str | None] = mapped_column(Text, nullable=True)
    credential_key_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # Last 4 chars of the credential secret, for display only — e.g. "…a91c".
    credential_hint: Mapped[str | None] = mapped_column(String(4), nullable=True)
    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_test_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    last_test_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), onupdate=func.now()
    )


class OrgLiongardEnvironment(Base):
    """D.2 -- maps one WinGRC org to one Liongard Environment.

    Org-scoped (unlike IntegrationConnection above, which is deployment-wide
    -- the credential is one MSP-wide Liongard account, but *which*
    Environment under that account corresponds to *which* WinGRC org is
    per-tenant data, not shared reference data). D.1 left this as D.2's
    concern -- "a separate, org-scoped table, not a column" on
    integration_connection -- see that model's own docstring.

    1:1 today (unique org_id): an org syncing from more than one Liongard
    Environment isn't a case this connector needs to support yet. Extend to
    many-to-one if that changes rather than guessing now.

    liongard_environment_name is a display cache only, refreshed whenever
    the mapping is (re)set via connectors/liongard.py:list_environments()
    -- never the source of truth, which is liongard_environment_id plus
    Liongard's own Environment record.

    liongard_environment_id is ALSO unique (migration 0050) -- decided
    2026-09-16: a Liongard Environment already represents one client's
    infrastructure, so two WinGRC orgs sharing one would silently pull the
    same devices/identities into two separate scope graphs, i.e. one
    client's environment leaking into another org's CUI-boundary
    denominator. In a compliance tool that's a worse failure mode than in
    a general asset-management tool, so this is enforced, not left
    permissive on the chance some legitimate sharing case exists -- none
    was found. routers/scope.py's PUT endpoint checks this before the DB
    does, for a real error message; the DB constraint is the backstop.
    **The migration itself is written defensively** (checks for existing
    collisions before adding the constraint, skips it and logs rather than
    failing the deploy if any are found) -- see that migration's own
    docstring for why, and its result on THIS deploy specifically.
    """

    __tablename__ = "org_liongard_environment"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), unique=True, index=True)
    # NOT declared as unique=True at the column level -- migration 0050
    # adds the DB constraint conditionally (see its own docstring), so a
    # column-level unique=True here would claim a guarantee the schema
    # might not actually be enforcing on a given deployment. The intent is
    # still recorded here, in prose, for anyone reading the model.
    liongard_environment_id: Mapped[int] = mapped_column(Integer, nullable=False)
    liongard_environment_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), onupdate=func.now()
    )


class JobRun(Base):
    """One execution attempt of a scheduled job (scheduler.py) -- the
    outbound-email slice's sibling infrastructure prerequisite for D.3.

    This is a compliance record, not just worker plumbing: D.3's daily
    Liongard sync means the scope boundary can change with no human
    initiating it, and "when did this last run, did it succeed, what did
    it produce" is exactly the kind of thing a C3PAO can ask about. So
    this table follows the same discipline as `audit_log`/`sprs_snapshot`
    elsewhere in this codebase -- append-only, never retroactively
    rewritten. A row transitions running -> succeeded/failed exactly once
    and is never updated again after finished_at is set; a later
    successful run is a NEW row, not an edit of the failed one. Retention
    is deliberately unbounded here (no scheduled purge) -- silently
    truncating this history would be exactly the wrong failure mode for
    a compliance artifact.

    Deliberately separate from audit_log, not a new audit_log action type:
    audit_log records *actor-initiated* compliance mutations (a human or
    an authenticated API caller did X), and a cron tick has no actor. A
    job whose body changes something audit-worthy (a future D.3 sync
    writing scope_entity, once that lands through the dry-run/review/apply
    path) still writes its own ordinary audit_log row for that change --
    the two tables cross-reference by time/job_name, not by a shared key,
    since nothing here needs a hard FK into audit_log or vice versa.

    Deployment-wide, like IntegrationConnection/DeploymentSettings above --
    no org_id, no RLS. A job that touches per-org data must set
    app.current_org itself for that portion of its work (see scheduler.py's
    module docstring for the full RLS decision); this table records that
    the job ran, not which org(s) it touched.
    """

    __tablename__ = "job_run"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # No index=True here -- the migration's composite
    # ix_job_run_job_name_created_at (job_name, created_at DESC) already
    # serves a plain job_name lookup as its leading column; a separate
    # single-column index would be redundant.
    job_name: Mapped[str] = mapped_column(String(100), nullable=False)
    # When this run was due, per the registry's interval -- distinct from
    # started_at (when it actually acquired the lock and began), which can
    # lag scheduled_for under load.
    scheduled_for: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # NULL while status='running'; set exactly once, at the same time
    # status moves to its terminal value.
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)  # running|succeeded|failed
    # Small, job-defined summary of what the run produced (e.g.
    # {"expired_count": 3}) -- "a reference to whatever it produced," per
    # this slice's own spec. Never large/unbounded data; a job that
    # produces something substantial (e.g. a future dry-run diff) belongs
    # in its own table, referenced here by id, not inlined.
    result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # Populated on failure only; never cleared by a later run (that's a
    # new row). Exception str(), not a full traceback -- enough to explain
    # what happened without risking a stray secret from deep in a
    # traceback ending up here (see this codebase's existing discipline
    # around never logging credentials/tokens).
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # hostname:pid of whichever worker process ran this -- lets an
    # operator with multiple worker replicas tell them apart; not a
    # foreign key to anything.
    worker_id: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('running', 'succeeded', 'failed')", name="ck_job_run_status"
        ),
    )


class SprsSubmission(Base):
    """Record of a human action in SPRS (the DoD's own scoring system) --
    what was actually FILED, never to be confused with what
    `assessment.sprs_score`/`sprs_snapshot` say WinGRC *computed*. Three
    distinct facts, kept in three distinct places: what WinGRC computed
    (`sprs_snapshot`), what was filed with SPRS (this table), and an
    assessment being complete in WinGRC (`Assessment.status`).
    Completing an assessment (status -> 'submitted',
    engine.py:complete_assessment) never creates a row here -- it only
    prompts a human to, since finishing an assessment in WinGRC files
    nothing with the DoD; the customer still logs into SPRS separately.

    Org-scoped, NOT assessment-scoped: assessment_id is nullable because
    the first submission is typically captured at onboarding, before any
    assessment exists in this system at all (a customer's prior SPRS
    score from a spreadsheet). Populated only when a submission follows
    a completion here.

    `score` is a plain value, never a live reference to
    `assessment.sprs_score`/`sprs_snapshot` -- it records what was
    actually filed. A later recompute of the live score must never
    retroactively change what this row claims was submitted to the DoD,
    and the onboarding case's score may not correspond to anything this
    app would ever calculate (it came from a spreadsheet, possibly under
    a different scoring methodology or scope boundary).

    Append-only, same discipline as `sprs_snapshot`/`audit_log`: once
    inserted, `score`/`submitted_date`/`submitted_by_*`/`note` are never
    updated -- "the score we filed last March" must not become editable.
    A correction is a NEW row, not an edit. `voided_at`/`voided_reason`
    are the one exception, and a narrow one: a single one-way annotation
    (NULL -> set, never changed again, never unset) marking a row as
    superseded by a later correction -- it says "this row is no longer
    authoritative," it does not change what the row claims was filed.
    The "current" submission for the reminder clock and for display is
    the most recent (`submitted_date` desc, `created_at` desc as
    tiebreak) row with `voided_at IS NULL`.

    Contact deletion must not destroy this record -- checked against the
    contact-lifecycle precedent (docs/roadmap.md's RACI copy-forward
    entry) before reusing its FK shape rather than assuming it fits:
    `RaciAssignment.contact_id` is `ON DELETE CASCADE`, correct there
    because RACI is a live "who is responsible now" fact that should
    disappear with the person. A submission record is the opposite --
    "who filed this in March 2026" must remain true and readable forever,
    including after that person leaves and their `contact` row is
    hard-deleted (contact has no soft-delete; see that same roadmap
    entry). So `submitted_by_contact_id` is `ON DELETE SET NULL` (matching
    `User.contact_id`'s existing precedent, not RaciAssignment's), and the
    submitter's name/email are denormalized into `submitted_by_name`/
    `submitted_by_email` at write time -- the same "store a value, not a
    live reference" principle this table already applies to `score`,
    applied to identity instead of score. The row stays fully meaningful
    after the contact is gone; only the live link (for e.g. clicking
    through to the contact's current record) goes stale.
    """

    __tablename__ = "sprs_submission"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id"), index=True
    )
    # Nullable: populated only when this submission follows a completion
    # in this system (engine.py:complete_assessment's prompt flow) --
    # onboarding-captured history predates any assessment here.
    assessment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assessment.id"), nullable=True, index=True
    )
    score: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    submitted_date: Mapped[date] = mapped_column(Date, nullable=False)
    submitted_by_contact_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("contact.id", ondelete="SET NULL"), nullable=True
    )
    # Denormalized submitter identity -- survives the contact row being
    # hard-deleted. See class docstring.
    submitted_by_name: Mapped[str] = mapped_column(String(200), nullable=False)
    submitted_by_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Who/when this row was RECORDED IN WINGRC -- distinct from
    # submitted_by_*/submitted_date, which describe the SPRS filing
    # itself and may have been entered by an MSP engineer on the
    # customer's behalf, well after the fact (the onboarding case).
    created_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # One-way annotation only -- see class docstring. NULL means current/
    # authoritative (subject to the "most recent wins" rule above); once
    # set, never cleared or re-set.
    voided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    voided_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class SprsReminderLog(Base):
    """One row per annual-reminder email actually sent for one org's
    current SPRS submission -- the idempotency record scheduler.py's
    sprs_annual_reminder job checks before sending, so a due reminder
    fires exactly once per submission's anniversary rather than every
    tick after the due date passes. A new submission (a new row, new id)
    makes the org eligible for a fresh reminder on its own new
    anniversary -- this table is keyed to the submission it fired for,
    not to the org alone, so that reset happens automatically.

    Deployment-internal bookkeeping, not a user-facing record -- no
    audit_log entry, no UI. Never updated after insert.
    """

    __tablename__ = "sprs_reminder_log"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id"), index=True
    )
    submission_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sprs_submission.id", ondelete="CASCADE"),
        unique=True,
        index=True,
    )
    sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


# ---------------------------------------------------------------------------
# Periodic review & attestation (D.3's first half -- ROADMAP.md D.3; the
# daily Liongard sync is the second half, deliberately not built here).
#
# The point of this feature: an assessor-showable record that the MSP and
# the client periodically reviewed the org's authorized users and devices
# and signed off -- replacing a meeting Jarrod currently schedules and
# minutes by hand. That bar means every table below exists to answer,
# durably: who approved, when, and exactly what they were shown -- and to
# prove that hasn't drifted since. Same "immutable snapshot" discipline as
# BundleSnapshot/bundle_service.py: a cycle's item list is copied at open
# time, never re-derived from live scope_entity rows afterward.
#
# Non-response is itself evidence (see ReviewCycleReviewer/
# ReviewCycleReminderLog) -- a cycle nobody answers still closes into a
# durable record of who was asked, when reminders went out, and who never
# responded. That may be the single most valuable output of this feature:
# it is the thing Jarrod currently has nothing for.
#
# Attesting confirms the list; it never mutates scope. See
# ReviewCycleFlag -- a reviewer who thinks an item is wrong produces a
# flag for MSP follow-up, never an automatic scope_entity change. Actually
# changing scope still goes through the existing reconcile()/dry-run/apply
# path, unmodified and untouched by anything in this feature.
#
# "Non-response is evidence" only holds if the request was actually
# delivered -- a real bug on the first live deployment (wl-util-1,
# 2026-09-13, a cycle opened with no SMTP credential and no
# WINGRC_PUBLIC_URL configured) proved that wrong: every reviewer closed
# as 'no_response', asserting two named people failed to respond to a
# request that was never sent. ReviewCycleReviewer.notified_at/
# notification_error now track delivery per reviewer (see that class's
# own docstring), and close_cycle distinguishes 'no_response' (asked,
# didn't answer) from 'not_notified' (never successfully asked) --
# and, at the cycle level, 'closed_unattested' (a review was attempted)
# from 'closed_undeliverable' (nobody could be reached at all).
# ---------------------------------------------------------------------------


class ReviewCycle(Base):
    """One periodic review-and-attestation cycle for one org's authorized
    users and devices -- both subject types in a single cycle (not two),
    because they map to the same control (AC.L2-3.1.1: [a] users
    identified, [c] devices identified -- see routers/review_cycles.py's
    own docstring for how that mapping was derived, not recalled) and
    because a single combined sign-off matches the real workflow this
    replaces: one review meeting covering the whole authorized-entity
    list, not two separate ones. `subject_type` lives on ReviewCycleItem,
    not here, specifically so a third subject type is addable later
    without a new cycle concept -- not built now, no third type exists.

    Append-only once opened, like `audit_log`/`sprs_snapshot`: a cycle is
    never edited after the fact. status only ever moves forward
    (open -> completed | closed_unattested | closed_undeliverable), and a
    correction is a new cycle, not a reopened or rewritten old one.

    closed_undeliverable: distinct from closed_unattested. Added after a
    live bug (wl-util-1, 2026-09-13): closed_unattested's own wording
    ("closed without full attestation") implies a review was attempted
    and reviewers simply didn't answer -- true when at least one reviewer
    was ever successfully notified, false when NONE were (e.g. no SMTP
    credential and no WINGRC_PUBLIC_URL configured on the deployment). A
    cycle where nobody could be reached had no review attempt to fail to
    complete, so it gets its own honest terminal status rather than
    reusing wording that asserts the opposite. See
    review_cycles.close_cycle.

    cadence_months is a COPY of Organization.review_cadence_months at
    the moment this cycle opened -- not a live read -- so a later cadence
    change doesn't retroactively change what this cycle's own due_at meant
    when reviewers were asked to respond by it.
    """

    __tablename__ = "review_cycle"
    __table_args__ = (
        CheckConstraint(
            "status IN ('open', 'completed', 'closed_unattested', 'closed_undeliverable')",
            name="ck_review_cycle_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id"), index=True
    )
    opened_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'open'"))
    cadence_months: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    # 'scheduler' for an auto-opened cycle, or a user id string for one an
    # MSP staff member opened manually (POST /orgs/{org_id}/review-cycles).
    opened_by: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ReviewCycleItem(Base):
    """One entity as it looked at the moment its cycle opened -- the
    immutable snapshot §0/§2 require. attributes/natural_key/
    scope_category are a COPY of the source scope_entity row, taken once
    at insert and never updated. scope_entity_id is a nullable, ON DELETE
    SET NULL provenance pointer for traceability only -- rendering the
    approval page or any evidence output from this table must read the
    copied columns here, never join through to the live scope_entity row.
    """

    __tablename__ = "review_cycle_item"
    __table_args__ = (
        CheckConstraint("subject_type IN ('user', 'device')", name="ck_review_cycle_item_subject"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    cycle_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("review_cycle.id", ondelete="CASCADE"), index=True
    )
    # Denormalized for RLS, matching sprs_snapshot/finding's precedent.
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id"), index=True
    )
    subject_type: Mapped[str] = mapped_column(String(10), nullable=False)
    scope_entity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("scope_entity.id", ondelete="SET NULL"), nullable=True
    )
    natural_key: Mapped[str] = mapped_column(String(400), nullable=False)
    scope_category: Mapped[str | None] = mapped_column(String(60), nullable=True)
    attributes: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ReviewCycleReviewer(Base):
    """One person asked to review one cycle, and -- once status='attested'
    -- their attestation record: who, when, and any comment. `user_id` is
    `ON DELETE SET NULL` (matching `SprsSubmission.submitted_by_contact_id`'s
    precedent from the same session) with `reviewer_name`/`reviewer_email`
    denormalized at request time, so the record of who was asked and who
    attested survives that user account later being anonymized or
    hard-deleted (ADR 0006).

    status is the per-reviewer state machine: requested -> viewed (first
    GET of the cycle by this reviewer) -> attested (POST attest). A
    reviewer who never attests before the cycle closes is left at
    'requested' or 'viewed' -- NOT silently deleted or hidden; see
    engine-level close logic, which stamps every still-open reviewer row
    to 'no_response' (if ever actually notified) or 'not_notified' (if
    not) at close time rather than leaving an ambiguous partial state.
    That non-response set, together with ReviewCycleReminderLog, is the
    non-response evidence §3 calls the most valuable part of this
    feature.

    notified_at / notification_error: delivery tracking, added after a
    live-deployment bug (wl-util-1, 2026-09-13) where a cycle with no
    SMTP credential and no WINGRC_PUBLIC_URL configured still closed both
    of its reviewers as 'no_response' -- an assertion that two named
    people failed to respond to a request that was never delivered.
    `email_service.send()` already returns a result the caller previously
    discarded (review_cycle_open) or consulted only for reminder-log
    idempotency, never persisted (review_cycle_sweep). notified_at is set
    the first time a notification to this reviewer succeeds and is never
    cleared afterward -- it answers "were they ever actually reached,"
    not "was the most recent attempt successful." notification_error
    holds the most recent failure's admin-facing reason (unconfigured vs.
    provider rejection are different operator problems) and is cleared to
    NULL once notified_at is set. See scheduler.py's _notify_reviewer.
    """

    __tablename__ = "review_cycle_reviewer"
    __table_args__ = (
        CheckConstraint("reviewer_side IN ('msp', 'client')", name="ck_review_cycle_reviewer_side"),
        CheckConstraint(
            "status IN ('requested', 'viewed', 'attested', 'no_response', 'not_notified')",
            name="ck_review_cycle_reviewer_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    cycle_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("review_cycle.id", ondelete="CASCADE"), index=True
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id"), index=True
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )
    reviewer_name: Mapped[str] = mapped_column(String(200), nullable=False)
    reviewer_email: Mapped[str] = mapped_column(String(320), nullable=False)
    reviewer_side: Mapped[str] = mapped_column(String(10), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'requested'")
    )
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    viewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notification_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class ReviewCycleReminderLog(Base):
    """Idempotency record for reminder emails -- one row per (reviewer,
    reminder number) actually sent, so scheduler.py's review_cycle_
    reminders job never re-sends the same reminder on a later tick.
    Mirrors sprs_reminder_log's exact shape/reasoning from the prior
    slice, keyed one level finer (per reviewer, not just per cycle) since
    each reviewer on a cycle gets their own reminder schedule based on
    their own response state.
    """

    __tablename__ = "review_cycle_reminder_log"
    __table_args__ = (
        UniqueConstraint("reviewer_id", "reminder_number", name="uq_review_cycle_reminder"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    cycle_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("review_cycle.id", ondelete="CASCADE"), index=True
    )
    reviewer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("review_cycle_reviewer.id", ondelete="CASCADE"), index=True
    )
    reminder_number: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ReviewCycleFlag(Base):
    """A reviewer's "this doesn't look right" on one item -- MSP
    follow-up, never a scope mutation (§4's hard boundary: approving
    attests, it does not change scope; the same is true in the other
    direction -- disputing an item doesn't change scope either, only
    routes it for a human to actually reconcile through the existing
    dry-run -> review -> apply path). v1 is a flag with a reason and a
    resolution note -- not a ticket queue, not an automatic change
    request.
    """

    __tablename__ = "review_cycle_flag"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    cycle_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("review_cycle_item.id", ondelete="CASCADE"), index=True
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id"), index=True
    )
    flagged_by_reviewer_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("review_cycle_reviewer.id", ondelete="SET NULL"),
        nullable=True,
    )
    flagged_by_name: Mapped[str] = mapped_column(String(200), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_note: Mapped[str | None] = mapped_column(Text, nullable=True)


# ---------------------------------------------------------------------------
# D.3 second half: daily Liongard sync + asset/user onboarding approval
# ---------------------------------------------------------------------------


class LiongardSyncResult(Base):
    """One Liongard pull-and-reconcile attempt for one org, persisted --
    the structural gap D.3's own spec didn't mention: before this, a
    dry-run's diff only ever existed in an HTTP response (routers/scope.py:
    liongard_sync_dry_run), carried between dry-run and apply by the
    browser. A scheduled job has no browser, so "the job produces a
    dry-run for review" needed something durable to produce, and this
    table -- plus LiongardSyncResultChange below -- is it.

    Same discipline as sprs_snapshot/audit_log/BundleSnapshot: append-only,
    never re-rendered from live data once written. The approval page reads
    LiongardSyncResultChange rows only, never re-pulls -- see
    liongard_sync.py's own module docstring for why that matters.

    status:
      pending_review    -- has at least one change_type='new' row still
                            awaiting a decision (see LiongardSyncResultChange
                            .resolution).
      reviewed          -- every 'new' row has been approved or rejected.
      superseded        -- a later sync for this same org completed before
                            this one was fully reviewed. Never deleted --
                            still visible, greyed out, so a reviewer who
                            opens it knows why (§0's explicit requirement:
                            "that behavior is visible to the reviewer").
      no_changes        -- the pull found nothing new/changed/missing;
                            auto-closed, nothing to review.

    Superseding, not merging or queueing, when a fresh sync lands before
    the last is reviewed -- chosen because merging risks blending two
    different observation times into one approval record (breaks the
    point-in-time discipline every other row above uses this same
    reasoning for), and queueing means a reviewer works through a backlog
    of diffs against a device that's since changed again, most of it
    noise by the time they get to it. "What's true as of the most recent
    pull" is the more honest thing to hand a reviewer than an accumulating
    queue.

    job_run_id is nullable: a manual "Sync now" (still the existing HTTP
    dry-run path) can also produce one of these, not only the scheduled
    job -- see liongard_sync.py:persist_sync_result's own docstring for
    why manual and scheduled syncs share one write path.
    """

    __tablename__ = "liongard_sync_result"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending_review', 'reviewed', 'superseded', 'no_changes')",
            name="ck_liongard_sync_result_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id"), index=True
    )
    job_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("job_run.id", ondelete="SET NULL"), nullable=True
    )
    # Denormalized copy of OrgLiongardEnvironment at pull time -- the
    # mapping can change later; this row records which Environment this
    # specific pull actually came from, same reasoning
    # ReviewCycle.cadence_months copies rather than reads live.
    liongard_environment_id: Mapped[int] = mapped_column(nullable=False)
    liongard_environment_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    pulled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'pending_review'")
    )
    # Small counts only (new/changed/missing per entity type) -- never the
    # full diff; that's LiongardSyncResultChange's job. Matches JobSpec's
    # own "small JSON-serializable summary" convention for job_run.result.
    summary: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    warnings: Mapped[list] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class LiongardSyncResultChange(Base):
    """One entity's diff row within a LiongardSyncResult -- the same shape
    routers/scope.py's ScopeChangeOut/ApplyIn already use for the
    interactive dry-run -> apply flow, deliberately: `incoming` is a full
    frozen CanonicalEntity snapshot (not just the diffed fields), so
    approving a row can feed the *existing* apply machinery
    (repo.upsert-equivalent) without a second write path being invented
    for connector-sourced changes specifically.

    Only change_type='new' rows go through the resolution/approval
    workflow below (D.3 is an ONBOARDING approval gate, not a general
    change-review tool). 'changed'/'missing' rows are persisted here for
    visibility only -- a reviewer sees the whole pull, same as the
    interactive dry-run always has -- and are actioned, if at all, through
    the existing manual dry-run -> apply screen, not a second apply
    mechanism built here. resolution stays NULL for those; only 'new' rows
    ever get 'approved'/'rejected'.
    """

    __tablename__ = "liongard_sync_result_change"
    __table_args__ = (
        CheckConstraint(
            "change_type IN ('new', 'changed', 'missing')",
            name="ck_liongard_sync_result_change_type",
        ),
        CheckConstraint(
            "resolution IN ('pending', 'approved', 'rejected')",
            name="ck_liongard_sync_result_change_resolution",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    sync_result_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("liongard_sync_result.id", ondelete="CASCADE"), index=True
    )
    # Denormalized from liongard_sync_result.org_id -- RLS policy needs it
    # directly on this table, same convention as ControlState.org_id/
    # review_cycle_item.org_id (every RLS-enabled child table in this
    # codebase carries its own copy, never relies on a join for the
    # policy).
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id"), index=True
    )
    change_type: Mapped[str] = mapped_column(String(10), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(40), nullable=False)
    natural_key: Mapped[str] = mapped_column(String(400), nullable=False)
    field_diffs: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    # The full CanonicalEntity this change would write, frozen at pull
    # time -- null only for change_type='missing' (nothing incoming),
    # matching ScopeChangeOut's own nullable `incoming`. Already carries
    # status='pending_approval' for a brand-new entity -- see
    # liongard_sync.py:pull_and_reconcile's own docstring for where that's
    # set.
    incoming: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    warnings: Mapped[list] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    # Only meaningful for change_type='new' -- see class docstring.
    resolution: Mapped[str | None] = mapped_column(
        String(10), nullable=True, server_default=text("'pending'")
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_by: Mapped[str | None] = mapped_column(String(100), nullable=True)


class LiongardSyncNotification(Base):
    """One notification attempt to one recipient for one LiongardSyncResult
    -- same notified_at/notification_error tracking shape as
    ReviewCycleReviewer (added there after a live wl-util-1 bug where a
    failed/never-attempted send left no trace; not repeating that gap
    here). One row per org contact holding security_officer or it_admin
    (models.py:ContactDocumentationRole), one email per org per sync --
    a digest naming the pending count and a link, never one email per
    device (§3's notification-volume concern).

    contact_id is nullable (ON DELETE SET NULL) so a later-deleted contact
    doesn't take this notification record with it -- recipient_name/
    recipient_email are denormalized at send time for exactly that reason,
    same as ReviewCycleReviewer's reviewer_name/reviewer_email.
    """

    __tablename__ = "liongard_sync_notification"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    sync_result_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("liongard_sync_result.id", ondelete="CASCADE"), index=True
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id"), index=True
    )
    contact_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("contact.id", ondelete="SET NULL"), nullable=True
    )
    recipient_name: Mapped[str] = mapped_column(String(200), nullable=False)
    recipient_email: Mapped[str] = mapped_column(String(320), nullable=False)
    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notification_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AssetApproval(Base):
    """The formal acceptance (or rejection) record D.3 exists to produce --
    who accepted which asset into the CUI environment, when, and what the
    baseline checklist showed at that moment. This IS the evidentiary
    artifact (per ROADMAP.md D.3's own framing); it is deliberately NOT
    wired into Evidence/EvidenceStateLink -- creating one Evidence
    artifact per approved asset would be evidence sprawl, exactly what
    CLAUDE.md's evidence-minimization hard rule exists to prevent. "Who
    approved this asset" is answerable from audit_log (real actor
    attribution) plus this row; a future bundle section can render
    straight from asset_approval, the same way the component inventory
    already renders straight from scope_entity, if that's ever wanted.

    decided_by/decided_by_name: the authenticated caller, never a
    submitted name -- same reasoning review_cycles.attest() already
    follows. decided_by is a string (user id), matching audit_log.actor's
    own convention rather than a hard FK, so a later user anonymization
    doesn't orphan this row's identity of record.

    sync_result_change_id is nullable: an asset can, in principle, be
    approved/rejected outside a sync-driven flow (e.g. a manually created
    scope_entity someone wants a formal acceptance record for) -- when set,
    it is EXACTLY the LiongardSyncResultChange this decision resolves, and
    that row's own `resolution`/`resolved_at`/`resolved_by` are updated in
    the same transaction (liongard_sync.py:approve_change/reject_change),
    never left to drift apart.

    checklist items live in AssetApprovalChecklistItem, not inline JSON,
    matching ReviewCycleItem's own precedent for a point-in-time snapshot
    that stays independently queryable.
    """

    __tablename__ = "asset_approval"
    __table_args__ = (
        CheckConstraint(
            "decision IN ('approved', 'rejected')", name="ck_asset_approval_decision"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id"), index=True
    )
    # RESTRICT, not CASCADE (migration 0057) -- deleting an asset must not
    # be able to silently take its own acceptance record with it. See that
    # migration's own docstring for why RESTRICT was chosen over SET NULL.
    scope_entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("scope_entity.id", ondelete="RESTRICT"), index=True
    )
    sync_result_change_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("liongard_sync_result_change.id", ondelete="SET NULL"),
        nullable=True,
    )
    decision: Mapped[str] = mapped_column(String(10), nullable=False)
    decided_by: Mapped[str] = mapped_column(String(100), nullable=False)
    decided_by_name: Mapped[str] = mapped_column(String(200), nullable=False)
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # Required for decision='rejected' (enforced in liongard_sync.py, not
    # a DB CHECK -- expressing "required iff X" as a CHECK constraint
    # against a sibling column is exactly the kind of thing this codebase
    # leaves to application validation elsewhere too, e.g. baseline_import
    # .py's coverage_basis-required-for-non-customer_owns check).
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class AssetApprovalChecklistItem(Base):
    """One product's row on the baseline checklist as it was shown at
    decision time -- v1 checklist source is the org's ACTIVATED Tools
    (OrgProduct/Product; deterministic, already exists, needs no AI and no
    new Liongard capability). `confirmed` is the reviewer's own manual
    tick, not an automated Liongard-metrics check -- no per-device agent-
    presence pull exists in connectors/liongard.py today (only device-
    profile/identity inventory), and this codebase's own hard-won lesson
    about this connector (the undocumented required `Sorting` field, found
    only by testing live) is not to guess at an unverified API surface.
    Automated per-device verification is a real, separate future slice
    against a live tenant, not built here -- see docs/roadmap.md's D.3
    entry for the explicit scope line.
    """

    __tablename__ = "asset_approval_checklist_item"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    approval_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("asset_approval.id", ondelete="CASCADE"), index=True
    )
    # Denormalized from asset_approval.org_id -- see LiongardSyncResultChange
    # .org_id's own comment for why every RLS-enabled child table carries
    # its own copy.
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id"), index=True
    )
    product_key: Mapped[str] = mapped_column(String(60), nullable=False)
    product_name: Mapped[str] = mapped_column(String(200), nullable=False)
    confirmed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
