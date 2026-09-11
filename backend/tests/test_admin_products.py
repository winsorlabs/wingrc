"""Integration tests for routers/admin_products.py (G.9): the deployment-
tier Tools/baseline-library screen -- library view, tool detail, the
cross-org footprint read, import (validate -> dry-run -> apply), publish/
unpublish enforcement, and document attachments.

Run in-container:
    docker compose exec backend pytest tests/test_admin_products.py -m integration -v
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

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
