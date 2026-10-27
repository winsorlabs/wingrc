"""GET /orgs/{org_id}/environment and get_scope's filters (roadmap Q.0).

The environment view lists devices and people *in the boundary*. "Active"
is not that: a rejected entity stays status=active with in_boundary=False
(domain.EntityStatus), so these tests seed one of each state and check
which list each lands in, that the rejected one is still reachable, and
that every total is a COUNT(*) rather than the length of a capped list.
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
    ScopeEntity,
)
from app.routers import environment as environment_module
from tests.conftest import _app_session, _authed, _grant, _make_fake_user

pytestmark = pytest.mark.integration

ROLES = ("msp_admin", "consultant_admin", "msp_engineer", "customer_poc", "c3pao_assessor")


def _org(db_session: Session, **fields) -> Organization:
    org = Organization(name=f"EnvOrg-{uuid.uuid4().hex[:8]}", **fields)
    db_session.add(org)
    db_session.flush()
    return org


def _client_as(db_session: Session, role: str, org: Organization) -> TestClient:
    user = _make_fake_user(
        role=role, org_id=org.id, email=f"{role}-{uuid.uuid4().hex[:8]}@example.com"
    )
    _grant(db_session, user, org_id=org.id)
    app.dependency_overrides[get_session] = _app_session(db_session)
    app.dependency_overrides[get_current_user] = _authed(db_session, user)
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


def _entity(
    db_session, org, key, *, entity_type="device", status="active", in_boundary=True, **attrs
):
    e = ScopeEntity(
        org_id=org.id,
        entity_type=entity_type,
        natural_key=key,
        status=status,
        in_boundary=in_boundary,
        source="manual",
        attributes=attrs,
    )
    db_session.add(e)
    db_session.flush()
    return e


@pytest.fixture
def seeded(db_session: Session) -> dict:
    org = _org(db_session)
    return {
        "org": org,
        "accepted": _entity(db_session, org, "DEV-ACCEPTED", hostname="ws01"),
        "rejected": _entity(db_session, org, "DEV-REJECTED", in_boundary=False),
        "pending": _entity(db_session, org, "DEV-PENDING", status="pending_approval"),
        "gone": _entity(db_session, org, "DEV-GONE", status="decommissioned"),
        "person": _entity(
            db_session, org, "jdoe", entity_type="person",
            **{"First Name": "Jane", "Last Name": "Doe"},
        ),
        "person_out": _entity(db_session, org, "xout", entity_type="person", in_boundary=False),
    }


def _keys(widget: dict) -> list[str]:
    return [r["natural_key"] for r in widget["rows"]]


def test_a_rejected_device_is_not_in_the_boundary_list(db_session, seeded):
    body = _client_as(db_session, "msp_engineer", seeded["org"]).get(
        f"/orgs/{seeded['org'].id}/environment"
    ).json()
    devices = body["devices_in_boundary"]
    assert _keys(devices) == ["DEV-ACCEPTED"]
    assert devices["total"] == 1
    assert (devices["status"], devices["in_boundary"]) == ("active", True)
    assert body["devices_out_of_boundary"] == 1
    assert _keys(body["pending_approval"]) == ["DEV-PENDING"]


def test_a_rejected_device_stays_reachable(db_session, seeded):
    """The dashboard's out-of-boundary line opens this read."""
    org = seeded["org"]
    rows = _client_as(db_session, "msp_engineer", org).get(
        f"/orgs/{org.id}/scope",
        params={"entity_type": "device", "status": "active", "in_boundary": "false"},
    ).json()
    assert [r["natural_key"] for r in rows] == ["DEV-REJECTED"]


def test_people_in_boundary_are_scope_people_not_logins(db_session, seeded):
    body = _client_as(db_session, "msp_engineer", seeded["org"]).get(
        f"/orgs/{seeded['org'].id}/environment"
    ).json()
    people = body["people_in_boundary"]
    assert [(r["natural_key"], r["display_name"]) for r in people["rows"]] == [("jdoe", "Jane Doe")]
    assert people["total"] == 1
    assert body["people_out_of_boundary"] == 1


def test_total_is_counted_not_the_length_of_the_capped_rows(db_session, monkeypatch):
    org = _org(db_session)
    for i in range(5):
        _entity(db_session, org, f"DEV-{i:02d}")
    monkeypatch.setattr(environment_module, "ENVIRONMENT_LIST_CAP", 3)
    body = _client_as(db_session, "msp_engineer", org).get(f"/orgs/{org.id}/environment").json()
    devices = body["devices_in_boundary"]
    assert len(devices["rows"]) == 3, (
        "the population must exceed the cap or this test proves nothing"
    )
    assert devices["total"] == 5
    assert devices["cap"] == 3


def test_an_org_with_no_assessment_and_an_empty_profile(db_session):
    org = _org(db_session)
    resp = _client_as(db_session, "msp_engineer", org).get(f"/orgs/{org.id}/environment")
    assert resp.status_code == 200
    body = resp.json()
    assert body["assessments"] == []
    info = body["org"]
    assert info["name"] == org.name
    assert all(
        info[f] is None
        for f in ("cage_code", "uei", "address_line1", "address_line2", "city",
                  "state_or_province", "postal_code", "phone_primary", "website")
    )
    assert info["missing_ssp_fields"] == ["cage_code", "uei"]
    for widget in ("devices_in_boundary", "people_in_boundary", "pending_approval"):
        assert body[widget]["rows"] == [] and body[widget]["total"] == 0


def test_org_info_names_the_framework_as_the_level(db_session):
    org = _org(db_session, cage_code="1ABC2", uei="ABCDEF123456")
    fw = Framework(key=f"fw-env-{uuid.uuid4().hex[:6]}", name="NIST SP 800-171", version="Rev 2")
    db_session.add(fw)
    db_session.flush()
    start_assessment(db_session, org.id, fw.id, "2026 self-assessment")
    body = _client_as(db_session, "msp_engineer", org).get(f"/orgs/{org.id}/environment").json()
    assert body["org"]["missing_ssp_fields"] == []
    [ref] = body["assessments"]
    got = (ref["name"], ref["framework_name"], ref["framework_version"], ref["assessment_type"])
    assert got == ("2026 self-assessment", "NIST SP 800-171", "Rev 2", "self")


@pytest.mark.parametrize("role", ROLES)
def test_every_role_with_access_reads_the_environment(db_session, seeded, role):
    """c3pao_assessor included, deliberately: the boundary inventory is
    evidence. Held here rather than left an accident of the HTTP method."""
    org = seeded["org"]
    client = _client_as(db_session, role, org)
    assert client.get(f"/orgs/{org.id}/environment").status_code == 200
    assert client.get(f"/orgs/{org.id}/scope").status_code == 200


def test_a_user_without_membership_is_refused(db_session, seeded):
    other = _org(db_session)
    client = _client_as(db_session, "msp_engineer", other)
    assert client.get(f"/orgs/{seeded['org'].id}/environment").status_code == 403


@pytest.mark.parametrize("role", [r for r in ROLES if r != "msp_admin"])
def test_the_audit_log_quick_link_is_refused_by_its_endpoint(db_session, seeded, role):
    """The Audit Log quick link renders for msp_admin only; the endpoint is
    what refuses everyone else."""
    org = seeded["org"]
    assert _client_as(db_session, role, org).get(f"/orgs/{org.id}/audit-log").status_code == 403


def test_get_scope_rejects_an_unknown_status_like_entity_type(db_session, seeded):
    org = seeded["org"]
    client = _client_as(db_session, "msp_engineer", org)
    bad_status = client.get(f"/orgs/{org.id}/scope", params={"status": "accepted"})
    bad_type = client.get(f"/orgs/{org.id}/scope", params={"entity_type": "laptop"})
    assert bad_status.status_code == 422 and "status must be one of" in bad_status.json()["detail"]
    assert bad_type.status_code == 422 and "entity_type must be one of" in bad_type.json()["detail"]


def test_get_scope_filters_and_pages(db_session, seeded):
    org = seeded["org"]
    client = _client_as(db_session, "msp_engineer", org)
    everything = client.get(f"/orgs/{org.id}/scope").json()
    assert len(everything) == 6, "no limit still returns every row"
    pending = client.get(f"/orgs/{org.id}/scope", params={"status": "pending_approval"}).json()
    assert [r["natural_key"] for r in pending] == ["DEV-PENDING"]
    devices = client.get(f"/orgs/{org.id}/scope", params={"entity_type": "device"}).json()
    page = client.get(
        f"/orgs/{org.id}/scope", params={"entity_type": "device", "limit": 2, "offset": 1}
    ).json()
    assert [r["id"] for r in page] == [r["id"] for r in devices[1:3]]


def test_reading_the_environment_changes_no_control_state_or_score(db_session, seeded):
    org = seeded["org"]
    fw = Framework(key=f"fw-env-{uuid.uuid4().hex[:6]}", name="FW", version="r2")
    db_session.add(fw)
    db_session.flush()
    ctrl = Control(
        framework_id=fw.id, control_id="AC.L2-3.1.1", family="AC", title="t",
        requirement_text="t", sprs_weight=5, sequence_order=1,
    )
    db_session.add(ctrl)
    db_session.flush()
    db_session.add(AssessmentObjective(control_id=ctrl.id, objective_key="a", text="t"))
    db_session.flush()
    assessment = start_assessment(db_session, org.id, fw.id, "A")
    recompute_sprs(db_session, assessment.id)
    db_session.flush()

    def snapshot():
        db_session.expire_all()
        states = sorted(
            (str(cs.id), cs.status)
            for cs in db_session.scalars(
                select(ControlState).where(ControlState.assessment_id == assessment.id)
            )
        )
        db_session.refresh(assessment)
        return states, assessment.sprs_score

    before = snapshot()
    assert before[0], "fixture must create control states or this test proves nothing"
    client = _client_as(db_session, "msp_engineer", org)
    assert client.get(f"/orgs/{org.id}/environment").status_code == 200
    assert client.get(f"/orgs/{org.id}/assessments/{assessment.id}/dashboard").status_code == 200
    assert snapshot() == before
