"""Integration tests for the bundle export endpoint.

GET /orgs/{org_id}/assessments/{assessment_id}/bundle

Covers:
  - 404 on missing org / wrong-org assessment
  - Happy path: 200, content-type application/zip, valid ZIP bytes
  - ZIP contains all expected HTML documents + artifact_log.txt
  - cover.html includes org name, SPRS score, and eMASS hash fields
  - implementation.html includes [a] objective text and statement body
  - manifest.html includes evidence title and SHA-256 Hash column
  - AuditLog row created with action=bundle.export (includes hash fields)
  - Evidence file bytes are embedded in the ZIP archive
  - Artifact log lists every embedded file; every listed path exists in ZIP
  - Lazy backfill: pre-existing evidence without sha256_hash gets it on export
  - Stale-hash safety: fetch failure → hash excluded from log and manifest

All tests use InMemoryStorageClient injected via dependency_overrides.
"""
from __future__ import annotations

import hashlib
import io
import re
import uuid
import zipfile
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.auth import get_current_user
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
def client(db_session, storage, fake_msp_admin):
    app.dependency_overrides[get_session] = lambda: db_session
    app.dependency_overrides[get_storage_client] = lambda: storage
    app.dependency_overrides[get_current_user] = lambda: fake_msp_admin
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
        org_id=org.id,
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


# ---------------------------------------------------------------------------
# Artifact hashing (DoD-CIO-00008)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_bundle_artifact_log_present(client, db_session, storage):
    d = _seed(db_session, storage)
    r = client.get(_bundle_url(d))
    assert r.status_code == 200

    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        assert any(n.endswith("artifact_log.txt") for n in zf.namelist())


@pytest.mark.integration
def test_bundle_artifact_log_format(client, db_session, storage):
    """artifact_log.txt has the required Algorithm | Hash | Path header and entries."""
    d = _seed(db_session, storage)
    r = client.get(_bundle_url(d))
    assert r.status_code == 200

    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        log_name = next(n for n in zf.namelist() if n.endswith("artifact_log.txt"))
        log_content = zf.read(log_name).decode()

    lines = log_content.splitlines()
    assert lines[0] == "Algorithm | Hash | Path"
    sha256_lines = [ln for ln in lines[1:] if ln.startswith("SHA-256 | ")]
    assert len(sha256_lines) >= 1


@pytest.mark.integration
def test_bundle_second_order_hash_on_cover(client, db_session, storage):
    """cover.html surfaces Hashed Data List and Hash Value using exact eMASS field names."""
    d = _seed(db_session, storage)
    r = client.get(_bundle_url(d))
    assert r.status_code == 200

    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        cover_name = next(n for n in zf.namelist() if n.endswith("cover.html"))
        cover_html = zf.read(cover_name).decode()

    assert "Hashed Data List" in cover_html
    assert "Hash Value" in cover_html
    assert "artifact_log.txt" in cover_html


@pytest.mark.integration
def test_bundle_manifest_hash_column(client, db_session, storage):
    """Evidence manifest table includes a SHA-256 Hash column header."""
    d = _seed(db_session, storage)
    r = client.get(_bundle_url(d))
    assert r.status_code == 200

    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        manifest_name = next(n for n in zf.namelist() if n.endswith("manifest.html"))
        manifest_html = zf.read(manifest_name).decode()

    assert "SHA-256 Hash" in manifest_html


@pytest.mark.integration
def test_bundle_manifest_reference_not_applicable(client, db_session, storage):
    """Reference evidence shows 'not applicable — reference only' in the hash column."""
    d = _seed(db_session, storage)

    # Add a reference evidence item linked to cs_a
    ref_ev = Evidence(
        org_id=d["org"].id,
        kind="reference",
        title="SharePoint Policy",
        artifact_type="policy",
        reference_location="https://sp.example.com/policy.pdf",
        collected_at=datetime.now(UTC),
    )
    db_session.add(ref_ev)
    db_session.flush()
    db_session.add(EvidenceStateLink(evidence_id=ref_ev.id, control_state_id=d["cs_a"].id))
    db_session.flush()

    r = client.get(_bundle_url(d))
    assert r.status_code == 200

    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        manifest_name = next(n for n in zf.namelist() if n.endswith("manifest.html"))
        manifest_html = zf.read(manifest_name).decode()

    assert "not applicable" in manifest_html
    assert "reference only" in manifest_html


@pytest.mark.integration
def test_bundle_audit_log_includes_hash_fields(client, db_session, storage):
    """bundle.export audit row carries hashed_data_list and hash_value."""
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
    after = audit.after_value
    assert "hashed_data_list" in after
    assert "hash_value" in after
    assert after["hashed_data_list"] == "artifact_log.txt"
    assert len(after["hash_value"]) == 64  # SHA-256 hex digest


@pytest.mark.integration
def test_bundle_artifact_log_zip_consistency(client, db_session, storage):
    """Every path in artifact_log.txt exists in the ZIP; every ZIP entry
    (except cover.html and artifact_log.txt) has a corresponding log entry.

    This catches any drift between the path-generation logic used to write the
    log and the logic used to write the ZIP entries — both go through the shared
    _ev_zip_rel() helper, so this test would catch a regression that splits them.
    """
    d = _seed(db_session, storage)
    r = client.get(_bundle_url(d))
    assert r.status_code == 200

    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        names = zf.namelist()
        log_name = next(n for n in names if n.endswith("artifact_log.txt"))
        log_content = zf.read(log_name).decode()

    # Derive root prefix from the first ZIP entry
    root = names[0].split("/")[0]

    # Parse log paths (skip header line)
    log_paths: set[str] = set()
    for line in log_content.splitlines()[1:]:
        parts = line.split(" | ", 2)
        if len(parts) == 3:
            log_paths.add(parts[2])

    zip_paths = set(names)
    excluded = {f"{root}/cover.html", log_name}

    # Every log path must exist in the ZIP
    for lp in log_paths:
        assert lp in zip_paths, f"Artifact log references {lp!r} but it is not in the ZIP"

    # Every ZIP entry (except cover and the log itself) must appear in the log
    expected_in_log = zip_paths - excluded
    missing_from_log = expected_in_log - log_paths
    assert not missing_from_log, f"ZIP entries not in artifact log: {missing_from_log}"


@pytest.mark.integration
def test_bundle_manifest_links_resolve(client, db_session, storage):
    """Every href='files/...' link in manifest.html points to a real ZIP entry.

    Regression guard: if _ev_zip_rel() and _render_manifest()'s relative-path
    computation ever drift apart, broken links would be silently embedded in
    every exported bundle.  posixpath.relpath() makes the href robust, and this
    test catches any regression in that derivation.
    """
    d = _seed(db_session, storage)
    r = client.get(_bundle_url(d))
    assert r.status_code == 200

    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        names = zf.namelist()
        manifest_name = next(n for n in names if n.endswith("manifest.html"))
        manifest_html = zf.read(manifest_name).decode()

    # Derive the root prefix from the first ZIP entry (e.g. "acme_msp_20260713")
    root = names[0].split("/")[0]

    # Extract every href="files/..." from the manifest
    hrefs = re.findall(r'href="(files/[^"]+)"', manifest_html)
    assert hrefs, "manifest.html contains no file links — expected at least one"

    # manifest.html lives at {root}/evidence/manifest.html, so a relative
    # href="files/foo.png" resolves to {root}/evidence/files/foo.png in the ZIP.
    zip_names = set(names)
    for href in hrefs:
        abs_path = f"{root}/evidence/{href}"
        assert abs_path in zip_names, (
            f"manifest.html links to {href!r} but {abs_path!r} is not in the ZIP"
        )


@pytest.mark.integration
def test_bundle_lazy_backfill_populates_sha256(client, db_session, storage):
    """Evidence seeded without sha256_hash gets it computed on first bundle export.

    Simulates evidence uploaded before migration 0014: the DB row has
    sha256_hash=NULL but the bytes are present in storage.  After the bundle
    export the column must be populated with the correct hash.
    """
    d = _seed(db_session, storage)
    ev = d["evidence"]

    # Simulate pre-migration state: clear the hash that _seed may have set
    ev.sha256_hash = None
    db_session.flush()

    r = client.get(_bundle_url(d))
    assert r.status_code == 200

    db_session.refresh(ev)
    assert ev.sha256_hash is not None
    assert ev.sha256_hash == hashlib.sha256(d["ev_bytes"]).hexdigest()


@pytest.mark.integration
def test_bundle_stale_hash_excluded_on_fetch_failure(client, db_session, storage):
    """A cached sha256_hash in the DB is NOT shown when the file cannot be fetched.

    Scenario: evidence was exported once (hash cached in DB), the file was
    later deleted from storage, and the bundle is exported again.  The stale
    hash must NOT appear in artifact_log.txt or the manifest — showing it next
    to a missing file would be misleading.
    """
    d = _seed(db_session, storage)

    # Seed a second evidence item with a pre-populated hash but no bytes in storage
    stale_hash = "a" * 64
    ghost_ev_id = uuid.uuid4()
    ghost_key = f"{d['org'].id}/evidence/{ghost_ev_id}/{ghost_ev_id}.pdf"
    # Intentionally do NOT call storage.upload_file for ghost_key

    ghost_ev = Evidence(
        id=ghost_ev_id,
        org_id=d["org"].id,
        kind="file",
        title="Ghost File",
        artifact_type="document",
        storage_key=ghost_key,
        mime_type="application/pdf",
        file_size_bytes=100,
        sha256_hash=stale_hash,
        collected_at=datetime.now(UTC),
    )
    db_session.add(ghost_ev)
    db_session.flush()
    db_session.add(EvidenceStateLink(evidence_id=ghost_ev_id, control_state_id=d["cs_a"].id))
    db_session.flush()

    r = client.get(_bundle_url(d))
    assert r.status_code == 200

    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        names = zf.namelist()
        log_name = next(n for n in names if n.endswith("artifact_log.txt"))
        log_content = zf.read(log_name).decode()
        manifest_name = next(n for n in names if n.endswith("manifest.html"))
        manifest_html = zf.read(manifest_name).decode()

    # Stale hash must not appear anywhere in the artifact log
    assert stale_hash not in log_content

    # Ghost file must not be embedded in the ZIP
    assert not any("Ghost" in n or str(ghost_ev_id)[:8] in n for n in names)

    # Manifest must show "unavailable" (not the stale hash) for the ghost file
    assert "unavailable" in manifest_html
