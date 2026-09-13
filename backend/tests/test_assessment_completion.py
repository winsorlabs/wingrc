"""Integration tests for the assessment completion state machine (§3 of
the "record of SPRS submissions" task): POST .../complete and
POST .../reopen (routers/assessments.py, engine.py:complete_assessment/
reopen_assessment).

Covers the two invariants the whole design rests on:
  - Completing an assessment creates NO SprsSubmission row -- asserted
    explicitly, the central invariant (§0/§6).
  - Completion gates NOTHING -- bundle export, evidence upload, and
    control-state edits all keep working identically after completion.
    locked_at is never touched by completion either.

Plus the ordinary state-machine mechanics: valid/invalid transitions,
audit logging, and reopen preserving the historical submitted_at in the
audit trail even though the column itself is cleared.

Run in-container:
    docker compose exec backend pytest tests/test_assessment_completion.py -m integration -v
"""

from __future__ import annotations

import io
import uuid
import zipfile

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.auth import get_current_user
from app.db import get_session
from app.engine import start_assessment
from app.main import app
from app.models import (
    Assessment,
    AssessmentObjective,
    AuditLog,
    Control,
    ControlState,
    Framework,
    Organization,
    SprsSubmission,
)
from app.storage import StorageClient, get_storage_client
from tests.conftest import _app_session, _authed, _grant, _make_fake_user

pytestmark = pytest.mark.integration


class InMemoryStorageClient(StorageClient):
    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}

    def upload_file(self, key: str, data: bytes, content_type: str) -> None:
        self.files[key] = data

    def presigned_url(
        self, key: str, expires_in: int = 300, download_filename: str | None = None
    ) -> str:
        return f"http://fake/{key}"

    def delete_file(self, key: str) -> None:
        self.files.pop(key, None)

    def get_bytes(self, key: str) -> bytes:
        return self.files.get(key, b"")


@pytest.fixture
def storage() -> InMemoryStorageClient:
    return InMemoryStorageClient()


@pytest.fixture
def fake_msp_admin():
    return _make_fake_user()


@pytest.fixture
def client(db_session, storage, fake_msp_admin):
    app.dependency_overrides[get_session] = _app_session(db_session)
    app.dependency_overrides[get_storage_client] = lambda: storage
    app.dependency_overrides[get_current_user] = _authed(db_session, fake_msp_admin)
    yield TestClient(app)
    app.dependency_overrides.clear()


def _seed(db_session, fake_msp_admin) -> dict:
    org = Organization(id=fake_msp_admin.org_id, name=f"CompletionOrg-{uuid.uuid4().hex[:8]}")
    fw = Framework(key=f"fw-completion-{uuid.uuid4().hex[:6]}", name="Test FW", version="r2")
    db_session.add_all([org, fw])
    db_session.flush()
    _grant(db_session, fake_msp_admin, org_id=org.id)

    ctrl = Control(
        framework_id=fw.id, control_id="AC.L2-3.1.1", family="AC", title="Access Control",
        requirement_text="req", sprs_weight=5, sequence_order=1,
    )
    db_session.add(ctrl)
    db_session.flush()
    obj = AssessmentObjective(control_id=ctrl.id, objective_key="a", text="obj a")
    db_session.add(obj)
    db_session.flush()

    assessment = start_assessment(db_session, org_id=org.id, framework_id=fw.id, name="Q1")
    db_session.commit()

    cs = db_session.scalars(
        select(ControlState).where(
            ControlState.assessment_id == assessment.id, ControlState.objective_id == obj.id
        )
    ).first()

    return {"org": org, "fw": fw, "assessment": assessment, "ctrl": ctrl, "obj": obj, "cs": cs}


def _complete_url(d: dict) -> str:
    return f"/orgs/{d['org'].id}/assessments/{d['assessment'].id}/complete"


def _reopen_url(d: dict) -> str:
    return f"/orgs/{d['org'].id}/assessments/{d['assessment'].id}/reopen"


def _bundle_url(d: dict) -> str:
    return f"/orgs/{d['org'].id}/assessments/{d['assessment'].id}/bundle"


def _upload_url(d: dict) -> str:
    return (
        f"/orgs/{d['org'].id}/assessments/{d['assessment'].id}"
        f"/control-states/{d['cs'].id}/evidence"
    )


def _patch_cs_url(d: dict) -> str:
    return (
        f"/orgs/{d['org'].id}/assessments/{d['assessment'].id}"
        f"/control-states/{d['cs'].id}"
    )


# ---------------------------------------------------------------------------
# Transitions
# ---------------------------------------------------------------------------


def test_complete_transitions_status_and_stamps_submitted_at(client, db_session, fake_msp_admin):
    d = _seed(db_session, fake_msp_admin)
    r = client.post(_complete_url(d))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "submitted"
    assert body["submitted_at"] is not None
    assert body["closed_at"] is None


def test_complete_writes_audit_log(client, db_session, fake_msp_admin):
    d = _seed(db_session, fake_msp_admin)
    client.post(_complete_url(d))

    rows = db_session.scalars(
        select(AuditLog).where(
            AuditLog.org_id == d["org"].id, AuditLog.action == "assessment.completed"
        )
    ).all()
    assert len(rows) == 1
    assert rows[0].after_value["status"] == "submitted"


def test_completing_an_already_submitted_assessment_409s(client, db_session, fake_msp_admin):
    d = _seed(db_session, fake_msp_admin)
    client.post(_complete_url(d))
    r = client.post(_complete_url(d))
    assert r.status_code == 409


def test_completing_a_nonexistent_assessment_404s(client, db_session, fake_msp_admin):
    org = Organization(id=fake_msp_admin.org_id, name="Empty")
    db_session.add(org)
    db_session.flush()
    _grant(db_session, fake_msp_admin, org_id=org.id)
    r = client.post(f"/orgs/{org.id}/assessments/{uuid.uuid4()}/complete")
    assert r.status_code == 404


def test_reopen_clears_status_and_submitted_at(client, db_session, fake_msp_admin):
    d = _seed(db_session, fake_msp_admin)
    client.post(_complete_url(d))

    r = client.post(_reopen_url(d))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "in_progress"
    assert body["submitted_at"] is None


def test_reopen_audit_entry_preserves_the_prior_submitted_at(client, db_session, fake_msp_admin):
    d = _seed(db_session, fake_msp_admin)
    completed = client.post(_complete_url(d)).json()
    assert completed["submitted_at"] is not None

    client.post(_reopen_url(d))

    rows = db_session.scalars(
        select(AuditLog).where(
            AuditLog.org_id == d["org"].id, AuditLog.action == "assessment.reopened"
        )
    ).all()
    assert len(rows) == 1
    assert rows[0].before_value["submitted_at"] == completed["submitted_at"]
    assert rows[0].after_value["status"] == "in_progress"


def test_reopening_an_in_progress_assessment_409s(client, db_session, fake_msp_admin):
    d = _seed(db_session, fake_msp_admin)
    r = client.post(_reopen_url(d))
    assert r.status_code == 409


# ---------------------------------------------------------------------------
# Central invariant: completion creates NO submission row
# ---------------------------------------------------------------------------


def test_completion_creates_no_sprs_submission_row(client, db_session, fake_msp_admin):
    d = _seed(db_session, fake_msp_admin)

    before_rows = db_session.scalars(
        select(SprsSubmission).where(SprsSubmission.org_id == d["org"].id)
    ).all()
    assert before_rows == []  # sanity: there was nothing before either

    r = client.post(_complete_url(d))
    assert r.status_code == 200

    after_rows = db_session.scalars(
        select(SprsSubmission).where(SprsSubmission.org_id == d["org"].id)
    ).all()
    assert after_rows == [], (
        "completing an assessment must never create an SprsSubmission row -- "
        "the customer still has to file with SPRS separately"
    )


# ---------------------------------------------------------------------------
# Hard constraint: completion gates nothing
# ---------------------------------------------------------------------------


def test_bundle_export_works_identically_before_and_after_completion(
    client, db_session, fake_msp_admin
):
    d = _seed(db_session, fake_msp_admin)

    before = client.get(_bundle_url(d))
    assert before.status_code == 200
    with zipfile.ZipFile(io.BytesIO(before.content)) as zf:
        before_names = set(zf.namelist())

    client.post(_complete_url(d))

    after = client.get(_bundle_url(d))
    assert after.status_code == 200
    with zipfile.ZipFile(io.BytesIO(after.content)) as zf:
        after_names = set(zf.namelist())

    assert before_names == after_names


def test_evidence_upload_works_after_completion(client, db_session, fake_msp_admin):
    d = _seed(db_session, fake_msp_admin)
    client.post(_complete_url(d))
    db_session.refresh(d["assessment"])
    assert d["assessment"].status == "submitted"

    r = client.post(
        _upload_url(d),
        files={"file": ("screenshot.png", b"\x89PNG\r\n\x1a\n extra", "image/png")},
        data={"artifact_type": "screenshot"},
    )
    assert r.status_code == 201, r.text


def test_control_state_edit_works_after_completion(client, db_session, fake_msp_admin):
    d = _seed(db_session, fake_msp_admin)
    client.post(_complete_url(d))

    r = client.patch(_patch_cs_url(d), json={"status": "met"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "met"


def test_completion_never_sets_locked_at(client, db_session, fake_msp_admin):
    d = _seed(db_session, fake_msp_admin)
    client.post(_complete_url(d))

    row = db_session.get(Assessment, d["assessment"].id)
    assert row.locked_at is None, (
        "locking is a separate, deliberate action with its own design -- "
        "completion must never auto-lock (see engine.py:complete_assessment)"
    )
