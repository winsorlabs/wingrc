"""Integration tests for PATCH /orgs/{org_id}/assessments/{assessment_id}/control-states/{id}.

Run in-container:
    docker compose exec backend pytest tests/test_patch_control_state.py -m integration -v
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import get_session
from app.main import app
from app.models import (
    Assessment,
    AssessmentObjective,
    Control,
    ControlState,
    ControlStateHistory,
    Framework,
    Organization,
)


@pytest.fixture
def client(db_session):
    app.dependency_overrides[get_session] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def _seed(db_session) -> dict:
    """Seed org → framework → control → objective → assessment → control_state."""
    org = Organization(name=f"PatchTestOrg-{uuid.uuid4().hex}")
    db_session.add(org)
    db_session.flush()

    fw = Framework(key=f"fw-{uuid.uuid4().hex}", name="Test FW", version="r2")
    db_session.add(fw)
    db_session.flush()

    ctrl = Control(
        framework_id=fw.id,
        control_id=f"AC.L2-{uuid.uuid4().hex[:6]}",
        family="AC",
        title="Test control",
        requirement_text="Test requirement",
        sprs_weight=1,
        sequence_order=1,
    )
    db_session.add(ctrl)
    db_session.flush()

    obj = AssessmentObjective(control_id=ctrl.id, objective_key="a", text="Test objective")
    db_session.add(obj)
    db_session.flush()

    assessment = Assessment(
        org_id=org.id,
        framework_id=fw.id,
        name="Test Assessment",
    )
    db_session.add(assessment)
    db_session.flush()

    cs = ControlState(
        assessment_id=assessment.id,
        org_id=org.id,
        objective_id=obj.id,
        status="not_met",
        responsibility="customer_owns",
    )
    db_session.add(cs)
    db_session.flush()

    return {"org": org, "assessment": assessment, "cs": cs}


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_patch_status_returns_200(client, db_session):
    d = _seed(db_session)
    url = f"/orgs/{d['org'].id}/assessments/{d['assessment'].id}/control-states/{d['cs'].id}"
    r = client.patch(url, json={"status": "met"})
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == str(d["cs"].id)
    assert body["status"] == "met"


@pytest.mark.integration
def test_patch_status_persists(client, db_session):
    d = _seed(db_session)
    url = f"/orgs/{d['org'].id}/assessments/{d['assessment'].id}/control-states/{d['cs'].id}"
    client.patch(url, json={"status": "partial"})
    db_session.expire(d["cs"])
    assert d["cs"].status == "partial"


@pytest.mark.integration
def test_patch_writes_history_row(client, db_session):
    d = _seed(db_session)
    url = f"/orgs/{d['org'].id}/assessments/{d['assessment'].id}/control-states/{d['cs'].id}"
    client.patch(url, json={"status": "met"})

    history = db_session.scalars(
        select(ControlStateHistory).where(
            ControlStateHistory.control_state_id == d["cs"].id
        )
    ).all()
    assert len(history) == 1
    assert history[0].previous_status == "not_met"
    assert history[0].new_status == "met"
    assert history[0].new_responsibility == "customer_owns"


@pytest.mark.integration
def test_patch_same_status_still_writes_history(client, db_session):
    d = _seed(db_session)
    url = f"/orgs/{d['org'].id}/assessments/{d['assessment'].id}/control-states/{d['cs'].id}"
    client.patch(url, json={"status": "not_met"})

    history = db_session.scalars(
        select(ControlStateHistory).where(
            ControlStateHistory.control_state_id == d["cs"].id
        )
    ).all()
    assert len(history) == 1
    assert history[0].previous_status == "not_met"
    assert history[0].new_status == "not_met"


@pytest.mark.integration
@pytest.mark.parametrize(
    "status",
    ["met", "not_met", "partial", "pending_evidence", "not_applicable", "inherited"],
)
def test_patch_all_valid_statuses_accepted(client, db_session, status):
    d = _seed(db_session)
    url = f"/orgs/{d['org'].id}/assessments/{d['assessment'].id}/control-states/{d['cs'].id}"
    r = client.patch(url, json={"status": status})
    assert r.status_code == 200
    assert r.json()["status"] == status


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_patch_invalid_status_returns_422(client, db_session):
    d = _seed(db_session)
    url = f"/orgs/{d['org'].id}/assessments/{d['assessment'].id}/control-states/{d['cs'].id}"
    r = client.patch(url, json={"status": "unknown_value"})
    assert r.status_code == 422


@pytest.mark.integration
def test_patch_wrong_org_returns_404(client, db_session):
    d = _seed(db_session)
    wrong_org = uuid.uuid4()
    url = f"/orgs/{wrong_org}/assessments/{d['assessment'].id}/control-states/{d['cs'].id}"
    r = client.patch(url, json={"status": "met"})
    assert r.status_code == 404


@pytest.mark.integration
def test_patch_wrong_assessment_returns_404(client, db_session):
    d = _seed(db_session)
    wrong_assessment = uuid.uuid4()
    url = f"/orgs/{d['org'].id}/assessments/{wrong_assessment}/control-states/{d['cs'].id}"
    r = client.patch(url, json={"status": "met"})
    assert r.status_code == 404


@pytest.mark.integration
def test_patch_control_state_not_in_assessment_returns_404(client, db_session):
    d = _seed(db_session)
    # Create a second assessment — cs belongs to the first one
    assessment2 = Assessment(
        org_id=d["org"].id,
        framework_id=d["assessment"].framework_id,
        name="Second Assessment",
    )
    db_session.add(assessment2)
    db_session.flush()

    url = f"/orgs/{d['org'].id}/assessments/{assessment2.id}/control-states/{d['cs'].id}"
    r = client.patch(url, json={"status": "met"})
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# SPRS recomputation on PATCH
# ---------------------------------------------------------------------------


def _seed_two_objectives(db_session) -> dict:
    """Seed one control (weight=3) with two objectives and two control_states."""
    org = Organization(name=f"SprsTestOrg-{uuid.uuid4().hex}")
    db_session.add(org)
    db_session.flush()

    fw = Framework(key=f"fw-{uuid.uuid4().hex}", name="SPRS FW", version="r2")
    db_session.add(fw)
    db_session.flush()

    ctrl = Control(
        framework_id=fw.id,
        control_id=f"AU.L2-{uuid.uuid4().hex[:6]}",
        family="AU",
        title="SPRS test control",
        requirement_text="SPRS test requirement",
        sprs_weight=3,
        sequence_order=1,
    )
    db_session.add(ctrl)
    db_session.flush()

    obj_a = AssessmentObjective(control_id=ctrl.id, objective_key="a", text="Objective a")
    obj_b = AssessmentObjective(control_id=ctrl.id, objective_key="b", text="Objective b")
    db_session.add_all([obj_a, obj_b])
    db_session.flush()

    assessment = Assessment(
        org_id=org.id, framework_id=fw.id, name="SPRS Assessment"
    )
    db_session.add(assessment)
    db_session.flush()

    cs_a = ControlState(
        assessment_id=assessment.id, org_id=org.id,
        objective_id=obj_a.id, status="not_met", responsibility="customer_owns",
    )
    cs_b = ControlState(
        assessment_id=assessment.id, org_id=org.id,
        objective_id=obj_b.id, status="not_met", responsibility="customer_owns",
    )
    db_session.add_all([cs_a, cs_b])
    db_session.flush()

    return {"org": org, "assessment": assessment, "ctrl": ctrl, "cs_a": cs_a, "cs_b": cs_b}


@pytest.mark.integration
def test_sprs_recomputed_when_sole_objective_marked_met(client, db_session):
    d = _seed(db_session)
    url = f"/orgs/{d['org'].id}/assessments/{d['assessment'].id}/control-states/{d['cs'].id}"
    r = client.patch(url, json={"status": "met"})
    assert r.status_code == 200
    assert r.json()["sprs_score"] == 110


@pytest.mark.integration
def test_sprs_recomputed_when_sole_objective_marked_inherited(client, db_session):
    d = _seed(db_session)
    url = f"/orgs/{d['org'].id}/assessments/{d['assessment'].id}/control-states/{d['cs'].id}"
    r = client.patch(url, json={"status": "inherited"})
    assert r.status_code == 200
    assert r.json()["sprs_score"] == 110


@pytest.mark.integration
def test_sprs_deducts_when_objective_partial(client, db_session):
    # partial ≠ met; full weight (1) deducted → 109
    d = _seed(db_session)
    url = f"/orgs/{d['org'].id}/assessments/{d['assessment'].id}/control-states/{d['cs'].id}"
    r = client.patch(url, json={"status": "partial"})
    assert r.json()["sprs_score"] == 109


@pytest.mark.integration
def test_sprs_deducts_full_weight_when_one_of_two_objectives_not_met(client, db_session):
    # control weight = 3; obj_a → met, obj_b still not_met → full 3 deducted → 107
    d = _seed_two_objectives(db_session)
    url_a = (
        f"/orgs/{d['org'].id}/assessments/{d['assessment'].id}"
        f"/control-states/{d['cs_a'].id}"
    )
    r = client.patch(url_a, json={"status": "met"})
    assert r.json()["sprs_score"] == 107  # obj_b still not_met → deduct 3


@pytest.mark.integration
def test_sprs_no_deduction_when_all_objectives_met(client, db_session):
    # Both objectives met → control satisfied → no deduction → 110
    d = _seed_two_objectives(db_session)
    base = f"/orgs/{d['org'].id}/assessments/{d['assessment'].id}/control-states"
    client.patch(f"{base}/{d['cs_a'].id}", json={"status": "met"})
    r = client.patch(f"{base}/{d['cs_b'].id}", json={"status": "met"})
    assert r.json()["sprs_score"] == 110
