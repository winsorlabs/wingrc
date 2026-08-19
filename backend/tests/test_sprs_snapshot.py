"""Integration tests for the sprs_snapshot table (G.2).

recompute_sprs (engine.py) is the single write path for assessment.sprs_score
— every call site (start_assessment, activate_org_product,
deactivate_org_product, bundle_service.snapshot_bundle, and
routers/assessments.py:patch_control_state) funnels through this one
function, so one hook point there covers every case. These tests confirm
that hook fires and produces a correctly ordered, correctly scored history,
without touching compute_sprs/recompute_sprs's own score computation
(test_assessment_engine.py covers that and must stay green, unmodified,
alongside these).

patch_control_state's call predates this table entirely (2026-07-09) but
was missed when this file was first written, which under-listed the call
sites as just three categories. Corrected 2026-08-19 — see
docs/PLAN-gui-restructure.md's G.2 section for the full writeup, including
the concurrency bug this omission's investigation surfaced (a real race in
recompute_sprs's unprotected read-then-write of assessment.sprs_score,
not fixed by this test file — it isn't reproducible without genuine
concurrent transactions, which a single-threaded test can't exercise).

Run in-container:
    docker compose exec backend pytest tests/test_sprs_snapshot.py -m integration -v
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.db import get_session
from app.engine import recompute_sprs, start_assessment
from app.main import app
from app.models import (
    AssessmentObjective,
    Control,
    ControlState,
    Framework,
    Organization,
    SprsSnapshot,
)
from app.storage import NullStorageClient, get_storage_client
from tests.conftest import _app_session, _authed, _grant

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def ref(db_session: Session) -> dict:
    """One org, one framework, one 5-point control with one objective.

    start_assessment() itself calls recompute_sprs() once at the end (see
    engine.py), so every test here already has one baseline snapshot row
    before its own explicit recompute_sprs() calls — accounted for via
    _snapshots(), not assumed to be zero.
    """
    org = Organization(name=f"SprsSnapOrg-{uuid.uuid4().hex[:6]}")
    fw = Framework(key=f"fw-snap-{uuid.uuid4().hex[:6]}", name="Test FW", version="r2")
    db_session.add_all([org, fw])
    db_session.flush()

    ctrl = Control(
        framework_id=fw.id,
        control_id="AC.L2-3.1.1",
        family="AC",
        title="Access control",
        requirement_text="Limit access",
        sprs_weight=5,
        sequence_order=1,
    )
    db_session.add(ctrl)
    db_session.flush()

    obj = AssessmentObjective(control_id=ctrl.id, objective_key="a", text="AC[a]")
    db_session.add(obj)
    db_session.flush()

    assessment = start_assessment(db_session, org.id, fw.id, "Snapshot Test")
    db_session.flush()

    return {"org": org, "fw": fw, "ctrl": ctrl, "obj": obj, "assessment": assessment}


@pytest.fixture
def client(db_session: Session, fake_msp_admin):
    app.dependency_overrides[get_session] = _app_session(db_session)
    app.dependency_overrides[get_storage_client] = lambda: NullStorageClient()
    app.dependency_overrides[get_current_user] = _authed(db_session, fake_msp_admin)
    yield TestClient(app)
    app.dependency_overrides.clear()


def _snapshots(db_session: Session, assessment_id: uuid.UUID) -> list[SprsSnapshot]:
    return list(
        db_session.scalars(
            select(SprsSnapshot)
            .where(SprsSnapshot.assessment_id == assessment_id)
            .order_by(SprsSnapshot.seq)
        ).all()
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_recompute_sprs_creates_a_snapshot_row(db_session: Session, ref: dict):
    assessment = ref["assessment"]
    before = _snapshots(db_session, assessment.id)
    assert len(before) == 1  # start_assessment()'s own recompute_sprs() call

    score = recompute_sprs(db_session, assessment.id)
    db_session.flush()

    after = _snapshots(db_session, assessment.id)
    assert len(after) == 2
    latest = after[-1]
    assert latest.score == score
    assert latest.assessment_id == assessment.id
    assert latest.org_id == ref["org"].id


def test_patch_control_state_recompute_also_produces_a_snapshot(
    client: TestClient, db_session: Session, ref: dict, fake_msp_admin
):
    """Confirms the hook fires on the control-state PATCH call path too —
    no activation, deactivation, or bundle export required. This is the
    path the assessment board's "mark met" dropdown uses
    (ObjectiveRow.tsx -> api.patchControlState ->
    routers/assessments.py:patch_control_state), and the one omitted from
    this table's original call-site list (see module docstring)."""
    assessment = ref["assessment"]
    _grant(db_session, fake_msp_admin, org_id=ref["org"].id)

    cs = db_session.scalars(
        select(ControlState).where(
            ControlState.assessment_id == assessment.id,
            ControlState.objective_id == ref["obj"].id,
        )
    ).first()
    assert cs is not None
    assert cs.status == "not_met"

    before = _snapshots(db_session, assessment.id)

    resp = client.patch(
        f"/orgs/{ref['org'].id}/assessments/{assessment.id}/control-states/{cs.id}",
        json={"status": "met"},
    )
    assert resp.status_code == 200
    body = resp.json()

    after = _snapshots(db_session, assessment.id)
    assert len(after) == len(before) + 1

    latest = after[-1]
    assert latest.score == body["sprs_score"]

    db_session.refresh(assessment)
    assert assessment.sprs_score == body["sprs_score"]
    assert assessment.sprs_score == latest.score


def test_patch_control_state_score_reflects_its_own_edit_under_autoflush_false(
    client: TestClient, db_session: Session, ref: dict, fake_msp_admin
):
    """Regression test for the real bug behind a 2026-08-19 report ("Dashboard's
    SPRS score lags the assessment screen by exactly one recompute," even after
    the SELECT ... FOR UPDATE race fix). Root cause: app/db.py's production
    SessionLocal sets autoflush=False, but patch_control_state set
    cs.status = body.status and called recompute_sprs() with no flush in
    between — recompute_sprs's own control_state SELECT never saw the pending
    change, so it computed a score missing the very edit that triggered it,
    deterministically one recompute behind every single call. Fixed by making
    recompute_sprs() flush first, unconditionally, regardless of caller
    discipline or session autoflush setting.

    test_patch_control_state_recompute_also_produces_a_snapshot (above) does
    NOT catch this: db_session's autoflush defaults to True (conftest.py's
    Session(...) call never sets it), which silently flushes the pending
    change before recompute_sprs's SELECT runs regardless of whether
    recompute_sprs does its own flush — a happy-path pass that would have
    stayed green whether or not this fix was ever applied. This test sets
    db_session.autoflush = False first, specifically to remove that
    accidental safety net and exercise the same ordering production hits.
    """
    assessment = ref["assessment"]
    _grant(db_session, fake_msp_admin, org_id=ref["org"].id)

    cs = db_session.scalars(
        select(ControlState).where(
            ControlState.assessment_id == assessment.id,
            ControlState.objective_id == ref["obj"].id,
        )
    ).first()
    assert cs is not None
    assert cs.status == "not_met"

    db_session.autoflush = False
    try:
        resp = client.patch(
            f"/orgs/{ref['org'].id}/assessments/{assessment.id}/control-states/{cs.id}",
            json={"status": "met"},
        )
    finally:
        db_session.autoflush = True

    assert resp.status_code == 200
    body = resp.json()

    # met/inherited-satisfied -> full credit, no deduction for this control.
    # If the bug were still present, this PATCH's own response and the
    # persisted state would both reflect the score as of BEFORE this edit
    # (not_met still deducting ref["ctrl"]'s weight), not after it.
    assert body["sprs_score"] == 110

    db_session.refresh(assessment)
    assert assessment.sprs_score == 110

    latest = _snapshots(db_session, assessment.id)[-1]
    assert latest.score == 110


def test_two_recomputes_with_different_states_produce_two_distinct_rows_in_order(
    db_session: Session, ref: dict
):
    assessment = ref["assessment"]
    baseline_count = len(_snapshots(db_session, assessment.id))

    # First recompute: objective still not_met (seeded default).
    score1 = recompute_sprs(db_session, assessment.id)
    db_session.flush()

    # Flip the objective to met, then recompute again — a genuinely different state.
    cs = db_session.scalars(
        select(ControlState).where(
            ControlState.assessment_id == assessment.id,
            ControlState.objective_id == ref["obj"].id,
        )
    ).first()
    assert cs is not None
    cs.status = "met"
    db_session.flush()

    score2 = recompute_sprs(db_session, assessment.id)
    db_session.flush()

    assert score1 != score2, "test setup should exercise two genuinely different scores"

    snapshots = _snapshots(db_session, assessment.id)
    assert len(snapshots) == baseline_count + 2

    new_rows = snapshots[-2:]
    assert new_rows[0].id != new_rows[1].id
    assert new_rows[0].score == score1
    assert new_rows[1].score == score2
    # seq, not computed_at, is the ordering guarantee — both rows can land in
    # the same transaction (as they do here) and get an identical
    # computed_at value; see models.py's SprsSnapshot docstring / migration
    # 0020's precedent for password_history.
    assert new_rows[0].seq < new_rows[1].seq


def test_bundle_export_recompute_also_produces_a_snapshot(
    client: TestClient, db_session: Session, ref: dict, fake_msp_admin
):
    """Confirms the hook fires on the bundle-export call path too, not just
    direct recompute_sprs() calls — bundle_service.snapshot_bundle() calls
    recompute_sprs() before rendering (CLAUDE.md: "SPRS recomputed first")."""
    assessment = ref["assessment"]
    _grant(db_session, fake_msp_admin, org_id=ref["org"].id)

    before = len(_snapshots(db_session, assessment.id))

    resp = client.get(f"/orgs/{ref['org'].id}/assessments/{assessment.id}/bundle")
    assert resp.status_code == 200

    after = _snapshots(db_session, assessment.id)
    assert len(after) == before + 1


# ---------------------------------------------------------------------------
# Row-locking (2026-08-19 fix for the concurrent-recompute lost-update race)
# ---------------------------------------------------------------------------
#
# What this does NOT do: reproduce the original race end-to-end (two
# concurrent recomputes, one with an incomplete snapshot, racing on which
# stale write lands last). That needs genuine concurrent transactions
# racing on real wall-clock timing to interleave a specific way — not
# something a deterministic pytest test can reliably force. Two
# *sequential* recompute_sprs() calls in the same transaction (the shape
# every other test in this file uses) would prove nothing about the
# fix, since there's no concurrency for the lock to arbitrate.
#
# What it does instead: proves the lock itself is real. A second,
# genuinely independent connection's SELECT ... FOR UPDATE NOWAIT on the
# same assessment row must fail immediately with Postgres's
# lock-not-available error while the first connection holds the lock
# uncommitted. NOWAIT makes this deterministic and fast — no thread
# coordination, no sleep-based timing, no risk of a flaky pass.
#
# Why the setup/teardown looks nothing like this file's other tests:
# cross-connection lock visibility requires a genuinely COMMITTED row —
# db_session's fixture (used everywhere else here) deliberately never
# commits anything for real; it's rolled back at every test's teardown
# (see conftest.py's db_session docstring), which is exactly why it's
# safe for every other test to leave no trace. That same property makes
# it useless here: a second, independent connection can never see an
# uncommitted row. This test uses db_engine directly, commits its own
# minimal org/framework/assessment, and deletes them explicitly in a
# finally block — the one place in this file (and the only place in the
# backend test suite this session touched) that manages its own real
# cleanup instead of relying on rollback.


def test_recompute_sprs_locks_the_assessment_row(db_engine):
    org_id = uuid.uuid4()
    fw_id = uuid.uuid4()
    assessment_id = uuid.uuid4()

    setup_conn = db_engine.connect()
    try:
        setup_trans = setup_conn.begin()
        setup_conn.execute(
            text("INSERT INTO organization (id, name) VALUES (:id, :name)"),
            {"id": org_id, "name": f"LockTestOrg-{uuid.uuid4().hex[:6]}"},
        )
        setup_conn.execute(
            text(
                "INSERT INTO framework (id, key, name, version) "
                "VALUES (:id, :key, 'Lock Test FW', 'r2')"
            ),
            {"id": fw_id, "key": f"fw-lock-{uuid.uuid4().hex[:6]}"},
        )
        setup_conn.execute(
            text(
                "INSERT INTO assessment"
                " (id, org_id, framework_id, name, assessment_type, status)"
                " VALUES (:id, :org_id, :fw_id, 'Lock Test', 'self', 'in_progress')"
            ),
            {"id": assessment_id, "org_id": org_id, "fw_id": fw_id},
        )
        setup_trans.commit()

        conn_a = db_engine.connect()
        conn_b = db_engine.connect()
        try:
            trans_a = conn_a.begin()
            conn_a.execute(
                text("SELECT id FROM assessment WHERE id = :id FOR UPDATE"),
                {"id": assessment_id},
            )
            # conn_a now holds the row lock; deliberately not committed yet,
            # simulating a recompute_sprs() call still in flight.

            trans_b = conn_b.begin()
            try:
                with pytest.raises(OperationalError):
                    conn_b.execute(
                        text("SELECT id FROM assessment WHERE id = :id FOR UPDATE NOWAIT"),
                        {"id": assessment_id},
                    )
            finally:
                trans_b.rollback()
            trans_a.rollback()
        finally:
            conn_a.close()
            conn_b.close()
    finally:
        cleanup_trans = setup_conn.begin()
        setup_conn.execute(text("DELETE FROM assessment WHERE id = :id"), {"id": assessment_id})
        setup_conn.execute(text("DELETE FROM framework WHERE id = :id"), {"id": fw_id})
        setup_conn.execute(text("DELETE FROM organization WHERE id = :id"), {"id": org_id})
        cleanup_trans.commit()
        setup_conn.close()
