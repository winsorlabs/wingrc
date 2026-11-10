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
    NEEDS_REVIEW = "needs_review"


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

    A control is fully satisfied iff ALL its objectives have a status in
    SATISFYING_STATUSES (met, inherited, not_applicable). Any other status
    causes the full sprs_weight to deduct. Controls with no objectives in
    the lookup are skipped.
    """
    deducting = deducting_controls(objectives_by_control, objective_statuses)
    return SPRS_MAX - sum(w for c, w in control_weights.items() if c in deducting)


# The objective statuses that leave a practice's points in place. A
# practice deducts its full weight when any objective has another status.
#
# not_applicable is here by 32 CFR 170.24(b)(3): "During an assessment, an
# assessment objective assessed as N/A is equivalent to the same assessment
# objective being assessed as MET." Deduction attaches only to NOT MET
# (170.24(c)(2): "For each requirement NOT MET, the associated value of the
# security requirement is subtracted from the maximum score"), and an N/A
# practice stays in the maximum, which is "the total number of CMMC Level 2
# security requirements". The CMMC Assessment Guide, Level 2, v2.13 (Sept
# 2024), p. 10: "Each assessment objective in NIST SP 800-171A must yield a
# finding of MET or NOT APPLICABLE in order for the overall security
# requirement to be scored as MET"; p. 11: "The security requirement is NOT
# MET when one or more applicable assessment objectives is NOT MET."
# Until 2026-11-10 not_applicable deducted (docs/roadmap.md, "N/A
# objectives stop deducting").
#
# A practice whose objectives are all not_applicable is satisfied by the
# same membership. Neither source says so in those words; it is read from
# p. 10's "MET or NOT APPLICABLE" for each objective.
#
# inherited's place here has not been checked against the authority
# (docs/roadmap.md, "What inherited means for the score is unchecked").
#
# Held by tests/test_assessment_engine.py's
# test_sprs_satisfying_statuses_are_met_inherited_and_not_applicable. The
# dashboard's heatmap reads this set, and the frontend's lib/sprs.ts holds
# its copy.
SATISFYING_STATUSES: frozenset[str] = frozenset(
    {ControlStatus.MET, ControlStatus.INHERITED, ControlStatus.NOT_APPLICABLE}
)


def deducting_controls(
    objectives_by_control: dict[str, list[str]],
    objective_statuses: dict[str, str],
) -> set[str]:
    """The controls compute_sprs deducts: any objective whose status is not
    in SATISFYING_STATUSES. An objective with no status counts as not met.

    Shared with the activation screen, which says which practices already
    deduct when a downgrade leaves the score where it was.
    """
    return {
        control_id
        for control_id, objectives in objectives_by_control.items()
        if objectives
        and not all(
            objective_statuses.get(obj_id, ControlStatus.NOT_MET) in SATISFYING_STATUSES
            for obj_id in objectives
        )
    }


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
      - Recording a control_state_contributor row for the activating
        product (see that model's docstring) -- possibly alongside other
        products already contributing to the same objective; see
        resolve_contributor_responsibility() / contributor_added_status()
        below for how a control_state with multiple contributors resolves
        its actual status/responsibility, which this function's own
        per-product output does NOT attempt (it only ever describes what
        ONE product's baseline claims in isolation).
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


def resolve_contributor_responsibility(classifications: list[str]) -> str:
    """Resolve one control_state.responsibility from the classifications of
    ALL its current contributors (multiple products can now cover the same
    objective -- see models.py:ControlStateContributor).

    Rule: 'shared' wins over 'provider_satisfies' whenever both are present
    -- the weakest (most customer-inclusive) claim governs, not the
    strongest. Decided explicitly (Jarrod, 2026-09-16) over the alternative
    (strongest claim wins, i.e. any provider_satisfies contributor clears
    the objective regardless of what else covers it): if even one
    contributing product's own baseline mapping says the customer still has
    a real action item (classification=shared, customer_action populated),
    that obligation is real and must keep surfacing in the CRM/SSP. Letting
    a DIFFERENT, unrelated product's provider_satisfies claim silently
    override it would hide a genuine customer responsibility from the exact
    document that exists to state responsibilities plainly -- the same
    conservative-bias principle baseline_import.py's disclaim-flag check
    already applies to a single product's own text (false positive costs a
    glance; false negative puts unearned credit in an SSP).

    classifications must be non-empty (a control_state always has at least
    one contributor by the time this is called) and contain only
    'provider_satisfies'/'shared' -- customer_owns baseline_control rows
    are excluded from ever becoming a contributor at the query layer
    (engine.py:_run_loop), the same filter magic_loop_updates() above
    already applies for the single-product case.
    """
    return (
        Responsibility.SHARED
        if "shared" in classifications
        else Responsibility.PROVIDER_SATISFIES
    )


class ActivationChoice(StrEnum):
    """A person's answer, at activation, for one judged objective."""

    KEEP = "keep"
    NEEDS_REVIEW = "needs_review"


# Statuses a person sets by judgement, which a tool's arrival must not
# decide alone (Jarrod, 2026-11-03: ask at activation). needs_review is
# not here: every answer leaves it needs_review, so there is nothing to
# ask. not_met and pending_evidence are the designed flow, not a judgement
# a tool could overturn.
JUDGED_STATUSES = frozenset({
    ControlStatus.MET,
    ControlStatus.PARTIAL,
    ControlStatus.NOT_APPLICABLE,
    ControlStatus.INHERITED,
})

# The activation screen's default per judged status:
#   met            -> needs_review: the tool is new information and brings
#                     evidence tasks of its own, so re-confirming is honest.
#   partial        -> needs_review: the tool may complete what was partial;
#                     a person should say so. It deducts either way.
#   not_applicable -> keep: no reason was found that a tool covering a
#                     control makes it applicable.
#   inherited      -> keep: who satisfies the control does not change
#                     because a tool now runs in the environment.
ACTIVATION_DEFAULTS: dict[str, ActivationChoice] = {
    ControlStatus.MET: ActivationChoice.NEEDS_REVIEW,
    ControlStatus.PARTIAL: ActivationChoice.NEEDS_REVIEW,
    ControlStatus.NOT_APPLICABLE: ActivationChoice.KEEP,
    ControlStatus.INHERITED: ActivationChoice.KEEP,
}


def is_judgement_in_question(current_status: str, is_first_contributor: bool) -> bool:
    """Whether a product joining this objective would decide a person's
    judgement: it is the first contributor, and the status is judged."""
    return is_first_contributor and current_status in JUDGED_STATUSES


def contributor_added_status(
    current_status: str,
    is_first_contributor: bool,
    choice: str | None = None,
) -> str:
    """Resolve control_state.status when a product is added as a contributor.

    An ADDITIONAL contributor onto an objective that already has one never
    changes status: adding a second tool's coverage is not a reason to
    un-verify what a human already confirmed. Before 2026-09-16 _run_loop
    overwrote status to pending_evidence on every activation, which
    regressed a met objective the moment a second overlapping product
    activated (confirmed on a bench stack).

    A FIRST contributor:
      - not_met / pending_evidence -> pending_evidence, the designed flow;
      - needs_review -> needs_review, which every answer would give;
      - a judged status (JUDGED_STATUSES) -> the person's choice: keep it,
        or needs_review. With no choice, which is every path where nobody
        is asked, needs_review: it defers the judgement rather than
        destroying it or silently keeping one that may now be stale.

    Until 2026-11-03 a first contributor set pending_evidence from any
    status, a hand-set met or not_applicable included (docs/roadmap.md,
    "Activating a tool overwrites a status a person set").
    Every starting status, both ways, is held by
    tests/test_assessment_engine.py's contributor_added_status tests.
    """
    if not is_first_contributor:
        return current_status
    if current_status == ControlStatus.NEEDS_REVIEW:
        return ControlStatus.NEEDS_REVIEW
    if current_status in JUDGED_STATUSES:
        if choice == ActivationChoice.KEEP:
            return current_status
        return ControlStatus.NEEDS_REVIEW
    return ControlStatus.PENDING_EVIDENCE
