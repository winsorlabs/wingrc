"""Integration tests for GET/POST /orgs/{org_id}/assessments's last_activity_at (G.4).

Derived at read time as MAX(control_state.updated_at,
implementation_statement.updated_at) per assessment, not stored — see
docs/PLAN-gui-restructure.md's G.4 section for why (every future write to
either table already bumps its own updated_at for free; a stored column
would need every future mutation site to remember to also bump it too).

Deliberately sets updated_at directly on ORM objects rather than relying
on real wall-clock gaps between operations: this test suite runs each
test inside one db_session transaction (see conftest.py), and Postgres's
now()/CURRENT_TIMESTAMP (what updated_at's onupdate=func.now() calls)
returns transaction-START time, not statement-execution time — the same
root cause as migration 0020's password_history bug and sprs_snapshot's
seq column. Two ControlState rows touched in the same test transaction
would otherwise get an identical updated_at, making "which one is more
recent" untestable here even though the two would differ in production
(separate requests = separate transactions = genuinely different now()).
Setting updated_at explicitly sidesteps this entirely rather than fighting
it, and makes the ordering assertion deterministic regardless of timing.

Run in-container:
    docker compose exec backend pytest tests/test_assessments_list.py -m integration -v
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.db import get_session
from app.engine import start_assessment
from app.main import app
from app.models import (
    AssessmentObjective,
    Control,
    ControlState,
    Framework,
    ImplementationStatement,
    Organization,
)
from tests.conftest import _app_session, _authed, _grant

pytestmark = pytest.mark.integration


@pytest.fixture
def client(db_session: Session, fake_msp_admin):
    app.dependency_overrides[get_session] = _app_session(db_session)
    app.dependency_overrides[get_current_user] = _authed(db_session, fake_msp_admin)
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def ref(db_session: Session) -> dict:
    """One org + framework + control/objective, shared by every assessment
    this file creates off it (multiple assessments per org+framework is
    allowed — no uniqueness constraint on that pair — matching real usage,
    e.g. annual re-assessments)."""
    org = Organization(name=f"ActivityOrg-{uuid.uuid4().hex[:6]}")
    fw = Framework(key=f"fw-activity-{uuid.uuid4().hex[:6]}", name="Test FW", version="r2")
    db_session.add_all([org, fw])
    db_session.flush()

    ctrl = Control(
        framework_id=fw.id, control_id="AC.L2-3.1.1", family="AC",
        title="Access control", requirement_text="Limit access",
        sprs_weight=5, sequence_order=1,
    )
    db_session.add(ctrl)
    db_session.flush()

    obj = AssessmentObjective(control_id=ctrl.id, objective_key="a", text="AC[a]")
    db_session.add(obj)
    db_session.flush()

    return {"org": org, "fw": fw, "ctrl": ctrl, "obj": obj}


def _first_control_state(db_session: Session, assessment_id: uuid.UUID) -> ControlState:
    cs = db_session.scalars(
        select(ControlState).where(ControlState.assessment_id == assessment_id)
    ).first()
    assert cs is not None
    return cs


def test_last_activity_at_present_and_defaults_to_started_at(
    client: TestClient, db_session: Session, ref: dict, fake_msp_admin
):
    _grant(db_session, fake_msp_admin, org_id=ref["org"].id)
    a = start_assessment(db_session, ref["org"].id, ref["fw"].id, "Untouched Assessment")

    resp = client.get(f"/orgs/{ref['org'].id}/assessments")
    assert resp.status_code == 200
    out = next(x for x in resp.json() if x["id"] == str(a.id))
    assert "last_activity_at" in out
    # Nothing has touched this assessment beyond its own seed — falls back
    # to started_at (the seed's own control_state.updated_at, in practice,
    # not literally started_at, but the two happen in the same call and
    # are indistinguishable to this assertion; the point is it's populated
    # and not some far-past/far-future placeholder).
    last_activity = datetime.fromisoformat(out["last_activity_at"])
    started_at = datetime.fromisoformat(out["started_at"])
    assert abs((last_activity - started_at).total_seconds()) < 5


def test_last_activity_at_reflects_control_state_activity_not_creation_order(
    client: TestClient, db_session: Session, ref: dict, fake_msp_admin
):
    """The core ordering-correctness case: A and B are created in the same
    call order (A first, though within one test transaction their
    started_at values tie exactly — see this file's own module docstring
    on why). A's control_state is then explicitly touched to be more
    recent than anything on B — last_activity_at must reflect that
    activity difference, not started_at/creation order, proving this is
    genuinely derived from activity rather than just mirroring started_at."""
    _grant(db_session, fake_msp_admin, org_id=ref["org"].id)
    a = start_assessment(db_session, ref["org"].id, ref["fw"].id, "Assessment A (touched)")
    b = start_assessment(db_session, ref["org"].id, ref["fw"].id, "Assessment B (untouched)")

    cs_a = _first_control_state(db_session, a.id)
    cs_a.updated_at = datetime.now(UTC) + timedelta(days=1)
    db_session.flush()

    resp = client.get(f"/orgs/{ref['org'].id}/assessments")
    assert resp.status_code == 200
    body = {x["id"]: x for x in resp.json()}
    a_activity = datetime.fromisoformat(body[str(a.id)]["last_activity_at"])
    b_activity = datetime.fromisoformat(body[str(b.id)]["last_activity_at"])

    assert a_activity > b_activity, (
        "A's control_state was touched more recently than B's — "
        "last_activity_at must reflect that, not creation order"
    )


def test_last_activity_at_reflects_implementation_statement_activity(
    client: TestClient, db_session: Session, ref: dict, fake_msp_admin
):
    """Proves the implementation_statement side of the MAX() is genuinely
    consulted, not just control_state."""
    _grant(db_session, fake_msp_admin, org_id=ref["org"].id)
    a = start_assessment(db_session, ref["org"].id, ref["fw"].id, "Assessment A (statement)")
    b = start_assessment(db_session, ref["org"].id, ref["fw"].id, "Assessment B (untouched)")

    stmt = ImplementationStatement(
        org_id=ref["org"].id, objective_id=ref["obj"].id, assessment_id=a.id,
        body="Drafted.", status="draft",
    )
    db_session.add(stmt)
    db_session.flush()
    stmt.updated_at = datetime.now(UTC) + timedelta(days=2)
    db_session.flush()

    resp = client.get(f"/orgs/{ref['org'].id}/assessments")
    assert resp.status_code == 200
    body = {x["id"]: x for x in resp.json()}
    a_activity = datetime.fromisoformat(body[str(a.id)]["last_activity_at"])
    b_activity = datetime.fromisoformat(body[str(b.id)]["last_activity_at"])

    assert a_activity > b_activity


def test_create_assessment_response_includes_last_activity_at(
    client: TestClient, db_session: Session, ref: dict, fake_msp_admin
):
    _grant(db_session, fake_msp_admin, org_id=ref["org"].id)
    resp = client.post(
        f"/orgs/{ref['org'].id}/assessments",
        json={"framework_id": str(ref["fw"].id), "name": "Freshly Created"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert "last_activity_at" in body
    last_activity = datetime.fromisoformat(body["last_activity_at"])
    started_at = datetime.fromisoformat(body["started_at"])
    assert abs((last_activity - started_at).total_seconds()) < 5
