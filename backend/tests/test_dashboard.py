"""Integration tests for GET /orgs/{org_id}/assessments/{assessment_id}/dashboard (G.3).

One shared seed builds a deterministic, non-trivial state touching every
table the nine widgets read; each test asserts on one widget's slice of
the same response. Deliberate seed choices, so the expected numbers below
aren't arbitrary:

  AC.L2-3.1.1 (family AC, weight 5, objectives a/b) -- [a]=met, [b]=inherited
    -> control fully satisfied (both in {met, inherited})
  AC.L2-3.1.2 (family AC, weight 3, objective a)     -- [a]=needs_review
    -> not satisfied; also the needs-review-queue widget's one row
  IA.L2-3.5.1 (family IA, weight 5, objective a)      -- [a]=pending_evidence,
    WITH an evidence_state_link attached -- not satisfied, but must NOT
    appear in blocked_objectives (it has evidence; the anti-join is the
    whole point of that widget)
  SC.L2-3.13.11 (family SC, weight 5, objective a)    -- [a]=pending_evidence,
    NO evidence link -- the one row blocked_objectives must return

  SPRS: AC.1.1 satisfied, AC.1.2/IA.5.1/SC.13.11 not -> deductions 3+5+5=13
  -> score = 110 - 13 = 97.

  Implementation statements: AC.1.1[a]=approved, AC.1.1[b]=draft. 5 total
  objectives, 2 with a statement -> not_started = 3.

  Evidence tasks: task_expiring (expires in 10 days, linked to IA.5.1[a],
  RACI 'R' = Jane) appears in evidence_expiring AND Jane's RACI bucket.
  task_far (expires in 60 days, linked to SC.13.11[a], no RACI assignment)
  is outside the 30-day window (excluded from evidence_expiring) but still
  open, so it lands in the RACI "unassigned" bucket. task_no_expiry and
  task_archived are unlinked to any control_state -- excluded from the
  RACI widget's inner join by construction, and from evidence_expiring by
  their own filters (no expires_at / is_archived respectively).

  POA&M: one Finding on SC.13.11[a], two PoamItems -- one 'open', one
  'delayed'.

Run in-container:
    docker compose exec backend pytest tests/test_dashboard.py -m integration -v
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
from app.engine import recompute_sprs, start_assessment
from app.main import app
from app.models import (
    AssessmentObjective,
    Contact,
    Control,
    ControlState,
    Evidence,
    EvidenceStateLink,
    EvidenceTask,
    EvidenceTaskStateLink,
    Finding,
    Framework,
    ImplementationStatement,
    Organization,
    PoamItem,
    RaciAssignment,
)
from tests.conftest import _app_session, _authed, _grant

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client(db_session: Session, fake_msp_admin):
    app.dependency_overrides[get_session] = _app_session(db_session)
    app.dependency_overrides[get_current_user] = _authed(db_session, fake_msp_admin)
    yield TestClient(app)
    app.dependency_overrides.clear()


def _make_control(db_session, fw, *, control_id, family, weight, objective_keys):
    ctrl = Control(
        framework_id=fw.id,
        control_id=control_id,
        family=family,
        title=control_id,
        requirement_text=control_id,
        sprs_weight=weight,
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
    org = Organization(name=f"DashOrg-{uuid.uuid4().hex[:6]}")
    fw = Framework(key=f"fw-dash-{uuid.uuid4().hex[:6]}", name="Test FW", version="r2")
    db_session.add_all([org, fw])
    db_session.flush()

    ac1, ac1_objs = _make_control(
        db_session, fw, control_id="AC.L2-3.1.1", family="AC", weight=5, objective_keys=["a", "b"]
    )
    ac2, ac2_objs = _make_control(
        db_session, fw, control_id="AC.L2-3.1.2", family="AC", weight=3, objective_keys=["a"]
    )
    ia1, ia1_objs = _make_control(
        db_session, fw, control_id="IA.L2-3.5.1", family="IA", weight=5, objective_keys=["a"]
    )
    sc1, sc1_objs = _make_control(
        db_session, fw, control_id="SC.L2-3.13.11", family="SC", weight=5, objective_keys=["a"]
    )

    assessment = start_assessment(db_session, org.id, fw.id, "Dashboard Test")
    db_session.flush()

    ac1a = _cs(db_session, assessment.id, ac1_objs["a"].id)
    ac1b = _cs(db_session, assessment.id, ac1_objs["b"].id)
    ac2a = _cs(db_session, assessment.id, ac2_objs["a"].id)
    ia1a = _cs(db_session, assessment.id, ia1_objs["a"].id)
    sc1a = _cs(db_session, assessment.id, sc1_objs["a"].id)

    ac1a.status = "met"
    ac1b.status = "inherited"
    ac2a.status = "needs_review"
    ia1a.status = "pending_evidence"
    sc1a.status = "pending_evidence"
    db_session.flush()

    # Evidence attached to ia1a only -- keeps it out of blocked_objectives
    # despite being pending_evidence, unlike sc1a.
    evidence = Evidence(
        org_id=org.id,
        title="IdP config export",
        kind="reference",
        artifact_type="export",
        reference_location="https://example.test/idp-export",
        collected_at=datetime.now(UTC),
    )
    db_session.add(evidence)
    db_session.flush()
    db_session.add(EvidenceStateLink(evidence_id=evidence.id, control_state_id=ia1a.id))
    db_session.flush()

    # Implementation statements: AC.1.1[a]=approved, AC.1.1[b]=draft.
    db_session.add_all([
        ImplementationStatement(
            org_id=org.id, objective_id=ac1_objs["a"].id, assessment_id=assessment.id,
            body="Approved statement.", status="approved",
        ),
        ImplementationStatement(
            org_id=org.id, objective_id=ac1_objs["b"].id, assessment_id=assessment.id,
            body="Draft statement.", status="draft",
        ),
    ])
    db_session.flush()

    # Contact + RACI: Jane is 'R' on ia1a.
    jane = Contact(
        org_id=org.id, name="Jane Smith", email="jane@example.com", affiliation="customer"
    )
    db_session.add(jane)
    db_session.flush()
    db_session.add(RaciAssignment(control_state_id=ia1a.id, contact_id=jane.id, raci_letter="R"))
    db_session.flush()

    now = datetime.now(UTC)
    task_expiring = EvidenceTask(
        org_id=org.id, assessment_id=assessment.id, title="IdP export (expiring)",
        artifact_type="export", status="open", expires_at=now + timedelta(days=10),
    )
    task_far = EvidenceTask(
        org_id=org.id, assessment_id=assessment.id, title="Firewall export (not expiring soon)",
        artifact_type="export", status="open", expires_at=now + timedelta(days=60),
    )
    task_no_expiry = EvidenceTask(
        org_id=org.id, assessment_id=assessment.id, title="No expiry set",
        artifact_type="export", status="open", expires_at=None,
    )
    task_archived = EvidenceTask(
        org_id=org.id, assessment_id=assessment.id, title="Archived, ignore",
        artifact_type="export", status="open", expires_at=now + timedelta(days=5),
        is_archived=True,
    )
    db_session.add_all([task_expiring, task_far, task_no_expiry, task_archived])
    db_session.flush()

    db_session.add_all([
        EvidenceTaskStateLink(task_id=task_expiring.id, control_state_id=ia1a.id),
        EvidenceTaskStateLink(task_id=task_far.id, control_state_id=sc1a.id),
    ])
    db_session.flush()

    finding = Finding(
        assessment_id=assessment.id, org_id=org.id, control_state_id=sc1a.id,
        title="FIPS crypto gap", description="Not configured.",
        severity="high", finding_type="gap", status="open",
    )
    db_session.add(finding)
    db_session.flush()
    db_session.add_all([
        PoamItem(
            org_id=org.id, finding_id=finding.id, title="Remediate FIPS gap",
            description="Enable FIPS mode.", status="open",
        ),
        PoamItem(
            org_id=org.id, finding_id=finding.id, title="Vendor patch pending",
            description="Waiting on vendor.", status="delayed",
        ),
    ])
    db_session.flush()

    recompute_sprs(db_session, assessment.id)
    db_session.flush()

    return {
        "org": org, "fw": fw, "assessment": assessment,
        "ac1": ac1, "ac2": ac2, "ia1": ia1, "sc1": sc1,
        "ac1a": ac1a, "ac1b": ac1b, "ac2a": ac2a, "ia1a": ia1a, "sc1a": sc1a,
        "jane": jane,
        "task_expiring": task_expiring, "task_far": task_far,
    }


@pytest.fixture
def dashboard_json(client: TestClient, db_session: Session, ref: dict, fake_msp_admin) -> dict:
    _grant(db_session, fake_msp_admin, org_id=ref["org"].id)
    resp = client.get(f"/orgs/{ref['org'].id}/assessments/{ref['assessment'].id}/dashboard")
    assert resp.status_code == 200
    return resp.json()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_family_heatmap(dashboard_json: dict):
    by_family = {row["family"]: row for row in dashboard_json["family_heatmap"]}
    assert by_family["AC"] == {"family": "AC", "controls_met": 1, "controls_total": 2}
    assert by_family["IA"] == {"family": "IA", "controls_met": 0, "controls_total": 1}
    assert by_family["SC"] == {"family": "SC", "controls_met": 0, "controls_total": 1}


def test_sprs_widget(dashboard_json: dict):
    sprs = dashboard_json["sprs"]
    assert sprs["current_score"] == 97
    assert len(sprs["trajectory"]) >= 2
    assert sprs["trajectory"][-1]["score"] == 97
    # seq-ordered ascending, per SprsSnapshot's own ordering guarantee.
    scores_in_order = [p["score"] for p in sprs["trajectory"]]
    assert scores_in_order[-1] == 97


def test_statement_authoring_progress(dashboard_json: dict):
    progress = dashboard_json["statement_progress"]
    assert progress == {"draft": 1, "reviewed": 0, "approved": 1, "not_started": 3}


def test_evidence_expiring_within_30_days(dashboard_json: dict, ref: dict):
    items = dashboard_json["evidence_expiring"]
    task_ids = {item["task_id"] for item in items}
    assert task_ids == {str(ref["task_expiring"].id)}


def test_needs_review_queue(dashboard_json: dict, ref: dict):
    assert dashboard_json["needs_review_count"] == 1
    assert len(dashboard_json["needs_review"]) == 1
    item = dashboard_json["needs_review"][0]
    assert item["control_state_id"] == str(ref["ac2a"].id)
    assert item["control_id"] == "AC.L2-3.1.2"
    assert item["family"] == "AC"
    assert item["objective_key"] == "a"


def test_blocked_objectives_anti_join(dashboard_json: dict, ref: dict):
    """The plan flags this as the easiest of the nine widgets to get subtly
    wrong: ia1a is pending_evidence too, but has an evidence_state_link, so
    it must NOT appear here -- only sc1a (pending_evidence, zero links)
    should."""
    assert dashboard_json["blocked_objectives_count"] == 1
    ids = {item["control_state_id"] for item in dashboard_json["blocked_objectives"]}
    assert ids == {str(ref["sc1a"].id)}
    item = dashboard_json["blocked_objectives"][0]
    assert item["control_id"] == "SC.L2-3.13.11"
    assert item["family"] == "SC"


def test_raci_open_tasks_assigned_and_unassigned_buckets(dashboard_json: dict, ref: dict):
    buckets = {
        (b["contact_id"], b["contact_name"]): b["open_task_count"]
        for b in dashboard_json["raci_open_tasks"]
    }
    assert buckets[(str(ref["jane"].id), "Jane Smith")] == 1
    assert buckets[(None, None)] == 1


def test_poam_summary(dashboard_json: dict):
    assert dashboard_json["poam_summary"] == {
        "open": 1, "on_track": 0, "delayed": 1, "completed": 0, "cancelled": 0,
    }


def test_dashboard_404_on_org_assessment_mismatch(
    client: TestClient, db_session: Session, fake_msp_admin
):
    other_org = Organization(name=f"OtherOrg-{uuid.uuid4().hex[:6]}")
    db_session.add(other_org)
    db_session.flush()
    _grant(db_session, fake_msp_admin, org_id=other_org.id)

    resp = client.get(f"/orgs/{other_org.id}/assessments/{uuid.uuid4()}/dashboard")
    assert resp.status_code == 404
