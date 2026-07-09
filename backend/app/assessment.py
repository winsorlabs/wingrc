"""Assessment engine: StrEnum domain types and pure business logic.

Five design decisions documented here:

1. STATUS vs RESPONSIBILITY DISAMBIGUATION
   control_state.status tracks evidence completeness:
     not_met          — no product covers this, or product not activated
     pending_evidence — product claims coverage; awaiting evidence confirmation
     partial          — manually set; some but not all objectives implemented
     met              — coverage confirmed with attached evidence
     not_applicable   — scoped out for this tenant
     inherited        — satisfied by external authorized system (CSP / FedRAMP ATO)

   control_state.responsibility tracks WHO owns the control:
     provider_satisfies — a baseline-library tool is the primary implementor
     shared             — tool + customer share responsibility
     customer_owns      — customer alone must implement
     external_system    — external authorized system (e.g. FedRAMP CSP) is
                          responsible. Named "external_system" not "inherited"
                          to avoid collision with ControlStatus.INHERITED.

2. PENDING_EVIDENCE vs PARTIAL
   The magic loop sets status=pending_evidence, not partial. Rationale:
   - "partial" is ambiguous: partial implementation or partial evidence?
   - "pending_evidence" is precise: product claims coverage; the evidence gate
     is the only remaining step. State machine: not_met → pending_evidence → met.
   - "partial" is reserved for manual annotation: engineer marks a control that
     is being implemented but not yet complete or fully evidenced.

3. IMPLEMENTATION_STATEMENT vs CONTROL_STATE GRANULARITY
   implementation_statement is intentionally per-control (not per-objective)
   because SSP narratives are written at the practice level — one paragraph per
   NIST 800-171 requirement. control_state is per-objective for SPRS precision.
   These serve different consumers: scoring engine needs per-objective granularity;
   the SSP writer needs one coherent paragraph per practice.

4. ASSESSMENT INSTANTIATION ORDER
   Starting an assessment inserts control_state rows for ALL framework objectives
   (status=not_met, responsibility=customer_owns). Every objective has a home
   before any product is activated. The magic loop then upserts the product-covered
   objectives to pending_evidence. Controls with no product behind them stay
   not_met — visible as gaps from day one.

5. SPRS SCORING RULE
   A control is fully satisfied only if ALL its objectives have status in
   {met, inherited}. Any objective in any other state causes the full sprs_weight
   to be deducted. SPRS = 110 − Σ(weight of controls with any unsatisfied objective).
"""
from __future__ import annotations

from enum import StrEnum

SPRS_MAX = 110


class ControlStatus(StrEnum):
    NOT_MET = "not_met"
    PENDING_EVIDENCE = "pending_evidence"
    PARTIAL = "partial"
    MET = "met"
    NOT_APPLICABLE = "not_applicable"
    INHERITED = "inherited"


class Responsibility(StrEnum):
    PROVIDER_SATISFIES = "provider_satisfies"
    SHARED = "shared"
    CUSTOMER_OWNS = "customer_owns"
    EXTERNAL_SYSTEM = "external_system"


class AssessmentType(StrEnum):
    SELF = "self"
    THIRD_PARTY = "third_party"
    C3PAO = "c3pao"


class AssessmentStatus(StrEnum):
    IN_PROGRESS = "in_progress"
    SUBMITTED = "submitted"
    CLOSED = "closed"


class OrgProductStatus(StrEnum):
    CANDIDATE = "candidate"
    ACTIVE = "active"
    DECOMMISSIONED = "decommissioned"


class EvidenceType(StrEnum):
    SCREENSHOT = "screenshot"
    EXPORT = "export"
    DOCUMENT = "document"
    LINK = "link"
    POLICY = "policy"


class TaskStatus(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    SKIPPED = "skipped"
    WAIVED = "waived"


class FindingSeverity(StrEnum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFORMATIONAL = "informational"


class FindingType(StrEnum):
    GAP = "gap"
    DEFICIENCY = "deficiency"
    WEAKNESS = "weakness"
    OBSERVATION = "observation"


class FindingStatus(StrEnum):
    OPEN = "open"
    IN_REMEDIATION = "in_remediation"
    CLOSED = "closed"
    ACCEPTED_RISK = "accepted_risk"


class PoamStatus(StrEnum):
    OPEN = "open"
    ON_TRACK = "on_track"
    DELAYED = "delayed"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class StatementStatus(StrEnum):
    DRAFT = "draft"
    REVIEWED = "reviewed"
    APPROVED = "approved"


# ---------------------------------------------------------------------------
# Pure domain functions — no database access, unit-testable in isolation
# ---------------------------------------------------------------------------


def compute_sprs(
    control_weights: dict[str, int],
    objectives_by_control: dict[str, list[str]],
    objective_statuses: dict[str, str],
) -> int:
    """Compute the SPRS score from raw objective states.

    Args:
        control_weights:        {control_id: sprs_weight}
        objectives_by_control:  {control_id: [objective_id, ...]}
        objective_statuses:     {objective_id: ControlStatus value}

    Returns:
        Integer SPRS score. Max is 110; minimum is -204 (all 110 controls unmet:
        44×5 + 14×3 + 52×1 = 314 max deduction). Source: DoD SP 800-171
        Assessment Methodology v1.2.1 (Jun 2020). Note: 3.5.3 (MFA) and
        3.13.11 (FIPS crypto) have partial-credit rules (−3 if partial, −5 if
        absent) not captured by this flat model; both are stored at weight=5.

    A control is fully satisfied iff ALL its objectives have status in
    {met, inherited}. Any other status causes the full sprs_weight to deduct.
    Controls with no objectives in the lookup are skipped.
    """
    _satisfied: set[str] = {ControlStatus.MET, ControlStatus.INHERITED}
    deductions = 0
    for control_id, weight in control_weights.items():
        objectives = objectives_by_control.get(control_id, [])
        if not objectives:
            continue
        all_satisfied = all(
            objective_statuses.get(obj_id, ControlStatus.NOT_MET) in _satisfied
            for obj_id in objectives
        )
        if not all_satisfied:
            deductions += weight
    return SPRS_MAX - deductions


def magic_loop_updates(
    baseline_entries: list[dict],
    objective_lookup: dict[tuple[str, str], str],
) -> list[dict]:
    """Compute control_state updates the magic loop should apply.

    Pure function — no database calls. Returns upsert descriptors that the
    DB layer applies.

    Args:
        baseline_entries: list of dicts, each with:
            - control_id: str        (e.g. "AU.L2-3.3.1")
            - objectives: list[str]  (objective keys: ["a", "b", "c"])
            - classification: str    (a baseline.Classification value)
        objective_lookup: {(control_id, objective_key): objective_id}

    Returns:
        list of {objective_id, status, responsibility} dicts.
        Only provider_satisfies and shared entries are emitted.
        customer_owns entries are excluded — no state change, no tasks.

    The DB layer is responsible for:
      - Upserting each result into control_state.
      - Linking sourced_from_product_id to the activating product.
      - Creating evidence_task rows from baseline_evidence_specs.
    """
    updates: list[dict] = []
    for entry in baseline_entries:
        cls = entry.get("classification", "")
        if cls in ("customer_owns",):
            continue
        if cls == "provider_satisfies":
            responsibility = Responsibility.PROVIDER_SATISFIES
        else:
            responsibility = Responsibility.SHARED
        for obj_key in entry.get("objectives", []):
            obj_id = objective_lookup.get((entry["control_id"], obj_key))
            if obj_id is None:
                continue
            updates.append(
                {
                    "objective_id": obj_id,
                    "status": ControlStatus.PENDING_EVIDENCE,
                    "responsibility": responsibility,
                }
            )
    return updates
