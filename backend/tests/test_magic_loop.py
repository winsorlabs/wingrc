"""Integration tests: magic loop end-to-end against real Postgres.

These tests require a running Postgres with the schema migrated.
They are marked `integration` and skipped automatically if no DB is available.

What is proven:
  1. start_assessment seeds control_state for every framework objective.
  2. activate_org_product flips product-covered objectives to pending_evidence.
  3. customer_owns objectives are never touched.
  4. control_state_history rows are written for each status change.
  5. evidence_tasks are seeded from baseline_evidence_specs, collection-batched.
  6. task creation is idempotent — re-activating does not create duplicate tasks.
  7. start_assessment auto-fires the loop for pre-existing active products.
"""
from __future__ import annotations

from datetime import datetime, UTC

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.assessment import OrgProductStatus
from app.engine import activate_org_product, start_assessment
from app.models import (
    Assessment,
    AssessmentObjective,
    BaselineControl,
    BaselineEvidenceSpec,
    Control,
    ControlState,
    ControlStateHistory,
    EvidenceTask,
    Framework,
    OrgProduct,
    Organization,
    Product,
)

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Reference-data fixture (created fresh inside each test's transaction)
# ---------------------------------------------------------------------------


@pytest.fixture
def ref(db_session: Session) -> dict:
    """Minimal reference data for magic-loop tests.

    Two controls in one framework:
      AC.L2-3.1.1  (shared by the test product, objectives a + b)
      IA.L2-3.5.1  (customer_owns, objective a)

    One product with:
      baseline_control for AC → shared, two objectives, one evidence spec
      baseline_control for IA → customer_owns, no evidence spec
    """
    org = Organization(name="Test MSP — magic loop")
    fw = Framework(
        key="nist-800-171-r2-test",
        name="NIST 800-171 Rev 2",
        version="r2",
    )
    db_session.add_all([org, fw])
    db_session.flush()

    ac = Control(
        framework_id=fw.id,
        control_id="AC.L2-3.1.1",
        family="AC",
        title="Limit system access to authorized users",
        requirement_text="Limit system access...",
        sprs_weight=5,
        sequence_order=1,
    )
    ia = Control(
        framework_id=fw.id,
        control_id="IA.L2-3.5.1",
        family="IA",
        title="Identify system users",
        requirement_text="Identify system users...",
        sprs_weight=3,
        sequence_order=2,
    )
    db_session.add_all([ac, ia])
    db_session.flush()

    ac_obj_a = AssessmentObjective(
        control_id=ac.id, objective_key="a", text="AC.L2-3.1.1[a]"
    )
    ac_obj_b = AssessmentObjective(
        control_id=ac.id, objective_key="b", text="AC.L2-3.1.1[b]"
    )
    ia_obj_a = AssessmentObjective(
        control_id=ia.id, objective_key="a", text="IA.L2-3.5.1[a]"
    )
    db_session.add_all([ac_obj_a, ac_obj_b, ia_obj_a])
    db_session.flush()

    product = Product(
        framework_id=fw.id,
        key="test-portal-product",
        name="Test Portal",
        provider="Test Inc",
        category="ESP",
        asset_type="SPA",
        role="Test product for magic-loop integration tests",
    )
    db_session.add(product)
    db_session.flush()

    bc_ac = BaselineControl(
        product_id=product.id,
        control_id=ac.id,
        objectives=["a", "b"],
        classification="shared",
        candidate_state="pending_evidence",
        provider_contribution="Portal provides RBAC for access control.",
        customer_action="Assign roles and review access quarterly.",
    )
    bc_ia = BaselineControl(
        product_id=product.id,
        control_id=ia.id,
        objectives=["a"],
        classification="customer_owns",
        candidate_state="not_satisfied_by_product",
        note="Test Portal does not manage identity. Customer IdP owns IA.",
    )
    db_session.add_all([bc_ac, bc_ia])
    db_session.flush()

    spec = BaselineEvidenceSpec(
        baseline_control_id=bc_ac.id,
        artifact_description="Portal user/role list",
        evidence_type="export",
        kb_reference="How to export roles from the portal",
    )
    db_session.add(spec)
    db_session.flush()

    op = OrgProduct(
        org_id=org.id, product_id=product.id, status="candidate"
    )
    db_session.add(op)
    db_session.flush()

    return {
        "org": org,
        "framework": fw,
        "ac": ac,
        "ia": ia,
        "ac_obj_a": ac_obj_a,
        "ac_obj_b": ac_obj_b,
        "ia_obj_a": ia_obj_a,
        "product": product,
        "bc_ac": bc_ac,
        "bc_ia": bc_ia,
        "spec": spec,
        "org_product": op,
    }


# ---------------------------------------------------------------------------
# 1. Assessment instantiation
# ---------------------------------------------------------------------------


def test_start_assessment_creates_assessment_row(db_session: Session, ref: dict):
    a = start_assessment(
        db_session, ref["org"].id, ref["framework"].id, "Q1 Assessment"
    )
    fetched = db_session.get(Assessment, a.id)
    assert fetched is not None
    assert fetched.status == "in_progress"
    assert fetched.assessment_type == "self"
    assert fetched.org_id == ref["org"].id


def test_start_assessment_seeds_all_objectives(db_session: Session, ref: dict):
    """Every framework objective gets a not_met/customer_owns control_state."""
    a = start_assessment(
        db_session, ref["org"].id, ref["framework"].id, "Q1"
    )
    states = db_session.scalars(
        select(ControlState).where(ControlState.assessment_id == a.id)
    ).all()

    # 3 objectives: AC[a], AC[b], IA[a]
    assert len(states) == 3
    assert all(s.status == "not_met" for s in states)
    assert all(s.responsibility == "customer_owns" for s in states)
    assert all(s.sourced_from_product_id is None for s in states)


def test_start_assessment_no_tasks_without_active_product(
    db_session: Session, ref: dict
):
    """No evidence tasks when no product is active at assessment start."""
    a = start_assessment(
        db_session, ref["org"].id, ref["framework"].id, "Q1"
    )
    tasks = db_session.scalars(
        select(EvidenceTask).where(EvidenceTask.assessment_id == a.id)
    ).all()
    assert tasks == []


# ---------------------------------------------------------------------------
# 2. Magic loop via activate_org_product
# ---------------------------------------------------------------------------


def test_magic_loop_flips_covered_objectives_to_pending(
    db_session: Session, ref: dict
):
    """Product-covered objectives become pending_evidence; customer_owns unchanged."""
    a = start_assessment(
        db_session, ref["org"].id, ref["framework"].id, "Q1"
    )
    result = activate_org_product(
        db_session, ref["org"].id, ref["product"].id, a.id
    )

    states = {
        cs.objective_id: cs
        for cs in db_session.scalars(
            select(ControlState).where(ControlState.assessment_id == a.id)
        ).all()
    }

    ac_a = states[ref["ac_obj_a"].id]
    ac_b = states[ref["ac_obj_b"].id]
    ia_a = states[ref["ia_obj_a"].id]

    assert ac_a.status == "pending_evidence"
    assert ac_a.responsibility == "shared"
    assert ac_a.sourced_from_product_id == ref["product"].id

    assert ac_b.status == "pending_evidence"
    assert ac_b.responsibility == "shared"

    # customer_owns — must not be touched
    assert ia_a.status == "not_met"
    assert ia_a.responsibility == "customer_owns"
    assert ia_a.sourced_from_product_id is None

    assert result["objectives_updated"] == 2


def test_magic_loop_writes_org_product_active(db_session: Session, ref: dict):
    a = start_assessment(
        db_session, ref["org"].id, ref["framework"].id, "Q1"
    )
    activate_org_product(
        db_session, ref["org"].id, ref["product"].id, a.id
    )
    op = db_session.scalars(
        select(OrgProduct).where(
            OrgProduct.org_id == ref["org"].id,
            OrgProduct.product_id == ref["product"].id,
        )
    ).first()
    assert op.status == "active"
    assert op.configured is True
    assert op.activated_at is not None


# ---------------------------------------------------------------------------
# 3. control_state_history
# ---------------------------------------------------------------------------


def test_magic_loop_writes_history_for_each_change(db_session: Session, ref: dict):
    a = start_assessment(
        db_session, ref["org"].id, ref["framework"].id, "Q1"
    )
    activate_org_product(
        db_session, ref["org"].id, ref["product"].id, a.id
    )

    ac_a_state = db_session.scalars(
        select(ControlState).where(
            ControlState.assessment_id == a.id,
            ControlState.objective_id == ref["ac_obj_a"].id,
        )
    ).first()

    history = db_session.scalars(
        select(ControlStateHistory).where(
            ControlStateHistory.control_state_id == ac_a_state.id
        )
    ).all()

    assert len(history) == 1
    h = history[0]
    assert h.previous_status == "not_met"
    assert h.new_status == "pending_evidence"
    assert h.previous_responsibility == "customer_owns"
    assert h.new_responsibility == "shared"
    assert "Test Portal" in h.change_reason


def test_customer_owns_gets_no_history(db_session: Session, ref: dict):
    """History is only written for changed states; customer_owns never changes."""
    a = start_assessment(
        db_session, ref["org"].id, ref["framework"].id, "Q1"
    )
    activate_org_product(
        db_session, ref["org"].id, ref["product"].id, a.id
    )

    ia_a_state = db_session.scalars(
        select(ControlState).where(
            ControlState.assessment_id == a.id,
            ControlState.objective_id == ref["ia_obj_a"].id,
        )
    ).first()

    history = db_session.scalars(
        select(ControlStateHistory).where(
            ControlStateHistory.control_state_id == ia_a_state.id
        )
    ).all()
    assert history == []


# ---------------------------------------------------------------------------
# 4. Evidence tasks
# ---------------------------------------------------------------------------


def test_magic_loop_seeds_evidence_tasks(db_session: Session, ref: dict):
    """One task per baseline_evidence_spec is created for covered controls."""
    a = start_assessment(
        db_session, ref["org"].id, ref["framework"].id, "Q1"
    )
    result = activate_org_product(
        db_session, ref["org"].id, ref["product"].id, a.id
    )

    tasks = db_session.scalars(
        select(EvidenceTask).where(EvidenceTask.assessment_id == a.id)
    ).all()

    assert result["tasks_created"] == 1
    assert len(tasks) == 1
    t = tasks[0]
    assert t.title == "Portal user/role list"
    assert t.artifact_type == "export"
    assert t.status == "pending"
    assert t.collection_session == "Test Portal — initial collection"
    assert t.baseline_spec_id == ref["spec"].id
    assert t.org_id == ref["org"].id


def test_customer_owns_generates_no_tasks(db_session: Session, ref: dict):
    """customer_owns baseline_controls produce zero evidence tasks."""
    a = start_assessment(
        db_session, ref["org"].id, ref["framework"].id, "Q1"
    )
    activate_org_product(
        db_session, ref["org"].id, ref["product"].id, a.id
    )
    tasks = db_session.scalars(
        select(EvidenceTask).where(EvidenceTask.assessment_id == a.id)
    ).all()
    # Only the shared AC control has a spec → one task
    assert len(tasks) == 1
    assert tasks[0].artifact_type == "export"


# ---------------------------------------------------------------------------
# 5. Idempotency
# ---------------------------------------------------------------------------


def test_double_activation_does_not_duplicate_tasks(db_session: Session, ref: dict):
    """Calling activate_org_product twice does not create duplicate tasks."""
    a = start_assessment(
        db_session, ref["org"].id, ref["framework"].id, "Q1"
    )
    activate_org_product(
        db_session, ref["org"].id, ref["product"].id, a.id
    )
    result2 = activate_org_product(
        db_session, ref["org"].id, ref["product"].id, a.id
    )

    tasks = db_session.scalars(
        select(EvidenceTask).where(EvidenceTask.assessment_id == a.id)
    ).all()
    assert len(tasks) == 1  # no duplicates
    assert result2["tasks_created"] == 0  # second call skipped them


# ---------------------------------------------------------------------------
# 6. Auto-fire at assessment start
# ---------------------------------------------------------------------------


def test_start_assessment_auto_fires_for_active_products(
    db_session: Session, ref: dict
):
    """start_assessment fires the loop for pre-existing active+configured products."""
    # Activate the product BEFORE the assessment exists
    op = ref["org_product"]
    op.status = OrgProductStatus.ACTIVE
    op.configured = True
    op.activated_at = datetime.now(UTC)
    db_session.flush()

    # Now start an assessment — it should auto-fire the loop
    a = start_assessment(
        db_session, ref["org"].id, ref["framework"].id, "Q1 auto-fire"
    )

    states = {
        cs.objective_id: cs
        for cs in db_session.scalars(
            select(ControlState).where(ControlState.assessment_id == a.id)
        ).all()
    }

    # AC objectives should already be pending_evidence
    assert states[ref["ac_obj_a"].id].status == "pending_evidence"
    assert states[ref["ac_obj_b"].id].status == "pending_evidence"
    # IA stays not_met
    assert states[ref["ia_obj_a"].id].status == "not_met"

    # Tasks seeded too
    tasks = db_session.scalars(
        select(EvidenceTask).where(EvidenceTask.assessment_id == a.id)
    ).all()
    assert len(tasks) == 1
