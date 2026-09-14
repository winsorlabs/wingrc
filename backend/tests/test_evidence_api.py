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
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.auth import create_session, get_current_user
from app.config import get_settings
from app.db import get_session
from app.engine import start_assessment
from app.main import app
from app.models import (
    AssessmentObjective,
    AuditLog,
    Control,
    ControlState,
    Evidence,
    EvidenceTask,
    EvidenceTaskStateLink,
    Framework,
    Organization,
    OrgMembership,
    User,
)
from app.storage import NullStorageClient, StorageClient, get_storage_client
from tests.conftest import _app_session, _authed, _grant

# ---------------------------------------------------------------------------
# In-memory storage mock
# ---------------------------------------------------------------------------


class InMemoryStorageClient(StorageClient):
    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.deleted: list[str] = []

    def upload_file(self, key: str, data: bytes, content_type: str) -> None:
        self.files[key] = data

    def presigned_url(
        self, key: str, expires_in: int = 300, download_filename: str | None = None
    ) -> str:
        url = f"http://fake-storage/{key}"
        if download_filename:
            url += f"?download_filename={download_filename}"
        return url

    def delete_file(self, key: str) -> None:
        self.deleted.append(key)
        self.files.pop(key, None)

    def get_bytes(self, key: str) -> bytes:
        if key not in self.files:
            raise FileNotFoundError(key)
        return self.files[key]

    def stream_bytes(self, key: str, chunk_size: int = 8):
        if key not in self.files:
            raise FileNotFoundError(key)
        data = self.files[key]
        # Deliberately chunked (not one big yield) so a test asserting on
        # multiple chunks actually exercises multiple next() calls, the
        # same shape production's iterate_in_threadpool wrapping sees.
        for i in range(0, len(data), chunk_size):
            yield data[i : i + chunk_size]


class _StorageCallSpy(InMemoryStorageClient):
    """Records every method call so a test can assert storage was never
    touched (e.g. the ownership check rejects before any storage call)."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[str] = []

    def upload_file(self, key: str, data: bytes, content_type: str) -> None:
        self.calls.append("upload_file")
        super().upload_file(key, data, content_type)

    def get_bytes(self, key: str) -> bytes:
        self.calls.append("get_bytes")
        return super().get_bytes(key)

    def stream_bytes(self, key: str, chunk_size: int = 8):
        self.calls.append("stream_bytes")
        yield from super().stream_bytes(key, chunk_size)

    def delete_file(self, key: str) -> None:
        self.calls.append("delete_file")
        super().delete_file(key)

    def is_configured(self) -> bool:
        self.calls.append("is_configured")
        return super().is_configured()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def storage():
    return InMemoryStorageClient()


@pytest.fixture
def client(db_session, storage, fake_msp_admin):
    app.dependency_overrides[get_session] = _app_session(db_session)
    app.dependency_overrides[get_storage_client] = lambda: storage
    app.dependency_overrides[get_current_user] = _authed(db_session, fake_msp_admin)
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def real_session_client(db_session, storage):
    """Exercises the real get_current_user -> _resolve_session path (not
    the _authed dependency-override bypass every other fixture here uses)
    -- needed to prove the download route isn't accidentally exempt from
    session-idle-timeout / deactivated-account re-checks that only that
    real path performs. Mirrors test_session_idle.py's own client fixture.
    """
    app.dependency_overrides[get_session] = _app_session(db_session)
    app.dependency_overrides[get_storage_client] = lambda: storage
    yield TestClient(app)
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


def _seed(db_session, *, org_id: uuid.UUID | None = None, fake_msp_admin=None) -> dict:
    """Org + framework (one control AC.L2-3.1.1 with objective [a]) + assessment.

    Pass fake_msp_admin (whenever org_id=fake_msp_admin.org_id) to also grant
    that identity org_membership on the seeded org — required for
    require_org_access to authorize any client request against it.
    """
    org_kwargs: dict = {"name": f"EvTestOrg-{uuid.uuid4().hex[:6]}"}
    if org_id is not None:
        org_kwargs["id"] = org_id
    org = Organization(**org_kwargs)
    fw = Framework(key=f"fw-ev-{uuid.uuid4().hex[:6]}", name="NIST r2", version="r2")
    db_session.add_all([org, fw])
    db_session.flush()
    if fake_msp_admin is not None:
        _grant(db_session, fake_msp_admin, org_id=org.id)

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


def _seed_real_user_with_evidence(db_session, storage, *, is_active: bool = True) -> dict:
    """A real User + OrgMembership + one file Evidence row, seeded directly
    (not through the _authed bypass) so a real cookie session actually
    resolves to genuine access -- for the get_current_user-path tests
    below, where the point is proving the real session-resolution checks
    apply to the download route too.
    """
    org = Organization(name=f"RealSessOrg-{uuid.uuid4().hex[:6]}")
    db_session.add(org)
    db_session.flush()
    user = User(
        home_org_id=org.id,
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        display_name="Real Session User",
        login_method="local",
        role="msp_admin",
        is_active=is_active,
    )
    db_session.add(user)
    db_session.flush()
    db_session.add(OrgMembership(user_id=user.id, org_id=org.id, role="msp_admin"))

    storage_key = f"{org.id}/evidence/{uuid.uuid4()}/f.png"
    storage.files[storage_key] = b"\x89PNG\r\n\x1a\n real bytes"
    ev = Evidence(
        org_id=org.id,
        kind="file",
        title="f.png",
        artifact_type="screenshot",
        storage_key=storage_key,
        mime_type="image/png",
        file_size_bytes=len(storage.files[storage_key]),
        collected_at=datetime.now(UTC),
    )
    db_session.add(ev)
    db_session.flush()
    return {"org": org, "user": user, "evidence": ev}


# ---------------------------------------------------------------------------
# File upload — happy paths
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_upload_creates_evidence_and_does_not_change_status(
    client, db_session, storage, fake_msp_admin
):
    """Upload stores the bytes, returns EvidenceOut, and leaves status untouched."""
    d = _seed(db_session, org_id=fake_msp_admin.org_id, fake_msp_admin=fake_msp_admin)
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
    # download_url is a same-origin API route, never a direct storage URL --
    # the presigned-URL bearer-credential property this route replaces.
    assert body["download_url"] == f"/orgs/{d['org'].id}/evidence/{body['id']}/download"
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
def test_list_evidence_returns_uploaded_item(client, db_session, fake_msp_admin):
    d = _seed(db_session, org_id=fake_msp_admin.org_id, fake_msp_admin=fake_msp_admin)
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
    assert item["download_url"] == f"/orgs/{d['org'].id}/evidence/{item['id']}/download"
    assert item["reference_location"] is None


@pytest.mark.integration
def test_evidence_count_increments_in_control_states(client, db_session, fake_msp_admin):
    d = _seed(db_session, org_id=fake_msp_admin.org_id, fake_msp_admin=fake_msp_admin)

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
def test_download_streams_correct_bytes_content_type_and_filename(
    client, db_session, fake_msp_admin
):
    """No redirect to a presigned URL any more -- the response itself IS
    the file, byte-for-byte identical to what was uploaded, with the right
    Content-Type and a Content-Disposition forcing save-as under the
    original filename."""
    raw = b"\x89PNG\r\n\x1a\n" + b"fake png body" * 50
    d = _seed(db_session, org_id=fake_msp_admin.org_id, fake_msp_admin=fake_msp_admin)
    up = client.post(
        _upload_url(d),
        files={"file": ("mfa.png", raw, "image/png")},
        data={"artifact_type": "screenshot"},
    )
    ev_id = up.json()["id"]

    r = client.get(_download_url(d, ev_id))
    assert r.status_code == 200
    assert r.content == raw
    assert r.headers["content-type"] == "image/png"
    assert r.headers["content-length"] == str(len(raw))
    cd = r.headers["content-disposition"]
    assert cd.startswith("attachment;")
    assert 'filename="mfa.png"' in cd


@pytest.mark.integration
def test_download_filename_uses_custom_title_with_extension_appended(
    client, db_session, fake_msp_admin
):
    """A custom title without an extension still gets one on the download,
    so the saved file has the right type — see storage.download_filename()."""
    d = _seed(db_session, org_id=fake_msp_admin.org_id, fake_msp_admin=fake_msp_admin)
    up = client.post(
        _upload_url(d),
        files={"file": ("original-name.pdf", b"%PDF-1.4", "application/pdf")},
        data={"artifact_type": "document", "title": "Firewall config export"},
    )
    ev_id = up.json()["id"]

    r = client.get(_download_url(d, ev_id))
    assert r.status_code == 200
    assert 'filename="Firewall config export.pdf"' in r.headers["content-disposition"]


@pytest.mark.integration
def test_no_presigned_storage_url_issued_for_evidence(client, db_session, fake_msp_admin):
    """Grep the actual responses, not just the UI: upload/list/download must
    never hand back a direct storage URL for evidence -- only this API's own
    same-origin route."""
    d = _seed(db_session, org_id=fake_msp_admin.org_id, fake_msp_admin=fake_msp_admin)
    up = client.post(
        _upload_url(d),
        files={"file": ("mfa.png", b"\x89PNG\r\n\x1a\n", "image/png")},
        data={"artifact_type": "screenshot"},
    )
    up_url = up.json()["download_url"]
    assert up_url is not None and not up_url.startswith("http")
    assert up_url.startswith(f"/orgs/{d['org'].id}/evidence/")

    list_url = client.get(_upload_url(d)).json()[0]["download_url"]
    assert list_url == up_url


@pytest.mark.integration
def test_download_cross_org_evidence_id_unreachable(client, db_session, fake_msp_admin):
    """A guessed evidence_id belonging to a DIFFERENT org must 404 even
    though the caller has real membership on the org_id in the URL --
    require_org_access() only confirms the caller belongs to *org_id*, not
    that evidence_id is actually one of its own rows. The other org's
    evidence is seeded directly (not via the authenticated client — the
    caller has no membership there at all, so the upload endpoint itself
    would correctly 403 it)."""
    other = _seed(db_session)  # unrelated org, no membership for the caller
    other_ev = Evidence(
        org_id=other["org"].id,
        kind="file",
        title="secret.png",
        artifact_type="screenshot",
        storage_key=f"{other['org'].id}/evidence/leaked/leaked.png",
        mime_type="image/png",
        file_size_bytes=10,
        collected_at=datetime.now(UTC),
    )
    db_session.add(other_ev)
    db_session.flush()

    mine = _seed(db_session, org_id=fake_msp_admin.org_id, fake_msp_admin=fake_msp_admin)

    r = client.get(f"/orgs/{mine['org'].id}/evidence/{other_ev.id}/download")
    assert r.status_code == 404


@pytest.mark.integration
def test_download_ownership_check_precedes_any_storage_call(client, db_session, fake_msp_admin):
    d = _seed(db_session, org_id=fake_msp_admin.org_id, fake_msp_admin=fake_msp_admin)
    spy = _StorageCallSpy()
    app.dependency_overrides[get_storage_client] = lambda: spy
    try:
        r = client.get(f"/orgs/{d['org'].id}/evidence/{uuid.uuid4()}/download")
    finally:
        app.dependency_overrides.pop(get_storage_client, None)
    assert r.status_code == 404
    assert spy.calls == []


@pytest.mark.integration
def test_download_expired_session_401(real_session_client, db_session, storage):
    """A session past the idle-timeout window must 401 on the download
    route exactly as it does on every other route -- proves this route
    isn't accidentally exempt from the real get_current_user path (the
    fixture here bypasses nothing, unlike every other test in this file).
    Mirrors test_session_idle.py's own scenario/methodology.
    """
    seeded = _seed_real_user_with_evidence(db_session, storage)
    idle_minutes = get_settings().session_idle_minutes
    now = datetime.now(UTC)
    session_row, raw = create_session(db_session, seeded["user"])
    db_session.flush()
    session_row.last_activity_at = now - timedelta(minutes=idle_minutes + 5)
    session_row.expires_at = now + timedelta(hours=8)  # nowhere near absolute expiry
    db_session.flush()

    real_session_client.cookies.set("wingrc_session", raw)
    r = real_session_client.get(
        f"/orgs/{seeded['org'].id}/evidence/{seeded['evidence'].id}/download"
    )
    assert r.status_code == 401


@pytest.mark.integration
def test_download_deactivated_account_403(real_session_client, db_session, storage):
    """A deactivated account's still-valid session cookie must not unlock
    a download -- _resolve_session re-checks user.is_active on every
    request, not just at login."""
    seeded = _seed_real_user_with_evidence(db_session, storage, is_active=False)
    _row, raw = create_session(db_session, seeded["user"])
    db_session.flush()

    real_session_client.cookies.set("wingrc_session", raw)
    r = real_session_client.get(
        f"/orgs/{seeded['org'].id}/evidence/{seeded['evidence'].id}/download"
    )
    assert r.status_code == 403


@pytest.mark.integration
def test_download_storage_not_configured_404(client, db_session, fake_msp_admin):
    d = _seed(db_session, org_id=fake_msp_admin.org_id, fake_msp_admin=fake_msp_admin)
    up = client.post(
        _upload_url(d),
        files={"file": ("mfa.png", b"\x89PNG\r\n\x1a\n", "image/png")},
        data={"artifact_type": "screenshot"},
    )
    ev_id = up.json()["id"]

    app.dependency_overrides[get_storage_client] = lambda: NullStorageClient()
    try:
        r = client.get(_download_url(d, ev_id))
    finally:
        app.dependency_overrides.pop(get_storage_client, None)
    assert r.status_code == 404
    assert r.json()["detail"] == "Storage not configured"


@pytest.mark.integration
def test_download_large_file_streams_via_stream_bytes_not_get_bytes(
    client, db_session, fake_msp_admin
):
    """Demonstrates the download path uses the chunked primitive, not the
    whole-object one -- a StorageClient double whose get_bytes() raises,
    and whose stream_bytes() is a real generator (bytes only materialize
    chunk by chunk, never as one big blob) still serves a correct
    response."""
    chunk_size = 4096
    chunk_count = 50
    big = bytes(i % 256 for i in range(chunk_size * chunk_count))

    class LargeFileStorage(InMemoryStorageClient):
        def get_bytes(self, key: str) -> bytes:
            raise AssertionError("download path must not call get_bytes()")

        def stream_bytes(self, key: str, chunk_size: int = 262_144):
            # Independent chunk_size from the outer closure's on purpose --
            # proves the caller-supplied default from StorageClient isn't
            # what determines chunking here; production always calls this
            # with no explicit chunk_size (its own default), same as here.
            for i in range(0, len(big), 4096):
                yield big[i : i + 4096]

    d = _seed(db_session, org_id=fake_msp_admin.org_id, fake_msp_admin=fake_msp_admin)
    up = client.post(
        _upload_url(d),
        files={"file": ("huge.png", b"\x89PNG\r\n\x1a\n", "image/png")},
        data={"artifact_type": "screenshot"},
    )
    ev_id = up.json()["id"]
    # The real upload was tiny -- correct the stored size to match what
    # LargeFileStorage actually serves, or the Content-Length header
    # download_evidence sends (from Evidence.file_size_bytes) would lie.
    ev = db_session.get(Evidence, uuid.UUID(ev_id))
    ev.file_size_bytes = len(big)
    db_session.flush()

    app.dependency_overrides[get_storage_client] = lambda: LargeFileStorage()
    try:
        r = client.get(_download_url(d, ev_id))
    finally:
        app.dependency_overrides.pop(get_storage_client, None)
    assert r.status_code == 200
    assert r.content == big


@pytest.mark.integration
def test_download_writes_audit_log_entry(client, db_session, fake_msp_admin):
    d = _seed(db_session, org_id=fake_msp_admin.org_id, fake_msp_admin=fake_msp_admin)
    up = client.post(
        _upload_url(d),
        files={"file": ("mfa.png", b"\x89PNG\r\n\x1a\n", "image/png")},
        data={"artifact_type": "screenshot"},
    )
    ev_id = up.json()["id"]

    r = client.get(_download_url(d, ev_id))
    assert r.status_code == 200

    rows = db_session.scalars(
        select(AuditLog).where(
            AuditLog.org_id == d["org"].id,
            AuditLog.action == "evidence.download",
            AuditLog.entity_id == uuid.UUID(ev_id),
        )
    ).all()
    assert len(rows) == 1
    assert rows[0].after_value["title"] == "mfa.png"
    assert rows[0].after_value["artifact_type"] == "screenshot"
    assert rows[0].actor == str(fake_msp_admin.id)


@pytest.mark.integration
def test_list_and_upload_do_not_write_download_audit_log(client, db_session, fake_msp_admin):
    """Building a download_url into a response is not itself an access --
    only a real GET against the download route is. Otherwise every page
    view listing evidence would fire a download-audit row for content
    nobody actually looked at."""
    d = _seed(db_session, org_id=fake_msp_admin.org_id, fake_msp_admin=fake_msp_admin)
    client.post(
        _upload_url(d),
        files={"file": ("mfa.png", b"\x89PNG\r\n\x1a\n", "image/png")},
        data={"artifact_type": "screenshot"},
    )
    client.get(_upload_url(d))

    rows = db_session.scalars(
        select(AuditLog).where(
            AuditLog.org_id == d["org"].id, AuditLog.action == "evidence.download"
        )
    ).all()
    assert rows == []


@pytest.mark.integration
def test_delete_removes_evidence_and_does_not_change_status(
    client, db_session, storage, fake_msp_admin
):
    d = _seed(db_session, org_id=fake_msp_admin.org_id, fake_msp_admin=fake_msp_admin)
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
def test_upload_disallowed_mime_type_rejected(client, db_session, fake_msp_admin):
    d = _seed(db_session, org_id=fake_msp_admin.org_id, fake_msp_admin=fake_msp_admin)
    r = client.post(
        _upload_url(d),
        files={"file": ("malware.exe", b"MZ\x90\x00", "application/x-msdownload")},
        data={"artifact_type": "document"},
    )
    assert r.status_code == 415


@pytest.mark.integration
def test_upload_disallowed_extension_rejected(client, db_session, fake_msp_admin):
    d = _seed(db_session, org_id=fake_msp_admin.org_id, fake_msp_admin=fake_msp_admin)
    r = client.post(
        _upload_url(d),
        files={"file": ("script.php", b"<?php echo 1; ?>", "application/pdf")},
        data={"artifact_type": "document"},
    )
    assert r.status_code == 415


@pytest.mark.integration
def test_upload_magic_byte_mismatch_rejected(client, db_session, fake_msp_admin):
    """File claims to be PNG but bytes don't start with the PNG magic header."""
    d = _seed(db_session, org_id=fake_msp_admin.org_id, fake_msp_admin=fake_msp_admin)
    r = client.post(
        _upload_url(d),
        files={"file": ("not_really.png", b"this is definitely not a PNG", "image/png")},
        data={"artifact_type": "screenshot"},
    )
    assert r.status_code == 415


@pytest.mark.integration
def test_upload_magic_byte_mismatch_pdf_rejected(client, db_session, fake_msp_admin):
    """File claims to be PDF but bytes don't start with %PDF."""
    d = _seed(db_session, org_id=fake_msp_admin.org_id, fake_msp_admin=fake_msp_admin)
    r = client.post(
        _upload_url(d),
        files={"file": ("fake.pdf", b"MZ\x90\x00 not a pdf", "application/pdf")},
        data={"artifact_type": "document"},
    )
    assert r.status_code == 415


@pytest.mark.integration
def test_upload_oversized_file_rejected(client, db_session, fake_msp_admin):
    d = _seed(db_session, org_id=fake_msp_admin.org_id, fake_msp_admin=fake_msp_admin)
    big = b"\x89PNG\r\n\x1a\n" + b"x" * (51 * 1024 * 1024)
    r = client.post(
        _upload_url(d),
        files={"file": ("huge.png", big, "image/png")},
        data={"artifact_type": "screenshot"},
    )
    assert r.status_code == 413


@pytest.mark.integration
def test_upload_wrong_org_returns_404(client, db_session, fake_msp_admin):
    """URL org_id is the caller's own org (passes the ownership guard) but the
    assessment/control-state actually belong to a different, unrelated org —
    the handler's own org-consistency check must still 404."""
    d = _seed(db_session)  # unrelated org holds the real assessment
    db_session.add(Organization(id=fake_msp_admin.org_id, name="Caller Org"))
    db_session.flush()
    _grant(db_session, fake_msp_admin)

    bad_url = (
        f"/orgs/{fake_msp_admin.org_id}/assessments/{d['assessment'].id}"
        f"/control-states/{d['cs'].id}/evidence"
    )
    r = client.post(
        bad_url,
        files={"file": ("x.png", b"\x89PNG\r\n\x1a\n", "image/png")},
        data={"artifact_type": "screenshot"},
    )
    assert r.status_code == 404


@pytest.mark.integration
def test_delete_nonexistent_returns_404(client, db_session, fake_msp_admin):
    d = _seed(db_session, org_id=fake_msp_admin.org_id, fake_msp_admin=fake_msp_admin)
    r = client.delete(f"{_upload_url(d)}/{uuid.uuid4()}")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Reference add
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_add_references_batch(client, db_session, fake_msp_admin):
    d = _seed(db_session, org_id=fake_msp_admin.org_id, fake_msp_admin=fake_msp_admin)
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
def test_add_reference_unix_path(client, db_session, fake_msp_admin):
    d = _seed(db_session, org_id=fake_msp_admin.org_id, fake_msp_admin=fake_msp_admin)
    r = client.post(_refs_url(d), json=[
        {"title": "Unix path", "location": "/mnt/nas/cmmc/evidence.pdf",
         "artifact_type": "document"}
    ])
    assert r.status_code == 201
    assert r.json()[0]["reference_location"] == "/mnt/nas/cmmc/evidence.pdf"


@pytest.mark.integration
def test_add_reference_empty_location_rejected(client, db_session, fake_msp_admin):
    d = _seed(db_session, org_id=fake_msp_admin.org_id, fake_msp_admin=fake_msp_admin)
    r = client.post(_refs_url(d), json=[
        {"title": "Bad ref", "location": "", "artifact_type": "document"}
    ])
    assert r.status_code == 422


@pytest.mark.integration
def test_add_reference_invalid_location_rejected(client, db_session, fake_msp_admin):
    """A bare word (not a URL or path) must be rejected."""
    d = _seed(db_session, org_id=fake_msp_admin.org_id, fake_msp_admin=fake_msp_admin)
    r = client.post(_refs_url(d), json=[
        {"title": "Bad ref", "location": "just_a_word_no_scheme", "artifact_type": "document"}
    ])
    assert r.status_code == 422


@pytest.mark.integration
def test_add_reference_empty_title_rejected(client, db_session, fake_msp_admin):
    d = _seed(db_session, org_id=fake_msp_admin.org_id, fake_msp_admin=fake_msp_admin)
    r = client.post(_refs_url(d), json=[
        {"title": "   ", "location": "https://example.com/doc.pdf", "artifact_type": "document"}
    ])
    assert r.status_code == 422


@pytest.mark.integration
def test_add_reference_bad_artifact_type_rejected(client, db_session, fake_msp_admin):
    d = _seed(db_session, org_id=fake_msp_admin.org_id, fake_msp_admin=fake_msp_admin)
    r = client.post(_refs_url(d), json=[
        {"title": "Ref", "location": "https://example.com/", "artifact_type": "video"}
    ])
    assert r.status_code == 422


@pytest.mark.integration
def test_add_reference_empty_list_rejected(client, db_session, fake_msp_admin):
    d = _seed(db_session, org_id=fake_msp_admin.org_id, fake_msp_admin=fake_msp_admin)
    r = client.post(_refs_url(d), json=[])
    assert r.status_code == 422


@pytest.mark.integration
def test_download_reference_returns_404(client, db_session, fake_msp_admin):
    """References have no stored file — download endpoint must 404."""
    d = _seed(db_session, org_id=fake_msp_admin.org_id, fake_msp_admin=fake_msp_admin)
    ref = client.post(_refs_url(d), json=[
        {"title": "SP link", "location": "https://sp.example.com/doc.pdf",
         "artifact_type": "document"}
    ])
    ev_id = ref.json()[0]["id"]
    r = client.get(_download_url(d, ev_id))
    assert r.status_code == 404


@pytest.mark.integration
def test_delete_reference_does_not_call_storage(client, db_session, storage, fake_msp_admin):
    """Deleting a reference only removes the DB row — no storage.delete_file call."""
    d = _seed(db_session, org_id=fake_msp_admin.org_id, fake_msp_admin=fake_msp_admin)
    ref = client.post(_refs_url(d), json=[
        {"title": "Ref", "location": "https://example.com/f.pdf", "artifact_type": "document"}
    ])
    ev_id = ref.json()[0]["id"]

    r = client.delete(f"{_upload_url(d)}/{ev_id}")
    assert r.status_code == 204
    assert client.get(_upload_url(d)).json() == []
    assert len(storage.deleted) == 0  # no storage operation


@pytest.mark.integration
def test_list_shows_both_file_and_reference(client, db_session, storage, fake_msp_admin):
    d = _seed(db_session, org_id=fake_msp_admin.org_id, fake_msp_admin=fake_msp_admin)

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
    assert fil["download_url"] == f"/orgs/{d['org'].id}/evidence/{fil['id']}/download"
    assert fil["note"] is None


# ---------------------------------------------------------------------------
# Invariants
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_evidence_never_changes_control_state_status(client, db_session, fake_msp_admin):
    """Attaching evidence (both kinds) must never alter control_state.status."""
    d = _seed(db_session, org_id=fake_msp_admin.org_id, fake_msp_admin=fake_msp_admin)
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
def test_customer_owns_objective_accepts_evidence(client, db_session, storage, fake_msp_admin):
    """Evidence attachment must not be blocked on customer_owns responsibility."""
    d = _seed(db_session, org_id=fake_msp_admin.org_id, fake_msp_admin=fake_msp_admin)
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
def test_evidence_manifest_shape_and_human_readable_ids(
    client, db_session, storage, fake_msp_admin
):
    """Manifest uses control_id / objective_key as human-readable identifiers."""
    d = _seed(db_session, org_id=fake_msp_admin.org_id, fake_msp_admin=fake_msp_admin)

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
def test_evidence_manifest_objective_with_no_evidence(client, db_session, fake_msp_admin):
    """Objectives with no evidence appear in the manifest with an empty evidence list."""
    d = _seed(db_session, org_id=fake_msp_admin.org_id, fake_msp_admin=fake_msp_admin)

    r = client.get(_manifest_url(d))
    assert r.status_code == 200
    m = r.json()

    assert len(m["objectives"]) == 1
    assert m["objectives"][0]["evidence"] == []


@pytest.mark.integration
def test_evidence_manifest_wrong_org_returns_404(client, db_session, fake_msp_admin):
    """Same shape as test_upload_wrong_org_returns_404: URL org is the caller's
    own (passes the guard), assessment belongs to a different org (404s)."""
    d = _seed(db_session)  # unrelated org holds the real assessment
    db_session.add(Organization(id=fake_msp_admin.org_id, name="Caller Org"))
    db_session.flush()
    _grant(db_session, fake_msp_admin)

    url = f"/orgs/{fake_msp_admin.org_id}/assessments/{d['assessment'].id}/evidence-manifest"
    r = client.get(url)
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# SHA-256 hashing
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_upload_populates_sha256(client, db_session, storage, fake_msp_admin):
    """File upload endpoint stores sha256_hash matching the uploaded bytes."""
    d = _seed(db_session, org_id=fake_msp_admin.org_id, fake_msp_admin=fake_msp_admin)
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
def test_upload_hash_matches_storage_roundtrip(client, db_session, storage, fake_msp_admin):
    """The hash stored in the DB equals the hash of bytes returned by get_bytes().

    This exercises the actual path bundle export uses: snapshot_bundle calls
    storage.get_bytes(key) to fetch bytes for embedding.  If those bytes differ
    from what was uploaded, the hash would mismatch and the artifact log would be
    wrong.  InMemoryStorageClient stores and returns bytes faithfully, proving
    the contract.
    """
    d = _seed(db_session, org_id=fake_msp_admin.org_id, fake_msp_admin=fake_msp_admin)
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
def test_collect_task_populates_sha256(client, db_session, storage, fake_msp_admin):
    """Task-collect endpoint stores sha256_hash matching the uploaded bytes."""
    d = _seed(db_session, org_id=fake_msp_admin.org_id, fake_msp_admin=fake_msp_admin)
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
def test_references_do_not_get_sha256(client, db_session, fake_msp_admin):
    """Reference evidence (no stored bytes) must have sha256_hash = NULL."""
    d = _seed(db_session, org_id=fake_msp_admin.org_id, fake_msp_admin=fake_msp_admin)

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
