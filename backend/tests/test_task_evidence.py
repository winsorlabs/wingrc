"""Tests for task-based evidence collection (fan-out endpoints).

Covers:
  - File upload on a task fans out EvidenceStateLink to ALL linked control_states
  - Fan-out does NOT auto-mark any ControlState as 'met' — evidence is a candidate
  - Reference collect fans out the same way
  - Task status becomes 'collected'; completed_evidence_id is set
  - Archived task is rejected (422)
  - Task with no linked states still creates the Evidence and marks task collected
  - Cadence field present in list_evidence_tasks when objective has cadence set
  - Re-collect on already-collected task adds more evidence, keeps task collected
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import get_session
from app.engine import start_assessment
from app.main import app
from app.models import (
    AssessmentObjective,
    Control,
    ControlState,
    Evidence,
    EvidenceStateLink,
    EvidenceTask,
    EvidenceTaskStateLink,
    Framework,
    Organization,
)
from app.storage import StorageClient, get_storage_client

# ---------------------------------------------------------------------------
# Storage stub
# ---------------------------------------------------------------------------


class InMemoryStorageClient(StorageClient):
    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}

    def upload_file(self, key: str, data: bytes, content_type: str) -> None:
        self.files[key] = data

    def presigned_url(self, key: str, expires_in: int = 300) -> str:
        return f"http://fake/{key}"

    def delete_file(self, key: str) -> None:
        self.files.pop(key, None)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def storage():
    return InMemoryStorageClient()


@pytest.fixture
def client(db_session, storage):
    app.dependency_overrides[get_session] = lambda: db_session
    app.dependency_overrides[get_storage_client] = lambda: storage
    yield TestClient(app)
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


def _seed_multi(db_session, *, n_objectives: int = 2, cadence: str | None = None) -> dict:
    """Org + framework with n_objectives objectives + assessment + evidence task linked to all."""
    org = Organization(name=f"TaskEvOrg-{uuid.uuid4().hex[:6]}")
    fw = Framework(key=f"fw-tkev-{uuid.uuid4().hex[:6]}", name="NIST r2", version="r2")
    db_session.add_all([org, fw])
    db_session.flush()

    ctrl = Control(
        framework_id=fw.id,
        control_id="AU.L2-3.3.1",
        family="AU",
        title="Audit",
        requirement_text="Audit logs.",
        sprs_weight=3,
        sequence_order=10,
    )
    db_session.add(ctrl)
    db_session.flush()

    objectives = []
    for key in "abcdefghij"[:n_objectives]:
        obj = AssessmentObjective(
            control_id=ctrl.id,
            objective_key=key,
            text=f"Objective [{key}]",
            cadence=cadence,
        )
        db_session.add(obj)
        objectives.append(obj)
    db_session.flush()

    assessment = start_assessment(db_session, org_id=org.id, framework_id=fw.id, name="TaskEv Test")
    db_session.flush()

    # Collect the ControlState rows for our objectives
    obj_ids = [o.id for o in objectives]
    cs_rows = db_session.scalars(
        select(ControlState).where(
            ControlState.assessment_id == assessment.id,
            ControlState.objective_id.in_(obj_ids),
        )
    ).all()

    # Create a task linked to all of them
    task = EvidenceTask(
        org_id=org.id,
        assessment_id=assessment.id,
        title="Audit log export",
        artifact_type="export",
        status="open",
    )
    db_session.add(task)
    db_session.flush()

    for cs in cs_rows:
        db_session.add(EvidenceTaskStateLink(task_id=task.id, control_state_id=cs.id))
    db_session.flush()

    return {
        "org": org,
        "fw": fw,
        "assessment": assessment,
        "ctrl": ctrl,
        "objectives": objectives,
        "cs_rows": cs_rows,
        "task": task,
    }


def _collect_file_url(d: dict) -> str:
    return (
        f"/orgs/{d['org'].id}/assessments/{d['assessment'].id}"
        f"/evidence-tasks/{d['task'].id}/collect"
    )


def _collect_ref_url(d: dict) -> str:
    return _collect_file_url(d) + "/reference"


def _tasks_url(d: dict) -> str:
    return f"/orgs/{d['org'].id}/assessments/{d['assessment'].id}/evidence-tasks"


# ---------------------------------------------------------------------------
# Fan-out: file upload
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_collect_file_fans_out_evidence_to_all_linked_states(client, db_session, storage):
    """One file upload creates one Evidence + EvidenceStateLink for each linked CS."""
    d = _seed_multi(db_session, n_objectives=3)
    n_states = len(d["cs_rows"])
    assert n_states == 3

    r = client.post(
        _collect_file_url(d),
        files={"file": ("log.pdf", b"%PDF-1.4 fake", "application/pdf")},
        data={"artifact_type": "export", "title": "Audit Log Export"},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["kind"] == "file"
    assert body["title"] == "Audit Log Export"

    ev_id = uuid.UUID(body["id"])

    # Exactly one Evidence row
    ev = db_session.get(Evidence, ev_id)
    assert ev is not None
    assert ev.kind == "file"

    # EvidenceStateLink fan-out: one per linked CS
    links = db_session.scalars(
        select(EvidenceStateLink).where(EvidenceStateLink.evidence_id == ev_id)
    ).all()
    assert len(links) == n_states

    linked_cs_ids = {lnk.control_state_id for lnk in links}
    expected_cs_ids = {cs.id for cs in d["cs_rows"]}
    assert linked_cs_ids == expected_cs_ids


@pytest.mark.integration
def test_collect_file_does_not_auto_mark_states_met(client, db_session, storage):
    """Attaching evidence via task collect MUST NOT change any ControlState status."""
    d = _seed_multi(db_session, n_objectives=2)

    # Record statuses before
    before = {cs.id: cs.status for cs in d["cs_rows"]}

    r = client.post(
        _collect_file_url(d),
        files={"file": ("doc.pdf", b"%PDF-1.4 fake", "application/pdf")},
        data={"artifact_type": "document"},
    )
    assert r.status_code == 201, r.text

    # Reload each CS and verify status unchanged
    db_session.expire_all()
    for cs in d["cs_rows"]:
        fresh = db_session.get(ControlState, cs.id)
        assert fresh.status == before[cs.id], (
            f"ControlState {cs.id} status changed from {before[cs.id]!r} to {fresh.status!r} "
            "— evidence collect must not auto-mark states"
        )


@pytest.mark.integration
def test_collect_file_marks_task_collected(client, db_session, storage):
    """Task status becomes 'collected' and completed_evidence_id is set."""
    d = _seed_multi(db_session, n_objectives=1)

    r = client.post(
        _collect_file_url(d),
        files={"file": ("log.png", b"\x89PNG\r\n\x1a\n extra", "image/png")},
        data={"artifact_type": "screenshot"},
    )
    assert r.status_code == 201, r.text
    ev_id = uuid.UUID(r.json()["id"])

    db_session.expire_all()
    task = db_session.get(EvidenceTask, d["task"].id)
    assert task.status == "collected"
    assert task.completed_evidence_id == ev_id


# ---------------------------------------------------------------------------
# Fan-out: reference
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_collect_reference_fans_out_to_all_linked_states(client, db_session, storage):
    """Reference collect creates EvidenceStateLink for each linked CS."""
    d = _seed_multi(db_session, n_objectives=2)
    n_states = len(d["cs_rows"])

    r = client.post(
        _collect_ref_url(d),
        json={
            "title": "Audit Log Share",
            "location": "\\\\server\\logs\\audit.csv",
            "artifact_type": "export",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["kind"] == "reference"
    assert body["reference_location"] == "\\\\server\\logs\\audit.csv"

    ev_id = uuid.UUID(body["id"])
    links = db_session.scalars(
        select(EvidenceStateLink).where(EvidenceStateLink.evidence_id == ev_id)
    ).all()
    assert len(links) == n_states


@pytest.mark.integration
def test_collect_reference_does_not_auto_mark_states_met(client, db_session, storage):
    """Reference collect must not change any ControlState status."""
    d = _seed_multi(db_session, n_objectives=2)
    before = {cs.id: cs.status for cs in d["cs_rows"]}

    r = client.post(
        _collect_ref_url(d),
        json={
            "title": "Audit Log Share",
            "location": "https://sharepoint.example.com/audit",
            "artifact_type": "export",
        },
    )
    assert r.status_code == 201, r.text

    db_session.expire_all()
    for cs in d["cs_rows"]:
        fresh = db_session.get(ControlState, cs.id)
        assert fresh.status == before[cs.id]


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_collect_archived_task_rejected(client, db_session, storage):
    """Collecting evidence on an archived task returns 422."""
    d = _seed_multi(db_session, n_objectives=1)

    # Archive the task directly
    task = db_session.get(EvidenceTask, d["task"].id)
    task.is_archived = True
    db_session.commit()

    r = client.post(
        _collect_file_url(d),
        files={"file": ("log.pdf", b"%PDF-1.4 fake", "application/pdf")},
        data={"artifact_type": "document"},
    )
    assert r.status_code == 422
    assert "archived" in r.json()["detail"].lower()


@pytest.mark.integration
def test_collect_task_no_linked_states_still_creates_evidence(client, db_session, storage):
    """Task with zero linked states: evidence still created, task still marked collected."""
    org = Organization(name=f"NoLink-{uuid.uuid4().hex[:6]}")
    fw = Framework(key=f"fw-nolink-{uuid.uuid4().hex[:6]}", name="NIST r2", version="r2")
    db_session.add_all([org, fw])
    db_session.flush()

    ctrl = Control(
        framework_id=fw.id, control_id="AC.L2-3.1.99", family="AC",
        title="X", requirement_text=".", sprs_weight=1, sequence_order=99,
    )
    db_session.add(ctrl)
    db_session.flush()

    obj = AssessmentObjective(control_id=ctrl.id, objective_key="a", text="Test")
    db_session.add(obj)
    db_session.flush()

    assessment = start_assessment(db_session, org_id=org.id, framework_id=fw.id, name="NoLink")
    db_session.flush()

    # Task with NO state links
    task = EvidenceTask(
        org_id=org.id,
        assessment_id=assessment.id,
        title="Orphan task",
        artifact_type="document",
        status="open",
    )
    db_session.add(task)
    db_session.commit()

    url = f"/orgs/{org.id}/assessments/{assessment.id}/evidence-tasks/{task.id}/collect"
    r = client.post(
        url,
        files={"file": ("note.pdf", b"%PDF-1.4 fake", "application/pdf")},
        data={"artifact_type": "document"},
    )
    assert r.status_code == 201, r.text

    db_session.expire_all()
    task = db_session.get(EvidenceTask, task.id)
    assert task.status == "collected"
    assert task.completed_evidence_id is not None

    links = db_session.scalars(
        select(EvidenceStateLink).where(
            EvidenceStateLink.evidence_id == task.completed_evidence_id
        )
    ).all()
    assert len(links) == 0


@pytest.mark.integration
def test_recollect_already_collected_task_adds_more_evidence(client, db_session, storage):
    """Re-uploading on a collected task adds a second Evidence row; task stays collected."""
    d = _seed_multi(db_session, n_objectives=1)

    for i in range(2):
        r = client.post(
            _collect_file_url(d),
            files={"file": (f"log{i}.pdf", b"%PDF-1.4 fake", "application/pdf")},
            data={"artifact_type": "export"},
        )
        assert r.status_code == 201

    db_session.expire_all()
    task = db_session.get(EvidenceTask, d["task"].id)
    assert task.status == "collected"

    # Two evidence artifacts, each linked to the one CS
    cs_id = d["cs_rows"][0].id
    links = db_session.scalars(
        select(EvidenceStateLink).where(EvidenceStateLink.control_state_id == cs_id)
    ).all()
    assert len(links) == 2


# ---------------------------------------------------------------------------
# Cadence in task list
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_task_list_includes_cadence_when_objective_has_one(client, db_session):
    """list_evidence_tasks returns cadence from the linked objective."""
    d = _seed_multi(db_session, n_objectives=1, cadence="quarterly")

    r = client.get(_tasks_url(d))
    assert r.status_code == 200, r.text
    tasks = r.json()
    assert len(tasks) == 1
    assert tasks[0]["cadence"] == "quarterly"


@pytest.mark.integration
def test_task_list_cadence_null_when_not_set(client, db_session):
    """list_evidence_tasks returns cadence=null when objective has no cadence."""
    d = _seed_multi(db_session, n_objectives=1, cadence=None)

    r = client.get(_tasks_url(d))
    assert r.status_code == 200, r.text
    tasks = r.json()
    assert len(tasks) == 1
    assert tasks[0]["cadence"] is None


@pytest.mark.integration
def test_task_list_cadence_first_nonnull_wins_when_mixed(client, db_session):
    """With multiple objectives, first non-null cadence is used for the task."""
    d = _seed_multi(db_session, n_objectives=2, cadence=None)

    # Set cadence only on the second objective
    obj_b = d["objectives"][1]
    obj_b.cadence = "monthly"
    db_session.commit()

    r = client.get(_tasks_url(d))
    assert r.status_code == 200
    tasks = r.json()
    assert len(tasks) == 1
    assert tasks[0]["cadence"] == "monthly"


@pytest.mark.integration
def test_task_list_exposes_is_archived_and_archived_at(client, db_session):
    """EvidenceTaskOut now includes is_archived and archived_at."""
    d = _seed_multi(db_session, n_objectives=1)

    r = client.get(_tasks_url(d))
    assert r.status_code == 200
    task_out = r.json()[0]
    assert "is_archived" in task_out
    assert task_out["is_archived"] is False
    assert "archived_at" in task_out
    assert task_out["archived_at"] is None
