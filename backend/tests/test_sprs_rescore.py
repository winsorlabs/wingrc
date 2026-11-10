"""engine.rescore_assessments and `wingrc sprs-rescore`: the one deliberate
recompute of every stored SPRS score when not_applicable stopped deducting
(2026-11-10; docs/roadmap.md, "N/A objectives stop deducting").

The stored scores below are set by hand to what the earlier rule produced,
which is the state a deployment is in before the command runs.
"""

from __future__ import annotations

import uuid
from datetime import date

import pytest
from sqlalchemy import select

from app.engine import rescore_assessments, start_assessment
from app.models import (
    AssessmentObjective,
    AuditLog,
    Control,
    ControlState,
    Framework,
    Organization,
    SprsSnapshot,
    SprsSubmission,
)

pytestmark = pytest.mark.integration


def _assessment(db_session, statuses: dict[str, str], stored: int):
    """One org and assessment over three practices: P5 (objectives a, b),
    P3 (c) and P1 (d), weighted 5, 3 and 1. `statuses` sets each
    objective; `stored` is the score as the earlier rule left it."""
    tag = uuid.uuid4().hex[:6]
    org = Organization(name=f"RescoreOrg-{tag}")
    fw = Framework(key=f"fw-rescore-{tag}", name="Test FW", version="r2")
    db_session.add_all([org, fw])
    db_session.flush()
    objs = {}
    for seq, (key, weight, letters) in enumerate(
        [("P5", 5, "ab"), ("P3", 3, "c"), ("P1", 1, "d")], start=1
    ):
        ctrl = Control(
            framework_id=fw.id, control_id=key, family="AC", title=key,
            requirement_text=key, sprs_weight=weight, sequence_order=seq,
        )
        db_session.add(ctrl)
        db_session.flush()
        for letter in letters:
            obj = AssessmentObjective(control_id=ctrl.id, objective_key=letter, text=letter)
            db_session.add(obj)
            db_session.flush()
            objs[letter] = obj
    assessment = start_assessment(db_session, org.id, fw.id, f"Rescore {tag}")
    for letter, status in statuses.items():
        db_session.scalars(
            select(ControlState).where(
                ControlState.assessment_id == assessment.id,
                ControlState.objective_id == objs[letter].id,
            )
        ).one().status = status
    assessment.sprs_score = stored
    db_session.commit()
    return assessment


@pytest.fixture
def with_na(db_session):
    # P5: a N/A, b met -> deducted 5 under the earlier rule, satisfied now.
    # P3: c met. P1: d not met, deducts either way. Earlier: 110-5-1 = 104.
    return _assessment(
        db_session, {"a": "not_applicable", "b": "met", "c": "met", "d": "not_met"}, 104
    )


@pytest.fixture
def without_na(db_session):
    # No not_applicable anywhere: P3 deducts under both rules. 110-3 = 107.
    return _assessment(db_session, {"a": "met", "b": "met", "c": "not_met", "d": "met"}, 107)


def _outcome(results, assessment):
    row = next(r for r in results if r["assessment_id"] == str(assessment.id))
    return row["sprs_before"], row["sprs_after"], row["stopped_deducting"], row["explained"]


def _snapshots(db_session, assessment):
    return set(db_session.scalars(
        select(SprsSnapshot.id).where(SprsSnapshot.assessment_id == assessment.id)
    ))


def _rescore_audits(db_session, assessment):
    return db_session.scalars(
        select(AuditLog).where(
            AuditLog.entity_id == assessment.id, AuditLog.action == "assessment.sprs_rescore"
        )
    ).all()


def test_dry_run_reports_every_assessment_and_writes_nothing(db_session, with_na, without_na):
    snaps = _snapshots(db_session, with_na)
    results = rescore_assessments(db_session, reason="test", dry_run=True)

    assert _outcome(results, with_na) == (104, 109, ["P5"], True)
    assert _outcome(results, without_na) == (107, 107, [], True)
    db_session.refresh(with_na)
    assert with_na.sprs_score == 104
    assert _snapshots(db_session, with_na) == snaps
    assert _rescore_audits(db_session, with_na) == []


def test_apply_moves_only_the_assessment_with_not_applicable(db_session, with_na, without_na):
    snaps_na, snaps_plain = _snapshots(db_session, with_na), _snapshots(db_session, without_na)
    rescore_assessments(db_session, reason="170.24(b)(3)", dry_run=False)

    db_session.refresh(with_na)
    db_session.refresh(without_na)
    assert (with_na.sprs_score, without_na.sprs_score) == (109, 107)

    added = _snapshots(db_session, with_na) - snaps_na
    assert snaps_na <= _snapshots(db_session, with_na), "earlier snapshots must stay"
    assert [db_session.get(SprsSnapshot, s).score for s in added] == [109]
    [audit] = _rescore_audits(db_session, with_na)
    assert audit.before_value == {"sprs_score": 104}
    assert audit.after_value == {"sprs_score": 109, "stopped_deducting": ["P5"]}
    assert audit.context["reason"] == "170.24(b)(3)"
    assert audit.context["explained"] is True

    assert _snapshots(db_session, without_na) == snaps_plain
    assert _rescore_audits(db_session, without_na) == []


def test_a_second_run_finds_nothing_to_explain(db_session, with_na):
    """Once rescored, the assessment still has an N/A practice that does not
    deduct, but nothing moves, so nothing stopped. Found on the bench copy
    of live, 2026-11-10: the first version reported this UNEXPLAINED, and
    would have for any assessment given an N/A objective after the fix."""
    rescore_assessments(db_session, reason="test", dry_run=False)
    results = rescore_assessments(db_session, reason="test", dry_run=True)
    assert _outcome(results, with_na) == (109, 109, [], True)


def test_a_score_moving_without_not_applicable_is_unexplained(db_session):
    # Stored 100, but nothing here was ever not applicable: the earlier
    # rule gave 107 too. The move is not this change's, and says so.
    stale = _assessment(db_session, {"a": "met", "b": "met", "c": "not_met", "d": "met"}, 100)
    results = rescore_assessments(db_session, reason="test", dry_run=True)
    assert _outcome(results, stale) == (100, 107, [], False)


def test_a_filed_submission_keeps_its_score(db_session, with_na):
    filed = SprsSubmission(
        org_id=with_na.org_id, assessment_id=with_na.id, score=104,
        submitted_date=date(2026, 11, 1), submitted_by_name="Filer",
    )
    db_session.add(filed)
    db_session.commit()
    rescore_assessments(db_session, reason="test", dry_run=False)
    db_session.refresh(filed)
    assert filed.score == 104
