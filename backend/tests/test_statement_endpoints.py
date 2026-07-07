"""Integration tests for GET/PUT .../controls/{id}/statement endpoints."""
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
    Framework,
    ImplementationStatement,
    Organization,
)


@pytest.fixture
def client(db_session):
    app.dependency_overrides[get_session] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def _seed(db_session) -> dict:
    """Seed org → framework → control → objective → assessment."""
    org = Organization(name=f"StmtTestOrg-{uuid.uuid4().hex}")
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

    obj = AssessmentObjective(
        control_id=ctrl.id, objective_key="a", text="Test objective"
    )
    db_session.add(obj)
    db_session.flush()

    assessment = Assessment(
        org_id=org.id,
        framework_id=fw.id,
        name="Test Assessment",
    )
    db_session.add(assessment)
    db_session.flush()

    return {"org": org, "fw": fw, "ctrl": ctrl, "obj": obj, "assessment": assessment}


def _url(d: dict) -> str:
    return (
        f"/orgs/{d['org'].id}/assessments/{d['assessment'].id}"
        f"/controls/{d['ctrl'].id}/statement"
    )


# ---------------------------------------------------------------------------
# GET — no statement yet
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_get_statement_empty(client, db_session):
    d = _seed(db_session)
    r = client.get(_url(d))
    assert r.status_code == 200
    body = r.json()
    assert body["id"] is None
    assert body["body"] == ""
    assert body["status"] is None


# ---------------------------------------------------------------------------
# PUT — create
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_put_creates_statement(client, db_session):
    d = _seed(db_session)
    r = client.put(_url(d), json={"body": "We implement this via...", "status": "draft"})
    assert r.status_code == 200
    body = r.json()
    assert body["id"] is not None
    assert body["body"] == "We implement this via..."
    assert body["status"] == "draft"


@pytest.mark.integration
def test_put_all_valid_statuses(client, db_session):
    for status in ("draft", "reviewed", "approved"):
        d = _seed(db_session)
        r = client.put(_url(d), json={"body": "text", "status": status})
        assert r.status_code == 200
        assert r.json()["status"] == status


@pytest.mark.integration
def test_put_invalid_status_returns_422(client, db_session):
    d = _seed(db_session)
    r = client.put(_url(d), json={"body": "text", "status": "unknown"})
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# GET after PUT
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_get_after_put_returns_saved_data(client, db_session):
    d = _seed(db_session)
    client.put(_url(d), json={"body": "Our approach is...", "status": "reviewed"})
    r = client.get(_url(d))
    assert r.status_code == 200
    body = r.json()
    assert body["body"] == "Our approach is..."
    assert body["status"] == "reviewed"


# ---------------------------------------------------------------------------
# PUT idempotency — second PUT updates in place, no duplicate row
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_put_is_idempotent(client, db_session):
    d = _seed(db_session)
    r1 = client.put(_url(d), json={"body": "Version 1", "status": "draft"})
    r2 = client.put(_url(d), json={"body": "Version 2", "status": "reviewed"})
    assert r1.json()["id"] == r2.json()["id"]

    rows = db_session.scalars(
        select(ImplementationStatement).where(
            ImplementationStatement.assessment_id == d["assessment"].id,
            ImplementationStatement.control_id == d["ctrl"].id,
        )
    ).all()
    assert len(rows) == 1
    assert rows[0].body == "Version 2"
    assert rows[0].status == "reviewed"


# ---------------------------------------------------------------------------
# PUT must NOT touch control_state (isolation constraint)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_put_statement_does_not_modify_control_state(client, db_session):
    d = _seed(db_session)
    cs = ControlState(
        assessment_id=d["assessment"].id,
        org_id=d["org"].id,
        objective_id=d["obj"].id,
        status="not_met",
        responsibility="customer_owns",
    )
    db_session.add(cs)
    db_session.flush()

    client.put(_url(d), json={"body": "Some statement", "status": "approved"})

    db_session.expire(cs)
    assert cs.status == "not_met"
    assert cs.responsibility == "customer_owns"


# ---------------------------------------------------------------------------
# 404 cases
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_get_wrong_org_returns_404(client, db_session):
    d = _seed(db_session)
    url = (
        f"/orgs/{uuid.uuid4()}/assessments/{d['assessment'].id}"
        f"/controls/{d['ctrl'].id}/statement"
    )
    assert client.get(url).status_code == 404


@pytest.mark.integration
def test_put_wrong_org_returns_404(client, db_session):
    d = _seed(db_session)
    url = (
        f"/orgs/{uuid.uuid4()}/assessments/{d['assessment'].id}"
        f"/controls/{d['ctrl'].id}/statement"
    )
    assert client.put(url, json={"body": "x", "status": "draft"}).status_code == 404


@pytest.mark.integration
def test_get_wrong_assessment_returns_404(client, db_session):
    d = _seed(db_session)
    url = (
        f"/orgs/{d['org'].id}/assessments/{uuid.uuid4()}"
        f"/controls/{d['ctrl'].id}/statement"
    )
    assert client.get(url).status_code == 404


@pytest.mark.integration
def test_put_control_wrong_framework_returns_404(client, db_session):
    d = _seed(db_session)
    other_fw = Framework(key=f"fw-{uuid.uuid4().hex}", name="Other FW", version="r1")
    db_session.add(other_fw)
    db_session.flush()
    other_ctrl = Control(
        framework_id=other_fw.id,
        control_id=f"AC.L2-{uuid.uuid4().hex[:6]}",
        family="AC",
        title="Other ctrl",
        requirement_text="req",
        sprs_weight=1,
        sequence_order=1,
    )
    db_session.add(other_ctrl)
    db_session.flush()
    url = (
        f"/orgs/{d['org'].id}/assessments/{d['assessment'].id}"
        f"/controls/{other_ctrl.id}/statement"
    )
    assert client.put(url, json={"body": "x", "status": "draft"}).status_code == 404
