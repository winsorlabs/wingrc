"""Integration tests: multiple products satisfying the same control.

Real motivating case (docs/roadmap.md's multi-tool-coverage writeup): an
MSP's Kaseya suite (Datto RMM, IT Glue, RocketCyber, SaaS Alerts) overlaps
by design on access control, logging, and system integrity. Before this
slice, control_state.sourced_from_product_id could only ever name one
product -- activating a second overlapping product silently overwrote it,
including regressing an already-met, evidenced objective back to
pending_evidence (confirmed live on a bench stack, 2026-09-16, before this
was designed).

What is proven here:
  1. Two products covering the same objective are BOTH recorded as
     contributors (models.py:ControlStateContributor).
  2. Activating a second contributor never regresses an already-met status.
  3. responsibility resolves per Jarrod's explicit decision: the weakest
     (most customer-inclusive) claim wins when contributors disagree.
  4. Deactivating one of several contributors -> needs_review, the
     survivor(s) stay recorded, and change_reason names both the departing
     and surviving product(s).
  5. Deactivating the last contributor -> today's pre-existing behavior,
     unchanged (see test_deactivation.py for the exhaustive single-product
     coverage this mirrors).
  6. SPRS score is identical whether an objective is covered by one
     contributor or three -- compute_sprs only ever reads per-objective
     status, never responsibility or contributor count.

Run in-container:
    docker compose exec backend pytest tests/test_multi_tool_coverage.py -m integration -v
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.engine import activate_org_product, deactivate_org_product, start_assessment
from app.models import (
    AssessmentObjective,
    BaselineControl,
    Control,
    ControlState,
    ControlStateContributor,
    ControlStateHistory,
    Framework,
    Organization,
    OrgProduct,
    Product,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def overlap_ref(db_session: Session) -> dict:
    """One control (one objective), two products that both claim it --
    mirrors two overlapping Kaseya products covering the same objective.

    product_a: provider_satisfies, no evidence spec (keeps the fixture
        focused; evidence-task dedup across products is already covered
        by _run_loop's existing artifact-key dedup, unchanged by this slice).
    product_b: shared, with its own customer_action text -- the real case
        that motivated the responsibility-conflict decision.
    """
    org = Organization(name=f"OverlapOrg-{uuid.uuid4().hex[:6]}")
    fw = Framework(key=f"fw-overlap-{uuid.uuid4().hex[:6]}", name="Overlap FW", version="r1")
    db_session.add_all([org, fw])
    db_session.flush()

    ctrl = Control(
        framework_id=fw.id, control_id="AC.L2-3.1.1", family="AC",
        title="Limit system access", requirement_text="...", sprs_weight=5, sequence_order=1,
    )
    db_session.add(ctrl)
    db_session.flush()

    obj_a = AssessmentObjective(control_id=ctrl.id, objective_key="a", text="[a]")
    db_session.add(obj_a)
    db_session.flush()

    product_a = Product(
        framework_id=fw.id, key=f"prod-a-{uuid.uuid4().hex[:6]}", name="Product A",
        provider="Vendor A", category="ESP", asset_type="SPA", role="test",
        is_published=True,
    )
    product_b = Product(
        framework_id=fw.id, key=f"prod-b-{uuid.uuid4().hex[:6]}", name="Product B",
        provider="Vendor B", category="ESP", asset_type="SPA", role="test",
        is_published=True,
    )
    db_session.add_all([product_a, product_b])
    db_session.flush()

    bc_a = BaselineControl(
        product_id=product_a.id, control_id=ctrl.id, objectives=["a"],
        classification="provider_satisfies", candidate_state="pending_evidence",
        provider_contribution="Product A fully manages this.",
    )
    bc_b = BaselineControl(
        product_id=product_b.id, control_id=ctrl.id, objectives=["a"],
        classification="shared", candidate_state="pending_evidence",
        provider_contribution="Product B assists.",
        customer_action="Customer reviews Product B's alert triage weekly.",
    )
    db_session.add_all([bc_a, bc_b])
    db_session.flush()

    op_a = OrgProduct(org_id=org.id, product_id=product_a.id, status="candidate")
    op_b = OrgProduct(org_id=org.id, product_id=product_b.id, status="candidate")
    db_session.add_all([op_a, op_b])
    db_session.flush()

    return {
        "org": org, "fw": fw, "ctrl": ctrl, "obj_a": obj_a,
        "product_a": product_a, "product_b": product_b,
        "bc_a": bc_a, "bc_b": bc_b,
    }


def _state(db_session: Session, assessment_id, objective_id) -> ControlState:
    return db_session.scalars(
        select(ControlState).where(
            ControlState.assessment_id == assessment_id,
            ControlState.objective_id == objective_id,
        )
    ).first()


def _contributor_product_ids(db_session: Session, control_state_id) -> set:
    return set(
        db_session.scalars(
            select(ControlStateContributor.product_id).where(
                ControlStateContributor.control_state_id == control_state_id
            )
        ).all()
    )


# ---------------------------------------------------------------------------
# 1. Both contributors recorded
# ---------------------------------------------------------------------------


def test_two_products_both_recorded_as_contributors(db_session: Session, overlap_ref: dict):
    a = start_assessment(db_session, overlap_ref["org"].id, overlap_ref["fw"].id, "Overlap")
    activate_org_product(db_session, overlap_ref["org"].id, overlap_ref["product_a"].id, a.id)
    activate_org_product(db_session, overlap_ref["org"].id, overlap_ref["product_b"].id, a.id)

    cs = _state(db_session, a.id, overlap_ref["obj_a"].id)
    assert _contributor_product_ids(db_session, cs.id) == {
        overlap_ref["product_a"].id,
        overlap_ref["product_b"].id,
    }


def test_responsibility_resolves_to_shared_when_mixed(db_session: Session, overlap_ref: dict):
    """product_a is provider_satisfies, product_b is shared -- the weakest
    (most customer-inclusive) claim wins per Jarrod's explicit decision."""
    a = start_assessment(db_session, overlap_ref["org"].id, overlap_ref["fw"].id, "Overlap")
    activate_org_product(db_session, overlap_ref["org"].id, overlap_ref["product_a"].id, a.id)

    cs = _state(db_session, a.id, overlap_ref["obj_a"].id)
    assert cs.responsibility == "provider_satisfies"

    activate_org_product(db_session, overlap_ref["org"].id, overlap_ref["product_b"].id, a.id)
    db_session.refresh(cs)
    assert cs.responsibility == "shared"


# ---------------------------------------------------------------------------
# 2. Status must not regress when a second contributor is added
# ---------------------------------------------------------------------------


def test_second_contributor_does_not_regress_met_status(db_session: Session, overlap_ref: dict):
    a = start_assessment(db_session, overlap_ref["org"].id, overlap_ref["fw"].id, "Overlap")
    activate_org_product(db_session, overlap_ref["org"].id, overlap_ref["product_a"].id, a.id)

    cs = _state(db_session, a.id, overlap_ref["obj_a"].id)
    assert cs.status == "pending_evidence"

    # A human confirms coverage with evidence.
    cs.status = "met"
    db_session.flush()

    activate_org_product(db_session, overlap_ref["org"].id, overlap_ref["product_b"].id, a.id)
    db_session.refresh(cs)

    assert cs.status == "met", (
        "Adding a second contributor must never un-verify an already-"
        "confirmed status -- this is the exact regression confirmed live "
        "on a bench stack before this fix (docs/roadmap.md)."
    )
    # But the new contributor IS recorded, and responsibility DOES update.
    assert _contributor_product_ids(db_session, cs.id) == {
        overlap_ref["product_a"].id,
        overlap_ref["product_b"].id,
    }
    assert cs.responsibility == "shared"


def test_second_contributor_writes_history_without_changing_status(
    db_session: Session, overlap_ref: dict
):
    a = start_assessment(db_session, overlap_ref["org"].id, overlap_ref["fw"].id, "Overlap")
    activate_org_product(db_session, overlap_ref["org"].id, overlap_ref["product_a"].id, a.id)
    cs = _state(db_session, a.id, overlap_ref["obj_a"].id)
    cs.status = "met"
    db_session.flush()

    activate_org_product(db_session, overlap_ref["org"].id, overlap_ref["product_b"].id, a.id)

    history = db_session.scalars(
        select(ControlStateHistory)
        .where(ControlStateHistory.control_state_id == cs.id)
        .order_by(ControlStateHistory.changed_at)
    ).all()
    # entry 0: not_met -> pending_evidence (product A's initial activation)
    # entry 1: contributor-added audit entry (status unchanged: met -> met)
    assert len(history) == 2
    added = history[1]
    assert added.previous_status == "met"
    assert added.new_status == "met"
    assert "Product B" in added.change_reason
    assert "also covers" in added.change_reason


def test_reactivating_the_same_product_is_idempotent(db_session: Session, overlap_ref: dict):
    """Calling activate_org_product twice for the SAME product must not
    duplicate its contributor row or write a second history entry."""
    a = start_assessment(db_session, overlap_ref["org"].id, overlap_ref["fw"].id, "Overlap")
    activate_org_product(db_session, overlap_ref["org"].id, overlap_ref["product_a"].id, a.id)
    activate_org_product(db_session, overlap_ref["org"].id, overlap_ref["product_a"].id, a.id)

    cs = _state(db_session, a.id, overlap_ref["obj_a"].id)
    contributors = db_session.scalars(
        select(ControlStateContributor).where(ControlStateContributor.control_state_id == cs.id)
    ).all()
    assert len(contributors) == 1

    history = db_session.scalars(
        select(ControlStateHistory).where(ControlStateHistory.control_state_id == cs.id)
    ).all()
    assert len(history) == 1


# ---------------------------------------------------------------------------
# 3. Deactivating one of several contributors
# ---------------------------------------------------------------------------


def test_deactivate_one_of_two_leaves_survivor_recorded(db_session: Session, overlap_ref: dict):
    a = start_assessment(db_session, overlap_ref["org"].id, overlap_ref["fw"].id, "Overlap")
    activate_org_product(db_session, overlap_ref["org"].id, overlap_ref["product_a"].id, a.id)
    activate_org_product(db_session, overlap_ref["org"].id, overlap_ref["product_b"].id, a.id)

    cs = _state(db_session, a.id, overlap_ref["obj_a"].id)
    cs.status = "met"
    db_session.flush()

    result = deactivate_org_product(
        db_session, overlap_ref["org"].id, overlap_ref["product_a"].id, a.id
    )
    db_session.refresh(cs)

    assert cs.status == "needs_review", (
        "Jarrod's explicit decision: losing a tool is exactly when a "
        "coverage claim deserves a second look, even if another tool "
        "still covers it."
    )
    assert result["controls_flagged"] == 1
    assert _contributor_product_ids(db_session, cs.id) == {overlap_ref["product_b"].id}
    # Only product_b remains -> responsibility recomputed from it alone.
    assert cs.responsibility == "shared"


def test_deactivate_one_of_two_history_names_both_products(
    db_session: Session, overlap_ref: dict
):
    a = start_assessment(db_session, overlap_ref["org"].id, overlap_ref["fw"].id, "Overlap")
    activate_org_product(db_session, overlap_ref["org"].id, overlap_ref["product_a"].id, a.id)
    activate_org_product(db_session, overlap_ref["org"].id, overlap_ref["product_b"].id, a.id)

    deactivate_org_product(db_session, overlap_ref["org"].id, overlap_ref["product_a"].id, a.id)

    cs = _state(db_session, a.id, overlap_ref["obj_a"].id)
    history = db_session.scalars(
        select(ControlStateHistory)
        .where(
            ControlStateHistory.control_state_id == cs.id,
            ControlStateHistory.new_status == "needs_review",
        )
    ).all()
    assert len(history) == 1
    reason = history[0].change_reason
    assert "Product A" in reason, "must name which tool went away"
    assert "Product B" in reason, "must name which tool still covers it"


def test_deactivate_both_of_two_second_removal_matches_last_contributor_behavior(
    db_session: Session, overlap_ref: dict
):
    """After both contributors are gone, the second removal behaves exactly
    like today's single-product deactivation: needs_review, responsibility
    left unchanged (not reset to customer_owns)."""
    a = start_assessment(db_session, overlap_ref["org"].id, overlap_ref["fw"].id, "Overlap")
    activate_org_product(db_session, overlap_ref["org"].id, overlap_ref["product_a"].id, a.id)
    activate_org_product(db_session, overlap_ref["org"].id, overlap_ref["product_b"].id, a.id)

    deactivate_org_product(db_session, overlap_ref["org"].id, overlap_ref["product_a"].id, a.id)
    cs = _state(db_session, a.id, overlap_ref["obj_a"].id)
    resp_after_first_removal = cs.responsibility

    result = deactivate_org_product(
        db_session, overlap_ref["org"].id, overlap_ref["product_b"].id, a.id
    )
    db_session.refresh(cs)

    assert cs.status == "needs_review"
    assert cs.responsibility == resp_after_first_removal, (
        "last-contributor-removed responsibility is left exactly as it "
        "was -- today's pre-existing, unchanged behavior"
    )
    assert result["controls_flagged"] == 1
    assert _contributor_product_ids(db_session, cs.id) == set()


# ---------------------------------------------------------------------------
# 4. SPRS scoring is unaffected by contributor count
# ---------------------------------------------------------------------------


def _three_product_ref(db_session: Session) -> dict:
    """Same shape as overlap_ref but with THREE products covering one
    objective, all provider_satisfies -- for the 1-vs-3-contributor SPRS
    comparison."""
    org = Organization(name=f"TripleOrg-{uuid.uuid4().hex[:6]}")
    fw = Framework(key=f"fw-triple-{uuid.uuid4().hex[:6]}", name="Triple FW", version="r1")
    db_session.add_all([org, fw])
    db_session.flush()

    ctrl = Control(
        framework_id=fw.id, control_id="AC.L2-3.1.1", family="AC",
        title="Limit system access", requirement_text="...", sprs_weight=5, sequence_order=1,
    )
    db_session.add(ctrl)
    db_session.flush()

    obj_a = AssessmentObjective(control_id=ctrl.id, objective_key="a", text="[a]")
    db_session.add(obj_a)
    db_session.flush()

    products = []
    for i in range(3):
        p = Product(
            framework_id=fw.id, key=f"triple-{i}-{uuid.uuid4().hex[:6]}", name=f"Triple {i}",
            provider="Vendor", category="ESP", asset_type="SPA", role="test",
            is_published=True,
        )
        db_session.add(p)
        db_session.flush()
        bc = BaselineControl(
            product_id=p.id, control_id=ctrl.id, objectives=["a"],
            classification="provider_satisfies", candidate_state="pending_evidence",
        )
        db_session.add(bc)
        op = OrgProduct(org_id=org.id, product_id=p.id, status="candidate")
        db_session.add(op)
        products.append(p)
    db_session.flush()

    return {"org": org, "fw": fw, "ctrl": ctrl, "obj_a": obj_a, "products": products}


def test_sprs_identical_for_one_vs_three_contributors(db_session: Session):
    ref = _three_product_ref(db_session)

    a = start_assessment(db_session, ref["org"].id, ref["fw"].id, "OneContributor")
    activate_org_product(db_session, ref["org"].id, ref["products"][0].id, a.id)
    cs = _state(db_session, a.id, ref["obj_a"].id)
    cs.status = "met"
    db_session.flush()
    from app.engine import recompute_sprs

    score_one = recompute_sprs(db_session, a.id)

    activate_org_product(db_session, ref["org"].id, ref["products"][1].id, a.id)
    activate_org_product(db_session, ref["org"].id, ref["products"][2].id, a.id)
    db_session.refresh(cs)
    assert cs.status == "met"  # still not regressed with 3 contributors
    score_three = recompute_sprs(db_session, a.id)

    assert score_one == score_three, (
        "SPRS must be identical regardless of contributor count -- "
        "compute_sprs only ever reads per-objective status, never "
        "responsibility or contributor count."
    )
