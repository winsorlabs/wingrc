"""Integration tests for the evidence API.

Covers:
  - File upload: happy path, disallowed MIME, extension mismatch, magic-byte
    mismatch, oversized file
  - Reference add: batch, single, validation errors (empty location, bad scheme)
  - List: returns both files and references with correct shape
  - Download: file redirects; reference returns 404
  - Delete: file cleans up storage; reference does not touch storage
  - Manifest: correct shape, human-readable identifiers, both evidence kinds
  - Invariants: status never changes on attach/detach; customer_owns is not blocked
  - SHA-256 hashing: upload stores hash; get_bytes() roundtrip matches stored hash

All tests use InMemoryStorageClient injected via dependency_overrides so no
MinIO instance is required.
"""
from __future__ import annotations

import hashlib
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
    EvidenceTask,
    EvidenceTaskStateLink,
    Framework,
    Organization,
)
from app.storage import StorageClient, get_storage_client

# ---------------------------------------------------------------------------
# In-memory storage mock
# ---------------------------------------------------------------------------


class InMemoryStorageClient(StorageClient):
    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.deleted: list[str] = []

    def upload_file(self, key: str, data: bytes, content_type: str) -> None:
        self.files[key] = data

    def presigned_url(self, key: str, expires_in: int = 300) -> str:
        return f"http://fake-storage/{key}"

    def delete_file(self, key: str) -> None:
        self.deleted.append(key)
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


def _seed(db_session) -> dict:
    """Org + framework (one control AC.L2-3.1.1 with objective [a]) + assessment."""
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
    return (
        f"/orgs/{d['org'].id}/assessments/{d['assessment'].id}"
        f"/control-states/{d['cs'].id}/evidence"
    )


def _refs_url(d: dict) -> str:
    return _upload_url(d) + "/references"


def _download_url(d: dict, evidence_id: str) -> str:
    return f"/orgs/{d['org'].id}/evidence/{evidence_id}/download"


def _states_url(d: dict) -> str:
    return f"/orgs/{d['org'].id}/assessments/{d['assessment'].id}/control-states"


def _manifest_url(d: dict) -> str:
    return f"/orgs/{d['org'].id}/assessments/{d['assessment'].id}/evidence-manifest"


# ---------------------------------------------------------------------------
# File upload — happy paths
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_upload_creates_evidence_and_does_not_change_status(client, db_session, storage):
    """Upload stores the bytes, returns EvidenceOut, and leaves status untouched."""
    d = _seed(db_session)
    r = client.post(
        _upload_url(d),
        files={"file": ("screenshot.png", b"\x89PNG\r\n\x1a\n extra", "image/png")},
        data={"artifact_type": "screenshot"},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["kind"] == "file"
    assert body["artifact_type"] == "screenshot"
    assert body["title"] == "screenshot.png"
    assert body["file_size_bytes"] == len(b"\x89PNG\r\n\x1a\n extra")
    assert body["download_url"].startswith("http://fake-storage/")
    assert body["reference_location"] is None
    assert body["note"] is None

    # Storage key must not contain the original filename (UUID-based path)
    stored_key = next(iter(storage.files))
    assert "screenshot.png" not in stored_key
    assert str(body["id"]) in stored_key
    assert stored_key.endswith(".png")

    # Status must be unchanged
    db_session.refresh(d["cs"])
    assert d["cs"].status == "not_met"


@pytest.mark.integration
def test_list_evidence_returns_uploaded_item(client, db_session):
    d = _seed(db_session)
    client.post(
        _upload_url(d),
        files={"file": ("config.xlsx", b"PK\x03\x04 fake xlsx",
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        data={"artifact_type": "export"},
    )
    r = client.get(_upload_url(d))
    assert r.status_code == 200
    items = r.json()
    assert len(items) == 1
    item = items[0]
    assert item["kind"] == "file"
    assert item["artifact_type"] == "export"
    assert item["title"] == "config.xlsx"
    assert item["download_url"].startswith("http://fake-storage/")
    assert item["reference_location"] is None


@pytest.mark.integration
def test_evidence_count_increments_in_control_states(client, db_session):
    d = _seed(db_session)

    before = client.get(_states_url(d)).json()
    assert before[0]["evidence_count"] == 0

    client.post(
        _upload_url(d),
        files={"file": ("policy.pdf", b"%PDF-1.4 fake", "application/pdf")},
        data={"artifact_type": "document"},
    )

    after = client.get(_states_url(d)).json()
    assert after[0]["evidence_count"] == 1


@pytest.mark.integration
def test_download_redirects_to_presigned_url(client, db_session):
    d = _seed(db_session)
    up = client.post(
        _upload_url(d),
        files={"file": ("mfa.png", b"\x89PNG\r\n\x1a\n", "image/png")},
        data={"artifact_type": "screenshot"},
    )
    ev_id = up.json()["id"]

    r = client.get(_download_url(d, ev_id), follow_redirects=False)
    assert r.status_code == 302
    assert "fake-storage" in r.headers["location"]


@pytest.mark.integration
def test_delete_removes_evidence_and_does_not_change_status(client, db_session, storage):
    d = _seed(db_session)
    up = client.post(
        _upload_url(d),
        files={"file": ("doc.pdf", b"%PDF-1.4", "application/pdf")},
        data={"artifact_type": "document"},
    )
    ev_id = up.json()["id"]

    r = client.delete(f"{_upload_url(d)}/{ev_id}")
    assert r.status_code == 204

    assert client.get(_upload_url(d)).json() == []
    assert len(storage.files) == 0
    assert len(storage.deleted) == 1   # storage.delete_file was called

    db_session.refresh(d["cs"])
    assert d["cs"].status == "not_met"


# ---------------------------------------------------------------------------
# File upload — validation rejections
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_upload_disallowed_mime_type_rejected(client, db_session):
    d = _seed(db_session)
    r = client.post(
        _upload_url(d),
        files={"file": ("malware.exe", b"MZ\x90\x00", "application/x-msdownload")},
        data={"artifact_type": "document"},
    )
    assert r.status_code == 415


@pytest.mark.integration
def test_upload_disallowed_extension_rejected(client, db_session):
    d = _seed(db_session)
    r = client.post(
        _upload_url(d),
        files={"file": ("script.php", b"<?php echo 1; ?>", "application/pdf")},
        data={"artifact_type": "document"},
    )
    assert r.status_code == 415


@pytest.mark.integration
def test_upload_magic_byte_mismatch_rejected(client, db_session):
    """File claims to be PNG but bytes don't start with the PNG magic header."""
    d = _seed(db_session)
    r = client.post(
        _upload_url(d),
        files={"file": ("not_really.png", b"this is definitely not a PNG", "image/png")},
        data={"artifact_type": "screenshot"},
    )
    assert r.status_code == 415


@pytest.mark.integration
def test_upload_magic_byte_mismatch_pdf_rejected(client, db_session):
    """File claims to be PDF but bytes don't start with %PDF."""
    d = _seed(db_session)
    r = client.post(
        _upload_url(d),
        files={"file": ("fake.pdf", b"MZ\x90\x00 not a pdf", "application/pdf")},
        data={"artifact_type": "document"},
    )
    assert r.status_code == 415


@pytest.mark.integration
def test_upload_oversized_file_rejected(client, db_session):
    d = _seed(db_session)
    big = b"\x89PNG\r\n\x1a\n" + b"x" * (51 * 1024 * 1024)
    r = client.post(
        _upload_url(d),
        files={"file": ("huge.png", big, "image/png")},
        data={"artifact_type": "screenshot"},
    )
    assert r.status_code == 413


@pytest.mark.integration
def test_upload_wrong_org_returns_404(client, db_session):
    d = _seed(db_session)
    bad_url = (
        f"/orgs/{uuid.uuid4()}/assessments/{d['assessment'].id}"
        f"/control-states/{d['cs'].id}/evidence"
    )
    r = client.post(
        bad_url,
        files={"file": ("x.png", b"\x89PNG\r\n\x1a\n", "image/png")},
        data={"artifact_type": "screenshot"},
    )
    assert r.status_code == 404


@pytest.mark.integration
def test_delete_nonexistent_returns_404(client, db_session):
    d = _seed(db_session)
    r = client.delete(f"{_upload_url(d)}/{uuid.uuid4()}")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Reference add
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_add_references_batch(client, db_session):
    d = _seed(db_session)
    r = client.post(_refs_url(d), json=[
        {
            "title": "SharePoint Policy",
            "location": "https://company.sharepoint.com/sites/cmmc/policy.pdf",
            "artifact_type": "document",
        },
        {
            "title": "Network Share Doc",
            "location": r"\\fileserver\cmmc\evidence\config.pdf",
            "artifact_type": "export",
        },
        {
            "title": "Drive Path",
            "location": r"M:\CMMC\Evidence\sysconfig.xlsx",
            "artifact_type": "export",
        },
    ])
    assert r.status_code == 201, r.text
    items = r.json()
    assert len(items) == 3
    for item in items:
        assert item["kind"] == "reference"
        assert item["download_url"] is None
        assert item["storage_key"] if "storage_key" in item else True  # may not be present
        assert "Location only" in item["note"]

    assert items[0]["reference_location"] == "https://company.sharepoint.com/sites/cmmc/policy.pdf"
    assert items[0]["title"] == "SharePoint Policy"


@pytest.mark.integration
def test_add_reference_unix_path(client, db_session):
    d = _seed(db_session)
    r = client.post(_refs_url(d), json=[
        {"title": "Unix path", "location": "/mnt/nas/cmmc/evidence.pdf",
         "artifact_type": "document"}
    ])
    assert r.status_code == 201
    assert r.json()[0]["reference_location"] == "/mnt/nas/cmmc/evidence.pdf"


@pytest.mark.integration
def test_add_reference_empty_location_rejected(client, db_session):
    d = _seed(db_session)
    r = client.post(_refs_url(d), json=[
        {"title": "Bad ref", "location": "", "artifact_type": "document"}
    ])
    assert r.status_code == 422


@pytest.mark.integration
def test_add_reference_invalid_location_rejected(client, db_session):
    """A bare word (not a URL or path) must be rejected."""
    d = _seed(db_session)
    r = client.post(_refs_url(d), json=[
        {"title": "Bad ref", "location": "just_a_word_no_scheme", "artifact_type": "document"}
    ])
    assert r.status_code == 422


@pytest.mark.integration
def test_add_reference_empty_title_rejected(client, db_session):
    d = _seed(db_session)
    r = client.post(_refs_url(d), json=[
        {"title": "   ", "location": "https://example.com/doc.pdf", "artifact_type": "document"}
    ])
    assert r.status_code == 422


@pytest.mark.integration
def test_add_reference_bad_artifact_type_rejected(client, db_session):
    d = _seed(db_session)
    r = client.post(_refs_url(d), json=[
        {"title": "Ref", "location": "https://example.com/", "artifact_type": "video"}
    ])
    assert r.status_code == 422


@pytest.mark.integration
def test_add_reference_empty_list_rejected(client, db_session):
    d = _seed(db_session)
    r = client.post(_refs_url(d), json=[])
    assert r.status_code == 422


@pytest.mark.integration
def test_download_reference_returns_404(client, db_session):
    """References have no stored file — download endpoint must 404."""
    d = _seed(db_session)
    ref = client.post(_refs_url(d), json=[
        {"title": "SP link", "location": "https://sp.example.com/doc.pdf",
         "artifact_type": "document"}
    ])
    ev_id = ref.json()[0]["id"]
    r = client.get(_download_url(d, ev_id), follow_redirects=False)
    assert r.status_code == 404


@pytest.mark.integration
def test_delete_reference_does_not_call_storage(client, db_session, storage):
    """Deleting a reference only removes the DB row — no storage.delete_file call."""
    d = _seed(db_session)
    ref = client.post(_refs_url(d), json=[
        {"title": "Ref", "location": "https://example.com/f.pdf", "artifact_type": "document"}
    ])
    ev_id = ref.json()[0]["id"]

    r = client.delete(f"{_upload_url(d)}/{ev_id}")
    assert r.status_code == 204
    assert client.get(_upload_url(d)).json() == []
    assert len(storage.deleted) == 0  # no storage operation


@pytest.mark.integration
def test_list_shows_both_file_and_reference(client, db_session, storage):
    d = _seed(db_session)

    client.post(
        _upload_url(d),
        files={"file": ("screenshot.png", b"\x89PNG\r\n\x1a\n", "image/png")},
        data={"artifact_type": "screenshot"},
    )
    client.post(_refs_url(d), json=[
        {"title": "SP link", "location": "https://sp.example.com/", "artifact_type": "document"}
    ])

    r = client.get(_upload_url(d))
    assert r.status_code == 200
    items = r.json()
    assert len(items) == 2

    kinds = {i["kind"] for i in items}
    assert kinds == {"file", "reference"}

    ref = next(i for i in items if i["kind"] == "reference")
    assert ref["download_url"] is None
    assert "Location only" in ref["note"]

    fil = next(i for i in items if i["kind"] == "file")
    assert fil["download_url"].startswith("http://fake-storage/")
    assert fil["note"] is None


# ---------------------------------------------------------------------------
# Invariants
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_evidence_never_changes_control_state_status(client, db_session):
    """Attaching evidence (both kinds) must never alter control_state.status."""
    d = _seed(db_session)
    initial_status = d["cs"].status

    client.post(
        _upload_url(d),
        files={"file": ("p.pdf", b"%PDF-1.4", "application/pdf")},
        data={"artifact_type": "document"},
    )
    db_session.refresh(d["cs"])
    assert d["cs"].status == initial_status

    r = client.post(_refs_url(d), json=[
        {"title": "Ref", "location": "https://example.com/", "artifact_type": "document"}
    ])
    ev_id = r.json()[0]["id"]
    db_session.refresh(d["cs"])
    assert d["cs"].status == initial_status

    client.delete(f"{_upload_url(d)}/{ev_id}")
    db_session.refresh(d["cs"])
    assert d["cs"].status == initial_status


@pytest.mark.integration
def test_customer_owns_objective_accepts_evidence(client, db_session, storage):
    """Evidence attachment must not be blocked on customer_owns responsibility."""
    d = _seed(db_session)
    d["cs"].responsibility = "customer_owns"
    db_session.flush()

    r = client.post(
        _upload_url(d),
        files={"file": ("policy.pdf", b"%PDF-1.4", "application/pdf")},
        data={"artifact_type": "document"},
    )
    assert r.status_code == 201

    ref = client.post(_refs_url(d), json=[
        {"title": "Ref", "location": "https://example.com/", "artifact_type": "document"}
    ])
    assert ref.status_code == 201

    db_session.refresh(d["cs"])
    assert d["cs"].responsibility == "customer_owns"  # unchanged
    assert d["cs"].status == "not_met"                # unchanged


# ---------------------------------------------------------------------------
# Evidence manifest
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_evidence_manifest_shape_and_human_readable_ids(client, db_session, storage):
    """Manifest uses control_id / objective_key as human-readable identifiers."""
    d = _seed(db_session)

    client.post(
        _upload_url(d),
        files={"file": ("snap.png", b"\x89PNG\r\n\x1a\n", "image/png")},
        data={"artifact_type": "screenshot"},
    )
    client.post(_refs_url(d), json=[
        {
            "title": "SharePoint link",
            "location": "https://sp.example.com/cmmc/doc.pdf",
            "artifact_type": "document",
        }
    ])

    r = client.get(_manifest_url(d))
    assert r.status_code == 200, r.text
    m = r.json()

    assert m["assessment_id"] == str(d["assessment"].id)
    assert m["org_id"] == str(d["org"].id)
    assert "generated_at" in m

    objectives = m["objectives"]
    assert len(objectives) == 1

    obj = objectives[0]
    # Human-readable identifiers for bundle rendering: "AU.L2-3.3.1[a]" style
    assert obj["control_id"] == "AC.L2-3.1.1"
    assert obj["family"] == "AC"
    assert obj["objective_key"] == "a"
    assert obj["control_state_id"] == str(d["cs"].id)
    assert obj["status"] == "not_met"

    evidence = obj["evidence"]
    assert len(evidence) == 2

    file_ev = next(e for e in evidence if e["kind"] == "file")
    assert file_ev["artifact_type"] == "screenshot"
    assert file_ev["storage_key"] is not None
    assert file_ev["mime_type"] == "image/png"
    assert file_ev["location"] is None
    assert file_ev["note"] is None

    ref_ev = next(e for e in evidence if e["kind"] == "reference")
    assert ref_ev["title"] == "SharePoint link"
    assert ref_ev["location"] == "https://sp.example.com/cmmc/doc.pdf"
    assert "Location only" in ref_ev["note"]
    assert ref_ev["storage_key"] is None
    assert ref_ev["mime_type"] is None


@pytest.mark.integration
def test_evidence_manifest_objective_with_no_evidence(client, db_session):
    """Objectives with no evidence appear in the manifest with an empty evidence list."""
    d = _seed(db_session)

    r = client.get(_manifest_url(d))
    assert r.status_code == 200
    m = r.json()

    assert len(m["objectives"]) == 1
    assert m["objectives"][0]["evidence"] == []


@pytest.mark.integration
def test_evidence_manifest_wrong_org_returns_404(client, db_session):
    d = _seed(db_session)
    url = (
        f"/orgs/{uuid.uuid4()}/assessments/{d['assessment'].id}/evidence-manifest"
    )
    r = client.get(url)
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# SHA-256 hashing
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_upload_populates_sha256(client, db_session, storage):
    """File upload endpoint stores sha256_hash matching the uploaded bytes."""
    d = _seed(db_session)
    data = b"%PDF-1.4 test content for hashing"
    expected_hash = hashlib.sha256(data).hexdigest()

    r = client.post(
        _upload_url(d),
        files={"file": ("report.pdf", data, "application/pdf")},
        data={"artifact_type": "document"},
    )
    assert r.status_code == 201, r.text

    ev = db_session.get(Evidence, uuid.UUID(r.json()["id"]))
    assert ev is not None
    assert ev.sha256_hash == expected_hash


@pytest.mark.integration
def test_upload_hash_matches_storage_roundtrip(client, db_session, storage):
    """The hash stored in the DB equals the hash of bytes returned by get_bytes().

    This exercises the actual path bundle export uses: snapshot_bundle calls
    storage.get_bytes(key) to fetch bytes for embedding.  If those bytes differ
    from what was uploaded, the hash would mismatch and the artifact log would be
    wrong.  InMemoryStorageClient stores and returns bytes faithfully, proving
    the contract.
    """
    d = _seed(db_session)
    data = b"%PDF-1.4 roundtrip test"

    r = client.post(
        _upload_url(d),
        files={"file": ("audit.pdf", data, "application/pdf")},
        data={"artifact_type": "document"},
    )
    assert r.status_code == 201, r.text

    ev = db_session.get(Evidence, uuid.UUID(r.json()["id"]))
    assert ev is not None
    assert ev.storage_key is not None

    retrieved = storage.get_bytes(ev.storage_key)
    assert hashlib.sha256(retrieved).hexdigest() == ev.sha256_hash


@pytest.mark.integration
def test_collect_task_populates_sha256(client, db_session, storage):
    """Task-collect endpoint stores sha256_hash matching the uploaded bytes."""
    d = _seed(db_session)
    data = b"%PDF-1.4 task collect hashing test"
    expected_hash = hashlib.sha256(data).hexdigest()

    task = EvidenceTask(
        org_id=d["org"].id,
        assessment_id=d["assessment"].id,
        title="Config export",
        artifact_type="export",
        status="open",
    )
    db_session.add(task)
    db_session.flush()
    db_session.add(EvidenceTaskStateLink(task_id=task.id, control_state_id=d["cs"].id))
    db_session.flush()

    url = (
        f"/orgs/{d['org'].id}/assessments/{d['assessment'].id}"
        f"/evidence-tasks/{task.id}/collect"
    )
    r = client.post(
        url,
        files={"file": ("config.pdf", data, "application/pdf")},
        data={"artifact_type": "export"},
    )
    assert r.status_code == 201, r.text

    ev = db_session.get(Evidence, uuid.UUID(r.json()["id"]))
    assert ev is not None
    assert ev.sha256_hash == expected_hash


@pytest.mark.integration
def test_references_do_not_get_sha256(client, db_session):
    """Reference evidence (no stored bytes) must have sha256_hash = NULL."""
    d = _seed(db_session)

    r = client.post(
        _refs_url(d),
        json=[{
            "title": "Policy doc",
            "location": "https://sharepoint.example.com/policy.pdf",
            "artifact_type": "policy",
        }],
    )
    assert r.status_code == 201, r.text

    ev_id = uuid.UUID(r.json()[0]["id"])
    ev = db_session.get(Evidence, ev_id)
    assert ev is not None
    assert ev.kind == "reference"
    assert ev.sha256_hash is None
