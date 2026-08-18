"""Integration tests for the sprs_snapshot table (G.2).

recompute_sprs (engine.py) is the single write path for assessment.sprs_score
— activation, deactivation, and pre-bundle-export all call into it — so one
hook point there covers every case. These tests confirm that hook fires and
produces a correctly ordered, correctly scored history, without touching
compute_sprs/recompute_sprs's own score computation (test_assessment_engine.py
covers that and must stay green, unmodified, alongside these).

Run in-container:
    docker compose exec backend pytest tests/test_sprs_snapshot.py -m integration -v
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
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
