"""Integration tests for GET/PUT .../controls/{id}/statements (batch, per-objective)."""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.auth import get_current_user
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
def client(db_session, fake_msp_admin):
    app.dependency_overrides[get_session] = lambda: db_session
    app.dependency_overrides[get_current_user] = lambda: fake_msp_admin
    yield TestClient(app)
    app.dependency_overrides.clear()


def _seed(db_session, *, org_id: uuid.UUID | None = None) -> dict:
    """Seed org -> framework -> control -> 3 objectives -> assessment."""
    org_kwargs: dict = {"name": f"StmtTestOrg-{uuid.uuid4().hex}"}
    if org_id is not None:
        org_kwargs["id"] = org_id
    org = Organization(**org_kwargs)
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
        discussion="Access control policies limit system access to authorized entities.",
        sprs_weight=1,
        sequence_order=1,
    )
    db_session.add(ctrl)
    db_session.flush()

    obj_a = AssessmentObjective(
        control_id=ctrl.id,
        objective_key="a",
        text="Authorized users are identified.",
        guidance="Examine: access control policy; list of users.",
    )
    obj_b = AssessmentObjective(
        control_id=ctrl.id,
        objective_key="b",
        text="Authorized devices are identified.",
        guidance="Examine: device inventory; network diagrams.",
    )
    obj_c = AssessmentObjective(
        control_id=ctrl.id,
        objective_key="c",
        text="System access is limited to authorized users.",
    )
    db_session.add_all([obj_a, obj_b, obj_c])
    db_session.flush()

    assessment = Assessment(
        org_id=org.id,
        framework_id=fw.id,
        name="Test Assessment",
    )
    db_session.add(assessment)
    db_session.flush()

    return {
        "org": org,
        "fw": fw,
        "ctrl": ctrl,
        "obj_a": obj_a,
        "obj_b": obj_b,
        "obj_c": obj_c,
        "assessment": assessment,
    }


def _base_url(d: dict) -> str:
    return (
        f"/orgs/{d['org'].id}/assessments/{d['assessment'].id}"
        f"/controls/{d['ctrl'].id}/statements"
    )


# ---------------------------------------------------------------------------
# GET — no statements yet
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_get_statements_empty(client, db_session, fake_msp_admin):
    d = _seed(db_session, org_id=fake_msp_admin.org_id)
    r = client.get(_base_url(d))
    assert r.status_code == 200
    items = r.json()
    assert len(items) == 3
    assert all(item["id"] is None for item in items)
    assert all(item["body"] == "" for item in items)
    assert all(item["status"] is None for item in items)
    keys = [item["objective_key"] for item in items]
    assert keys == ["a", "b", "c"]


@pytest.mark.integration
def test_get_statements_returns_guidance(client, db_session, fake_msp_admin):
    d = _seed(db_session, org_id=fake_msp_admin.org_id)
    items = client.get(_base_url(d)).json()
    obj_a_item = next(i for i in items if i["objective_key"] == "a")
    obj_c_item = next(i for i in items if i["objective_key"] == "c")
    assert obj_a_item["objective_guidance"] == "Examine: access control policy; list of users."
    assert obj_c_item["objective_guidance"] is None


@pytest.mark.integration
def test_get_statements_returns_control_discussion(client, db_session, fake_msp_admin):
    d = _seed(db_session, org_id=fake_msp_admin.org_id)
    items = client.get(_base_url(d)).json()
    assert all(
        item["control_discussion"]
        == "Access control policies limit system access to authorized entities."
        for item in items
    )


# ---------------------------------------------------------------------------
# PUT — create batch
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_put_creates_statements(client, db_session, fake_msp_admin):
    d = _seed(db_session, org_id=fake_msp_admin.org_id)
    payload = [
        {"objective_id": str(d["obj_a"].id), "body": "We identify users via AD.",
         "status": "draft"},
        {"objective_id": str(d["obj_b"].id), "body": "Devices are in CMDB.", "status": "reviewed"},
    ]
    r = client.put(_base_url(d), json=payload)
    assert r.status_code == 200
    items = r.json()
    assert len(items) == 2
    assert items[0]["body"] == "We identify users via AD."
    assert items[0]["status"] == "draft"
    assert items[1]["body"] == "Devices are in CMDB."
    assert items[1]["status"] == "reviewed"
    assert all(item["id"] is not None for item in items)


@pytest.mark.integration
def test_put_all_valid_statuses(client, db_session, fake_msp_admin):
    d = _seed(db_session, org_id=fake_msp_admin.org_id)
    for status in ("draft", "reviewed", "approved"):
        payload = [{"objective_id": str(d["obj_a"].id), "body": "text", "status": status}]
        r = client.put(_base_url(d), json=payload)
        assert r.status_code == 200
        assert r.json()[0]["status"] == status


@pytest.mark.integration
def test_put_invalid_status_returns_422(client, db_session, fake_msp_admin):
    d = _seed(db_session, org_id=fake_msp_admin.org_id)
    payload = [{"objective_id": str(d["obj_a"].id), "body": "text", "status": "invalid"}]
    assert client.put(_base_url(d), json=payload).status_code == 422


# ---------------------------------------------------------------------------
# GET after PUT
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_get_after_put_reflects_saved_data(client, db_session, fake_msp_admin):
    d = _seed(db_session, org_id=fake_msp_admin.org_id)
    payload = [
        {"objective_id": str(d["obj_a"].id), "body": "Our approach is...", "status": "reviewed"},
        {"objective_id": str(d["obj_b"].id), "body": "Devices tracked in...", "status": "draft"},
    ]
    client.put(_base_url(d), json=payload)

    items = client.get(_base_url(d)).json()
    by_key = {i["objective_key"]: i for i in items}
    assert by_key["a"]["body"] == "Our approach is..."
    assert by_key["a"]["status"] == "reviewed"
    assert by_key["b"]["body"] == "Devices tracked in..."
    assert by_key["c"]["body"] == ""
    assert by_key["c"]["status"] is None


# ---------------------------------------------------------------------------
# PUT idempotency
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_put_is_idempotent(client, db_session, fake_msp_admin):
    d = _seed(db_session, org_id=fake_msp_admin.org_id)
    p1 = [{"objective_id": str(d["obj_a"].id), "body": "Version 1", "status": "draft"}]
    p2 = [{"objective_id": str(d["obj_a"].id), "body": "Version 2", "status": "approved"}]
    r1 = client.put(_base_url(d), json=p1)
    r2 = client.put(_base_url(d), json=p2)
    assert r1.json()[0]["id"] == r2.json()[0]["id"]

    rows = db_session.scalars(
        select(ImplementationStatement).where(
            ImplementationStatement.assessment_id == d["assessment"].id,
            ImplementationStatement.objective_id == d["obj_a"].id,
        )
    ).all()
    assert len(rows) == 1
    assert rows[0].body == "Version 2"
    assert rows[0].status == "approved"


# ---------------------------------------------------------------------------
# PUT must NOT touch control_state (isolation constraint)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_put_statement_does_not_modify_control_state(client, db_session, fake_msp_admin):
    d = _seed(db_session, org_id=fake_msp_admin.org_id)
    cs = ControlState(
        assessment_id=d["assessment"].id,
        org_id=d["org"].id,
        objective_id=d["obj_a"].id,
        status="not_met",
        responsibility="customer_owns",
    )
    db_session.add(cs)
    db_session.flush()

    payload = [{"objective_id": str(d["obj_a"].id), "body": "Some statement", "status": "approved"}]
    client.put(_base_url(d), json=payload)

    db_session.expire(cs)
    assert cs.status == "not_met"
    assert cs.responsibility == "customer_owns"


# ---------------------------------------------------------------------------
# Partial-save: PUT for subset of objectives leaves others untouched
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_put_partial_subset_does_not_affect_others(client, db_session, fake_msp_admin):
    d = _seed(db_session, org_id=fake_msp_admin.org_id)
    # Write obj_a
    client.put(
        _base_url(d),
        json=[{"objective_id": str(d["obj_a"].id), "body": "A only", "status": "draft"}],
    )
    # Write obj_b separately, not obj_a again
    client.put(
        _base_url(d),
        json=[{"objective_id": str(d["obj_b"].id), "body": "B only", "status": "reviewed"}],
    )

    items = client.get(_base_url(d)).json()
    by_key = {i["objective_key"]: i for i in items}
    assert by_key["a"]["body"] == "A only"
    assert by_key["b"]["body"] == "B only"
    assert by_key["c"]["body"] == ""


# ---------------------------------------------------------------------------
# 404 cases
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_get_wrong_org_returns_404(client, db_session, fake_msp_admin):
    """URL org_id is the caller's own org (passes the guard); the real
    assessment belongs to a different, unrelated org — handler 404s."""
    d = _seed(db_session)  # unrelated org holds the real assessment
    db_session.add(Organization(id=fake_msp_admin.org_id, name="Caller Org"))
    db_session.flush()
    url = (
        f"/orgs/{fake_msp_admin.org_id}/assessments/{d['assessment'].id}"
        f"/controls/{d['ctrl'].id}/statements"
    )
    assert client.get(url).status_code == 404


@pytest.mark.integration
def test_put_wrong_org_returns_404(client, db_session, fake_msp_admin):
    """Same shape as test_get_wrong_org_returns_404."""
    d = _seed(db_session)  # unrelated org holds the real assessment
    db_session.add(Organization(id=fake_msp_admin.org_id, name="Caller Org"))
    db_session.flush()
    url = (
        f"/orgs/{fake_msp_admin.org_id}/assessments/{d['assessment'].id}"
        f"/controls/{d['ctrl'].id}/statements"
    )
    payload = [{"objective_id": str(d["obj_a"].id), "body": "x", "status": "draft"}]
    assert client.put(url, json=payload).status_code == 404


@pytest.mark.integration
def test_get_wrong_assessment_returns_404(client, db_session, fake_msp_admin):
    d = _seed(db_session, org_id=fake_msp_admin.org_id)
    url = f"/orgs/{d['org'].id}/assessments/{uuid.uuid4()}/controls/{d['ctrl'].id}/statements"
    assert client.get(url).status_code == 404


@pytest.mark.integration
def test_put_control_wrong_framework_returns_404(client, db_session, fake_msp_admin):
    d = _seed(db_session, org_id=fake_msp_admin.org_id)
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
        f"/controls/{other_ctrl.id}/statements"
    )
    payload = [{"objective_id": str(d["obj_a"].id), "body": "x", "status": "draft"}]
    assert client.put(url, json=payload).status_code == 404
