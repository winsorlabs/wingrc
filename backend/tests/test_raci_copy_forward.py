"""Integration tests for RACI copy-forward on new assessment creation
(engine.py:copy_forward_raci) -- the decision recorded but not built in
docs/PLAN-gui-restructure.md's G.7 section, built here.

Engine-level tests call copy_forward_raci directly (no HTTP) to isolate
the join/matching logic from the router. Router-level tests at the bottom
cover the API response shape and the audit log entry.

Run in-container:
    docker compose exec backend pytest tests/test_raci_copy_forward.py -m integration -v
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.db import get_session
from app.engine import copy_forward_raci, start_assessment
from app.main import app
from app.models import (
    Assessment,
    AssessmentObjective,
    AuditLog,
    Contact,
    Control,
    ControlState,
    Framework,
    Organization,
    RaciAssignment,
)
from tests.conftest import _app_session, _authed, _grant

pytestmark = pytest.mark.integration


def _make_control(db_session, fw, *, control_id, family="AC", objective_keys=("a",)):
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


def _contact(db_session, org, *, name, email, affiliation="customer") -> Contact:
    c = Contact(org_id=org.id, name=name, email=email, affiliation=affiliation)
    db_session.add(c)
    db_session.flush()
    return c


def _org_and_fw(db_session):
    org = Organization(name=f"RaciFwdOrg-{uuid.uuid4().hex[:6]}")
    fw = Framework(key=f"fw-raci-fwd-{uuid.uuid4().hex[:6]}", name="Test FW", version="r2")
    db_session.add_all([org, fw])
    db_session.flush()
    return org, fw


# ---------------------------------------------------------------------------
# The headline case: objective identity, not control_state row identity
# ---------------------------------------------------------------------------


def test_carries_by_objective_not_by_control_state_id(db_session: Session):
    org, fw = _org_and_fw(db_session)
    ctrl, objs = _make_control(db_session, fw, control_id="AC.L2-3.1.1")

    old = start_assessment(db_session, org.id, fw.id, "Old")
    jane = _contact(db_session, org, name="Jane", email="jane@example.com")
    old_cs = _cs(db_session, old.id, objs["a"].id)
    db_session.add(RaciAssignment(control_state_id=old_cs.id, contact_id=jane.id, raci_letter="R"))
    db_session.flush()

    new = start_assessment(db_session, org.id, fw.id, "New")
    new_cs = _cs(db_session, new.id, objs["a"].id)
    assert new_cs.id != old_cs.id, (
        "fixture assumption: a new assessment gets new control_state rows"
    )

    summary = copy_forward_raci(
        db_session, org_id=org.id, framework_id=fw.id, new_assessment_id=new.id
    )
    assert summary["source_assessment_id"] == str(old.id)
    assert summary["carried"] == 1
    assert summary["skipped_no_match"] == 0
    assert summary["skipped_inactive_contact"] == 0

    rows = db_session.scalars(
        select(RaciAssignment).where(RaciAssignment.control_state_id == new_cs.id)
    ).all()
    assert len(rows) == 1
    assert rows[0].contact_id == jane.id
    assert rows[0].raci_letter == "R"
    # The old assignment is untouched -- this is a copy, not a move.
    assert (
        db_session.scalars(
            select(RaciAssignment).where(RaciAssignment.control_state_id == old_cs.id)
        ).one()
    ).contact_id == jane.id


def test_multiple_letters_and_contacts_on_one_objective_all_carry(db_session: Session):
    org, fw = _org_and_fw(db_session)
    ctrl, objs = _make_control(db_session, fw, control_id="AC.L2-3.1.1")

    old = start_assessment(db_session, org.id, fw.id, "Old")
    jane = _contact(db_session, org, name="Jane", email="jane@example.com")
    bob = _contact(db_session, org, name="Bob", email="bob@example.com", affiliation="msp")
    old_cs = _cs(db_session, old.id, objs["a"].id)
    db_session.add_all(
        [
            RaciAssignment(control_state_id=old_cs.id, contact_id=jane.id, raci_letter="R"),
            RaciAssignment(control_state_id=old_cs.id, contact_id=jane.id, raci_letter="A"),
            RaciAssignment(control_state_id=old_cs.id, contact_id=bob.id, raci_letter="C"),
        ]
    )
    db_session.flush()

    new = start_assessment(db_session, org.id, fw.id, "New")
    summary = copy_forward_raci(
        db_session, org_id=org.id, framework_id=fw.id, new_assessment_id=new.id
    )
    assert summary["carried"] == 3

    new_cs = _cs(db_session, new.id, objs["a"].id)
    rows = db_session.scalars(
        select(RaciAssignment).where(RaciAssignment.control_state_id == new_cs.id)
    ).all()
    assert {(r.contact_id, r.raci_letter) for r in rows} == {
        (jane.id, "R"),
        (jane.id, "A"),
        (bob.id, "C"),
    }


def test_objective_with_no_prior_assignment_stays_empty(db_session: Session):
    org, fw = _org_and_fw(db_session)
    ctrl, objs = _make_control(db_session, fw, control_id="AC.L2-3.1.1", objective_keys=("a", "b"))

    old = start_assessment(db_session, org.id, fw.id, "Old")
    jane = _contact(db_session, org, name="Jane", email="jane@example.com")
    old_cs_a = _cs(db_session, old.id, objs["a"].id)
    db_session.add(
        RaciAssignment(control_state_id=old_cs_a.id, contact_id=jane.id, raci_letter="R")
    )
    db_session.flush()
    # objective "b" deliberately gets no assignment on the source assessment.

    new = start_assessment(db_session, org.id, fw.id, "New")
    summary = copy_forward_raci(
        db_session, org_id=org.id, framework_id=fw.id, new_assessment_id=new.id
    )
    assert summary["carried"] == 1
    assert summary["total_objectives"] == 2
    assert summary["unassigned_objectives"] == 1

    new_cs_b = _cs(db_session, new.id, objs["b"].id)
    rows = db_session.scalars(
        select(RaciAssignment).where(RaciAssignment.control_state_id == new_cs_b.id)
    ).all()
    assert rows == []


# ---------------------------------------------------------------------------
# Framework / catalog drift
# ---------------------------------------------------------------------------


def test_objective_absent_from_new_assessment_is_skipped_without_error(db_session: Session):
    """Simulates a catalog change removing an objective between the source
    assessment and the new one: builds the new assessment's control_state
    set for only a subset of the framework's current objectives, rather
    than going through start_assessment's normal seed-every-objective
    path -- the only way to construct "old assessment had objective X,
    new one doesn't" without actually deleting catalog rows (which the FK
    from control_state.objective_id blocks outright while any
    control_state row still references them)."""
    org, fw = _org_and_fw(db_session)
    ctrl, objs = _make_control(db_session, fw, control_id="AC.L2-3.1.1", objective_keys=("a", "b"))

    old = start_assessment(db_session, org.id, fw.id, "Old")
    jane = _contact(db_session, org, name="Jane", email="jane@example.com")
    old_cs_b = _cs(db_session, old.id, objs["b"].id)
    db_session.add(
        RaciAssignment(control_state_id=old_cs_b.id, contact_id=jane.id, raci_letter="R")
    )
    db_session.flush()

    new = Assessment(org_id=org.id, framework_id=fw.id, name="New", status="in_progress")
    db_session.add(new)
    db_session.flush()
    db_session.add(
        ControlState(
            assessment_id=new.id,
            org_id=org.id,
            objective_id=objs["a"].id,
            status="not_met",
            responsibility="customer_owns",
        )
    )
    db_session.flush()

    summary = copy_forward_raci(
        db_session, org_id=org.id, framework_id=fw.id, new_assessment_id=new.id
    )
    assert summary["source_assessment_id"] == str(old.id)
    assert summary["carried"] == 0
    assert summary["skipped_no_match"] == 1
    assert summary["skipped_inactive_contact"] == 0


# ---------------------------------------------------------------------------
# Contacts: hard-delete + cascade means "inactive" isn't a state that
# survives to be seen, but the defensive skip branch is real.
# ---------------------------------------------------------------------------


def test_hard_deleted_contact_leaves_nothing_to_carry_or_skip(db_session: Session):
    """Contact has no deleted_at/is_active flag -- deletion is a hard
    DELETE, and RaciAssignment.contact_id is ON DELETE CASCADE. Deleting
    the contact removes the assignment too, before copy-forward ever runs
    -- there's nothing left to carry AND nothing left to count as
    skipped. This is the real, reachable behavior; contrast with the next
    test, which exercises the defensive branch by constructing a state
    this schema doesn't normally allow."""
    org, fw = _org_and_fw(db_session)
    ctrl, objs = _make_control(db_session, fw, control_id="AC.L2-3.1.1")

    old = start_assessment(db_session, org.id, fw.id, "Old")
    departed = _contact(db_session, org, name="Departed", email="departed@example.com")
    old_cs = _cs(db_session, old.id, objs["a"].id)
    db_session.add(
        RaciAssignment(control_state_id=old_cs.id, contact_id=departed.id, raci_letter="R")
    )
    db_session.flush()

    db_session.delete(departed)
    db_session.flush()
    assert (
        db_session.scalars(
            select(RaciAssignment).where(RaciAssignment.control_state_id == old_cs.id)
        ).all()
        == []
    ), "fixture assumption: ON DELETE CASCADE already removed the assignment"

    new = start_assessment(db_session, org.id, fw.id, "New")
    summary = copy_forward_raci(
        db_session, org_id=org.id, framework_id=fw.id, new_assessment_id=new.id
    )
    assert summary["carried"] == 0
    assert summary["skipped_inactive_contact"] == 0
    assert summary["unassigned_objectives"] == summary["total_objectives"]


def test_assignment_referencing_a_missing_contact_is_skipped_not_carried(db_session: Session):
    """Exercises copy_forward_raci's defensive skipped_inactive_contact
    branch directly. Cannot be produced through any normal write path in
    this schema (see the test above) -- constructed here by disabling
    raci_assignment's own triggers for one raw insert, the only way to get
    a contact_id that doesn't resolve to a live Contact row past the FK.
    This is deliberately testing dead-under-today's-schema defensive code,
    not a scenario a user can currently reach -- kept so the branch has
    real coverage if a future soft-deactivation feature (mirroring ADR
    0006's user anonymize/hard-delete split) ever produces a row like
    this without cleaning up assignments."""
    org, fw = _org_and_fw(db_session)
    ctrl, objs = _make_control(db_session, fw, control_id="AC.L2-3.1.1")

    old = start_assessment(db_session, org.id, fw.id, "Old")
    old_cs = _cs(db_session, old.id, objs["a"].id)
    ghost_contact_id = uuid.uuid4()  # never a real Contact row

    db_session.execute(text("ALTER TABLE raci_assignment DISABLE TRIGGER ALL"))
    db_session.execute(
        text(
            "INSERT INTO raci_assignment "
            "(id, control_state_id, contact_id, raci_letter, created_at) "
            "VALUES (:id, :cs, :contact, 'R', now())"
        ),
        {"id": uuid.uuid4(), "cs": old_cs.id, "contact": ghost_contact_id},
    )
    db_session.execute(text("ALTER TABLE raci_assignment ENABLE TRIGGER ALL"))
    db_session.flush()

    new = start_assessment(db_session, org.id, fw.id, "New")
    summary = copy_forward_raci(
        db_session, org_id=org.id, framework_id=fw.id, new_assessment_id=new.id
    )
    assert summary["carried"] == 0
    assert summary["skipped_inactive_contact"] == 1

    new_cs = _cs(db_session, new.id, objs["a"].id)
    rows = db_session.scalars(
        select(RaciAssignment).where(RaciAssignment.control_state_id == new_cs.id)
    ).all()
    assert rows == []


# ---------------------------------------------------------------------------
# Which prior assessment
# ---------------------------------------------------------------------------


def test_first_assessment_for_org_creates_cleanly_with_no_prior(db_session: Session):
    org, fw = _org_and_fw(db_session)
    _make_control(db_session, fw, control_id="AC.L2-3.1.1")

    first = start_assessment(db_session, org.id, fw.id, "First")
    summary = copy_forward_raci(
        db_session, org_id=org.id, framework_id=fw.id, new_assessment_id=first.id
    )
    assert summary["source_assessment_id"] is None
    assert summary["carried"] == 0
    assert summary["skipped_no_match"] == 0
    assert summary["skipped_inactive_contact"] == 0
    assert summary["unassigned_objectives"] == summary["total_objectives"]


def test_picks_the_most_recently_started_prior_assessment(db_session: Session):
    org, fw = _org_and_fw(db_session)
    ctrl, objs = _make_control(db_session, fw, control_id="AC.L2-3.1.1")
    jane = _contact(db_session, org, name="Jane", email="jane@example.com")
    bob = _contact(db_session, org, name="Bob", email="bob@example.com", affiliation="msp")

    older = start_assessment(db_session, org.id, fw.id, "Older")
    older_cs = _cs(db_session, older.id, objs["a"].id)
    db_session.add(
        RaciAssignment(control_state_id=older_cs.id, contact_id=jane.id, raci_letter="R")
    )
    db_session.flush()

    newer = start_assessment(db_session, org.id, fw.id, "Newer")
    newer_cs = _cs(db_session, newer.id, objs["a"].id)
    db_session.add(RaciAssignment(control_state_id=newer_cs.id, contact_id=bob.id, raci_letter="R"))
    db_session.flush()
    # Force a real ordering: db_session's fixture runs everything inside one
    # transaction, so func.now() (started_at's server_default) can return
    # the identical instant for both inserts above (same root cause noted
    # in test_assessments_list.py). Set started_at explicitly rather than
    # relying on wall-clock gaps within one transaction.
    older.started_at = datetime.now(UTC) - timedelta(days=1)
    newer.started_at = datetime.now(UTC)
    db_session.flush()

    new = start_assessment(db_session, org.id, fw.id, "New")
    summary = copy_forward_raci(
        db_session, org_id=org.id, framework_id=fw.id, new_assessment_id=new.id
    )
    assert summary["source_assessment_id"] == str(newer.id)

    new_cs = _cs(db_session, new.id, objs["a"].id)
    row = db_session.scalars(
        select(RaciAssignment).where(RaciAssignment.control_state_id == new_cs.id)
    ).one()
    assert row.contact_id == bob.id, "must carry from the newer assessment, not the older one"


def test_prior_assessment_on_a_different_framework_is_ignored(db_session: Session):
    org, fw_a = _org_and_fw(db_session)
    fw_b = Framework(key=f"fw-other-{uuid.uuid4().hex[:6]}", name="Other FW", version="r2")
    db_session.add(fw_b)
    db_session.flush()

    _make_control(db_session, fw_a, control_id="AC.L2-3.1.1")
    ctrl_b, objs_b = _make_control(db_session, fw_b, control_id="AC.L2-3.1.1")

    other_fw_assessment = start_assessment(db_session, org.id, fw_b.id, "Other framework")
    jane = _contact(db_session, org, name="Jane", email="jane@example.com")
    cs_b = _cs(db_session, other_fw_assessment.id, objs_b["a"].id)
    db_session.add(RaciAssignment(control_state_id=cs_b.id, contact_id=jane.id, raci_letter="R"))
    db_session.flush()

    # First assessment on fw_a -- a prior assessment exists for this org
    # (on fw_b), but not on this framework.
    new = start_assessment(db_session, org.id, fw_a.id, "First on fw_a")
    summary = copy_forward_raci(
        db_session, org_id=org.id, framework_id=fw_a.id, new_assessment_id=new.id
    )
    assert summary["source_assessment_id"] is None
    assert summary["carried"] == 0


# ---------------------------------------------------------------------------
# Router: response shape + audit log
# ---------------------------------------------------------------------------


@pytest.fixture
def client(db_session: Session, fake_msp_admin):
    app.dependency_overrides[get_session] = _app_session(db_session)
    app.dependency_overrides[get_current_user] = _authed(db_session, fake_msp_admin)
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_create_assessment_response_includes_raci_copy_forward_summary(
    client: TestClient, db_session: Session, fake_msp_admin
):
    org, fw = _org_and_fw(db_session)
    _grant(db_session, fake_msp_admin, org_id=org.id)
    _make_control(db_session, fw, control_id="AC.L2-3.1.1")

    old = start_assessment(db_session, org.id, fw.id, "Old")
    jane = _contact(db_session, org, name="Jane", email="jane@example.com")
    old_objs = db_session.scalars(
        select(AssessmentObjective)
        .join(Control, AssessmentObjective.control_id == Control.id)
        .where(Control.framework_id == fw.id)
    ).all()
    old_cs = _cs(db_session, old.id, old_objs[0].id)
    db_session.add(RaciAssignment(control_state_id=old_cs.id, contact_id=jane.id, raci_letter="R"))
    db_session.commit()

    resp = client.post(
        f"/orgs/{org.id}/assessments",
        json={"framework_id": str(fw.id), "name": "New via API"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["raci_copy_forward"]["source_assessment_id"] == str(old.id)
    assert body["raci_copy_forward"]["carried"] == 1
    assert "note" in body["raci_copy_forward"]


def test_create_assessment_writes_raci_copy_forward_audit_entry(
    client: TestClient, db_session: Session, fake_msp_admin
):
    org, fw = _org_and_fw(db_session)
    _grant(db_session, fake_msp_admin, org_id=org.id)
    _make_control(db_session, fw, control_id="AC.L2-3.1.1")

    old = start_assessment(db_session, org.id, fw.id, "Old")
    db_session.commit()

    resp = client.post(
        f"/orgs/{org.id}/assessments",
        json={"framework_id": str(fw.id), "name": "New via API"},
    )
    assert resp.status_code == 201
    new_id = uuid.UUID(resp.json()["id"])

    entry = db_session.scalars(
        select(AuditLog).where(
            AuditLog.action == "raci.copy_forward", AuditLog.entity_id == new_id
        )
    ).one()
    assert entry.after_value["source_assessment_id"] == str(old.id)
    assert entry.org_id == org.id
