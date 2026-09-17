"""DB adapter: assessment lifecycle and magic loop.

Bridges the pure functions in assessment.py with the SQLAlchemy session.
This is the only module that performs DB writes for assessment operations.

Entry points:
  start_assessment   — create an Assessment + seed all objective states,
                       then fire the loop for every already-active product.
  copy_forward_raci  — deliberately separate from start_assessment, not a
                       step inside it (see its own docstring for why) —
                       carries RACI assignments from the org's most recent
                       prior assessment on the same framework onto the
                       freshly seeded control_state rows.
  activate_org_product — mark a product active and fire the loop for one
                         assessment, updating states, writing history,
                         and seeding evidence tasks.
  complete_assessment / reopen_assessment — the in_progress <-> submitted
                       transition. A recorded milestone only — gates no
                       capability. Never touches locked_at (unimplemented)
                       or creates an SprsSubmission row (that's a human's
                       separate, later action — see sprs_submissions.py).
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import case, select
from sqlalchemy.orm import Session

from .assessment import (
    ControlStatus,
    OrgProductStatus,
    Responsibility,
    compute_sprs,
    contributor_added_status,
    magic_loop_updates,
    resolve_contributor_responsibility,
)
from .audit import log_event
from .models import (
    Assessment,
    AssessmentObjective,
    BaselineControl,
    BaselineEvidenceSpec,
    Contact,
    Control,
    ControlState,
    ControlStateContributor,
    ControlStateHistory,
    EvidenceStateLink,
    EvidenceTask,
    EvidenceTaskStateLink,
    Organization,
    OrgProduct,
    Product,
    ProductBaselineVersion,
    RaciAssignment,
    SprsSnapshot,
)


def recompute_sprs(session: Session, assessment_id: uuid.UUID) -> int:
    """Recompute and persist SPRS score for one assessment.

    Queries all control_state rows, runs the objective→control rollup via
    compute_sprs(), writes the result to assessment.sprs_score, and flushes.
    Returns the computed integer score.

    Acquires SELECT ... FOR UPDATE on the assessment row FIRST, before
    reading control_state — this closes a lost-update race across this
    function's five call sites (start_assessment, activate_org_product,
    deactivate_org_product, bundle_service.snapshot_bundle,
    patch_control_state): under READ COMMITTED, two concurrent recomputes
    for the same assessment could each read control_state before either
    committed, each computing from an incomplete snapshot, and merely
    race on which stale write landed last. Locking here first forces
    whichever transaction acquires the lock second to wait for the first
    to fully commit before its own control_state read even executes, so
    that read is guaranteed to see everything the first transaction
    changed. Locking only at the final write (as this function used to)
    does not fix this — both reads can still happen before either commit.
    See docs/PLAN-gui-restructure.md's G.2 section for the bug this
    closes and why every call site's own lock-acquisition order
    (control_state/org_product rows before reaching this function, this
    function's assessment lock last) was checked for deadlock risk
    against bundle export's reverse order (assessment lock first, then
    an unrelated evidence-row update) before landing this.

    Uses an explicit `select(...).with_for_update()` rather than
    `session.get(Assessment, assessment_id, with_for_update=True)`
    deliberately: several callers (e.g. patch_control_state) already hold
    the same Assessment row in the session's identity map from an
    earlier plain `session.get()`, and this avoids any doubt about
    whether `Session.get()`'s identity-map short-circuit could return
    that cached instance without actually re-issuing the locking SELECT.

    Flushes FIRST, before anything else: `app/db.py`'s production
    `SessionLocal` sets `autoflush=False`, so a caller's own pending
    unflushed ORM changes (e.g. patch_control_state's `cs.status =
    body.status`, called immediately before this function with no flush
    in between) are otherwise invisible to the SELECT below — this
    function would compute a score missing the very edit that triggered
    it, one recompute behind, every single time. `activate_org_product`/
    `deactivate_org_product`/`_run_loop` all happen to flush their own
    writes before reaching this function already, so this was never
    visible from those call sites; `patch_control_state` did not, and
    this was live and deterministic (not a race — every call from that
    endpoint hit it) until this fix. Found via a G.3 smoke-test report
    ("Dashboard's SPRS score lags the assessment screen's live
    computation by exactly one recompute") after the earlier
    SELECT ... FOR UPDATE fix (which addresses a real but different,
    genuinely concurrent race) didn't close it. Not caught by this
    file's own test suite before now because tests/conftest.py's test
    session doesn't set `autoflush`, defaulting to `True` — masking
    exactly this bug. See docs/PLAN-gui-restructure.md's G.2 section for
    the full writeup.
    """
    session.flush()

    locked_assessment = session.execute(
        select(Assessment).where(Assessment.id == assessment_id).with_for_update()
    ).scalar_one_or_none()

    rows = session.execute(
        select(
            ControlState.objective_id,
            ControlState.status,
            Control.control_id,
            Control.sprs_weight,
        )
        .join(AssessmentObjective, ControlState.objective_id == AssessmentObjective.id)
        .join(Control, AssessmentObjective.control_id == Control.id)
        .where(ControlState.assessment_id == assessment_id)
    ).all()

    control_weights: dict[str, int] = {}
    objectives_by_control: dict[str, list[str]] = {}
    objective_statuses: dict[str, str] = {}

    for row in rows:
        obj_id_str = str(row.objective_id)
        ctrl_id = row.control_id
        control_weights[ctrl_id] = row.sprs_weight
        objectives_by_control.setdefault(ctrl_id, []).append(obj_id_str)
        objective_statuses[obj_id_str] = row.status

    score = compute_sprs(control_weights, objectives_by_control, objective_statuses)

    if locked_assessment is not None:
        locked_assessment.sprs_score = score
        session.add(
            SprsSnapshot(
                assessment_id=assessment_id, org_id=locked_assessment.org_id, score=score
            )
        )
        session.flush()

    return score


def start_assessment(
    session: Session,
    org_id: uuid.UUID,
    framework_id: uuid.UUID,
    name: str,
    assessment_type: str = "self",
) -> Assessment:
    """Create an assessment and materialise control_state for every objective.

    Per assessment.py design note 4:
      1. All objectives start as not_met / customer_owns.
      2. The magic loop then fires for every org_product that is already
         active + configured, updating the product-covered objectives to
         pending_evidence and seeding evidence tasks.

    Products activated after this call trigger activate_org_product directly.

    RACI copy-forward is deliberately NOT a step in here — see
    copy_forward_raci()'s own docstring below for why it's a separate call
    the router makes explicitly, not something every caller of this
    function gets for free. Return type/signature stay untouched by this
    (40+ existing call sites, mostly test fixtures, construct an assessment
    via this function with no expectation of RACI side effects).
    """
    assessment = Assessment(
        org_id=org_id,
        framework_id=framework_id,
        name=name,
        assessment_type=assessment_type,
        status="in_progress",
    )
    session.add(assessment)
    session.flush()

    _seed_control_states(session, org_id, framework_id, assessment.id)

    # Fire the loop for products already marked active before the assessment started.
    active_products = session.scalars(
        select(OrgProduct).where(
            OrgProduct.org_id == org_id,
            OrgProduct.status == OrgProductStatus.ACTIVE,
            OrgProduct.configured.is_(True),
        )
    ).all()
    for op in active_products:
        if op.baseline_version_id is None:
            # Defensive only -- activate_org_product always pins a version
            # on activation, and the migration 0054 backfill pinned every
            # pre-existing row. An active OrgProduct with no pin would mean
            # it was created some other way; skip rather than crash so one
            # bad row can't break every other product's magic loop on
            # assessment creation.
            continue
        _run_loop(session, org_id, op.product_id, op.baseline_version_id, assessment.id)

    recompute_sprs(session, assessment.id)
    return assessment


def copy_forward_raci(
    session: Session,
    *,
    org_id: uuid.UUID,
    framework_id: uuid.UUID,
    new_assessment_id: uuid.UUID,
) -> dict:
    """Carry RACI assignments from the org's most recent prior assessment
    on the same framework onto new_assessment_id's freshly seeded
    control_state rows.

    Recorded decision: docs/PLAN-gui-restructure.md's G.7 section ("does
    RACI carry forward when a new assessment starts, or begin empty? ...
    decided: copy forward from the most recent prior assessment, editable
    from there") -- not implemented there, built here.

    Deliberately a separate call the router makes right after
    start_assessment(), not a step inside it: start_assessment() has 40+
    existing call sites (mostly test fixtures) that construct an
    assessment with no expectation of RACI side effects, and changing its
    return type to carry a summary dict alongside the Assessment would
    touch every one of them for a behavior only the real creation endpoint
    needs. Same reasoning `backfill_missing_control_states` already
    established for staying its own function rather than folding into
    start_assessment.

    Must run in the same transaction as the assessment/control_state
    creation it follows (caller commits once, after both) -- an assessment
    that exists without its copy-forward having run, or vice versa, is
    exactly the kind of inconsistent partial state this codebase avoids
    elsewhere via one-transaction-per-operation.

    THE JOIN: RaciAssignment is keyed to control_state_id, and every new
    assessment gets entirely new control_state rows -- so this is not a
    row copy. control_state.objective_id points at AssessmentObjective.id,
    a stable, deployment-wide catalog row (seeds/catalog.py upserts by
    existing (control_id, objective_key) rather than ever re-inserting or
    deleting one -- verified, not assumed, before relying on this).
    UNIQUE(assessment_id, objective_id) on control_state means objective_id
    is control_state's natural key within one assessment. So: for each
    source assignment, resolve its control_state's objective_id, look up
    that same objective_id's control_state.id in the new assessment, and
    create an equivalent assignment there. No text-based (control_id,
    objective_key) matching is needed -- objective_id already *is* that
    identity, more directly.

    WHICH PRIOR ASSESSMENT -- revisited now that completion exists
    (engine.py:complete_assessment, below), and updated: when this was
    first written, "most recent completed" (status in submitted/closed)
    would have made copy-forward permanently unreachable, since nothing
    anywhere ever set Assessment.status to anything but "in_progress" --
    checked before choosing, not assumed, at the time. That's no longer
    true. Now: prefer the most recent COMPLETED assessment (status in
    submitted/closed) on the same framework; only if none exists yet,
    fall back to the most recent by started_at regardless of status (the
    original rule, preserved so an org with no completed assessment yet
    -- including every org that existed before this change -- is no
    worse off than before). Rationale for preferring completed once one
    exists: nothing here blocks parallel/experimental assessments (a
    hard product constraint -- completion gates no capability), so a
    more-recent-but-still-in-progress assessment could just as easily be
    a throwaway/testing one as the org's real current cycle; a completed
    assessment is the stronger signal of "this was the actual prior
    cycle." Scoped to org_id + the SAME framework_id as the new
    assessment either way (a different framework's objective_ids can
    never match this assessment's control_state rows anyway -- scoping
    the query avoids a more-recent-but-incompatible assessment silently
    shadowing an older same-framework one that would have actually
    matched).

    FRAMEWORK / CATALOG DRIFT: an objective present in the source
    assessment but absent from the new one's control_state set (framework
    mismatch, or -- currently unreachable given the upsert-only seeding
    behavior above, but defended anyway -- a hypothetical future
    destructive catalog change) is skipped, not treated as an error and
    not allowed to abort the rest. Every other matching objective still
    carries. This is "carry only exact objective matches," chosen over
    "skip entirely on any mismatch": a partial framework/catalog drift
    (most objectives still line up, a handful don't) is the realistic
    case, and refusing to carry anything just because one objective
    doesn't line up would throw away otherwise-good data over an edge
    case, then hand the MSP the exact all-320-unassigned outcome this
    feature exists to avoid, for no benefit.

    INACTIVE CONTACTS: Contact has no deleted_at/is_active field in this
    codebase -- checked before implementing, not assumed. Contacts are
    hard-deleted (routers/contacts.py's DELETE endpoint), and
    RaciAssignment.contact_id is ON DELETE CASCADE, so a departed contact
    whose row has actually been removed already has zero RaciAssignment
    rows anywhere, including on the source assessment being copied from --
    there is nothing left to skip by the time this function runs. Still
    checks contact existence defensively per assignment (cheap, one batched
    query) and counts anything that somehow fails it as
    skipped_inactive_contact, both as a genuine safety net and so this
    function needs no changes if a soft-deactivation concept is added to
    Contact later (mirroring ADR 0006's user anonymize/hard-delete split).
    Expect this count to read 0 on every real run today.

    Returns a summary dict, audit-logged by the caller-facing router path
    via one raci.copy_forward entry (this function itself does not call
    log_event -- see routers/assessments.py's create_assessment, which
    owns the single commit this shares a transaction with):
        {
          "source_assessment_id": str | None,   # None => nothing to carry
          "carried": int,
          "skipped_no_match": int,               # objective not in new assessment
          "skipped_inactive_contact": int,       # see above; expect 0 today
          "total_objectives": int,               # new assessment's own count
          "unassigned_objectives": int,          # total_objectives - assigned
          "note": str,                           # human-readable one-liner
        }
    """
    total_objectives = session.scalars(
        select(ControlState.id).where(ControlState.assessment_id == new_assessment_id)
    ).all()
    total_count = len(total_objectives)

    source = session.scalars(
        select(Assessment)
        .where(
            Assessment.org_id == org_id,
            Assessment.framework_id == framework_id,
            Assessment.id != new_assessment_id,
        )
        .order_by(
            case((Assessment.status.in_(("submitted", "closed")), 0), else_=1),
            Assessment.started_at.desc(),
        )
        .limit(1)
    ).first()

    if source is None:
        return {
            "source_assessment_id": None,
            "carried": 0,
            "skipped_no_match": 0,
            "skipped_inactive_contact": 0,
            "total_objectives": total_count,
            "unassigned_objectives": total_count,
            "note": "No prior assessment on this framework to carry RACI from.",
        }

    source_rows = session.execute(
        select(RaciAssignment, ControlState.objective_id)
        .join(ControlState, RaciAssignment.control_state_id == ControlState.id)
        .where(ControlState.assessment_id == source.id)
    ).all()

    new_cs_by_objective: dict[uuid.UUID, uuid.UUID] = dict(
        session.execute(
            select(ControlState.objective_id, ControlState.id).where(
                ControlState.assessment_id == new_assessment_id
            )
        ).all()
    )

    contact_ids = {a.contact_id for a, _ in source_rows}
    existing_contact_ids = (
        set(session.scalars(select(Contact.id).where(Contact.id.in_(contact_ids))).all())
        if contact_ids
        else set()
    )

    carried = 0
    skipped_no_match = 0
    skipped_inactive_contact = 0
    assigned_objective_ids: set[uuid.UUID] = set()

    for assignment, objective_id in source_rows:
        new_cs_id = new_cs_by_objective.get(objective_id)
        if new_cs_id is None:
            skipped_no_match += 1
            continue
        if assignment.contact_id not in existing_contact_ids:
            skipped_inactive_contact += 1
            continue
        session.add(
            RaciAssignment(
                control_state_id=new_cs_id,
                contact_id=assignment.contact_id,
                raci_letter=assignment.raci_letter,
            )
        )
        carried += 1
        assigned_objective_ids.add(objective_id)

    session.flush()

    return {
        "source_assessment_id": str(source.id),
        "carried": carried,
        "skipped_no_match": skipped_no_match,
        "skipped_inactive_contact": skipped_inactive_contact,
        "total_objectives": total_count,
        "unassigned_objectives": total_count - len(assigned_objective_ids),
        "note": (
            f"Carried {carried} RACI assignment(s) from the prior assessment "
            f"({source.name}). Review before relying on them."
            if carried
            else f"No matching RACI assignments to carry from the prior assessment ({source.name})."
        ),
    }


def complete_assessment(
    session: Session, *, org_id: uuid.UUID, assessment_id: uuid.UUID
) -> Assessment:
    """The in_progress -> submitted transition ("completing" an
    assessment, per the product's own vocabulary -- see routers/
    assessments.py's complete_assessment_endpoint for why "submitted"
    here means "this WinGRC assessment cycle is complete," never "filed
    with SPRS," and why those two must never be conflated).

    Stamps submitted_at, nothing else. Does NOT create an SprsSubmission
    row, does NOT touch locked_at, and does NOT change what any other
    endpoint permits — completion is a recorded milestone, not a
    permission gate; every caller of this function must not add one
    either. locked_at is separate, deliberate, and not implemented yet
    (see models.py:Assessment / docs/roadmap.md) — auto-locking on
    completion here would silently narrow what "test bundle export
    before completion" (an explicit product requirement) actually means.

    Raises ValueError if the assessment doesn't belong to org_id or is
    not currently in_progress (only forward from in_progress is a
    "completion" -- an already-submitted assessment must be reopened
    first, not completed again, so submitted_at is never silently
    overwritten by a second completion).
    """
    assessment = session.get(Assessment, assessment_id)
    if assessment is None or assessment.org_id != org_id:
        raise ValueError("Assessment not found")
    if assessment.status != "in_progress":
        raise ValueError(f"Cannot complete an assessment with status {assessment.status!r}")

    assessment.status = "submitted"
    assessment.submitted_at = datetime.now(UTC)
    session.flush()
    return assessment


def reopen_assessment(
    session: Session, *, org_id: uuid.UUID, assessment_id: uuid.UUID
) -> Assessment:
    """The submitted -> in_progress transition, for a completion made in
    error. Clears submitted_at (the mutable "current state" column) --
    the historical fact "this was completed, then reopened" is preserved
    durably by the caller's audit_log entry (before_value captures the
    prior submitted_at), not by this column, matching how every other
    mutable-column-plus-audit-log pair in this codebase works (e.g.
    patch_user's role_change/activation_change entries).

    Never touches any SprsSubmission row -- a submission recorded after
    the since-reopened completion remains exactly as filed; reopening
    the WinGRC-internal milestone cannot retroactively un-file a DoD
    submission, and must not be implemented as though it could.

    Raises ValueError if the assessment doesn't belong to org_id or is
    not currently submitted.
    """
    assessment = session.get(Assessment, assessment_id)
    if assessment is None or assessment.org_id != org_id:
        raise ValueError("Assessment not found")
    if assessment.status != "submitted":
        raise ValueError(f"Cannot reopen an assessment with status {assessment.status!r}")

    assessment.status = "in_progress"
    assessment.submitted_at = None
    session.flush()
    return assessment


def activate_org_product(
    session: Session,
    org_id: uuid.UUID,
    product_id: uuid.UUID,
    assessment_id: uuid.UUID,
    configuration_notes: str | None = None,
) -> dict:
    """Mark a product active and fire the magic loop for one assessment.

    On re-activation after a prior deactivation:
      - Archived evidence tasks are restored (na→open; collected stays collected).
      - Archived evidence-state links from this product are unarchived.
      - States that regain evidence are set to needs_review, not pending_evidence:
        the MSP keeps their artifacts but must re-confirm coverage is still current.
    On first activation (no archived evidence), behaviour is the same as before.

    Baseline versioning (roadmap item P): a FIRST activation (no OrgProduct
    row yet, or one that's never been activated before) pins
    OrgProduct.baseline_version_id to the product's CURRENT version and is
    gated on Product.is_published, exactly as before. A REACTIVATION
    (an OrgProduct row that already has a pinned version from a prior
    activation) reuses that pinned version regardless of the product's
    current publish state -- the tenant already adopted a specific,
    reviewed version; a later reimport unpublishing the product's newest
    mapping must not block them from resuming their own historical one.
    Moving an already-active tenant onto a newer version is a separate,
    deliberate action -- see move_org_product_version.

    Returns {"objectives_updated": N, "tasks_created": N}.
    """
    product_check = session.get(Product, product_id)
    if product_check is None:
        raise ValueError(f"Product {product_id} not found")

    op = session.scalars(
        select(OrgProduct).where(
            OrgProduct.org_id == org_id,
            OrgProduct.product_id == product_id,
        )
    ).first()
    is_first_activation = op is None or op.baseline_version_id is None

    if is_first_activation:
        if not product_check.is_published:
            # G.9: is_published is the deliberate act that exposes a
            # reviewed baseline mapping to tenants. Blocking activation
            # here, not just filtering the tenant's own product list, is
            # what makes "cannot be activated by any path, including
            # direct API calls" actually true -- a client that already
            # knows product_id (e.g. from before it was unpublished, or
            # by guessing) must not be able to activate it by calling
            # this endpoint directly.
            raise ValueError(f"Product {product_id} is not published")
        if product_check.current_version_id is None:
            raise ValueError(f"Product {product_id} has no baseline version to activate")
        target_version_id = product_check.current_version_id
    else:
        target_version_id = op.baseline_version_id

    if op is None:
        op = OrgProduct(org_id=org_id, product_id=product_id)
        session.add(op)
    op.status = OrgProductStatus.ACTIVE
    op.configured = True
    op.activated_at = datetime.now(UTC)
    op.baseline_version_id = target_version_id
    if configuration_notes is not None:
        op.configuration_notes = configuration_notes
    session.flush()

    product = session.get(Product, product_id)
    reactivation_ctx: dict = {
        "via": "product_reactivation",
        "product_name": product.name if product else str(product_id),
        "assessment_id": str(assessment_id),
    }

    # Restore archived evidence tasks seeded by this product.
    archived_tasks = session.scalars(
        select(EvidenceTask)
        .join(BaselineEvidenceSpec, EvidenceTask.baseline_spec_id == BaselineEvidenceSpec.id)
        .join(BaselineControl, BaselineEvidenceSpec.baseline_control_id == BaselineControl.id)
        .where(
            EvidenceTask.assessment_id == assessment_id,
            EvidenceTask.org_id == org_id,
            BaselineControl.baseline_version_id == target_version_id,
            EvidenceTask.is_archived.is_(True),
        )
    ).all()

    for task in archived_tasks:
        prev_status = task.status
        task.is_archived = False
        task.archived_at = None
        if task.status == "na":
            task.status = "open"
        log_event(
            session,
            org_id=org_id,
            action="evidence_task.restore",
            entity_type="evidence_task",
            entity_id=task.id,
            before_value={"is_archived": True, "status": prev_status},
            after_value={"is_archived": False, "status": task.status},
            context={**reactivation_ctx, "collection_session": task.collection_session},
        )

    # Restore archived evidence-state links attributed to this product.
    archived_links = session.scalars(
        select(EvidenceStateLink)
        .join(ControlState, EvidenceStateLink.control_state_id == ControlState.id)
        .where(
            ControlState.assessment_id == assessment_id,
            EvidenceStateLink.is_archived.is_(True),
            EvidenceStateLink.archived_by_product == product_id,
        )
    ).all()

    restored_cs_ids: set[uuid.UUID] = set()
    for lnk in archived_links:
        lnk.is_archived = False
        lnk.archived_at = None
        lnk.archived_by_product = None
        restored_cs_ids.add(lnk.control_state_id)
        log_event(
            session,
            org_id=org_id,
            action="evidence_state_link.restore",
            entity_type="evidence_state_link",
            entity_id=lnk.id,
            before_value={"is_archived": True},
            after_value={"is_archived": False},
            context={**reactivation_ctx, "control_state_id": str(lnk.control_state_id)},
        )

    session.flush()

    result = _run_loop(session, org_id, product_id, target_version_id, assessment_id)

    # States that regained archived evidence need human re-confirmation, not pending.
    if restored_cs_ids:
        cs_with_restored = session.scalars(
            select(ControlState).where(
                ControlState.id.in_(list(restored_cs_ids)),
                ControlState.status == ControlStatus.PENDING_EVIDENCE,
            )
        ).all()
        restore_history: list[ControlStateHistory] = []
        product_name = product.name if product else str(product_id)
        for cs in cs_with_restored:
            restore_history.append(
                ControlStateHistory(
                    control_state_id=cs.id,
                    previous_status=ControlStatus.PENDING_EVIDENCE,
                    new_status=ControlStatus.NEEDS_REVIEW,
                    previous_responsibility=cs.responsibility,
                    new_responsibility=cs.responsibility,
                    change_reason=f"Reactivation: prior evidence restored from {product_name}",
                )
            )
            log_event(
                session,
                org_id=org_id,
                action="control_state.update",
                entity_type="control_state",
                entity_id=cs.id,
                before_value={"status": ControlStatus.PENDING_EVIDENCE},
                after_value={"status": ControlStatus.NEEDS_REVIEW},
                context={
                    **reactivation_ctx,
                    "via": "product_reactivation_with_prior_evidence",
                },
            )
            cs.status = ControlStatus.NEEDS_REVIEW
        session.add_all(restore_history)
        session.flush()

    recompute_sprs(session, assessment_id)
    return result


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _seed_control_states(
    session: Session,
    org_id: uuid.UUID,
    framework_id: uuid.UUID,
    assessment_id: uuid.UUID,
) -> None:
    """Bulk-insert one ControlState row per framework objective."""
    objectives = session.scalars(
        select(AssessmentObjective)
        .join(Control, AssessmentObjective.control_id == Control.id)
        .where(Control.framework_id == framework_id)
    ).all()

    session.add_all([
        ControlState(
            assessment_id=assessment_id,
            org_id=org_id,
            objective_id=obj.id,
            status=ControlStatus.NOT_MET,
            responsibility=Responsibility.CUSTOMER_OWNS,
        )
        for obj in objectives
    ])
    session.flush()


def _run_loop(
    session: Session,
    org_id: uuid.UUID,
    product_id: uuid.UUID,
    baseline_version_id: uuid.UUID,
    assessment_id: uuid.UUID,
) -> dict:
    """Core magic-loop logic: update states, write history, seed tasks.

    Separated from activate_org_product so start_assessment can call it for
    each pre-existing active product without repeating the OrgProduct update.

    baseline_version_id (roadmap item P): the tenant's OrgProduct.
    baseline_version_id pin -- the ONE version's claims this run evaluates,
    never "whatever the product's current version happens to be." Callers
    resolve the pin before calling this; it is never re-derived here.
    """
    product = session.get(Product, product_id)
    if product is None:
        raise ValueError(f"Product {product_id} not found")

    # --- baseline controls for this product's PINNED version ---
    # Exclude customer_owns (vendor disclaims) and platform_only (vendor covers
    # its own platform, not the customer's CUI systems).
    baseline_controls = session.scalars(
        select(BaselineControl)
        .where(BaselineControl.baseline_version_id == baseline_version_id)
        .where(BaselineControl.classification != "customer_owns")
        .where(BaselineControl.coverage_basis != "platform_only")
    ).all()

    if not baseline_controls:
        return {"objectives_updated": 0, "tasks_created": 0}

    assessment = session.get(Assessment, assessment_id)
    if assessment is None:
        raise ValueError(f"Assessment {assessment_id} not found")

    # --- build objective lookup ---
    control_ids = {bc.control_id for bc in baseline_controls}

    controls = session.scalars(
        select(Control).where(Control.id.in_(list(control_ids)))
    ).all()
    ctrl_uuid_to_str: dict[uuid.UUID, str] = {c.id: c.control_id for c in controls}

    objectives = session.scalars(
        select(AssessmentObjective)
        .where(AssessmentObjective.control_id.in_(list(control_ids)))
    ).all()

    # pure function expects: {(control_id_str, obj_key): objective_uuid_str}
    objective_lookup: dict[tuple[str, str], str] = {
        (ctrl_uuid_to_str[obj.control_id], obj.objective_key): str(obj.id)
        for obj in objectives
        if obj.control_id in ctrl_uuid_to_str
    }

    entries = [
        {
            "control_id": ctrl_uuid_to_str[bc.control_id],
            "objectives": bc.objectives or [],
            "classification": bc.classification,
        }
        for bc in baseline_controls
        if bc.control_id in ctrl_uuid_to_str
    ]

    # bc has at most one row per control_id within this version (DB unique
    # constraint uq_baseline_control_version_identity on (baseline_version_id,
    # control_id)), so this lookup is always unambiguous.
    bc_by_control_str: dict[str, BaselineControl] = {
        ctrl_uuid_to_str[bc.control_id]: bc
        for bc in baseline_controls
        if bc.control_id in ctrl_uuid_to_str
    }

    # --- pure function: what THIS product's own baseline claims ---
    updates = magic_loop_updates(entries, objective_lookup)
    if not updates:
        return {"objectives_updated": 0, "tasks_created": 0}

    # --- pre-load existing control_states to avoid N+1 ---
    updated_obj_ids = {uuid.UUID(u["objective_id"]) for u in updates}
    existing_states: dict[uuid.UUID, ControlState] = {
        cs.objective_id: cs
        for cs in session.scalars(
            select(ControlState).where(
                ControlState.assessment_id == assessment_id,
                ControlState.objective_id.in_(list(updated_obj_ids)),
            )
        ).all()
    }
    obj_id_to_control_str: dict[uuid.UUID, str] = {
        obj.id: ctrl_uuid_to_str[obj.control_id]
        for obj in objectives
        if obj.control_id in ctrl_uuid_to_str
    }

    # --- pre-load EVERY current contributor (any product) for these
    # control_states, so a new contributor's status/responsibility can be
    # resolved against the full set, not just this product's own claim.
    cs_ids = [cs.id for cs in existing_states.values()]
    contributors_by_cs: dict[uuid.UUID, list[tuple[uuid.UUID, str]]] = {}
    if cs_ids:
        for cs_id, contrib_product_id, classification in session.execute(
            select(
                ControlStateContributor.control_state_id,
                ControlStateContributor.product_id,
                BaselineControl.classification,
            )
            .join(
                BaselineControl,
                ControlStateContributor.baseline_control_id == BaselineControl.id,
            )
            .where(ControlStateContributor.control_state_id.in_(cs_ids))
        ).all():
            contributors_by_cs.setdefault(cs_id, []).append((contrib_product_id, classification))

    # --- apply updates + write history ---
    history_rows: list[ControlStateHistory] = []
    new_contributor_rows: list[ControlStateContributor] = []
    objectives_updated = 0

    for upd in updates:
        obj_id = uuid.UUID(upd["objective_id"])
        state = existing_states.get(obj_id)
        if state is None:
            continue

        bc = bc_by_control_str.get(obj_id_to_control_str.get(obj_id, ""))
        if bc is None:
            continue

        current_contributors = contributors_by_cs.get(state.id, [])
        if any(pid == product_id for pid, _ in current_contributors):
            # Already a contributor (idempotent re-activation) -- no-op,
            # matching the existing evidence-task dedup's idempotency.
            continue

        had_prior_contributors = bool(current_contributors)
        prev_status = state.status
        prev_resp = state.responsibility

        new_contributor_rows.append(
            ControlStateContributor(
                control_state_id=state.id,
                product_id=product_id,
                baseline_control_id=bc.id,
            )
        )
        all_classifications = [c for _, c in current_contributors] + [bc.classification]

        state.status = contributor_added_status(prev_status, not had_prior_contributors)
        state.responsibility = resolve_contributor_responsibility(all_classifications)

        history_rows.append(
            ControlStateHistory(
                control_state_id=state.id,
                previous_status=prev_status,
                new_status=state.status,
                previous_responsibility=prev_resp,
                new_responsibility=state.responsibility,
                change_reason=(
                    f"Magic loop: {product.name} activated"
                    if not had_prior_contributors
                    else f"Contributor added: {product.name} (also covers this control)"
                ),
            )
        )
        objectives_updated += 1

    session.add_all(new_contributor_rows)
    session.add_all(history_rows)
    session.flush()

    tasks_created = _fanout_evidence_tasks(
        session,
        org_id=org_id,
        assessment_id=assessment_id,
        product=product,
        baseline_controls=baseline_controls,
        ctrl_uuid_to_str=ctrl_uuid_to_str,
        objective_lookup=objective_lookup,
        existing_states=existing_states,
    )
    return {"objectives_updated": objectives_updated, "tasks_created": tasks_created}


def _fanout_evidence_tasks(
    session: Session,
    *,
    org_id: uuid.UUID,
    assessment_id: uuid.UUID,
    product: Product,
    baseline_controls: list[BaselineControl],
    ctrl_uuid_to_str: dict[uuid.UUID, str],
    objective_lookup: dict[tuple[str, str], str],
    existing_states: dict[uuid.UUID, ControlState],
) -> int:
    """Seed evidence tasks from baseline_controls' evidence specs
    (deduplicated, multi-objective links). Shared by _run_loop (first
    activation / reactivation) and move_org_product_version (roadmap item
    P: a version move can introduce baseline_control rows -- and their
    evidence specs -- this org_product has never seen before).

    Dedup strategy:
      1. By baseline_spec_id: a previously seeded spec never creates a new task
         (idempotency on re-activation, and on repeat version moves).
      2. By (title.lower(), artifact_type): if two specs describe the same
         artifact, they share one task (evidence minimisation across controls).
      3. Within-run: new tasks created this call are tracked so a second spec
         with the same artifact key reuses rather than duplicates.

    Existing task status is NEVER modified — a 'collected' task stays collected.
    Only new control_state links are added for gaps. Returns tasks_created.
    """
    existing_tasks = session.scalars(
        select(EvidenceTask).where(
            EvidenceTask.assessment_id == assessment_id,
            EvidenceTask.org_id == org_id,
        )
    ).all()

    task_by_spec_id: dict[uuid.UUID, EvidenceTask] = {
        t.baseline_spec_id: t
        for t in existing_tasks
        if t.baseline_spec_id is not None
    }
    task_by_artifact_key: dict[tuple[str, str], EvidenceTask] = {
        (t.title.strip().lower(), t.artifact_type): t
        for t in existing_tasks
    }

    existing_link_keys: set[tuple[uuid.UUID, uuid.UUID]] = set()
    if existing_tasks:
        existing_link_keys = {
            (lnk.task_id, lnk.control_state_id)
            for lnk in session.scalars(
                select(EvidenceTaskStateLink).where(
                    EvidenceTaskStateLink.task_id.in_([t.id for t in existing_tasks])
                )
            ).all()
        }

    new_task_by_artifact_key: dict[tuple[str, str], EvidenceTask] = {}
    tasks_created = 0

    for bc in baseline_controls:
        bc_ctrl_str = ctrl_uuid_to_str.get(bc.control_id)
        if not bc_ctrl_str:
            continue

        specs = session.scalars(
            select(BaselineEvidenceSpec)
            .where(BaselineEvidenceSpec.baseline_control_id == bc.id)
        ).all()

        for spec in specs:
            artifact_key = (spec.artifact_description.strip().lower(), spec.evidence_type)
            session_label = spec.kb_reference or f"{product.name} — initial collection"

            task = (
                task_by_spec_id.get(spec.id)
                or task_by_artifact_key.get(artifact_key)
                or new_task_by_artifact_key.get(artifact_key)
            )

            if task is None:
                task = EvidenceTask(
                    org_id=org_id,
                    assessment_id=assessment_id,
                    baseline_spec_id=spec.id,
                    title=spec.artifact_description,
                    artifact_type=spec.evidence_type,
                    status="open",
                    collection_session=session_label,
                )
                session.add(task)
                session.flush()
                new_task_by_artifact_key[artifact_key] = task
                tasks_created += 1

            # Link to every covered objective (not just the first)
            for obj_key in (bc.objectives or []):
                obj_id_str = objective_lookup.get((bc_ctrl_str, obj_key))
                if not obj_id_str:
                    continue
                cs = existing_states.get(uuid.UUID(obj_id_str))
                if cs is None:
                    continue
                link_key = (task.id, cs.id)
                if link_key not in existing_link_keys:
                    session.add(EvidenceTaskStateLink(
                        task_id=task.id,
                        control_state_id=cs.id,
                    ))
                    existing_link_keys.add(link_key)

    session.flush()
    return tasks_created


def deactivate_org_product(
    session: Session,
    org_id: uuid.UUID,
    product_id: uuid.UUID,
    assessment_id: uuid.UUID,
) -> dict:
    """Decommission a product and revert/archive all its contributions.

    Implements provenance-based reversal:
      - ALL control states this product is a control_state_contributor for →
        needs_review, regardless of current status (pending_evidence, partial, or
        met). Provenance is the canonical signal; a human may have confirmed a
        state as met while coverage still came from this product, but if the
        product is gone it needs review.
      - A control_state with NO contributor row for this product survives untouched.
      - Multi-contributor case (models.py:ControlStateContributor -- more than one
        product can cover the same objective): this product's contributor row is
        removed; if others remain, responsibility is recomputed from their
        classifications (resolve_contributor_responsibility) and change_reason
        names both the departing product and which product(s) still cover it. If
        this was the last contributor, responsibility is left exactly as it was
        (this is the original, pre-multi-contributor behavior, preserved
        unchanged) -- only status moves to needs_review either way.
      - Evidence-state links on ALL affected states → archived. NOTE: this
        remains all-or-nothing per control_state, not scoped to which product's
        evidence it is -- a state with two contributors has ALL its evidence
        archived if EITHER one deactivates, which can archive evidence that
        actually supports the surviving contributor. Pre-existing behavior,
        unchanged by the multi-contributor work; would need evidence-to-product
        provenance (a new capability) to fix. See docs/roadmap.md's multi-tool-
        coverage writeup.
      - Evidence tasks from this product → archived; open ones also closed (na).
        Already correctly scoped per-product (via baseline_control.product_id),
        unaffected by the multi-contributor change.
      - OrgProduct → decommissioned with deactivated_at timestamp.
      - SPRS recomputed (needs_review does not satisfy, so score reflects lost coverage).

    Every step is audited via log_event() with context["via"]="product_deactivation".

    Returns {"controls_flagged": N, "tasks_archived": N, "evidence_links_archived": N}.
    """
    product = session.get(Product, product_id)
    if product is None:
        raise ValueError(f"Product {product_id} not found")

    op = session.scalars(
        select(OrgProduct).where(
            OrgProduct.org_id == org_id,
            OrgProduct.product_id == product_id,
        )
    ).first()
    if op is None:
        raise ValueError("OrgProduct not found")

    now = datetime.now(UTC)
    deactivation_ctx: dict = {
        "via": "product_deactivation",
        "product_name": product.name,
        "product_key": product.key,
        "assessment_id": str(assessment_id),
    }

    # 1. Decommission OrgProduct
    op.status = OrgProductStatus.DECOMMISSIONED
    op.deactivated_at = now
    session.flush()

    log_event(
        session,
        org_id=org_id,
        action="org_product.deactivate",
        entity_type="org_product",
        entity_id=op.id,
        before_value={"status": "active"},
        after_value={"status": "decommissioned"},
        context=deactivation_ctx,
    )

    # 2. Classify control_states this product currently contributes to.
    # This product may not be the ONLY contributor: another product can
    # still cover the same objective (models.py:ControlStateContributor).
    # Either way the status falls to needs_review (Jarrod's explicit
    # decision, 2026-09-16: losing a tool is exactly when a coverage claim
    # deserves a second look, even if another tool still covers it) --
    # they differ only in whether responsibility is recomputed from the
    # survivors or left exactly as it was (the pre-existing, unchanged
    # behavior for the last-contributor-removed case).
    contributor_rows = session.execute(
        select(ControlStateContributor, ControlState)
        .join(ControlState, ControlStateContributor.control_state_id == ControlState.id)
        .where(
            ControlState.assessment_id == assessment_id,
            ControlStateContributor.product_id == product_id,
        )
    ).all()
    sourced_states = [cs for _, cs in contributor_rows]
    sourced_state_ids = [cs.id for cs in sourced_states]

    for contributor, _cs in contributor_rows:
        session.delete(contributor)
    session.flush()

    remaining_by_cs: dict[uuid.UUID, list[tuple[str, str]]] = {}
    if sourced_state_ids:
        for cs_id, classification, other_product_name in session.execute(
            select(
                ControlStateContributor.control_state_id,
                BaselineControl.classification,
                Product.name,
            )
            .join(
                BaselineControl,
                ControlStateContributor.baseline_control_id == BaselineControl.id,
            )
            .join(Product, ControlStateContributor.product_id == Product.id)
            .where(ControlStateContributor.control_state_id.in_(sourced_state_ids))
        ).all():
            remaining_by_cs.setdefault(cs_id, []).append((classification, other_product_name))

    history_rows: list[ControlStateHistory] = []
    controls_flagged = 0

    for cs in sourced_states:
        prev_status = cs.status
        prev_resp = cs.responsibility
        remaining = remaining_by_cs.get(cs.id, [])

        cs.status = ControlStatus.NEEDS_REVIEW
        if remaining:
            cs.responsibility = resolve_contributor_responsibility(
                [classification for classification, _ in remaining]
            )
            other_names = ", ".join(sorted({name for _, name in remaining}))
            reason = f"Contributor removed: {product.name} (still covered by {other_names})"
        else:
            # Last contributor removed -- today's pre-existing behavior,
            # unchanged: responsibility is left exactly as it was, only
            # status moves to needs_review.
            reason = f"Satisfying tool deactivated: {product.name}"

        history_rows.append(
            ControlStateHistory(
                control_state_id=cs.id,
                previous_status=prev_status,
                new_status=ControlStatus.NEEDS_REVIEW,
                previous_responsibility=prev_resp,
                new_responsibility=cs.responsibility,
                change_reason=reason,
            )
        )
        log_event(
            session,
            org_id=org_id,
            action="control_state.update",
            entity_type="control_state",
            entity_id=cs.id,
            before_value={
                "status": prev_status,
                "responsibility": prev_resp,
            },
            after_value={
                "status": ControlStatus.NEEDS_REVIEW,
                "responsibility": cs.responsibility,
                "remaining_contributors": len(remaining),
            },
            context=deactivation_ctx,
        )
        controls_flagged += 1

    session.add_all(history_rows)
    session.flush()

    # 3. Archive evidence_state_link rows on all tool-sourced states
    evidence_links_archived = 0
    if sourced_states:
        link_rows = session.scalars(
            select(EvidenceStateLink).where(
                EvidenceStateLink.control_state_id.in_(sourced_state_ids),
                EvidenceStateLink.is_archived.is_(False),
            )
        ).all()

        for lnk in link_rows:
            lnk.is_archived = True
            lnk.archived_at = now
            lnk.archived_by_product = product_id

            log_event(
                session,
                org_id=org_id,
                action="evidence_state_link.archive",
                entity_type="evidence_state_link",
                entity_id=lnk.id,
                before_value={"is_archived": False},
                after_value={"is_archived": True, "archived_by_product": str(product_id)},
                context={**deactivation_ctx, "control_state_id": str(lnk.control_state_id)},
            )
            evidence_links_archived += 1

    session.flush()

    # 4. Archive evidence tasks from this product (baseline_spec → baseline_control → product)
    tasks = session.scalars(
        select(EvidenceTask)
        .join(BaselineEvidenceSpec, EvidenceTask.baseline_spec_id == BaselineEvidenceSpec.id)
        .join(BaselineControl, BaselineEvidenceSpec.baseline_control_id == BaselineControl.id)
        .where(
            EvidenceTask.assessment_id == assessment_id,
            EvidenceTask.org_id == org_id,
            BaselineControl.product_id == product_id,
            EvidenceTask.is_archived.is_(False),
        )
    ).all()

    tasks_archived = 0
    for task in tasks:
        prev_status = task.status
        task.is_archived = True
        task.archived_at = now
        if task.status == "open":
            task.status = "na"

        log_event(
            session,
            org_id=org_id,
            action="evidence_task.archive",
            entity_type="evidence_task",
            entity_id=task.id,
            before_value={"status": prev_status, "is_archived": False},
            after_value={"status": task.status, "is_archived": True},
            context={**deactivation_ctx, "collection_session": task.collection_session},
        )
        tasks_archived += 1

    session.flush()

    # 5. Recompute SPRS — needs_review does not satisfy, score drops
    recompute_sprs(session, assessment_id)

    return {
        "controls_flagged": controls_flagged,
        "tasks_archived": tasks_archived,
        "evidence_links_archived": evidence_links_archived,
    }


def move_org_product_version(
    session: Session,
    org_id: uuid.UUID,
    product_id: uuid.UUID,
    assessment_id: uuid.UUID,
    target_version_id: uuid.UUID,
) -> dict:
    """Move an already-active tenant's OrgProduct from its currently
    pinned baseline version to a different one (normally newer).

    Roadmap item P (baseline versioning): the deliberate, per-tenant,
    human action §3 requires for "how does a tenant move to a new
    version." Never automatic on import -- OrgProduct.baseline_version_id
    only ever changes here or on first activation (engine.py:
    activate_org_product).

    Same precedent as deactivate_org_product's "losing a tool is exactly
    when a coverage claim deserves a second look": every objective whose
    claim actually differs between the old and new version lands in
    needs_review, never silently met/pending_evidence. An objective whose
    claim is IDENTICAL in both versions (same classification, same
    objective-key coverage) is left completely untouched -- its
    ControlStateContributor row keeps pointing at the OLD version's
    baseline_control row, which is fine and permanent (that row is
    immutable and its text is identical to the new version's equivalent
    row for that control by definition of "unchanged"); there is nothing
    to gain and real blast-radius to lose by force-touching it.

    Per objective, one of four things happens:
      - Newly covered (old version didn't claim it, new one does): a
        contributor row is added exactly as first activation would add
        one -- contributor_added_status/resolve_contributor_responsibility
        against the full current contributor set.
      - Coverage dropped (old version claimed it, new one doesn't): the
        contributor row is removed; if other products still cover it,
        responsibility is recomputed from them (same as
        deactivate_org_product's multi-contributor case); status ->
        needs_review either way.
      - Classification changed (both claim it, differently): the
        contributor row is repointed to the new version's baseline_control
        row and responsibility is recomputed from the full current set;
        status -> needs_review.
      - Unchanged: left alone entirely (see above).

    Evidence survives throughout -- nothing here archives an
    evidence_state_link or an evidence_task (contrast
    deactivate_org_product, which does both). New evidence specs the new
    version introduces (for newly-covered or reclassified controls) are
    fanned into tasks via the same _fanout_evidence_tasks used by
    activation; anything already collected under the old version's specs
    is untouched and still linked.

    Returns {"controls_gained": N, "controls_lost": N, "controls_changed": N,
    "tasks_created": N}.
    """
    op = session.scalars(
        select(OrgProduct).where(
            OrgProduct.org_id == org_id, OrgProduct.product_id == product_id
        )
    ).first()
    if op is None or op.status != OrgProductStatus.ACTIVE:
        raise ValueError("Active OrgProduct not found")
    if op.baseline_version_id == target_version_id:
        raise ValueError("OrgProduct is already on this baseline version")

    target_version = session.get(ProductBaselineVersion, target_version_id)
    if target_version is None or target_version.product_id != product_id:
        raise ValueError(f"Version {target_version_id} does not belong to product {product_id}")

    product = session.get(Product, product_id)
    if product is None:
        raise ValueError(f"Product {product_id} not found")

    from_version_id = op.baseline_version_id
    from_version = (
        session.get(ProductBaselineVersion, from_version_id) if from_version_id else None
    )
    from_number = from_version.version_number if from_version else None
    to_number = target_version.version_number

    move_ctx = {
        "via": "baseline_version_move",
        "product_name": product.name,
        "product_key": product.key,
        "assessment_id": str(assessment_id),
        "from_version": from_number,
        "to_version": to_number,
    }

    def _claimed(version_id: uuid.UUID | None) -> dict[uuid.UUID, BaselineControl]:
        if version_id is None:
            return {}
        return {
            bc.control_id: bc
            for bc in session.scalars(
                select(BaselineControl)
                .where(BaselineControl.baseline_version_id == version_id)
                .where(BaselineControl.classification != "customer_owns")
                .where(BaselineControl.coverage_basis != "platform_only")
            )
        }

    old_claims = _claimed(from_version_id)
    new_claims = _claimed(target_version_id)
    all_control_ids = set(old_claims) | set(new_claims)

    controls = (
        session.scalars(select(Control).where(Control.id.in_(all_control_ids))).all()
        if all_control_ids
        else []
    )
    ctrl_uuid_to_str: dict[uuid.UUID, str] = {c.id: c.control_id for c in controls}

    objectives = (
        session.scalars(
            select(AssessmentObjective).where(
                AssessmentObjective.control_id.in_(all_control_ids)
            )
        ).all()
        if all_control_ids
        else []
    )
    objective_lookup: dict[tuple[str, str], uuid.UUID] = {
        (ctrl_uuid_to_str[o.control_id], o.objective_key): o.id
        for o in objectives
        if o.control_id in ctrl_uuid_to_str
    }
    obj_ids_needed = set(objective_lookup.values())
    control_states_by_obj: dict[uuid.UUID, ControlState] = {
        cs.objective_id: cs
        for cs in (
            session.scalars(
                select(ControlState).where(
                    ControlState.assessment_id == assessment_id,
                    ControlState.objective_id.in_(list(obj_ids_needed)),
                )
            ).all()
            if obj_ids_needed
            else []
        )
    }

    cs_ids = [cs.id for cs in control_states_by_obj.values()]
    # (product_id, contributor_row_id, classification) per control_state --
    # every CURRENT contributor (any product), to resolve responsibility
    # against the full set, same reasoning _run_loop uses.
    contributors_by_cs: dict[uuid.UUID, list[tuple[uuid.UUID, uuid.UUID, str]]] = {}
    if cs_ids:
        for cs_id, contrib_id, contrib_product_id, classification in session.execute(
            select(
                ControlStateContributor.control_state_id,
                ControlStateContributor.id,
                ControlStateContributor.product_id,
                BaselineControl.classification,
            )
            .join(
                BaselineControl,
                ControlStateContributor.baseline_control_id == BaselineControl.id,
            )
            .where(ControlStateContributor.control_state_id.in_(cs_ids))
        ).all():
            contributors_by_cs.setdefault(cs_id, []).append(
                (contrib_product_id, contrib_id, classification)
            )

    history_rows: list[ControlStateHistory] = []
    new_contributor_rows: list[ControlStateContributor] = []
    contributor_ids_to_delete: list[uuid.UUID] = []
    bcs_needing_fanout: dict[uuid.UUID, BaselineControl] = {}
    controls_gained = 0
    controls_lost = 0
    controls_changed = 0

    for ctrl_id in all_control_ids:
        old_bc = old_claims.get(ctrl_id)
        new_bc = new_claims.get(ctrl_id)
        ctrl_str = ctrl_uuid_to_str.get(ctrl_id)
        if ctrl_str is None:
            continue
        old_objs = set(old_bc.objectives or []) if old_bc else set()
        new_objs = set(new_bc.objectives or []) if new_bc else set()

        for obj_key in old_objs | new_objs:
            obj_id = objective_lookup.get((ctrl_str, obj_key))
            if obj_id is None:
                continue
            cs = control_states_by_obj.get(obj_id)
            if cs is None:
                continue

            current = contributors_by_cs.get(cs.id, [])
            this_contributor = next((c for c in current if c[0] == product_id), None)
            in_old, in_new = obj_key in old_objs, obj_key in new_objs

            if in_old and not in_new:
                if this_contributor is None:
                    continue
                _, contrib_row_id, _ = this_contributor
                contributor_ids_to_delete.append(contrib_row_id)
                remaining = [c for c in current if c[0] != product_id]
                prev_status, prev_resp = cs.status, cs.responsibility
                cs.status = ControlStatus.NEEDS_REVIEW
                if remaining:
                    cs.responsibility = resolve_contributor_responsibility(
                        [cls for _, _, cls in remaining]
                    )
                    reason = (
                        f"Version move ({from_number}->{to_number}): {product.name} no "
                        "longer covers this (still covered by other tool(s))"
                    )
                else:
                    reason = (
                        f"Version move ({from_number}->{to_number}): {product.name} no "
                        "longer covers this"
                    )
                history_rows.append(
                    ControlStateHistory(
                        control_state_id=cs.id,
                        previous_status=prev_status,
                        new_status=cs.status,
                        previous_responsibility=prev_resp,
                        new_responsibility=cs.responsibility,
                        change_reason=reason,
                    )
                )
                controls_lost += 1

            elif not in_old and in_new:
                if this_contributor is not None:
                    continue
                had_prior = bool(current)
                prev_status, prev_resp = cs.status, cs.responsibility
                new_contributor_rows.append(
                    ControlStateContributor(
                        control_state_id=cs.id,
                        product_id=product_id,
                        baseline_control_id=new_bc.id,
                    )
                )
                all_classes = [cls for _, _, cls in current] + [new_bc.classification]
                cs.status = contributor_added_status(prev_status, not had_prior)
                cs.responsibility = resolve_contributor_responsibility(all_classes)
                history_rows.append(
                    ControlStateHistory(
                        control_state_id=cs.id,
                        previous_status=prev_status,
                        new_status=cs.status,
                        previous_responsibility=prev_resp,
                        new_responsibility=cs.responsibility,
                        change_reason=(
                            f"Version move ({from_number}->{to_number}): {product.name} "
                            "now covers this"
                        ),
                    )
                )
                bcs_needing_fanout[ctrl_id] = new_bc
                controls_gained += 1

            elif this_contributor is not None and old_bc.classification != new_bc.classification:
                _, contrib_row_id, _ = this_contributor
                contrib = session.get(ControlStateContributor, contrib_row_id)
                contrib.baseline_control_id = new_bc.id
                new_classes = [
                    new_bc.classification if pid == product_id else cls
                    for pid, _, cls in current
                ]
                prev_status, prev_resp = cs.status, cs.responsibility
                cs.status = ControlStatus.NEEDS_REVIEW
                cs.responsibility = resolve_contributor_responsibility(new_classes)
                history_rows.append(
                    ControlStateHistory(
                        control_state_id=cs.id,
                        previous_status=prev_status,
                        new_status=cs.status,
                        previous_responsibility=prev_resp,
                        new_responsibility=cs.responsibility,
                        change_reason=(
                            f"Version move ({from_number}->{to_number}): {product.name}'s "
                            "mapping changed for this control"
                        ),
                    )
                )
                bcs_needing_fanout[ctrl_id] = new_bc
                controls_changed += 1
            # else: unchanged claim for this objective -- left alone (see
            # this function's own docstring for why).

    if contributor_ids_to_delete:
        for row in session.scalars(
            select(ControlStateContributor).where(
                ControlStateContributor.id.in_(contributor_ids_to_delete)
            )
        ):
            session.delete(row)
    session.add_all(new_contributor_rows)
    session.add_all(history_rows)
    session.flush()

    # Only control_states this move actually touched -- history_rows is
    # already exactly that set (unchanged claims never append one).
    cs_by_id = {cs.id: cs for cs in control_states_by_obj.values()}
    for h in history_rows:
        cs = cs_by_id.get(h.control_state_id)
        if cs is None:
            continue
        log_event(
            session,
            org_id=org_id,
            action="control_state.update",
            entity_type="control_state",
            entity_id=cs.id,
            before_value={
                "status": h.previous_status, "responsibility": h.previous_responsibility
            },
            after_value={"status": h.new_status, "responsibility": h.new_responsibility},
            context={**move_ctx, "change_reason": h.change_reason},
        )

    op.baseline_version_id = target_version_id
    session.flush()

    log_event(
        session,
        org_id=org_id,
        action="org_product.baseline_version_move",
        entity_type="org_product",
        entity_id=op.id,
        before_value={"baseline_version_id": str(from_version_id) if from_version_id else None,
                       "version_number": from_number},
        after_value={"baseline_version_id": str(target_version_id), "version_number": to_number},
        context=move_ctx,
    )

    tasks_created = 0
    if bcs_needing_fanout:
        tasks_created = _fanout_evidence_tasks(
            session,
            org_id=org_id,
            assessment_id=assessment_id,
            product=product,
            baseline_controls=list(bcs_needing_fanout.values()),
            ctrl_uuid_to_str=ctrl_uuid_to_str,
            objective_lookup={k: str(v) for k, v in objective_lookup.items()},
            existing_states=control_states_by_obj,
        )

    recompute_sprs(session, assessment_id)

    return {
        "controls_gained": controls_gained,
        "controls_lost": controls_lost,
        "controls_changed": controls_changed,
        "tasks_created": tasks_created,
    }


def backfill_missing_control_states(
    session: Session, *, reason: str, dry_run: bool = True
) -> list[dict]:
    """For every existing assessment, create a control_state row for any
    framework objective that doesn't already have one -- e.g. after
    seed_catalog() adds a new objective to a control that's missing one
    (see docs/roadmap.md's 2026-09 catalog-reconciliation writeup for the
    incident this was built for: 4 objectives absent from cmmc_l2.yaml
    since the catalog was first authored).

    Never defaults a backfilled objective to met: not_met/customer_owns,
    the exact same baseline _seed_control_states() uses for a brand-new
    assessment -- defaulting to met would silently assert an evaluation
    that never happened.

    Deliberately does NOT re-run the magic loop (_run_loop) for
    already-active org_products against the new rows. Checked, not
    assumed: as of writing, no baseline_control row in any file under
    baselines/ asserts real (provider_satisfies/shared/pending_evidence)
    coverage of any objective this function has ever been used to
    backfill -- the one match (RocketCyber's IA family entry) is
    classification=customer_owns, which _run_loop's own query already
    excludes. If a future baseline product ever does cover a backfilled
    objective, the next activate_org_product call for that product picks
    it up normally through the existing path -- nothing here needs to
    anticipate that.

    Each affected assessment gets exactly one audit_log entry
    (action="control_state.backfill") recording the before/after SPRS
    score and which objective ids were added, with *reason* in context --
    so a score change is explainable from the audit log, not mysterious.
    Uses recompute_sprs() (the single write path for assessment.sprs_score)
    rather than computing the score by hand, which also means each call
    writes a new SprsSnapshot row reflecting the corrected score going
    forward -- existing historical snapshots (migration 0028) are never
    touched, by construction: this function only ever INSERTs.

    dry_run=True (the default) computes and returns the full result set,
    then rolls back -- nothing is written. Pass dry_run=False to commit.
    Actor is explicitly "system": this is a maintenance operation with no
    human actor behind the specific click, the same convention audit.py's
    own docstring describes for a deliberate system-triggered action.

    Returns one dict per assessment that had objectives added:
        {"assessment_id", "org_id", "org_name", "assessment_name",
         "objectives_added", "added_objective_keys", "sprs_before", "sprs_after"}
    Assessments needing no backfill are omitted entirely.
    """
    results: list[dict] = []

    assessments = session.scalars(select(Assessment)).all()
    for assessment in assessments:
        objectives = session.execute(
            select(AssessmentObjective, Control.control_id)
            .join(Control, AssessmentObjective.control_id == Control.id)
            .where(Control.framework_id == assessment.framework_id)
        ).all()
        existing_obj_ids = set(
            session.scalars(
                select(ControlState.objective_id).where(
                    ControlState.assessment_id == assessment.id
                )
            ).all()
        )
        missing = [(obj, ctrl_id) for obj, ctrl_id in objectives if obj.id not in existing_obj_ids]
        if not missing:
            continue

        sprs_before = assessment.sprs_score

        session.add_all(
            [
                ControlState(
                    assessment_id=assessment.id,
                    org_id=assessment.org_id,
                    objective_id=obj.id,
                    status=ControlStatus.NOT_MET,
                    responsibility=Responsibility.CUSTOMER_OWNS,
                )
                for obj, _ in missing
            ]
        )
        session.flush()

        sprs_after = recompute_sprs(session, assessment.id)

        org = session.get(Organization, assessment.org_id)
        added_keys = [f"{ctrl_id}[{obj.objective_key}]" for obj, ctrl_id in missing]
        log_event(
            session,
            org_id=assessment.org_id,
            action="control_state.backfill",
            entity_type="assessment",
            entity_id=assessment.id,
            before_value={"sprs_score": sprs_before, "objective_count": len(existing_obj_ids)},
            after_value={
                "sprs_score": sprs_after,
                "objective_count": len(existing_obj_ids) + len(missing),
                "added_objective_ids": [str(obj.id) for obj, _ in missing],
                "added_objective_keys": added_keys,
            },
            context={"via": "cli", "reason": reason},
            actor="system",
            actor_type="system",
        )

        results.append(
            {
                "assessment_id": str(assessment.id),
                "org_id": str(assessment.org_id),
                "org_name": org.name if org else str(assessment.org_id),
                "assessment_name": assessment.name,
                "objectives_added": len(missing),
                "added_objective_keys": added_keys,
                "sprs_before": sprs_before,
                "sprs_after": sprs_after,
            }
        )

    if dry_run:
        session.rollback()
    else:
        session.commit()

    return results
