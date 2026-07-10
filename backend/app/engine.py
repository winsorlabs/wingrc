"""DB adapter: assessment lifecycle and magic loop.

Bridges the pure functions in assessment.py with the SQLAlchemy session.
This is the only module that performs DB writes for assessment operations.

Two entry points:
  start_assessment   — create an Assessment + seed all objective states,
                       then fire the loop for every already-active product.
  activate_org_product — mark a product active and fire the loop for one
                         assessment, updating states, writing history,
                         and seeding evidence tasks.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from .assessment import (
    ControlStatus,
    OrgProductStatus,
    Responsibility,
    compute_sprs,
    magic_loop_updates,
)
from .audit import log_event
from .models import (
    Assessment,
    AssessmentObjective,
    BaselineControl,
    BaselineEvidenceSpec,
    Control,
    ControlState,
    ControlStateHistory,
    EvidenceStateLink,
    EvidenceTask,
    EvidenceTaskStateLink,
    OrgProduct,
    Product,
)


def recompute_sprs(session: Session, assessment_id: uuid.UUID) -> int:
    """Recompute and persist SPRS score for one assessment.

    Queries all control_state rows, runs the objective→control rollup via
    compute_sprs(), writes the result to assessment.sprs_score, and flushes.
    Returns the computed integer score.
    """
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

    assessment = session.get(Assessment, assessment_id)
    if assessment is not None:
        assessment.sprs_score = score
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
        _run_loop(session, org_id, op.product_id, assessment.id)

    recompute_sprs(session, assessment.id)
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

    Returns {"objectives_updated": N, "tasks_created": N}.
    """
    op = session.scalars(
        select(OrgProduct).where(
            OrgProduct.org_id == org_id,
            OrgProduct.product_id == product_id,
        )
    ).first()
    if op is None:
        op = OrgProduct(org_id=org_id, product_id=product_id)
        session.add(op)
    op.status = OrgProductStatus.ACTIVE
    op.configured = True
    op.activated_at = datetime.now(UTC)
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
            BaselineControl.product_id == product_id,
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

    result = _run_loop(session, org_id, product_id, assessment_id)

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
    assessment_id: uuid.UUID,
) -> dict:
    """Core magic-loop logic: update states, write history, seed tasks.

    Separated from activate_org_product so start_assessment can call it for
    each pre-existing active product without repeating the OrgProduct update.
    """
    product = session.get(Product, product_id)
    if product is None:
        raise ValueError(f"Product {product_id} not found")

    # --- baseline controls for this product ---
    # Exclude customer_owns (vendor disclaims) and platform_only (vendor covers
    # its own platform, not the customer's CUI systems).
    baseline_controls = session.scalars(
        select(BaselineControl)
        .where(BaselineControl.product_id == product_id)
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

    # --- pure function ---
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

    # --- apply updates + write history ---
    history_rows: list[ControlStateHistory] = []
    objectives_updated = 0

    for upd in updates:
        obj_id = uuid.UUID(upd["objective_id"])
        state = existing_states.get(obj_id)
        if state is None:
            continue

        prev_status = state.status
        prev_resp = state.responsibility

        state.status = upd["status"]
        state.responsibility = upd["responsibility"]
        state.sourced_from_product_id = product_id

        history_rows.append(
            ControlStateHistory(
                control_state_id=state.id,
                previous_status=prev_status,
                new_status=upd["status"],
                previous_responsibility=prev_resp,
                new_responsibility=upd["responsibility"],
                change_reason=f"Magic loop: {product.name} activated",
            )
        )
        objectives_updated += 1

    session.add_all(history_rows)
    session.flush()

    # --- seed evidence tasks (deduplicated, multi-objective links) ---
    #
    # Dedup strategy:
    #   1. By baseline_spec_id: a previously seeded spec never creates a new task
    #      (idempotency on re-activation).
    #   2. By (title.lower(), artifact_type): if two specs describe the same
    #      artifact, they share one task (evidence minimisation across controls).
    #   3. Within-run: new tasks created this call are tracked so a second spec
    #      with the same artifact key reuses rather than duplicates.
    #
    # Existing task status is NEVER modified — a 'collected' task stays collected.
    # Only new control_state links are added for gaps.

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
    return {"objectives_updated": objectives_updated, "tasks_created": tasks_created}


def deactivate_org_product(
    session: Session,
    org_id: uuid.UUID,
    product_id: uuid.UUID,
    assessment_id: uuid.UUID,
) -> dict:
    """Decommission a product and revert/archive all its contributions.

    Implements provenance-based reversal:
      - ALL control states with sourced_from_product_id == this product → needs_review,
        regardless of current status (pending_evidence, partial, or met). The sourced
        pointer is the canonical signal; a human may have confirmed a state as met while
        coverage still came from this product, but if the product is gone it needs review.
      - Only states with sourced_from_product_id IS NULL survive untouched.
      - Evidence-state links on ALL tool-sourced states → archived.
      - Evidence tasks from this product → archived; open ones also closed (na).
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

    # 2. Classify control_states sourced from this product
    sourced_states = session.scalars(
        select(ControlState).where(
            ControlState.assessment_id == assessment_id,
            ControlState.sourced_from_product_id == product_id,
        )
    ).all()

    history_rows: list[ControlStateHistory] = []
    controls_flagged = 0

    for cs in sourced_states:
        prev_status = cs.status
        history_rows.append(
            ControlStateHistory(
                control_state_id=cs.id,
                previous_status=prev_status,
                new_status=ControlStatus.NEEDS_REVIEW,
                previous_responsibility=cs.responsibility,
                new_responsibility=cs.responsibility,
                change_reason=f"Satisfying tool deactivated: {product.name}",
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
                "sourced_from_product_id": str(product_id),
            },
            after_value={
                "status": ControlStatus.NEEDS_REVIEW,
                "sourced_from_product_id": None,
            },
            context=deactivation_ctx,
        )
        cs.status = ControlStatus.NEEDS_REVIEW
        cs.sourced_from_product_id = None
        controls_flagged += 1

    session.add_all(history_rows)
    session.flush()

    # 3. Archive evidence_state_link rows on all tool-sourced states
    evidence_links_archived = 0
    if sourced_states:
        sourced_state_ids = [cs.id for cs in sourced_states]
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
