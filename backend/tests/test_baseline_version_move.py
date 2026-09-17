"""Integration tests: engine.py:move_org_product_version (roadmap item P).

Fixture shape -- product with two immutable versions:

  v1 (what the org activates under):
    AC — shared, objectives [a, b]
    AU — provider_satisfies, objective [a]   (dropped in v2)
    IA — customer_owns, objective [a]         (excluded from the magic
                                                loop in both versions;
                                                never a contributor)

  v2 (what the org later moves onto):
    AC — provider_satisfies, objectives [a, b]   (classification changed)
    SC — provider_satisfies, objective [a]       (new in v2, not in v1)
    IA — customer_owns, objective [a]            (unchanged)

What is proven:
  1. AC[a]/AC[b] (classification changed, both versions cover it) ->
     needs_review, contributor repointed to v2's baseline_control,
     responsibility recomputed.
  2. AU[a] (only v1 covers it) -> needs_review, contributor removed.
  3. SC[a] (only v2 covers it) -> pending_evidence (first contributor,
     same as a fresh activation), contributor added, an evidence task
     fanned out from v2's spec.
  4. IA[a] (customer_owns throughout) -> completely untouched.
  5. Evidence collected on AC[a] before the move survives (evidence_state_
     link is NOT archived) -- contrast deactivate_org_product.
  6. OrgProduct.baseline_version_id actually moves to v2.
  7. audit_log gets org_product.baseline_version_move plus a
     control_state.update entry per touched objective; control_state_
     history gets one row per touched objective naming both versions.
  8. SPRS recomputes.

Run in-container:
    docker compose exec backend pytest tests/test_baseline_version_move.py -m integration -v
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.engine import activate_org_product, move_org_product_version, start_assessment
from app.models import (
    AssessmentObjective,
    AuditLog,
    BaselineControl,
    BaselineEvidenceSpec,
    Control,
    ControlState,
    ControlStateContributor,
    ControlStateHistory,
    Evidence,
    EvidenceStateLink,
    EvidenceTask,
    Framework,
    Organization,
    OrgProduct,
    Product,
    ProductBaselineVersion,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def ref(db_session: Session) -> dict:
    org = Organization(name=f"VersionMoveOrg-{uuid.uuid4().hex[:6]}")
    fw = Framework(key=f"fw-vmove-{uuid.uuid4().hex[:6]}", name="Version Move FW", version="r2")
    db_session.add_all([org, fw])
    db_session.flush()

    ac = Control(
        framework_id=fw.id, control_id="AC.L2-3.1.1", family="AC",
        title="Access control", requirement_text="...", sprs_weight=5, sequence_order=1,
    )
    au = Control(
        framework_id=fw.id, control_id="AU.L2-3.3.1", family="AU",
        title="Event logging", requirement_text="...", sprs_weight=3, sequence_order=2,
    )
    sc = Control(
        framework_id=fw.id, control_id="SC.L2-3.13.1", family="SC",
        title="System integrity", requirement_text="...", sprs_weight=1, sequence_order=3,
    )
    ia = Control(
        framework_id=fw.id, control_id="IA.L2-3.5.1", family="IA",
        title="Identify users", requirement_text="...", sprs_weight=3, sequence_order=4,
    )
    db_session.add_all([ac, au, sc, ia])
    db_session.flush()

    ac_a = AssessmentObjective(control_id=ac.id, objective_key="a", text="AC[a]")
    ac_b = AssessmentObjective(control_id=ac.id, objective_key="b", text="AC[b]")
    au_a = AssessmentObjective(control_id=au.id, objective_key="a", text="AU[a]")
    sc_a = AssessmentObjective(control_id=sc.id, objective_key="a", text="SC[a]")
    ia_a = AssessmentObjective(control_id=ia.id, objective_key="a", text="IA[a]")
    db_session.add_all([ac_a, ac_b, au_a, sc_a, ia_a])
    db_session.flush()

    product = Product(
        framework_id=fw.id, key=f"vmove-{uuid.uuid4().hex[:6]}", name="Version Move Tool",
        provider="Vendor", category="ESP", asset_type="SPA", role="test",
        is_published=True,
    )
    db_session.add(product)
    db_session.flush()

    v1 = ProductBaselineVersion(product_id=product.id, version_number=1)
    db_session.add(v1)
    db_session.flush()
    product.current_version_id = v1.id
    db_session.flush()

    v1_ac = BaselineControl(
        product_id=product.id, baseline_version_id=v1.id, control_id=ac.id,
        objectives=["a", "b"], classification="shared", candidate_state="pending_evidence",
        provider_contribution="v1 AC text",
    )
    v1_au = BaselineControl(
        product_id=product.id, baseline_version_id=v1.id, control_id=au.id,
        objectives=["a"], classification="provider_satisfies", candidate_state="pending_evidence",
    )
    v1_ia = BaselineControl(
        product_id=product.id, baseline_version_id=v1.id, control_id=ia.id,
        objectives=["a"], classification="customer_owns",
        candidate_state="not_satisfied_by_product",
    )
    db_session.add_all([v1_ac, v1_au, v1_ia])
    db_session.flush()
    v1_ac_spec = BaselineEvidenceSpec(
        baseline_control_id=v1_ac.id, artifact_description="AC v1 export", evidence_type="export",
    )
    db_session.add(v1_ac_spec)
    db_session.flush()

    op = OrgProduct(org_id=org.id, product_id=product.id, status="candidate")
    db_session.add(op)
    db_session.flush()

    assessment = start_assessment(db_session, org.id, fw.id, "Version Move Test")
    activate_org_product(db_session, org.id, product.id, assessment.id)
    db_session.flush()
    db_session.refresh(op)
    assert op.baseline_version_id == v1.id  # sanity: pinned to v1 at activation

    # --- v2 created by a later reimport, product moves to it as "current" ---
    v2 = ProductBaselineVersion(product_id=product.id, version_number=2)
    db_session.add(v2)
    db_session.flush()
    product.current_version_id = v2.id
    db_session.flush()

    v2_ac = BaselineControl(
        product_id=product.id, baseline_version_id=v2.id, control_id=ac.id,
        objectives=["a", "b"], classification="provider_satisfies",
        candidate_state="pending_evidence",
        provider_contribution="v2 AC text",
    )
    v2_sc = BaselineControl(
        product_id=product.id, baseline_version_id=v2.id, control_id=sc.id,
        objectives=["a"], classification="provider_satisfies", candidate_state="pending_evidence",
    )
    v2_ia = BaselineControl(
        product_id=product.id, baseline_version_id=v2.id, control_id=ia.id,
        objectives=["a"], classification="customer_owns",
        candidate_state="not_satisfied_by_product",
    )
    db_session.add_all([v2_ac, v2_sc, v2_ia])
    db_session.flush()
    v2_sc_spec = BaselineEvidenceSpec(
        baseline_control_id=v2_sc.id, artifact_description="SC v2 export", evidence_type="export",
    )
    db_session.add(v2_sc_spec)
    db_session.flush()

    return {
        "org": org, "fw": fw, "product": product, "assessment": assessment, "org_product": op,
        "ac": ac, "au": au, "sc": sc, "ia": ia,
        "ac_a": ac_a, "ac_b": ac_b, "au_a": au_a, "sc_a": sc_a, "ia_a": ia_a,
        "v1": v1, "v2": v2,
        "v1_ac": v1_ac, "v1_au": v1_au,
        "v2_ac": v2_ac, "v2_sc": v2_sc,
    }


def _state(db_session: Session, assessment_id, objective_id) -> ControlState:
    return db_session.scalars(
        select(ControlState).where(
            ControlState.assessment_id == assessment_id, ControlState.objective_id == objective_id
        )
    ).one()


def _contributor(db_session: Session, control_state_id):
    return db_session.scalars(
        select(ControlStateContributor).where(
            ControlStateContributor.control_state_id == control_state_id
        )
    ).first()


def test_move_result_counts(db_session: Session, ref: dict):
    result = move_org_product_version(
        db_session, ref["org"].id, ref["product"].id, ref["assessment"].id, ref["v2"].id
    )
    assert result["controls_gained"] == 1  # SC[a]
    assert result["controls_lost"] == 1  # AU[a]
    assert result["controls_changed"] == 2  # AC[a], AC[b]
    assert result["tasks_created"] == 1  # SC v2's evidence spec


def test_org_product_pin_moves_to_target_version(db_session: Session, ref: dict):
    move_org_product_version(
        db_session, ref["org"].id, ref["product"].id, ref["assessment"].id, ref["v2"].id
    )
    db_session.refresh(ref["org_product"])
    assert ref["org_product"].baseline_version_id == ref["v2"].id


def test_changed_classification_goes_to_needs_review_and_repoints_contributor(
    db_session: Session, ref: dict
):
    move_org_product_version(
        db_session, ref["org"].id, ref["product"].id, ref["assessment"].id, ref["v2"].id
    )
    ac_a_state = _state(db_session, ref["assessment"].id, ref["ac_a"].id)
    assert ac_a_state.status == "needs_review"
    assert ac_a_state.responsibility == "provider_satisfies"

    contributor = _contributor(db_session, ac_a_state.id)
    assert contributor is not None
    assert contributor.baseline_control_id == ref["v2_ac"].id, (
        "a changed classification must repoint the contributor at the new "
        "version's baseline_control row"
    )


def test_dropped_control_goes_to_needs_review_and_removes_contributor(
    db_session: Session, ref: dict
):
    move_org_product_version(
        db_session, ref["org"].id, ref["product"].id, ref["assessment"].id, ref["v2"].id
    )
    au_a_state = _state(db_session, ref["assessment"].id, ref["au_a"].id)
    assert au_a_state.status == "needs_review"
    assert _contributor(db_session, au_a_state.id) is None


def test_new_control_gets_first_contributor_and_evidence_task(db_session: Session, ref: dict):
    move_org_product_version(
        db_session, ref["org"].id, ref["product"].id, ref["assessment"].id, ref["v2"].id
    )
    sc_a_state = _state(db_session, ref["assessment"].id, ref["sc_a"].id)
    assert sc_a_state.status == "pending_evidence"
    contributor = _contributor(db_session, sc_a_state.id)
    assert contributor is not None
    assert contributor.baseline_control_id == ref["v2_sc"].id

    # Two tasks total: the one seeded by v1's activation (AC v1 export)
    # plus the new one this move fanned out from v2's SC spec.
    tasks = db_session.scalars(
        select(EvidenceTask).where(EvidenceTask.assessment_id == ref["assessment"].id)
    ).all()
    titles = {t.title for t in tasks}
    assert titles == {"AC v1 export", "SC v2 export"}


def test_unrelated_customer_owns_control_untouched(db_session: Session, ref: dict):
    before = _state(db_session, ref["assessment"].id, ref["ia_a"].id)
    before_status = before.status
    move_org_product_version(
        db_session, ref["org"].id, ref["product"].id, ref["assessment"].id, ref["v2"].id
    )
    after = _state(db_session, ref["assessment"].id, ref["ia_a"].id)
    assert after.status == before_status == "not_met"
    assert _contributor(db_session, after.id) is None


def test_evidence_survives_the_move(db_session: Session, ref: dict):
    """Contrast deactivate_org_product, which archives every evidence_state_
    link on a tool-sourced state -- a version move never does."""
    ac_a_state = _state(db_session, ref["assessment"].id, ref["ac_a"].id)
    ev = Evidence(
        org_id=ref["org"].id, title="AC v1 evidence", artifact_type="export",
        kind="reference", reference_location="http://example.com/ac",
        collected_at=datetime.now(UTC),
    )
    db_session.add(ev)
    db_session.flush()
    lnk = EvidenceStateLink(evidence_id=ev.id, control_state_id=ac_a_state.id)
    db_session.add(lnk)
    ac_a_state.status = "met"
    db_session.flush()

    move_org_product_version(
        db_session, ref["org"].id, ref["product"].id, ref["assessment"].id, ref["v2"].id
    )

    db_session.refresh(lnk)
    assert lnk.is_archived is False, "a version move must never archive existing evidence"


def test_move_writes_audit_log_and_history(db_session: Session, ref: dict):
    move_org_product_version(
        db_session, ref["org"].id, ref["product"].id, ref["assessment"].id, ref["v2"].id
    )

    move_entries = db_session.scalars(
        select(AuditLog).where(
            AuditLog.org_id == ref["org"].id,
            AuditLog.action == "org_product.baseline_version_move",
        )
    ).all()
    assert len(move_entries) == 1
    assert move_entries[0].context.get("from_version") == 1
    assert move_entries[0].context.get("to_version") == 2

    all_cs_entries = db_session.scalars(
        select(AuditLog).where(
            AuditLog.org_id == ref["org"].id,
            AuditLog.action == "control_state.update",
        )
    ).all()
    cs_entries = [
        e for e in all_cs_entries if (e.context or {}).get("via") == "baseline_version_move"
    ]
    # AC[a], AC[b], AU[a], SC[a] -- 4 touched objectives, IA[a] excluded.
    assert len(cs_entries) == 4

    ac_a_state = _state(db_session, ref["assessment"].id, ref["ac_a"].id)
    history = db_session.scalars(
        select(ControlStateHistory).where(ControlStateHistory.control_state_id == ac_a_state.id)
    ).all()
    assert any("Version move (1->2)" in (h.change_reason or "") for h in history)


def test_move_recomputes_sprs(db_session: Session, ref: dict):
    # pending_evidence/needs_review/not_met all deduct identically in
    # compute_sprs, and a control only stops deducting once EVERY one of
    # its objectives is met/inherited -- so both AC objectives need to be
    # met first (a realistic prior state) to give the move something real
    # to regress.
    ac_a_state = _state(db_session, ref["assessment"].id, ref["ac_a"].id)
    ac_b_state = _state(db_session, ref["assessment"].id, ref["ac_b"].id)
    ac_a_state.status = "met"
    ac_b_state.status = "met"
    db_session.flush()
    from app.engine import recompute_sprs

    before = recompute_sprs(db_session, ref["assessment"].id)

    move_org_product_version(
        db_session, ref["org"].id, ref["product"].id, ref["assessment"].id, ref["v2"].id
    )
    db_session.refresh(ref["assessment"])
    assert ref["assessment"].sprs_score == before - ref["ac"].sprs_weight, (
        "AC regressing from met to needs_review must deduct its full "
        "control weight, same as any other status change"
    )


def test_move_rejects_same_version(db_session: Session, ref: dict):
    with pytest.raises(ValueError, match="already on this"):
        move_org_product_version(
            db_session, ref["org"].id, ref["product"].id, ref["assessment"].id, ref["v1"].id
        )


def test_move_rejects_inactive_org_product(db_session: Session, ref: dict):
    ref["org_product"].status = "candidate"
    db_session.flush()
    with pytest.raises(ValueError, match="Active OrgProduct not found"):
        move_org_product_version(
            db_session, ref["org"].id, ref["product"].id, ref["assessment"].id, ref["v2"].id
        )


def test_move_rejects_version_from_a_different_product(db_session: Session, ref: dict):
    other_product = Product(
        framework_id=ref["fw"].id, key=f"other-{uuid.uuid4().hex[:6]}", name="Other Tool",
        provider="Vendor", category="ESP", asset_type="SPA", role="test", is_published=True,
    )
    db_session.add(other_product)
    db_session.flush()
    other_version = ProductBaselineVersion(product_id=other_product.id, version_number=1)
    db_session.add(other_version)
    db_session.flush()

    with pytest.raises(ValueError, match="does not belong to product"):
        move_org_product_version(
            db_session, ref["org"].id, ref["product"].id, ref["assessment"].id, other_version.id
        )
