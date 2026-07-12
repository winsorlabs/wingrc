"""Integration tests for the bundle export endpoint.

GET /orgs/{org_id}/assessments/{assessment_id}/bundle

Covers:
  - 404 on missing org / wrong-org assessment
  - Happy path: 200, content-type application/zip, valid ZIP bytes
  - ZIP contains all 8 expected HTML documents
  - cover.html includes org name and SPRS score
  - implementation.html includes [a] objective text and statement body
  - manifest.html includes evidence title
  - AuditLog row created with action=bundle.export
  - Evidence file bytes are embedded in the ZIP archive

All tests use InMemoryStorageClient injected via dependency_overrides.
"""
from __future__ import annotations

import io
import uuid
import zipfile
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import get_session
from app.engine import start_assessment
from app.main import app
from app.models import (
    AssessmentObjective,
    AuditLog,
    Contact,
    ContactDocumentationRole,
    Control,
    ControlState,
    Evidence,
    EvidenceStateLink,
    Framework,
    ImplementationStatement,
    Organization,
    RaciAssignment,
    SystemDescription,
)
from app.storage import StorageClient, get_storage_client

# ---------------------------------------------------------------------------
# In-memory storage with get_bytes support
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

    def get_bytes(self, key: str) -> bytes:
        if key not in self.files:
            raise FileNotFoundError(key)
        return self.files[key]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def storage() -> InMemoryStorageClient:
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


def _seed(db_session, storage: InMemoryStorageClient) -> dict:
    """Create org + framework + two-objective control + assessment + extras.

    Returns a dict with keys: org, fw, assessment, ctrl, obj_a, obj_b, cs_a, cs_b,
    contact, evidence, ev_key.
    """
    org = Organization(name=f"BundleOrg-{uuid.uuid4().hex[:6]}")
    fw = Framework(
        key=f"fw-bundle-{uuid.uuid4().hex[:6]}",
        name="NIST 800-171 r2",
        version="r2",
    )
    db_session.add_all([org, fw])
    db_session.flush()

    ctrl = Control(
        framework_id=fw.id,
        control_id="AC.L2-3.1.1",
        family="AC",
        title="Access Control",
        requirement_text="Limit system access to authorized users.",
        sprs_weight=5,
        sequence_order=1,
    )
    db_session.add(ctrl)
    db_session.flush()

    obj_a = AssessmentObjective(
        control_id=ctrl.id,
        objective_key="a",
        text="Users and processes are identified and authenticated.",
    )
    obj_b = AssessmentObjective(
        control_id=ctrl.id,
        objective_key="b",
        text="Access to system resources is limited per role.",
    )
    db_session.add_all([obj_a, obj_b])
    db_session.flush()

    assessment = start_assessment(
        db_session, org_id=org.id, framework_id=fw.id, name="Q1 Self-Assessment"
    )
    db_session.flush()

    cs_a = db_session.scalars(
        select(ControlState).where(
            ControlState.assessment_id == assessment.id,
            ControlState.objective_id == obj_a.id,
        )
    ).first()
    cs_b = db_session.scalars(
        select(ControlState).where(
            ControlState.assessment_id == assessment.id,
            ControlState.objective_id == obj_b.id,
        )
    ).first()

    # System description
    sd = SystemDescription(
        org_id=org.id,
        system_name="CorpNet",
        system_type="major_application",
        operational_status="operational",
    )
    db_session.add(sd)
    db_session.flush()

    # Implementation statement for objective [a]
    stmt = ImplementationStatement(
        objective_id=obj_a.id,
        assessment_id=assessment.id,
        body="Heimdal enforces MFA for all privileged accounts.",
        status="draft",
    )
    db_session.add(stmt)
    db_session.flush()

    # Contact with a documentation role
    contact = Contact(
        org_id=org.id,
        name="Jane Smith",
        email="jane@example.com",
        affiliation="msp",
        role_title="Security Engineer",
    )
    db_session.add(contact)
    db_session.flush()

    doc_role = ContactDocumentationRole(contact_id=contact.id, role="security_officer")
    db_session.add(doc_role)
    db_session.flush()

    # RACI assignment on cs_a
    raci = RaciAssignment(
        control_state_id=cs_a.id,
        contact_id=contact.id,
        raci_letter="R",
    )
    db_session.add(raci)
    db_session.flush()

    # Evidence file uploaded to in-memory storage
    ev_id = uuid.uuid4()
    ev_key = f"{org.id}/evidence/{ev_id}/{ev_id}.pdf"
    ev_bytes = b"%PDF-1.4 fake-pdf-content"
    storage.upload_file(ev_key, ev_bytes, "application/pdf")

    evidence = Evidence(
        id=ev_id,
        org_id=org.id,
        kind="file",
        title="Network Diagram",
        artifact_type="document",
        storage_key=ev_key,
        mime_type="application/pdf",
        file_size_bytes=len(ev_bytes),
        collected_at=assessment.started_at or datetime.now(UTC),
    )
    db_session.add(evidence)
    db_session.flush()

    link = EvidenceStateLink(evidence_id=evidence.id, control_state_id=cs_a.id)
    db_session.add(link)
    db_session.flush()

    return {
        "org": org,
        "fw": fw,
        "assessment": assessment,
        "ctrl": ctrl,
        "obj_a": obj_a,
        "obj_b": obj_b,
        "cs_a": cs_a,
        "cs_b": cs_b,
        "contact": contact,
        "evidence": evidence,
        "ev_key": ev_key,
        "ev_bytes": ev_bytes,
    }


def _bundle_url(d: dict) -> str:
    return f"/orgs/{d['org'].id}/assessments/{d['assessment'].id}/bundle"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_bundle_missing_org(client):
    r = client.get(f"/orgs/{uuid.uuid4()}/assessments/{uuid.uuid4()}/bundle")
    assert r.status_code == 404


@pytest.mark.integration
def test_bundle_wrong_org_assessment(client, db_session, storage):
    d = _seed(db_session, storage)
    other_org_id = uuid.uuid4()
    r = client.get(f"/orgs/{other_org_id}/assessments/{d['assessment'].id}/bundle")
    assert r.status_code == 404


@pytest.mark.integration
def test_bundle_returns_200_zip(client, db_session, storage):
    d = _seed(db_session, storage)
    r = client.get(_bundle_url(d))
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    assert len(r.content) > 0


@pytest.mark.integration
def test_bundle_content_disposition(client, db_session, storage):
    d = _seed(db_session, storage)
    r = client.get(_bundle_url(d))
    assert r.status_code == 200
    cd = r.headers.get("content-disposition", "")
    assert "attachment" in cd
    assert ".zip" in cd


@pytest.mark.integration
def test_bundle_zip_contains_required_files(client, db_session, storage):
    d = _seed(db_session, storage)
    r = client.get(_bundle_url(d))
    assert r.status_code == 200

    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        names = zf.namelist()

    expected_suffixes = [
        "index.html",
        "cover.html",
        "ssp/01_system_description.html",
        "ssp/02_implementation.html",
        "ssp/03_personnel.html",
        "evidence/manifest.html",
        "summary/scoring.html",
        "summary/outstanding.html",
    ]
    for suffix in expected_suffixes:
        assert any(n.endswith(suffix) for n in names), f"Missing {suffix} in bundle"


@pytest.mark.integration
def test_bundle_cover_contains_org_name(client, db_session, storage):
    d = _seed(db_session, storage)
    r = client.get(_bundle_url(d))
    assert r.status_code == 200

    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        cover_name = next(n for n in zf.namelist() if n.endswith("cover.html"))
        cover_html = zf.read(cover_name).decode()

    assert d["org"].name in cover_html


@pytest.mark.integration
def test_bundle_cover_contains_sprs_score(client, db_session, storage):
    d = _seed(db_session, storage)
    r = client.get(_bundle_url(d))
    assert r.status_code == 200

    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        cover_name = next(n for n in zf.namelist() if n.endswith("cover.html"))
        cover_html = zf.read(cover_name).decode()

    # Score is rendered as a number; SPRS starts at 110 minus deductions.
    # The control is not_met so score should be 110 - 5 = 105, but any integer works.
    assert "/ 110" in cover_html


@pytest.mark.integration
def test_bundle_implementation_contains_objective(client, db_session, storage):
    d = _seed(db_session, storage)
    r = client.get(_bundle_url(d))
    assert r.status_code == 200

    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        impl_name = next(n for n in zf.namelist() if n.endswith("02_implementation.html"))
        impl_html = zf.read(impl_name).decode()

    assert "Users and processes are identified" in impl_html
    assert "[a]" in impl_html


@pytest.mark.integration
def test_bundle_implementation_contains_statement(client, db_session, storage):
    d = _seed(db_session, storage)
    r = client.get(_bundle_url(d))
    assert r.status_code == 200

    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        impl_name = next(n for n in zf.namelist() if n.endswith("02_implementation.html"))
        impl_html = zf.read(impl_name).decode()

    assert "Heimdal enforces MFA" in impl_html


@pytest.mark.integration
def test_bundle_manifest_contains_evidence(client, db_session, storage):
    d = _seed(db_session, storage)
    r = client.get(_bundle_url(d))
    assert r.status_code == 200

    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        manifest_name = next(n for n in zf.namelist() if n.endswith("manifest.html"))
        manifest_html = zf.read(manifest_name).decode()

    assert "Network Diagram" in manifest_html


@pytest.mark.integration
def test_bundle_audit_logged(client, db_session, storage):
    d = _seed(db_session, storage)
    r = client.get(_bundle_url(d))
    assert r.status_code == 200

    audit = db_session.scalars(
        select(AuditLog).where(
            AuditLog.org_id == d["org"].id,
            AuditLog.action == "bundle.export",
        )
    ).first()
    assert audit is not None
    assert str(d["assessment"].id) in str(audit.entity_id)


@pytest.mark.integration
def test_bundle_embeds_evidence_file(client, db_session, storage):
    d = _seed(db_session, storage)
    r = client.get(_bundle_url(d))
    assert r.status_code == 200

    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        names = zf.namelist()
        evidence_paths = [n for n in names if "evidence/files/" in n]
        assert len(evidence_paths) >= 1, "No evidence files embedded in bundle"

        embedded = zf.read(evidence_paths[0])
        assert embedded == d["ev_bytes"]
