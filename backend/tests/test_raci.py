"""Integration tests for RACI endpoints (G.7 — docs/PLAN-gui-restructure.md).

The headline case is bulk-assign cascade without clobbering an existing
per-objective override — G.7 names this the one piece worth real test
weight, and it's the one most likely to silently destroy trust in the
screen if it regresses. CRUD round-trip for individual assignments covers
the rest.

Run in-container:
    docker compose exec backend pytest tests/test_raci.py -m integration -v
"""

from __future__ import annotations

import uuid

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
    Contact,
    Control,
    ControlState,
    Framework,
    Organization,
    RaciAssignment,
)
from tests.conftest import _app_session, _authed, _grant

pytestmark = pytest.mark.integration


@pytest.fixture
def client(db_session: Session, fake_msp_admin):
    app.dependency_overrides[get_session] = _app_session(db_session)
    app.dependency_overrides[get_current_user] = _authed(db_session, fake_msp_admin)
    yield TestClient(app)
    app.dependency_overrides.clear()


def _make_control(db_session, fw, *, control_id, family, objective_keys):
    ctrl = Control(
        framework_id=fw.id,
        control_id=control_id,
        family=family,
        title=control_id,
        requirement_text=control_id,
        sequence_order=1,
    )
    db_session.add(ctrl)
    db_session.flush()
    objs = {}
    for key in objective_keys:
        obj = AssessmentObjective(
            control_id=ctrl.id, objective_key=key, text=f"{control_id}[{key}]"
        )
        db_session.add(obj)
        db_session.flush()
        objs[key] = obj
    return ctrl, objs


def _cs(db_session, assessment_id, objective_id) -> ControlState:
    return db_session.scalars(
        select(ControlState).where(
            ControlState.assessment_id == assessment_id,
            ControlState.objective_id == objective_id,
        )
    ).first()


@pytest.fixture
def ref(db_session: Session) -> dict:
    org = Organization(name=f"RaciOrg-{uuid.uuid4().hex[:6]}")
    fw = Framework(key=f"fw-raci-{uuid.uuid4().hex[:6]}", name="Test FW", version="r2")
    db_session.add_all([org, fw])
    db_session.flush()

    # Family AC: two controls, three objectives total -- the family under
    # bulk-assign test. Family IA: one control/objective, used to prove
    # bulk-assign is scoped to the family it's given, not every control.
    ac1, ac1_objs = _make_control(
        db_session, fw, control_id="AC.L2-3.1.1", family="AC", objective_keys=["a", "b"]
    )
    ac2, ac2_objs = _make_control(
        db_session, fw, control_id="AC.L2-3.1.2", family="AC", objective_keys=["a"]
    )
    ia1, ia1_objs = _make_control(
        db_session, fw, control_id="IA.L2-3.5.1", family="IA", objective_keys=["a"]
    )

    assessment = start_assessment(db_session, org.id, fw.id, "RACI Test")
    db_session.flush()

    ac1a = _cs(db_session, assessment.id, ac1_objs["a"].id)
    ac1b = _cs(db_session, assessment.id, ac1_objs["b"].id)
    ac2a = _cs(db_session, assessment.id, ac2_objs["a"].id)
    ia1a = _cs(db_session, assessment.id, ia1_objs["a"].id)

    jane = Contact(
        org_id=org.id, name="Jane Smith", email="jane@example.com", affiliation="customer"
    )
    bob = Contact(org_id=org.id, name="Bob Jones", email="bob@example.com", affiliation="msp")
    db_session.add_all([jane, bob])
    db_session.flush()

    return {
        "org": org,
        "fw": fw,
        "assessment": assessment,
        "ac1a": ac1a,
        "ac1b": ac1b,
        "ac2a": ac2a,
        "ia1a": ia1a,
        "jane": jane,
        "bob": bob,
    }


def _url(ref: dict, suffix: str = "") -> str:
    return f"/orgs/{ref['org'].id}/assessments/{ref['assessment'].id}/raci{suffix}"


# ---------------------------------------------------------------------------
# Bulk-assign cascade -- the headline case
# ---------------------------------------------------------------------------


def test_bulk_assign_cascades_to_every_control_state_in_family(
    client, db_session, fake_msp_admin, ref
):
    _grant(db_session, fake_msp_admin, org_id=ref["org"].id)

    r = client.post(
        _url(ref, "/bulk"),
        json={
            "family": "AC",
            "contact_id": str(ref["jane"].id),
            "raci_letter": "R",
        },
    )
    assert r.status_code == 200
    assert r.json() == {"assigned": 3, "skipped": 0}

    rows = db_session.scalars(
        select(RaciAssignment).where(
            RaciAssignment.control_state_id.in_([ref["ac1a"].id, ref["ac1b"].id, ref["ac2a"].id])
        )
    ).all()
    assert len(rows) == 3
    assert {row.contact_id for row in rows} == {ref["jane"].id}
    assert {row.raci_letter for row in rows} == {"R"}

    # Family IA (untouched) must not have picked anything up.
    ia_rows = db_session.scalars(
        select(RaciAssignment).where(RaciAssignment.control_state_id == ref["ia1a"].id)
    ).all()
    assert ia_rows == []


def test_bulk_assign_does_not_clobber_existing_objective_override(
    client, db_session, fake_msp_admin, ref
):
    """The headline case from G.7: bulk-assign a family, override one
    objective, bulk-assign that same family again, confirm the override
    survives."""
    _grant(db_session, fake_msp_admin, org_id=ref["org"].id)

    r1 = client.post(
        _url(ref, "/bulk"),
        json={
            "family": "AC",
            "contact_id": str(ref["jane"].id),
            "raci_letter": "R",
        },
    )
    assert r1.status_code == 200
    assert r1.json() == {"assigned": 3, "skipped": 0}

    # Override ac1b: swap Jane -> Bob for 'R' on this one objective, the way
    # a human would from the per-objective override list (delete + re-add).
    override_row = db_session.scalars(
        select(RaciAssignment).where(
            RaciAssignment.control_state_id == ref["ac1b"].id, RaciAssignment.raci_letter == "R"
        )
    ).one()
    del_r = client.delete(_url(ref, f"/{override_row.id}"))
    assert del_r.status_code == 204
    post_r = client.post(
        _url(ref),
        json={
            "control_state_id": str(ref["ac1b"].id),
            "contact_id": str(ref["bob"].id),
            "raci_letter": "R",
        },
    )
    assert post_r.status_code == 201

    # Bulk-assign the same family + letter again, back to Jane.
    r2 = client.post(
        _url(ref, "/bulk"),
        json={
            "family": "AC",
            "contact_id": str(ref["jane"].id),
            "raci_letter": "R",
        },
    )
    assert r2.status_code == 200
    # ac1a and ac2a already had Jane/R from the first pass (skipped, not
    # re-created); ac1b now holds Bob/R (also skipped -- an existing row for
    # that (control_state, letter) pair, regardless of which contact holds
    # it, is exactly what "already has an override" means here).
    assert r2.json() == {"assigned": 0, "skipped": 3}

    # The real assertion: ac1b is still Bob, not reverted to Jane.
    ac1b_rows = db_session.scalars(
        select(RaciAssignment).where(
            RaciAssignment.control_state_id == ref["ac1b"].id, RaciAssignment.raci_letter == "R"
        )
    ).all()
    assert len(ac1b_rows) == 1
    assert ac1b_rows[0].contact_id == ref["bob"].id

    # And the untouched siblings are still Jane.
    for cs_id in (ref["ac1a"].id, ref["ac2a"].id):
        row = db_session.scalars(
            select(RaciAssignment).where(
                RaciAssignment.control_state_id == cs_id, RaciAssignment.raci_letter == "R"
            )
        ).one()
        assert row.contact_id == ref["jane"].id


def test_bulk_assign_different_letter_is_independent(client, db_session, fake_msp_admin, ref):
    """A manual override on letter C must not block a bulk-assign of letter
    R on the same control_state -- the clobber check is per (control_state,
    letter), not per control_state."""
    _grant(db_session, fake_msp_admin, org_id=ref["org"].id)

    post_r = client.post(
        _url(ref),
        json={
            "control_state_id": str(ref["ac1a"].id),
            "contact_id": str(ref["bob"].id),
            "raci_letter": "C",
        },
    )
    assert post_r.status_code == 201

    r = client.post(
        _url(ref, "/bulk"),
        json={
            "family": "AC",
            "contact_id": str(ref["jane"].id),
            "raci_letter": "R",
        },
    )
    assert r.status_code == 200
    assert r.json() == {"assigned": 3, "skipped": 0}

    letters = {
        row.raci_letter: row.contact_id
        for row in db_session.scalars(
            select(RaciAssignment).where(RaciAssignment.control_state_id == ref["ac1a"].id)
        ).all()
    }
    assert letters == {"C": ref["bob"].id, "R": ref["jane"].id}


def test_bulk_assign_unknown_family_404s(client, db_session, fake_msp_admin, ref):
    _grant(db_session, fake_msp_admin, org_id=ref["org"].id)
    r = client.post(
        _url(ref, "/bulk"),
        json={
            "family": "ZZ",
            "contact_id": str(ref["jane"].id),
            "raci_letter": "R",
        },
    )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Individual CRUD round-trip
# ---------------------------------------------------------------------------


def test_create_list_delete_round_trip(client, db_session, fake_msp_admin, ref):
    _grant(db_session, fake_msp_admin, org_id=ref["org"].id)

    create_r = client.post(
        _url(ref),
        json={
            "control_state_id": str(ref["ac1a"].id),
            "contact_id": str(ref["jane"].id),
            "raci_letter": "A",
        },
    )
    assert create_r.status_code == 201
    body = create_r.json()
    assert body["contact_name"] == "Jane Smith"
    assert body["raci_letter"] == "A"
    raci_id = body["id"]

    list_r = client.get(_url(ref))
    assert list_r.status_code == 200
    assert [row["id"] for row in list_r.json()] == [raci_id]

    del_r = client.delete(_url(ref, f"/{raci_id}"))
    assert del_r.status_code == 204

    list_r2 = client.get(_url(ref))
    assert list_r2.json() == []


def test_create_duplicate_triple_409s(client, db_session, fake_msp_admin, ref):
    _grant(db_session, fake_msp_admin, org_id=ref["org"].id)
    body = {
        "control_state_id": str(ref["ac1a"].id),
        "contact_id": str(ref["jane"].id),
        "raci_letter": "R",
    }
    assert client.post(_url(ref), json=body).status_code == 201
    assert client.post(_url(ref), json=body).status_code == 409


def test_create_rejects_invalid_letter(client, db_session, fake_msp_admin, ref):
    _grant(db_session, fake_msp_admin, org_id=ref["org"].id)
    r = client.post(
        _url(ref),
        json={
            "control_state_id": str(ref["ac1a"].id),
            "contact_id": str(ref["jane"].id),
            "raci_letter": "X",
        },
    )
    assert r.status_code == 422


def test_create_rejects_control_state_from_another_assessment(
    client, db_session, fake_msp_admin, ref
):
    _grant(db_session, fake_msp_admin, org_id=ref["org"].id)
    other_cs_id = uuid.uuid4()
    r = client.post(
        _url(ref),
        json={
            "control_state_id": str(other_cs_id),
            "contact_id": str(ref["jane"].id),
            "raci_letter": "R",
        },
    )
    assert r.status_code == 404
