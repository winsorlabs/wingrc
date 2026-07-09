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

from .assessment import ControlStatus, OrgProductStatus, Responsibility, magic_loop_updates
from .models import (
    Assessment,
    AssessmentObjective,
    BaselineControl,
    BaselineEvidenceSpec,
    Control,
    ControlState,
    ControlStateHistory,
    EvidenceTask,
    OrgProduct,
    Product,
)


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

    Idempotent on control_state (overwrites with same values on re-activation).
    Evidence tasks are deduplicated by (assessment_id, baseline_spec_id)
    to avoid accumulating duplicates on repeated calls.

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

    return _run_loop(session, org_id, product_id, assessment_id)


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

    # --- seed evidence tasks (deduplicated) ---
    existing_spec_ids: set[uuid.UUID] = {
        t.baseline_spec_id
        for t in session.scalars(
            select(EvidenceTask).where(
                EvidenceTask.assessment_id == assessment_id,
                EvidenceTask.org_id == org_id,
                EvidenceTask.baseline_spec_id.is_not(None),
            )
        ).all()
        if t.baseline_spec_id is not None
    }

    collection_session = f"{product.name} — initial collection"
    tasks_created = 0

    for bc in baseline_controls:
        bc_ctrl_str = ctrl_uuid_to_str.get(bc.control_id)
        if not bc_ctrl_str:
            continue

        specs = session.scalars(
            select(BaselineEvidenceSpec)
            .where(BaselineEvidenceSpec.baseline_control_id == bc.id)
        ).all()

        # Find the control_state to link this task to (first objective of the control)
        linked_cs_id: uuid.UUID | None = None
        if bc.objectives:
            first_obj_id_str = objective_lookup.get((bc_ctrl_str, bc.objectives[0]))
            if first_obj_id_str:
                cs = existing_states.get(uuid.UUID(first_obj_id_str))
                if cs:
                    linked_cs_id = cs.id

        for spec in specs:
            if spec.id in existing_spec_ids:
                continue  # already seeded; skip to keep activate idempotent

            session.add(
                EvidenceTask(
                    org_id=org_id,
                    assessment_id=assessment_id,
                    control_state_id=linked_cs_id,
                    baseline_spec_id=spec.id,
                    title=spec.artifact_description,
                    artifact_type=spec.evidence_type,
                    status="pending",
                    collection_session=collection_session,
                )
            )
            existing_spec_ids.add(spec.id)
            tasks_created += 1

    session.flush()
    return {"objectives_updated": objectives_updated, "tasks_created": tasks_created}
