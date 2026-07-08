"""Integration tests for the evidence upload/list/download/delete API.

Uses InMemoryStorageClient injected via dependency_overrides so no MinIO is
needed for these tests to pass.

Invariant verified:
  - Upload does NOT change control_state.status (tested in test 1)
  - Delete does NOT change control_state.status (tested in test 5)
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import get_session
from app.engine import start_assessment
from app.main import app
from app.models import AssessmentObjective, Control, ControlState, Framework, Organization
from app.storage import StorageClient, get_storage_client

# ---------------------------------------------------------------------------
# In-memory storage mock
# ---------------------------------------------------------------------------


class InMemoryStorageClient(StorageClient):
    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}

    def upload_file(self, key: str, data: bytes, content_type: str) -> None:
        self.files[key] = data

    def presigned_url(self, key: str, expires_in: int = 300) -> str:
        return f"http://fake-storage/{key}"

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
# Seed helper
# ---------------------------------------------------------------------------


def _seed(db_session) -> dict:
    """Org + framework (one control, one objective) + assessment + control_state."""
    org = Organization(name=f"EvTestOrg-{uuid.uuid4().hex[:6]}")
    fw = Framework(key=f"fw-ev-{uuid.uuid4().hex[:6]}", name="NIST r2", version="r2")
    db_session.add_all([org, fw])
    db_session.flush()

    ctrl = Control(
        framework_id=fw.id,
        control_id="AC.L2-3.1.1",
        family="AC",
        title="Access Control",
        requirement_text="Limit access.",
        sprs_weight=5,
        sequence_order=1,
    )
    db_session.add(ctrl)
    db_session.flush()

    obj = AssessmentObjective(control_id=ctrl.id, objective_key="a", text="Users identified.")
    db_session.add(obj)
    db_session.flush()

    assessment = start_assessment(db_session, org_id=org.id, framework_id=fw.id, name="Ev Test")
    db_session.flush()

    cs = db_session.scalars(
        select(ControlState).where(
            ControlState.assessment_id == assessment.id,
            ControlState.objective_id == obj.id,
        )
    ).first()

    return {"org": org, "fw": fw, "assessment": assessment, "ctrl": ctrl, "obj": obj, "cs": cs}


def _upload_url(d: dict) -> str:
    cs_id = d["cs"].id
    return f"/orgs/{d['org'].id}/assessments/{d['assessment'].id}/control-states/{cs_id}/evidence"


def _download_url(d: dict, evidence_id: str) -> str:
    return f"/orgs/{d['org'].id}/evidence/{evidence_id}/download"


def _states_url(d: dict) -> str:
    return f"/orgs/{d['org'].id}/assessments/{d['assessment'].id}/control-states"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_upload_creates_evidence_and_does_not_change_status(client, db_session, storage):
    """Upload stores the bytes and leaves control_state.status untouched."""
    d = _seed(db_session)
    r = client.post(
        _upload_url(d),
        files={"file": ("screenshot.png", b"\x89PNG fake data", "image/png")},
        data={"artifact_type": "screenshot"},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["artifact_type"] == "screenshot"
    assert body["title"] == "screenshot.png"
    assert body["file_size_bytes"] == len(b"\x89PNG fake data")
    assert body["download_url"].startswith("http://fake-storage/")

    # Storage captured the bytes
    assert len(storage.files) == 1

    # control_state.status must be unchanged (not_met is the seeded default)
    db_session.refresh(d["cs"])
    assert d["cs"].status == "not_met"


@pytest.mark.integration
def test_list_evidence_returns_uploaded_item(client, db_session):
    d = _seed(db_session)
    client.post(
        _upload_url(d),
        files={"file": ("config.xlsx", b"fake xlsx", "application/vnd.ms-excel")},
        data={"artifact_type": "export"},
    )
    r = client.get(_upload_url(d))
    assert r.status_code == 200
    items = r.json()
    assert len(items) == 1
    assert items[0]["artifact_type"] == "export"
    assert items[0]["title"] == "config.xlsx"
    assert items[0]["download_url"].startswith("http://fake-storage/")


@pytest.mark.integration
def test_evidence_count_increments_in_control_states(client, db_session):
    """After upload, GET control-states returns evidence_count=1 for the objective."""
    d = _seed(db_session)

    rows_before = client.get(_states_url(d)).json()
    assert rows_before[0]["evidence_count"] == 0

    client.post(
        _upload_url(d),
        files={"file": ("policy.pdf", b"%PDF fake", "application/pdf")},
        data={"artifact_type": "document"},
    )

    rows_after = client.get(_states_url(d)).json()
    assert rows_after[0]["evidence_count"] == 1


@pytest.mark.integration
def test_download_redirects_to_presigned_url(client, db_session):
    d = _seed(db_session)
    up = client.post(
        _upload_url(d),
        files={"file": ("mfa.png", b"\x89PNG", "image/png")},
        data={"artifact_type": "screenshot"},
    )
    ev_id = up.json()["id"]

    r = client.get(_download_url(d, ev_id), follow_redirects=False)
    assert r.status_code == 302
    assert "fake-storage" in r.headers["location"]


@pytest.mark.integration
def test_delete_removes_evidence_and_does_not_change_status(client, db_session, storage):
    """Delete removes the link, the row, and the storage object.  Status unchanged."""
    d = _seed(db_session)
    up = client.post(
        _upload_url(d),
        files={"file": ("doc.pdf", b"%PDF", "application/pdf")},
        data={"artifact_type": "document"},
    )
    ev_id = up.json()["id"]

    r = client.delete(f"{_upload_url(d)}/{ev_id}")
    assert r.status_code == 204

    # List is now empty
    assert client.get(_upload_url(d)).json() == []

    # Storage file was removed
    assert len(storage.files) == 0

    # Status unchanged
    db_session.refresh(d["cs"])
    assert d["cs"].status == "not_met"


@pytest.mark.integration
def test_upload_wrong_org_returns_404(client, db_session):
    d = _seed(db_session)
    bad_url = (
        f"/orgs/{uuid.uuid4()}/assessments/{d['assessment'].id}"
        f"/control-states/{d['cs'].id}/evidence"
    )
    r = client.post(
        bad_url,
        files={"file": ("x.png", b"\x89PNG", "image/png")},
        data={"artifact_type": "screenshot"},
    )
    assert r.status_code == 404


@pytest.mark.integration
def test_delete_nonexistent_returns_404(client, db_session):
    d = _seed(db_session)
    r = client.delete(f"{_upload_url(d)}/{uuid.uuid4()}")
    assert r.status_code == 404
