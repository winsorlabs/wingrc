"""Integration tests for routers/admin_products.py (G.9): the deployment-
tier Tools/baseline-library screen -- library view, tool detail, the
cross-org footprint read, import (validate -> dry-run -> apply), publish/
unpublish enforcement, and document attachments.

Run in-container:
    docker compose exec backend pytest tests/test_admin_products.py -m integration -v
"""
from __future__ import annotations

import json
import uuid
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.ai.base import AIProvider
from app.auth import get_current_user
from app.db import get_session
from app.engine import activate_org_product, start_assessment
from app.main import app
from app.models import (
    AssessmentObjective,
    BaselineControl,
    BaselineEvidenceSpec,
    Control,
    ControlState,
    Framework,
    Organization,
    OrgProduct,
    Product,
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
        return f"http://fake-storage/{key}"

    def delete_file(self, key: str) -> None:
        self.files.pop(key, None)

    def get_bytes(self, key: str) -> bytes:
        return self.files.get(key, b"")


@pytest.fixture
def admin_client(db_session, fake_msp_admin):
    app.dependency_overrides[get_session] = _app_session(db_session)
    app.dependency_overrides[get_current_user] = _authed(db_session, fake_msp_admin)
    app.dependency_overrides[get_storage_client] = lambda: InMemoryStorageClient()
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def poc_client(db_session):
    poc = _make_fake_user(role="customer_poc")
    app.dependency_overrides[get_session] = _app_session(db_session)
    app.dependency_overrides[get_current_user] = _authed(db_session, poc)
    app.dependency_overrides[get_storage_client] = lambda: InMemoryStorageClient()
    yield TestClient(app)
    app.dependency_overrides.clear()


def _seed_framework_and_control(db_session) -> dict:
    fw = db_session.scalars(
        select(Framework).where(Framework.key == "nist-800-171-r2")
    ).first()
    if fw is None:
        fw = Framework(key="nist-800-171-r2", name="NIST r2", version="r2")
        db_session.add(fw)
        db_session.flush()
    ctrl = Control(
        framework_id=fw.id,
        control_id=f"AC.TEST-{uuid.uuid4().hex[:6]}",
        family="AC",
        title="Test Control",
        requirement_text="Do the thing.",
        sprs_weight=1,
        sequence_order=1,
    )
    db_session.add(ctrl)
    db_session.flush()
    obj = AssessmentObjective(control_id=ctrl.id, objective_key="a", text="Objective a.")
    db_session.add(obj)
    db_session.flush()
    return {"fw": fw, "ctrl": ctrl, "obj": obj}


def _valid_yaml(key: str, control_id: str) -> bytes:
    return f"""
product:
  key: {key}
  name: Test Product
  provider: Acme
  category: ESP
  asset_type: SPA
  role: A test product.
controls:
  - control: {control_id}
    objectives: [a]
    classification: provider_satisfies
    coverage_basis: customer_system
    candidate_state: pending_evidence
    evidence:
      - {{artifact: "Config export", type: export}}
""".encode()


def _seed_product(db_session, *, published: bool = False) -> Product:
    seed = _seed_framework_and_control(db_session)
    product = Product(
        framework_id=seed["fw"].id,
        key=f"prod-{uuid.uuid4().hex[:8]}",
        name="Test Tool",
        provider="Acme",
        category="ESP",
        asset_type="SPA",
        role="Test.",
        is_published=published,
    )
    db_session.add(product)
    db_session.flush()
    bc = BaselineControl(
        product_id=product.id,
        control_id=seed["ctrl"].id,
        objectives=["a"],
        classification="provider_satisfies",
        coverage_basis="customer_system",
        candidate_state="pending_evidence",
    )
    db_session.add(bc)
    db_session.flush()
    db_session.add(
        BaselineEvidenceSpec(
            baseline_control_id=bc.id, artifact_description="Config export", evidence_type="export"
        )
    )
    db_session.flush()
    return product


# ---------------------------------------------------------------------------
# Library view + detail
# ---------------------------------------------------------------------------


def test_library_shows_both_published_and_unpublished(admin_client, db_session):
    """Unlike the tenant Tools panel, the admin library view is not
    filtered by is_published -- an admin needs to see and manage
    unpublished products too."""
    _seed_product(db_session, published=True)
    _seed_product(db_session, published=False)
    r = admin_client.get("/admin/products")
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 2
    assert {p["is_published"] for p in body} == {True, False}


def test_product_detail_shows_baseline_mapping(admin_client, db_session):
    product = _seed_product(db_session, published=True)
    r = admin_client.get(f"/admin/products/{product.id}")
    assert r.status_code == 200
    body = r.json()
    assert body["key"] == product.key
    assert len(body["baseline_controls"]) == 1
    bc = body["baseline_controls"][0]
    assert bc["classification"] == "provider_satisfies"
    assert bc["coverage_basis"] == "customer_system"
    assert len(bc["evidence_specs"]) == 1
    assert bc["evidence_specs"][0]["artifact_description"] == "Config export"


def test_product_detail_404_for_unknown_id(admin_client):
    r = admin_client.get(f"/admin/products/{uuid.uuid4()}")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Footprint (cross-org SECURITY DEFINER read)
# ---------------------------------------------------------------------------


def test_footprint_shows_every_org_regardless_of_current_org(admin_client, db_session):
    product = _seed_product(db_session, published=True)
    org_a = Organization(name=f"FootprintA-{uuid.uuid4().hex[:6]}")
    org_b = Organization(name=f"FootprintB-{uuid.uuid4().hex[:6]}")
    db_session.add_all([org_a, org_b])
    db_session.flush()

    db_session.add_all(
        [
            OrgProduct(org_id=org_a.id, product_id=product.id, status="active"),
            OrgProduct(org_id=org_b.id, product_id=product.id, status="candidate"),
        ]
    )
    db_session.flush()

    r = admin_client.get(f"/admin/products/{product.id}/footprint")
    assert r.status_code == 200
    rows = {row["org_name"]: row["status"] for row in r.json()}
    assert rows[org_a.name] == "active"
    assert rows[org_b.name] == "candidate"


# ---------------------------------------------------------------------------
# Import: dry-run
# ---------------------------------------------------------------------------


def test_import_dry_run_valid_new_product(admin_client, db_session):
    seed = _seed_framework_and_control(db_session)
    yaml_bytes = _valid_yaml("new-tool", seed["ctrl"].control_id)
    r = admin_client.post(
        "/admin/products/import/dry-run",
        files={"file": ("new-tool.yaml", yaml_bytes, "application/x-yaml")},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["problems"] == []
    assert body["product_is_new"] is True
    assert len(body["control_changes"]) == 1
    assert body["control_changes"][0]["change_type"] == "new"
    assert body["affected_org_count"] == 0


def test_import_dry_run_reports_every_problem_at_once(admin_client, db_session):
    seed = _seed_framework_and_control(db_session)
    bad_yaml = f"""
product:
  key: bad-tool
  name: Bad Tool
  provider: Acme
  category: ESP
controls:
  - control: {seed["ctrl"].control_id}
    objectives: [z]
    classification: not_a_real_classification
    coverage_basis: also_not_real
  - control: NOT.A.REAL.CONTROL
    classification: customer_owns
    evidence:
      - {{artifact: "should not exist", type: export}}
""".encode()
    r = admin_client.post(
        "/admin/products/import/dry-run",
        files={"file": ("bad-tool.yaml", bad_yaml, "application/x-yaml")},
    )
    assert r.status_code == 200
    problems = r.json()["problems"]
    # Every distinct problem class must be reported in the SAME response,
    # not just the first one encountered -- the task's own "report every
    # problem at once" requirement.
    assert any("classification" in p for p in problems)
    assert any("coverage_basis" in p for p in problems)
    assert any("objective key" in p and "z" in p for p in problems)
    assert any("unknown control id" in p for p in problems)
    assert any("customer_owns must not carry evidence" in p for p in problems)


def test_import_dry_run_warns_on_affected_orgs(admin_client, db_session):
    product = _seed_product(db_session, published=True)
    org = Organization(name=f"AffectedOrg-{uuid.uuid4().hex[:6]}")
    db_session.add(org)
    db_session.flush()
    db_session.add(OrgProduct(org_id=org.id, product_id=product.id, status="active"))
    db_session.flush()

    # Re-import the SAME product key with a changed classification.
    bc = db_session.scalars(
        select(BaselineControl).where(BaselineControl.product_id == product.id)
    ).first()
    ctrl_row = db_session.get(Control, bc.control_id)
    yaml_bytes = f"""
product:
  key: {product.key}
  name: Test Tool
  provider: Acme
  category: ESP
controls:
  - control: {ctrl_row.control_id}
    objectives: [a]
    classification: shared
    coverage_basis: customer_system
""".encode()
    r = admin_client.post(
        "/admin/products/import/dry-run",
        files={"file": ("reimport.yaml", yaml_bytes, "application/x-yaml")},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["affected_org_count"] == 1
    assert body["affected_org_names"] == [org.name]
    changed = [c for c in body["control_changes"] if c["change_type"] == "changed"]
    assert len(changed) == 1
    assert changed[0]["field_diffs"]["classification"] == ["provider_satisfies", "shared"]


# ---------------------------------------------------------------------------
# Import: dry-run-structured -- same validate()/build_preview() logic as
# dry-run above, but a JSON body in and out instead of a YAML file, for the
# per-control review table (ToolImportWizard.tsx, documents mode).
# ---------------------------------------------------------------------------


def _valid_structured_body(key: str, control_id: str) -> dict:
    return {
        "product": {
            "key": key,
            "name": "Test Product",
            "provider": "Acme",
            "category": "ESP",
            "asset_type": "SPA",
            "role": "A test product.",
        },
        "controls": [
            {
                "control": control_id,
                "objectives": ["a"],
                "classification": "provider_satisfies",
                "candidate_state": "pending_evidence",
                "evidence": [{"artifact": "Config export", "type": "export"}],
                # coverage_basis deliberately omitted -- the case this
                # endpoint exists to surface row-by-row.
            }
        ],
    }


def test_dry_run_structured_flags_missing_coverage_basis_with_row_and_field(
    admin_client, db_session
):
    seed = _seed_framework_and_control(db_session)
    body = _valid_structured_body("structured-tool", seed["ctrl"].control_id)
    r = admin_client.post("/admin/products/import/dry-run-structured", json=body)
    assert r.status_code == 200
    out = r.json()
    assert out["preview"]["problems"], "coverage_basis is unset -- must be flagged"
    row_problems = out["preview"]["row_problems"]
    assert any(
        p["row_index"] == 0 and p["field"] == "coverage_basis" for p in row_problems
    ), row_problems
    assert len(out["controls"]) == 1
    row = out["controls"][0]
    assert row["row_index"] == 0
    assert row["control"] == [seed["ctrl"].control_id]
    assert row["coverage_basis"] is None
    assert row["classification"] == "provider_satisfies"
    assert out["product"]["key"] == "structured-tool"


def test_dry_run_structured_edit_clears_the_flagged_row(admin_client, db_session):
    seed = _seed_framework_and_control(db_session)
    body = _valid_structured_body("structured-tool-2", seed["ctrl"].control_id)
    body["controls"][0]["coverage_basis"] = "customer_system"
    r = admin_client.post("/admin/products/import/dry-run-structured", json=body)
    assert r.status_code == 200
    out = r.json()
    assert out["preview"]["problems"] == []
    assert out["preview"]["row_problems"] == []
    assert out["controls"][0]["coverage_basis"] == "customer_system"


def test_dry_run_structured_malformed_classification_still_returns_the_row(
    admin_client, db_session
):
    """A row with an invalid enum value must still render -- a reviewer
    fixes the value via the row, not through a dropped/crashed response."""
    seed = _seed_framework_and_control(db_session)
    body = _valid_structured_body("structured-tool-3", seed["ctrl"].control_id)
    body["controls"][0]["classification"] = "not_a_real_classification"
    r = admin_client.post("/admin/products/import/dry-run-structured", json=body)
    assert r.status_code == 200
    out = r.json()
    assert len(out["controls"]) == 1
    assert out["controls"][0]["classification"] == "not_a_real_classification"
    assert any(p["field"] == "classification" for p in out["preview"]["row_problems"])


def test_dry_run_structured_missing_framework_returns_409(admin_client, db_session):
    # No _seed_framework_and_control() call -- the nist-800-171-r2
    # framework genuinely doesn't exist in this test's (rolled-back-per-
    # test) database.
    r = admin_client.post(
        "/admin/products/import/dry-run-structured",
        json={"product": {}, "controls": []},
    )
    assert r.status_code == 409


# ---------------------------------------------------------------------------
# Import: apply
# ---------------------------------------------------------------------------


def test_import_apply_creates_rows_unpublished(admin_client, db_session):
    seed = _seed_framework_and_control(db_session)
    yaml_bytes = _valid_yaml("applied-tool", seed["ctrl"].control_id)
    r = admin_client.post(
        "/admin/products/import/apply",
        files={"file": ("applied-tool.yaml", yaml_bytes, "application/x-yaml")},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["baseline_controls"] == 1
    assert body["evidence_specs"] == 1

    product = db_session.scalars(select(Product).where(Product.key == "applied-tool")).one()
    assert product.is_published is False


def test_import_apply_invalid_yaml_writes_nothing(admin_client, db_session):
    _seed_framework_and_control(db_session)
    bad_yaml = b"""
product:
  key: never-created
controls:
  - control: NOT.A.REAL.CONTROL
    classification: bogus
"""
    r = admin_client.post(
        "/admin/products/import/apply",
        files={"file": ("bad.yaml", bad_yaml, "application/x-yaml")},
    )
    assert r.status_code == 422
    assert (
        db_session.scalars(select(Product).where(Product.key == "never-created")).first()
        is None
    )


def test_reimport_resets_is_published_to_false(admin_client, db_session):
    product = _seed_product(db_session, published=True)
    bc = db_session.scalars(
        select(BaselineControl).where(BaselineControl.product_id == product.id)
    ).first()
    ctrl_row = db_session.get(Control, bc.control_id)
    yaml_bytes = _valid_yaml(product.key, ctrl_row.control_id)
    r = admin_client.post(
        "/admin/products/import/apply",
        files={"file": ("reimport.yaml", yaml_bytes, "application/x-yaml")},
    )
    assert r.status_code == 201
    db_session.refresh(product)
    assert product.is_published is False, (
        "re-importing an already-published product must force a fresh review, "
        "not silently carry the old publish decision forward"
    )


# ---------------------------------------------------------------------------
# Publish / unpublish enforcement
# ---------------------------------------------------------------------------


def test_unpublished_product_invisible_and_unactivatable(admin_client, db_session, fake_msp_admin):
    product = _seed_product(db_session, published=False)
    org = Organization(id=fake_msp_admin.org_id, name=f"UnpubOrg-{uuid.uuid4().hex[:6]}")
    db_session.add(org)
    db_session.flush()
    _grant(db_session, fake_msp_admin)
    assessment = start_assessment(
        db_session, org_id=org.id, framework_id=product.framework_id, name="Unpub Test"
    )
    db_session.flush()

    # Invisible in the tenant Tools panel.
    listed = admin_client.get(f"/orgs/{org.id}/assessments/{assessment.id}/products").json()
    assert listed == []

    # Cannot be activated via the real HTTP endpoint either.
    r = admin_client.post(
        f"/orgs/{org.id}/assessments/{assessment.id}/products/{product.id}/activate"
    )
    assert r.status_code == 404
    assert (
        db_session.scalars(
            select(OrgProduct).where(OrgProduct.product_id == product.id)
        ).first()
        is None
    )


def test_publish_makes_it_visible_and_activatable(admin_client, db_session, fake_msp_admin):
    product = _seed_product(db_session, published=False)
    org = Organization(id=fake_msp_admin.org_id, name=f"PubOrg-{uuid.uuid4().hex[:6]}")
    db_session.add(org)
    db_session.flush()
    _grant(db_session, fake_msp_admin)
    assessment = start_assessment(
        db_session, org_id=org.id, framework_id=product.framework_id, name="Pub Test"
    )
    db_session.flush()

    pub = admin_client.post(f"/admin/products/{product.id}/publish")
    assert pub.status_code == 200
    assert pub.json()["is_published"] is True

    listed = admin_client.get(f"/orgs/{org.id}/assessments/{assessment.id}/products").json()
    assert len(listed) == 1

    act = admin_client.post(
        f"/orgs/{org.id}/assessments/{assessment.id}/products/{product.id}/activate"
    )
    assert act.status_code == 200


def test_unpublish_hides_from_tenant_without_disturbing_existing_activation(
    admin_client, db_session, fake_msp_admin
):
    """The most likely place to do silent damage: unpublishing must not
    touch an org's existing OrgProduct row or the control_state the magic
    loop already set for it."""
    product = _seed_product(db_session, published=True)
    org = Organization(id=fake_msp_admin.org_id, name=f"UnpubExistingOrg-{uuid.uuid4().hex[:6]}")
    db_session.add(org)
    db_session.flush()
    _grant(db_session, fake_msp_admin)
    assessment = start_assessment(
        db_session, org_id=org.id, framework_id=product.framework_id, name="Unpub Existing"
    )
    db_session.flush()
    activate_org_product(
        db_session, org_id=org.id, product_id=product.id, assessment_id=assessment.id
    )
    db_session.flush()

    before_states = {
        cs.objective_id: (cs.status, cs.responsibility)
        for cs in db_session.scalars(
            select(ControlState).where(ControlState.assessment_id == assessment.id)
        )
    }
    op_before = db_session.scalars(
        select(OrgProduct).where(OrgProduct.product_id == product.id)
    ).one()
    assert op_before.status == "active"

    unpub = admin_client.post(f"/admin/products/{product.id}/unpublish")
    assert unpub.status_code == 200

    # Gone from the tenant's list...
    listed = admin_client.get(f"/orgs/{org.id}/assessments/{assessment.id}/products").json()
    assert listed == []

    # ...but the existing org_product row and every control_state are
    # byte-for-byte unchanged.
    op_after = db_session.scalars(
        select(OrgProduct).where(OrgProduct.product_id == product.id)
    ).one()
    assert op_after.status == "active"
    assert op_after.activated_at == op_before.activated_at

    after_states = {
        cs.objective_id: (cs.status, cs.responsibility)
        for cs in db_session.scalars(
            select(ControlState).where(ControlState.assessment_id == assessment.id)
        )
    }
    assert after_states == before_states


# ---------------------------------------------------------------------------
# Import: tightened coverage_basis validation (must be explicit for
# provider_satisfies/shared; still defaulted for customer_owns)
# ---------------------------------------------------------------------------


def test_dry_run_requires_coverage_basis_for_provider_satisfies(admin_client, db_session):
    seed = _seed_framework_and_control(db_session)
    yaml_bytes = f"""
product:
  key: no-coverage-basis
  name: Test Product
  provider: Acme
  category: ESP
controls:
  - control: {seed["ctrl"].control_id}
    objectives: [a]
    classification: provider_satisfies
    evidence:
      - {{artifact: "Config export", type: export}}
""".encode()
    r = admin_client.post(
        "/admin/products/import/dry-run",
        files={"file": ("x.yaml", yaml_bytes, "application/x-yaml")},
    )
    assert r.status_code == 200
    problems = r.json()["problems"]
    assert any(
        "coverage_basis must be explicitly set" in p for p in problems
    ), problems


def test_dry_run_allows_missing_coverage_basis_for_customer_owns(admin_client, db_session):
    seed = _seed_framework_and_control(db_session)
    yaml_bytes = f"""
product:
  key: customer-owns-no-basis
  name: Test Product
  provider: Acme
  category: ESP
controls:
  - control: {seed["ctrl"].control_id}
    classification: customer_owns
    note: "Customer owns this."
""".encode()
    r = admin_client.post(
        "/admin/products/import/dry-run",
        files={"file": ("x.yaml", yaml_bytes, "application/x-yaml")},
    )
    assert r.status_code == 200
    assert r.json()["problems"] == []


# ---------------------------------------------------------------------------
# Document ingestion -> existing review flow (no second ingest path)
# ---------------------------------------------------------------------------


def _stub_ingest_response(control_id: str) -> str:
    return json.dumps({
        "product": {
            "name": "Ingested Tool",
            "provider": "Acme",
            "role": "Does a thing.",
            "assumed_config": ["Agent deployed"],
            "source_docs": ["vendor_crm.pdf"],
        },
        "controls": [
            {
                "control": control_id,
                "objectives": ["a"],
                "classification": "provider_satisfies",
                "provider_contribution": "Does the thing.",
                "customer_action": "Configure the thing.",
                "evidence": [{"artifact": "Config export", "type": "export"}],
                "candidate_state": "pending_evidence",
            }
        ],
    })


class _StubIngestProvider(AIProvider):
    def __init__(self, response: str) -> None:
        self._response = response

    def complete(self, system, user, *, max_tokens=8192):
        return self._response

    @property
    def identity(self) -> str:
        return "anthropic:stub-model"


def _fake_pdf_bytes() -> bytes:
    return b"%PDF-1.4 fake content for magic-byte check"


def _real_pdf_bytes(
    text: str = (
        "Some real extractable text for a legitimate document, long enough "
        "to clear the ingestion threshold."
    ),
) -> bytes:
    """A genuinely valid, parseable PDF with a real text content stream --
    built with pypdf alone (already a real dependency; no reportlab or
    similar needed) so input-hardening tests can truncate/corrupt/encrypt
    a real file rather than asserting against synthetic bytes."""
    import io

    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    w = PdfWriter()
    page = w.add_blank_page(width=300, height=200)
    stream = DecodedStreamObject()
    stream.set_data(f"BT /F1 12 Tf 20 150 Td ({text}) Tj ET".encode())
    font_dict = DictionaryObject()
    font_dict[NameObject("/Type")] = NameObject("/Font")
    font_dict[NameObject("/Subtype")] = NameObject("/Type1")
    font_dict[NameObject("/BaseFont")] = NameObject("/Helvetica")
    resources = DictionaryObject()
    font_res = DictionaryObject()
    font_res[NameObject("/F1")] = w._add_object(font_dict)
    resources[NameObject("/Font")] = font_res
    page[NameObject("/Resources")] = resources
    page[NameObject("/Contents")] = w._add_object(stream)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def _truncated_pdf_bytes() -> bytes:
    full = _real_pdf_bytes()
    return full[: len(full) // 2]


def _blank_pdf_bytes() -> bytes:
    """A structurally valid PDF with no text content at all -- the same
    symptom a scanned-image-only page produces (extract_text() yields 0
    characters)."""
    import io

    from pypdf import PdfWriter

    w = PdfWriter()
    w.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def _encrypted_pdf_bytes() -> bytes:
    import io

    from pypdf import PdfWriter

    w = PdfWriter()
    w.add_blank_page(width=200, height=200)
    w.encrypt("secret123")
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


# Legacy Word 97-2003 .doc (OLE2/Compound File Binary) magic bytes.
_LEGACY_DOC_BYTES = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 200


# extract_text() is patched to return this in every test below that isn't
# specifically testing extraction itself -- long enough to clear
# importers/document.py's _MIN_EXTRACTED_TEXT_CHARS guard (added
# alongside the input-hardening slice) so these tests keep exercising
# what they're actually about (AI response handling, RBAC, etc.) instead
# of tripping a same-shaped-but-unrelated 422 from that guard.
_STUB_EXTRACTED_TEXT = (
    "Stub extracted document text, long enough to clear the minimum-"
    "extracted-text guard so these tests exercise what they're actually "
    "testing."
)


def test_ingest_from_documents_requires_ai_provider_configured(admin_client, db_session):
    """No "ai" IntegrationConnection row configured (the untouched default
    in tests) must degrade cleanly through this endpoint too -- a specific
    422, never a 500.

    extract_text() is patched here even though this test is "about" the
    missing AI provider: ingest_document() extracts text from every
    document BEFORE it ever consults the provider, so a real PDF parse
    would run first regardless -- caught live during bench verification
    (2026-09-14), where this test 500'd on real pypdf output for
    _fake_pdf_bytes()'s not-actually-a-PDF content once the ai extras
    were actually installed in the image for the first time.
    """
    _seed_framework_and_control(db_session)
    with patch("app.importers.document.extract_text", return_value=_STUB_EXTRACTED_TEXT):
        r = admin_client.post(
            "/admin/products/import/from-documents",
            files={"files": ("crm.pdf", _fake_pdf_bytes(), "application/pdf")},
            data={"product_key": "ingested-tool-noai"},
        )
    assert r.status_code == 422
    assert "No AI provider configured" in r.json()["detail"]


def test_ingest_from_documents_returns_yaml_and_preview_needing_coverage_basis(
    admin_client, db_session
):
    seed = _seed_framework_and_control(db_session)
    stub = _StubIngestProvider(_stub_ingest_response(seed["ctrl"].control_id))
    with (
        patch("app.routers.admin_products.get_ai_provider", return_value=stub),
        patch("app.importers.document.extract_text", return_value=_STUB_EXTRACTED_TEXT),
    ):
        r = admin_client.post(
            "/admin/products/import/from-documents",
            files={"files": ("crm.pdf", _fake_pdf_bytes(), "application/pdf")},
            data={"product_key": "ingested-tool"},
        )
    assert r.status_code == 200
    body = r.json()
    assert "ingested-tool" in body["yaml"]
    assert "coverage_basis" not in body["yaml"], (
        "the ingestion pipeline must never fill this in itself"
    )
    assert any("coverage_basis" in p for p in body["preview"]["problems"]), (
        "a reviewer must be forced to set coverage_basis before this could "
        "ever be applied -- same validate() gate a hand-authored YAML hits"
    )
    assert body["preview"]["product_is_new"] is True
    # Structured shape behind the yaml string, for the per-control review
    # table -- same content, no YAML parsing needed on the frontend.
    assert body["product"]["key"] == "ingested-tool"
    assert body["product"]["name"] == "Ingested Tool"
    assert len(body["controls"]) == 1
    row = body["controls"][0]
    assert row["control"] == [seed["ctrl"].control_id]
    assert row["classification"] == "provider_satisfies"
    assert row["coverage_basis"] is None
    assert any(
        p["row_index"] == 0 and p["field"] == "coverage_basis"
        for p in body["preview"]["row_problems"]
    )


def test_ingest_from_documents_apply_lands_unpublished_invisible_with_provenance(
    admin_client, db_session, fake_msp_admin
):
    """End to end: ingest -> reviewer fills in coverage_basis -> apply ->
    unpublished and invisible to tenants until published, with permanent
    AI provenance recorded and visible in the tool detail view."""
    seed = _seed_framework_and_control(db_session)
    stub = _StubIngestProvider(_stub_ingest_response(seed["ctrl"].control_id))
    with (
        patch("app.routers.admin_products.get_ai_provider", return_value=stub),
        patch("app.importers.document.extract_text", return_value=_STUB_EXTRACTED_TEXT),
    ):
        r = admin_client.post(
            "/admin/products/import/from-documents",
            files={"files": ("crm.pdf", _fake_pdf_bytes(), "application/pdf")},
            data={"product_key": "ingested-tool-2"},
        )
    assert r.status_code == 200

    import yaml as _yaml

    data_dict = _yaml.safe_load(r.json()["yaml"])
    for c in data_dict["controls"]:
        c["coverage_basis"] = "customer_system"
    reviewed_yaml = _yaml.safe_dump(data_dict, sort_keys=False).encode()

    apply_r = admin_client.post(
        "/admin/products/import/apply",
        files={"file": ("reviewed.yaml", reviewed_yaml, "application/x-yaml")},
    )
    assert apply_r.status_code == 201

    product = db_session.scalars(
        select(Product).where(Product.key == "ingested-tool-2")
    ).one()
    assert product.is_published is False
    assert product.ai_generated_at is not None
    assert product.ai_generated_model == "anthropic:stub-model"

    detail = admin_client.get(f"/admin/products/{product.id}").json()
    assert detail["ai_generated_at"] is not None
    assert detail["ai_generated_model"] == "anthropic:stub-model"

    # Invisible to a tenant via the real endpoint until published, exactly
    # like a hand-authored import (baseline_import.py's reset_published).
    org = Organization(id=fake_msp_admin.org_id, name=f"IngestOrg-{uuid.uuid4().hex[:6]}")
    db_session.add(org)
    db_session.flush()
    _grant(db_session, fake_msp_admin)
    assessment = start_assessment(
        db_session, org_id=org.id, framework_id=product.framework_id, name="Ingest Test"
    )
    db_session.flush()
    listed = admin_client.get(
        f"/orgs/{org.id}/assessments/{assessment.id}/products"
    ).json()
    assert listed == []


def test_ai_provenance_survives_a_later_hand_authored_reimport(admin_client, db_session):
    """Once a product is known to be AI-generated, that fact is permanent
    -- a later re-import (even a plain hand-authored YAML that says
    nothing about provenance) must not clear ai_generated_at/model.
    Mirrors AssessmentObjective.practitioner_notes_generated_at/_model's
    own philosophy; verified against the real write path
    (seeds/baselines.py:_seed_product), not just the domain type."""
    seed = _seed_framework_and_control(db_session)
    stub = _StubIngestProvider(_stub_ingest_response(seed["ctrl"].control_id))
    with (
        patch("app.routers.admin_products.get_ai_provider", return_value=stub),
        patch("app.importers.document.extract_text", return_value=_STUB_EXTRACTED_TEXT),
    ):
        r = admin_client.post(
            "/admin/products/import/from-documents",
            files={"files": ("crm.pdf", _fake_pdf_bytes(), "application/pdf")},
            data={"product_key": "ingested-tool-provenance"},
        )
    assert r.status_code == 200

    import yaml as _yaml

    data_dict = _yaml.safe_load(r.json()["yaml"])
    for c in data_dict["controls"]:
        c["coverage_basis"] = "customer_system"
    admin_client.post(
        "/admin/products/import/apply",
        files={
            "file": (
                "reviewed.yaml",
                _yaml.safe_dump(data_dict, sort_keys=False).encode(),
                "application/x-yaml",
            )
        },
    )
    product = db_session.scalars(
        select(Product).where(Product.key == "ingested-tool-provenance")
    ).one()
    original_generated_at = product.ai_generated_at
    original_generated_model = product.ai_generated_model
    assert original_generated_at is not None
    assert original_generated_model == "anthropic:stub-model"

    # A human re-imports a hand-authored YAML for the SAME product key --
    # no ai_generated_at/model in this file at all.
    hand_yaml = _valid_yaml("ingested-tool-provenance", seed["ctrl"].control_id)
    reimport = admin_client.post(
        "/admin/products/import/apply",
        files={"file": ("hand-authored.yaml", hand_yaml, "application/x-yaml")},
    )
    assert reimport.status_code == 201

    db_session.refresh(product)
    assert product.ai_generated_at == original_generated_at
    assert product.ai_generated_model == original_generated_model

    detail = admin_client.get(f"/admin/products/{product.id}").json()
    assert detail["ai_generated_at"] is not None
    assert detail["ai_generated_model"] == "anthropic:stub-model"


def test_ingest_from_documents_reimport_warns_on_affected_orgs(
    admin_client, db_session, fake_msp_admin
):
    """Re-running ingestion against a product that already has tenants
    active goes through the SAME dry-run path a hand-authored re-import
    does -- the affected-tenant warning fires here too, not just for
    plain YAML uploads. Guards against a second, silently-more-permissive
    ingest path ever being built."""
    seed = _seed_framework_and_control(db_session)
    stub = _StubIngestProvider(_stub_ingest_response(seed["ctrl"].control_id))
    with (
        patch("app.routers.admin_products.get_ai_provider", return_value=stub),
        patch("app.importers.document.extract_text", return_value=_STUB_EXTRACTED_TEXT),
    ):
        r = admin_client.post(
            "/admin/products/import/from-documents",
            files={"files": ("crm.pdf", _fake_pdf_bytes(), "application/pdf")},
            data={"product_key": "ingested-tool-affected"},
        )
    import yaml as _yaml

    data_dict = _yaml.safe_load(r.json()["yaml"])
    for c in data_dict["controls"]:
        c["coverage_basis"] = "customer_system"
    admin_client.post(
        "/admin/products/import/apply",
        files={
            "file": (
                "reviewed.yaml",
                _yaml.safe_dump(data_dict, sort_keys=False).encode(),
                "application/x-yaml",
            )
        },
    )
    product = db_session.scalars(
        select(Product).where(Product.key == "ingested-tool-affected")
    ).one()
    product.is_published = True
    org = Organization(name=f"AffectedIngestOrg-{uuid.uuid4().hex[:6]}")
    db_session.add(org)
    db_session.flush()
    db_session.add(OrgProduct(org_id=org.id, product_id=product.id, status="active"))
    db_session.flush()

    with (
        patch("app.routers.admin_products.get_ai_provider", return_value=stub),
        patch("app.importers.document.extract_text", return_value=_STUB_EXTRACTED_TEXT),
    ):
        r2 = admin_client.post(
            "/admin/products/import/from-documents",
            files={"files": ("crm-v2.pdf", _fake_pdf_bytes(), "application/pdf")},
            data={"product_key": "ingested-tool-affected"},
        )
    assert r2.status_code == 200
    preview = r2.json()["preview"]
    assert preview["affected_org_count"] == 1
    assert preview["affected_org_names"] == [org.name]


def test_ingest_from_documents_rejects_too_many_files(admin_client, db_session):
    _seed_framework_and_control(db_session)
    r = admin_client.post(
        "/admin/products/import/from-documents",
        files=[
            ("files", ("a.pdf", _fake_pdf_bytes(), "application/pdf")),
            ("files", ("b.pdf", _fake_pdf_bytes(), "application/pdf")),
            ("files", ("c.pdf", _fake_pdf_bytes(), "application/pdf")),
        ],
        data={"product_key": "too-many"},
    )
    assert r.status_code == 422


def test_ingest_from_documents_rejects_bad_extension(admin_client, db_session):
    _seed_framework_and_control(db_session)
    r = admin_client.post(
        "/admin/products/import/from-documents",
        files={"files": ("matrix.csv", b"a,b,c", "text/csv")},
        data={"product_key": "bad-ext"},
    )
    assert r.status_code == 415


# ---------------------------------------------------------------------------
# Document attachments
# ---------------------------------------------------------------------------


def test_document_upload_list_download_delete_roundtrip(admin_client, db_session):
    product = _seed_product(db_session, published=True)

    upload = admin_client.post(
        f"/admin/products/{product.id}/documents",
        files={"file": ("baseline.pdf", b"%PDF-1.4 fake", "application/pdf")},
        data={"title": "MSP Baseline Doc", "kind": "baseline_doc", "source_docs_ref": "v1.0"},
    )
    assert upload.status_code == 201
    doc = upload.json()
    assert doc["title"] == "MSP Baseline Doc"
    assert doc["kind"] == "baseline_doc"
    assert doc["source_docs_ref"] == "v1.0"

    listed = admin_client.get(f"/admin/products/{product.id}/documents").json()
    assert len(listed) == 1

    download = admin_client.get(
        f"/admin/products/{product.id}/documents/{doc['id']}/download", follow_redirects=False
    )
    assert download.status_code == 302
    assert "fake-storage" in download.headers["location"]

    delete = admin_client.delete(f"/admin/products/{product.id}/documents/{doc['id']}")
    assert delete.status_code == 204
    assert admin_client.get(f"/admin/products/{product.id}/documents").json() == []


def test_document_upload_rejects_bad_magic_bytes(admin_client, db_session):
    product = _seed_product(db_session, published=True)
    r = admin_client.post(
        f"/admin/products/{product.id}/documents",
        files={"file": ("fake.pdf", b"not actually a pdf", "application/pdf")},
        data={"kind": "other"},
    )
    assert r.status_code == 415


# ---------------------------------------------------------------------------
# Role gate
# ---------------------------------------------------------------------------


def test_customer_poc_gets_403_on_every_endpoint(poc_client, db_session):
    product = _seed_product(db_session, published=True)
    assert poc_client.get("/admin/products").status_code == 403
    assert poc_client.get(f"/admin/products/{product.id}").status_code == 403
    assert poc_client.get(f"/admin/products/{product.id}/footprint").status_code == 403
    assert poc_client.post(f"/admin/products/{product.id}/publish").status_code == 403
    assert poc_client.post(f"/admin/products/{product.id}/unpublish").status_code == 403
    assert poc_client.get(f"/admin/products/{product.id}/documents").status_code == 403
    r = poc_client.post(
        "/admin/products/import/dry-run",
        files={"file": ("x.yaml", b"product: {}", "application/x-yaml")},
    )
    assert r.status_code == 403
    r = poc_client.post(
        "/admin/products/import/dry-run-structured",
        json={"product": {}, "controls": []},
    )
    assert r.status_code == 403
    r = poc_client.post(
        "/admin/products/import/from-documents",
        files={"files": ("x.pdf", b"%PDF-1.4", "application/pdf")},
        data={"product_key": "x"},
    )
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# Input hardening: extraction-path failures produce a specific 422, never
# a raw 500. Real bytes, real extract_text() -- extract_text is NOT
# patched in this section, unlike every test above, since the whole point
# is exercising the actual pypdf/python-docx failure paths end to end
# through the real HTTP endpoint.
# ---------------------------------------------------------------------------


class _AssertNeverCalledProvider(AIProvider):
    """Fails loudly if the AI provider is ever reached -- every fixture in
    this section should be rejected during extraction, before any
    completion call, so reaching .complete() at all is itself the bug."""

    def complete(self, system, user, *, max_tokens=8192):
        raise AssertionError("AI provider must not be called for a document that fails extraction")


def test_ingest_from_documents_truncated_pdf_returns_422_not_500(admin_client, db_session):
    _seed_framework_and_control(db_session)
    with patch(
        "app.routers.admin_products.get_ai_provider",
        return_value=_AssertNeverCalledProvider(),
    ):
        r = admin_client.post(
            "/admin/products/import/from-documents",
            files={"files": ("crm.pdf", _truncated_pdf_bytes(), "application/pdf")},
            data={"product_key": "truncated-test"},
        )
    assert r.status_code == 422
    assert "crm.pdf" in r.json()["detail"]
    assert "corrupt or truncated" in r.json()["detail"]


def test_ingest_from_documents_password_protected_pdf_returns_422(admin_client, db_session):
    _seed_framework_and_control(db_session)
    with patch(
        "app.routers.admin_products.get_ai_provider",
        return_value=_AssertNeverCalledProvider(),
    ):
        r = admin_client.post(
            "/admin/products/import/from-documents",
            files={"files": ("crm.pdf", _encrypted_pdf_bytes(), "application/pdf")},
            data={"product_key": "encrypted-test"},
        )
    assert r.status_code == 422
    assert "crm.pdf" in r.json()["detail"]
    assert "password-protected" in r.json()["detail"]


def test_ingest_from_documents_scanned_pdf_with_no_text_returns_422(admin_client, db_session):
    _seed_framework_and_control(db_session)
    with patch(
        "app.routers.admin_products.get_ai_provider",
        return_value=_AssertNeverCalledProvider(),
    ):
        r = admin_client.post(
            "/admin/products/import/from-documents",
            files={"files": ("crm.pdf", _blank_pdf_bytes(), "application/pdf")},
            data={"product_key": "scanned-test"},
        )
    assert r.status_code == 422
    assert "crm.pdf" in r.json()["detail"]
    assert "extracted only 0 character" in r.json()["detail"]


def test_ingest_from_documents_legacy_doc_as_docx_returns_422(admin_client, db_session):
    _seed_framework_and_control(db_session)
    with patch(
        "app.routers.admin_products.get_ai_provider",
        return_value=_AssertNeverCalledProvider(),
    ):
        r = admin_client.post(
            "/admin/products/import/from-documents",
            files={
                "files": (
                    "crm.docx",
                    _LEGACY_DOC_BYTES,
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
            },
            data={"product_key": "legacy-doc-test"},
        )
    assert r.status_code == 422
    assert "crm.docx" in r.json()["detail"]
    assert "legacy Word 97-2003 .doc" in r.json()["detail"]


def test_ingest_from_documents_names_which_of_two_files_failed(admin_client, db_session):
    """Two documents, only the second is broken -- the message must name
    that one specifically, not just say "one of your files"."""
    _seed_framework_and_control(db_session)
    with patch(
        "app.routers.admin_products.get_ai_provider",
        return_value=_AssertNeverCalledProvider(),
    ):
        r = admin_client.post(
            "/admin/products/import/from-documents",
            files=[
                (
                    "files",
                    (
                        "baseline.pdf",
                        _real_pdf_bytes(
                            "A real baseline document with enough content to clear the threshold."
                        ),
                        "application/pdf",
                    ),
                ),
                ("files", ("crm.pdf", _blank_pdf_bytes(), "application/pdf")),
            ],
            data={"product_key": "two-file-test"},
        )
    assert r.status_code == 422
    assert "crm.pdf" in r.json()["detail"]
    assert "baseline.pdf" not in r.json()["detail"]


def test_ingest_from_documents_two_valid_documents_still_works(admin_client, db_session):
    """The regression this whole section must not cause: a real, valid
    two-document upload (the typical MSP-baseline + vendor-CRM pattern)
    must still succeed exactly as before."""
    seed = _seed_framework_and_control(db_session)
    stub = _StubIngestProvider(_stub_ingest_response(seed["ctrl"].control_id))
    with patch("app.routers.admin_products.get_ai_provider", return_value=stub):
        r = admin_client.post(
            "/admin/products/import/from-documents",
            files=[
                (
                    "files",
                    (
                        "baseline.pdf",
                        _real_pdf_bytes(
                            "MSP baseline document with enough real content to clear the threshold."
                        ),
                        "application/pdf",
                    ),
                ),
                (
                    "files",
                    (
                        "crm.pdf",
                        _real_pdf_bytes(
                            "Vendor CRM document with enough real content to clear the threshold."
                        ),
                        "application/pdf",
                    ),
                ),
            ],
            data={"product_key": "two-valid-docs-test"},
        )
    assert r.status_code == 200
    assert "two-valid-docs-test" in r.json()["yaml"]


# ---------------------------------------------------------------------------
# Product key normalization
# ---------------------------------------------------------------------------


def test_ingest_from_documents_rejects_invalid_key_shape_before_ai_call(admin_client, db_session):
    _seed_framework_and_control(db_session)
    with patch(
        "app.routers.admin_products.get_ai_provider",
        return_value=_AssertNeverCalledProvider(),
    ):
        r = admin_client.post(
            "/admin/products/import/from-documents",
            files={"files": ("crm.pdf", _fake_pdf_bytes(), "application/pdf")},
            data={"product_key": "datto rmm"},
        )
    assert r.status_code == 422
    assert "not valid" in r.json()["detail"]


def test_ingest_from_documents_normalizes_key_case(admin_client, db_session):
    seed = _seed_framework_and_control(db_session)
    stub = _StubIngestProvider(_stub_ingest_response(seed["ctrl"].control_id))
    with (
        patch("app.routers.admin_products.get_ai_provider", return_value=stub),
        patch("app.importers.document.extract_text", return_value=_STUB_EXTRACTED_TEXT),
    ):
        r = admin_client.post(
            "/admin/products/import/from-documents",
            files={"files": ("crm.pdf", _fake_pdf_bytes(), "application/pdf")},
            data={"product_key": "DattoRMM"},
        )
    assert r.status_code == 200
    import yaml as _yaml

    data_dict = _yaml.safe_load(r.json()["yaml"])
    assert data_dict["product"]["key"] == "dattormm"


def test_import_dry_run_reports_invalid_key_shape_as_problem(admin_client, db_session):
    seed = _seed_framework_and_control(db_session)
    yaml_bytes = _valid_yaml("datto rmm", seed["ctrl"].control_id)
    r = admin_client.post(
        "/admin/products/import/dry-run",
        files={"file": ("bad-key.yaml", yaml_bytes, "application/x-yaml")},
    )
    assert r.status_code == 200
    assert any("not valid" in p for p in r.json()["problems"])


def test_import_apply_rejects_invalid_key_shape(admin_client, db_session):
    seed = _seed_framework_and_control(db_session)
    yaml_bytes = _valid_yaml("datto rmm", seed["ctrl"].control_id)
    r = admin_client.post(
        "/admin/products/import/apply",
        files={"file": ("bad-key.yaml", yaml_bytes, "application/x-yaml")},
    )
    assert r.status_code == 422
    assert any("not valid" in p for p in r.json()["detail"]["problems"])
    assert (
        db_session.scalars(select(Product).where(Product.key == "datto rmm")).first() is None
    )


def test_import_apply_normalizes_key_case_and_dedupes_on_reimport(admin_client, db_session):
    """Importing the same tool twice under different casing must update
    the one existing row, not silently create a second product."""
    seed = _seed_framework_and_control(db_session)

    first_yaml = _valid_yaml("DattoRMM", seed["ctrl"].control_id)
    r1 = admin_client.post(
        "/admin/products/import/apply",
        files={"file": ("first.yaml", first_yaml, "application/x-yaml")},
    )
    assert r1.status_code == 201
    first_id = r1.json()["product_id"]

    stored = db_session.scalars(select(Product).where(Product.key == "dattormm")).one()
    assert stored.id == uuid.UUID(first_id)
    assert (
        db_session.scalars(select(Product).where(Product.key == "DattoRMM")).first() is None
    ), "must be stored lowercased, not verbatim"

    r2 = admin_client.post(
        "/admin/products/import/apply",
        files={
            "file": (
                "second.yaml",
                _valid_yaml("dattormm", seed["ctrl"].control_id),
                "application/x-yaml",
            )
        },
    )
    assert r2.status_code == 201
    assert r2.json()["product_id"] == first_id, (
        "different casing of the same key must resolve to the same product"
    )
    all_matching = db_session.scalars(select(Product).where(Product.key == "dattormm")).all()
    assert len(all_matching) == 1
