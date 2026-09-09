"""Integration tests for engine.py's backfill_missing_control_states().

Built for the 2026-09 catalog-reconciliation incident: 4 objectives were
silently missing from cmmc_l2.yaml since the catalog was first authored,
which means every existing assessment is missing a control_state row for
each. This function fills that gap for any assessment, going forward, not
just this one incident -- see engine.py's own docstring for the full
design rationale (never defaults to met, doesn't re-run the magic loop,
dry-run by default, one audit_log entry per affected assessment,
recompute_sprs is the only score-writing path so historical sprs_snapshot
rows are never touched).

Run in-container:
    docker compose exec backend pytest tests/test_backfill_missing_control_states.py \
        -m integration -v
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.engine import backfill_missing_control_states, start_assessment
from app.models import (
    AssessmentObjective,
    AuditLog,
    Control,
    ControlState,
    Framework,
    Organization,
    SprsSnapshot,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def ref(db_session):
    """Org + framework + one 5-pt control with objectives [a, b] + a
    started assessment. Simulates the pre-fix state by deleting [b]'s
    control_state after start_assessment seeds it -- exactly what "an
    objective existed in the catalog all along but a specific assessment
    is missing its control_state row" looks like, regardless of how that
    gap actually occurred historically."""
    org = Organization(name=f"BackfillOrg-{uuid.uuid4().hex[:6]}")
    fw = Framework(key=f"fw-backfill-{uuid.uuid4().hex[:6]}", name="Test FW", version="r2")
    db_session.add_all([org, fw])
    db_session.flush()

    ctrl = Control(
        framework_id=fw.id,
        control_id="AC.L2-TEST",
        family="AC",
        title="Test control",
        requirement_text="Test requirement",
        sprs_weight=5,
        sequence_order=1,
    )
    db_session.add(ctrl)
    db_session.flush()

    obj_a = AssessmentObjective(control_id=ctrl.id, objective_key="a", text="Objective A.")
    obj_b = AssessmentObjective(control_id=ctrl.id, objective_key="b", text="Objective B.")
    db_session.add_all([obj_a, obj_b])
    db_session.flush()

    assessment = start_assessment(db_session, org.id, fw.id, "Test Assessment")
    db_session.commit()

    # Simulate the historical gap: [b]'s control_state never existed for
    # this assessment (as if the catalog had been missing [b] entirely
    # when this assessment was started).
    b_state = db_session.scalars(
        select(ControlState).where(
            ControlState.assessment_id == assessment.id, ControlState.objective_id == obj_b.id
        )
    ).one()
    db_session.delete(b_state)
    db_session.flush()

    return {
        "org": org,
        "fw": fw,
        "ctrl": ctrl,
        "obj_a": obj_a,
        "obj_b": obj_b,
        "assessment": assessment,
    }


def test_dry_run_reports_but_writes_nothing(db_session, ref):
    results = backfill_missing_control_states(db_session, reason="test", dry_run=True)

    matching = [r for r in results if r["assessment_id"] == str(ref["assessment"].id)]
    assert len(matching) == 1
    assert matching[0]["objectives_added"] == 1
    assert matching[0]["added_objective_keys"] == ["AC.L2-TEST[b]"]

    # Nothing actually persisted -- re-querying shows [b] still missing.
    still_missing = db_session.scalars(
        select(ControlState).where(
            ControlState.assessment_id == ref["assessment"].id,
            ControlState.objective_id == ref["obj_b"].id,
        )
    ).first()
    assert still_missing is None


def test_apply_creates_not_met_customer_owns_state(db_session, ref):
    backfill_missing_control_states(db_session, reason="test", dry_run=False)

    state = db_session.scalars(
        select(ControlState).where(
            ControlState.assessment_id == ref["assessment"].id,
            ControlState.objective_id == ref["obj_b"].id,
        )
    ).one()
    assert state.status == "not_met"
    assert state.responsibility == "customer_owns"


def test_apply_recomputes_sprs_and_writes_new_snapshot_without_touching_old_ones(db_session, ref):
    assessment_id = ref["assessment"].id
    sprs_before = ref["assessment"].sprs_score

    existing_snapshot_ids = set(
        db_session.scalars(
            select(SprsSnapshot.id).where(SprsSnapshot.assessment_id == assessment_id)
        ).all()
    )

    results = backfill_missing_control_states(db_session, reason="test", dry_run=False)
    matching = next(r for r in results if r["assessment_id"] == str(assessment_id))

    assert matching["sprs_before"] == sprs_before
    # 5-pt control now has an unevaluated (not_met) objective -> full
    # control weight deducted, same as any other not_met objective would.
    assert matching["sprs_after"] < sprs_before

    db_session.refresh(ref["assessment"])
    assert ref["assessment"].sprs_score == matching["sprs_after"]

    # Old snapshot rows are untouched; exactly one new one was added.
    all_snapshot_ids = set(
        db_session.scalars(
            select(SprsSnapshot.id).where(SprsSnapshot.assessment_id == assessment_id)
        ).all()
    )
    assert existing_snapshot_ids <= all_snapshot_ids
    assert len(all_snapshot_ids - existing_snapshot_ids) == 1
    new_id = next(iter(all_snapshot_ids - existing_snapshot_ids))
    new_snapshot = db_session.get(SprsSnapshot, new_id)
    assert new_snapshot.score == matching["sprs_after"]


def test_apply_writes_one_audit_log_entry_with_reason_and_no_met_default(db_session, ref):
    backfill_missing_control_states(
        db_session, reason="2026-09 catalog gap fix", dry_run=False
    )

    entries = db_session.scalars(
        select(AuditLog).where(
            AuditLog.action == "control_state.backfill",
            AuditLog.entity_id == ref["assessment"].id,
        )
    ).all()
    assert len(entries) == 1
    entry = entries[0]
    assert entry.actor == "system"
    assert entry.actor_type == "system"
    assert entry.context["reason"] == "2026-09 catalog gap fix"
    assert entry.after_value["added_objective_keys"] == ["AC.L2-TEST[b]"]
    assert "sprs_score" in entry.before_value
    assert "sprs_score" in entry.after_value


def test_assessment_with_nothing_missing_is_not_reported(db_session, ref):
    # Fully backfill first, then run again -- the assessment should no
    # longer appear at all (idempotent: nothing left to add).
    backfill_missing_control_states(db_session, reason="first pass", dry_run=False)
    results = backfill_missing_control_states(db_session, reason="second pass", dry_run=False)

    matching = [r for r in results if r["assessment_id"] == str(ref["assessment"].id)]
    assert matching == []
