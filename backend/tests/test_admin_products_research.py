"""Integration tests for the AI-research-of-vendor-platform-documentation
endpoints on routers/admin_products.py:
  POST /admin/products/{id}/research/suggest-urls
  POST /admin/products/{id}/research/fetch
  POST /admin/products/{id}/import/from-documents-with-research

web_fetch.fetch_url_safely is monkeypatched throughout -- no real network
access in this file. web_fetch.py's own SSRF defenses are covered directly
and exhaustively in test_web_fetch.py; these tests are about the router
wiring (approval gate, caps, storage, merge integration), not re-proving
the fetcher's own safety properties.

Run in-container:
    docker compose exec backend pytest tests/test_admin_products_research.py -m integration -v
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
from app.main import app
from app.models import (
    AssessmentObjective,
    BaselineControl,
    Control,
    Framework,
    Product,
    ProductDocument,
)
from app.storage import StorageClient, get_storage_client
from app.web_fetch import FetchResult
from tests.conftest import _app_session, _authed

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
def storage():
    return InMemoryStorageClient()


@pytest.fixture
def admin_client(db_session, fake_msp_admin, storage):
    app.dependency_overrides[get_session] = _app_session(db_session)
    app.dependency_overrides[get_current_user] = _authed(db_session, fake_msp_admin)
    app.dependency_overrides[get_storage_client] = lambda: storage
    yield TestClient(app)
    app.dependency_overrides.clear()


def _seed_product(db_session, *, disclaimed_control: bool = False) -> dict:
    # /import/from-documents-with-research looks up a FIXED framework key
    # (seeds/baselines.py:_FRAMEWORK_KEY, "nist-800-171-r2") -- matching
    # test_admin_products.py:_seed_framework_and_control's own get-or-create
    # pattern exactly, not a fresh random key, or the endpoint 409s.
    fw = db_session.scalars(select(Framework).where(Framework.key == "nist-800-171-r2")).first()
    if fw is None:
        fw = Framework(key="nist-800-171-r2", name="NIST r2", version="r2")
        db_session.add(fw)
        db_session.flush()
    suffix = uuid.uuid4().hex[:6]
    ctrl = Control(
        framework_id=fw.id, control_id=f"AC.TEST-{suffix}", family="AC",
        title="Access Control", requirement_text="...", sprs_weight=5, sequence_order=1,
    )
    disclaimed_ctrl = Control(
        framework_id=fw.id, control_id=f"IA.TEST-{suffix}", family="IA",
        title="Identify Users", requirement_text="...", sprs_weight=3, sequence_order=2,
    )
    db_session.add_all([ctrl, disclaimed_ctrl])
    db_session.flush()
    db_session.add_all([
        AssessmentObjective(control_id=ctrl.id, objective_key="a", text="[a]"),
        AssessmentObjective(control_id=disclaimed_ctrl.id, objective_key="a", text="[a]"),
    ])
    db_session.flush()

    product = Product(
        framework_id=fw.id, key=f"researchtool-{uuid.uuid4().hex[:6]}", name="Research Tool",
        provider="Vendor Inc", category="ESP", asset_type="SPA", role="Test.",
        is_published=True,
    )
    db_session.add(product)
    db_session.flush()

    bc = BaselineControl(
        product_id=product.id, control_id=ctrl.id, objectives=["a"],
        classification="shared", coverage_basis="customer_system",
        candidate_state="pending_evidence",
    )
    db_session.add(bc)
    if disclaimed_control:
        bc_ia = BaselineControl(
            product_id=product.id, control_id=disclaimed_ctrl.id, objectives=["a"],
            classification="customer_owns", candidate_state="not_satisfied_by_product",
            note="Customer's IdP owns identity.",
        )
        db_session.add(bc_ia)
    db_session.flush()

    return {"fw": fw, "ctrl": ctrl, "disclaimed_ctrl": disclaimed_ctrl, "product": product}


class _StubProvider(AIProvider):
    def __init__(self, response: str):
        self._response = response

    def complete(self, system, user, *, max_tokens=8192):
        return self._response

    @property
    def identity(self) -> str:
        return "anthropic:stub-model"


_SUGGEST_RESPONSE = json.dumps({
    "suggestions": [
        {"url": "https://docs.vendor.com/admin/security", "rationale": "Official admin guide."},
    ]
})


# ---------------------------------------------------------------------------
# suggest-urls
# ---------------------------------------------------------------------------


def test_suggest_urls_returns_proposals_only(admin_client, db_session):
    seed = _seed_product(db_session)
    stub = _StubProvider(_SUGGEST_RESPONSE)
    with patch("app.routers.admin_products.get_ai_provider", return_value=stub):
        r = admin_client.post(f"/admin/products/{seed['product'].id}/research/suggest-urls")
    assert r.status_code == 200
    body = r.json()
    assert body["suggestions"] == [
        {"url": "https://docs.vendor.com/admin/security", "rationale": "Official admin guide."}
    ]


def test_suggest_urls_404_for_unknown_product(admin_client):
    stub = _StubProvider(_SUGGEST_RESPONSE)
    with patch("app.routers.admin_products.get_ai_provider", return_value=stub):
        r = admin_client.post(f"/admin/products/{uuid.uuid4()}/research/suggest-urls")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# fetch: the approval gate + caps + storage
# ---------------------------------------------------------------------------


def _fake_fetch_ok(url: str) -> FetchResult:
    return FetchResult(
        url=url, final_url=url, ok=True, status_code=200,
        content=b"<html><head><title>Security Admin Guide</title></head>"
        b"<body><p>Configure RBAC here.</p></body></html>",
        content_type="text/html", error=None,
    )


def _fake_fetch_blocked(url: str) -> FetchResult:
    return FetchResult(
        url=url, final_url=url, ok=False, status_code=None, content=None,
        content_type=None, error="127.0.0.1 is a private address -- refused.",
    )


def test_fetch_only_touches_urls_in_the_approved_list(admin_client, db_session, storage):
    """The approval gate, proven directly: fetch_url_safely is called with
    EXACTLY the URLs submitted, nothing else -- no expansion, no crawling."""
    seed = _seed_product(db_session)
    calls = []

    def recording_fetch(url):
        calls.append(url)
        return _fake_fetch_ok(url)

    with patch("app.routers.admin_products.fetch_url_safely", side_effect=recording_fetch):
        r = admin_client.post(
            f"/admin/products/{seed['product'].id}/research/fetch",
            json={"urls": ["https://docs.vendor.com/a", "https://docs.vendor.com/b"]},
        )
    assert r.status_code == 200
    assert calls == ["https://docs.vendor.com/a", "https://docs.vendor.com/b"]


def test_fetch_stores_successful_pages_as_web_research_documents(admin_client, db_session, storage):
    seed = _seed_product(db_session)
    with patch("app.routers.admin_products.fetch_url_safely", side_effect=_fake_fetch_ok):
        r = admin_client.post(
            f"/admin/products/{seed['product'].id}/research/fetch",
            json={"urls": ["https://docs.vendor.com/admin"]},
        )
    assert r.status_code == 200
    body = r.json()
    assert body["pages_fetched"] == 1
    assert body["results"][0]["ok"] is True
    assert body["results"][0]["title"] == "Security Admin Guide"
    doc_id = body["results"][0]["document_id"]
    assert doc_id is not None

    doc = db_session.get(ProductDocument, uuid.UUID(doc_id))
    assert doc is not None
    assert doc.kind == "web_research"
    assert doc.source_docs_ref == "https://docs.vendor.com/admin"
    assert doc.product_id == seed["product"].id
    assert storage.files[doc.storage_key] is not None  # the raw HTML is retrievable


def test_fetch_reports_per_url_failure_without_aborting_the_batch(
    admin_client, db_session, storage
):
    seed = _seed_product(db_session)

    def mixed_fetch(url):
        return _fake_fetch_ok(url) if "good" in url else _fake_fetch_blocked(url)

    with patch("app.routers.admin_products.fetch_url_safely", side_effect=mixed_fetch):
        r = admin_client.post(
            f"/admin/products/{seed['product'].id}/research/fetch",
            json={"urls": ["https://docs.vendor.com/good", "https://docs.vendor.com/bad"]},
        )
    assert r.status_code == 200
    body = r.json()
    assert body["pages_fetched"] == 1
    results_by_url = {res["url"]: res for res in body["results"]}
    assert results_by_url["https://docs.vendor.com/good"]["ok"] is True
    assert results_by_url["https://docs.vendor.com/bad"]["ok"] is False
    assert "private" in results_by_url["https://docs.vendor.com/bad"]["error"].lower()


def test_fetch_rejects_more_urls_than_the_per_run_cap(admin_client, db_session):
    seed = _seed_product(db_session)
    urls = [f"https://docs.vendor.com/{i}" for i in range(11)]
    with patch("app.routers.admin_products.fetch_url_safely", side_effect=_fake_fetch_ok):
        r = admin_client.post(
            f"/admin/products/{seed['product'].id}/research/fetch", json={"urls": urls}
        )
    assert r.status_code == 422


def test_fetch_reports_cost_before_any_ai_call(admin_client, db_session, storage):
    """§5: the fetch response itself carries the size/token estimate --
    no AI call has happened by this point, only HTTP fetches."""
    seed = _seed_product(db_session)
    with patch("app.routers.admin_products.fetch_url_safely", side_effect=_fake_fetch_ok):
        r = admin_client.post(
            f"/admin/products/{seed['product'].id}/research/fetch",
            json={"urls": ["https://docs.vendor.com/admin"]},
        )
    body = r.json()
    assert body["total_characters"] > 0
    assert body["estimated_added_tokens"] == body["total_characters"] // 4


# ---------------------------------------------------------------------------
# from-documents-with-research: the §0 merge, end to end through the API
# ---------------------------------------------------------------------------


def _stub_primary_response(control_id: str, *, also_disclaim: str | None = None) -> str:
    """The CRM/baseline document is re-parsed fresh on every ingestion run
    (that's the whole point of "attached documents first") -- so a
    disclaimed control must appear in THIS response too, exactly as a real
    re-parse of the same CRM text would reproduce it every time. This is
    what merge_research() actually checks web proposals against; it never
    reaches into the database for a product's already-applied
    baseline_control rows."""
    controls = [
        {
            "control": control_id,
            "objectives": ["a"],
            "classification": "shared",
            "provider_contribution": "Enables access control.",
            "evidence": [{"artifact": "Role export", "type": "export"}],
        }
    ]
    if also_disclaim:
        controls.append({
            "control": also_disclaim,
            "objectives": ["a"],
            "classification": "customer_owns",
            "note": "Customer's IdP owns identity.",
            "evidence": [],
        })
    product = {"name": "Research Tool", "provider": "Vendor Inc"}
    return json.dumps({"product": product, "controls": controls})


def _stub_web_response_proposing(control_id: str, classification: str) -> str:
    return json.dumps({
        "controls": [
            {
                "control": control_id,
                "objectives": ["a"],
                "classification": classification,
                "provider_contribution": "Documented capability.",
            }
        ]
    })


_STUB_EXTRACTED_TEXT = (
    "Stub extracted document text, long enough to clear the minimum-"
    "extracted-text guard so these tests exercise what they're actually testing."
)


def _seed_web_research_document(
    db_session, storage, product_id, *, url: str, html: bytes
) -> ProductDocument:
    doc_id = uuid.uuid4()
    key = f"products/{product_id}/{doc_id}/{doc_id}.html"
    storage.upload_file(key, html, "text/html")
    doc = ProductDocument(
        id=doc_id, product_id=product_id, title="Fetched page", kind="web_research",
        source_docs_ref=url, storage_key=key, mime_type="text/html", file_size_bytes=len(html),
    )
    db_session.add(doc)
    db_session.flush()
    return doc


def test_with_research_requires_at_least_one_document(admin_client, db_session, storage):
    seed = _seed_product(db_session)
    doc = _seed_web_research_document(
        db_session, storage, seed["product"].id,
        url="https://docs.vendor.com/x", html=b"<html></html>",
    )
    r = admin_client.post(
        f"/admin/products/{seed['product'].id}/import/from-documents-with-research",
        files=[],
        data={"research_document_ids": str(doc.id)},
    )
    assert r.status_code == 422


def test_with_research_new_control_is_added_and_reported(admin_client, db_session, storage):
    seed = _seed_product(db_session)
    web_html = b"<html><body>Audit logging documented here.</body></html>"
    doc = _seed_web_research_document(
        db_session, storage, seed["product"].id,
        url="https://docs.vendor.com/logging", html=web_html,
    )

    call_responses = iter([
        _stub_primary_response(seed["ctrl"].control_id),
        _stub_web_response_proposing("AU.L2-3.3.1", "provider_satisfies"),
    ])

    class _SequencedProvider(AIProvider):
        def complete(self, system, user, *, max_tokens=8192):
            return next(call_responses)

        @property
        def identity(self):
            return "anthropic:stub-model"

    # AU.L2-3.3.1 needs to be a real catalog control for build_preview to
    # accept it -- add it to the same framework.
    au = Control(
        framework_id=seed["fw"].id, control_id="AU.L2-3.3.1", family="AU",
        title="System Auditing", requirement_text="...", sprs_weight=3, sequence_order=3,
    )
    db_session.add(au)
    db_session.flush()
    db_session.add(AssessmentObjective(control_id=au.id, objective_key="a", text="[a]"))
    db_session.flush()

    with (
        patch("app.routers.admin_products.get_ai_provider", return_value=_SequencedProvider()),
        patch("app.importers.document.extract_text", return_value=_STUB_EXTRACTED_TEXT),
    ):
        r = admin_client.post(
            f"/admin/products/{seed['product'].id}/import/from-documents-with-research",
            files={"files": ("crm.pdf", b"%PDF-1.4 fake", "application/pdf")},
            data={"research_document_ids": str(doc.id)},
        )
    assert r.status_code == 200
    body = r.json()
    control_ids = {c["control"][0] for c in body["controls"]}
    assert "AU.L2-3.3.1" in control_ids
    added_row = next(c for c in body["controls"] if c["control"] == ["AU.L2-3.3.1"])
    assert added_row["source"] == "https://docs.vendor.com/logging"
    assert body["research"]["pages_included"] == 1
    assert body["research"]["controls_added_from_web"] == 1
    assert body["research"]["conflicts_flagged"] == 0
    # Nothing is written -- same dry-run discipline as /import/from-documents.
    remaining = db_session.query(BaselineControl).filter(BaselineControl.control_id == au.id)
    assert remaining.count() == 0


def test_with_research_disclaimed_control_is_flagged_not_upgraded(
    admin_client, db_session, storage
):
    """THE §0 headline test, exercised through the real HTTP endpoint."""
    seed = _seed_product(db_session, disclaimed_control=True)
    web_html = b"<html><body>Our SSO handles identity for you.</body></html>"
    doc = _seed_web_research_document(
        db_session, storage, seed["product"].id,
        url="https://docs.vendor.com/sso", html=web_html,
    )

    disclaimed_id = seed["disclaimed_ctrl"].control_id
    call_responses = iter([
        _stub_primary_response(seed["ctrl"].control_id, also_disclaim=disclaimed_id),
        _stub_web_response_proposing(disclaimed_id, "provider_satisfies"),
    ])

    class _SequencedProvider(AIProvider):
        def complete(self, system, user, *, max_tokens=8192):
            return next(call_responses)

        @property
        def identity(self):
            return "anthropic:stub-model"

    with (
        patch("app.routers.admin_products.get_ai_provider", return_value=_SequencedProvider()),
        patch("app.importers.document.extract_text", return_value=_STUB_EXTRACTED_TEXT),
    ):
        r = admin_client.post(
            f"/admin/products/{seed['product'].id}/import/from-documents-with-research",
            files={"files": ("crm.pdf", b"%PDF-1.4 fake", "application/pdf")},
            data={"research_document_ids": str(doc.id)},
        )
    assert r.status_code == 200
    body = r.json()

    ia_row = next(c for c in body["controls"] if c["control"] == [disclaimed_id])
    assert ia_row["classification"] == "customer_owns", "must NEVER be upgraded by web research"

    assert body["research"]["controls_added_from_web"] == 0
    assert body["research"]["conflicts_flagged"] == 1
    assert any(
        seed["disclaimed_ctrl"].control_id in f["message"] and "docs.vendor.com/sso" in f["message"]
        for f in body["preview"]["disclaim_flags"]
    )


def test_with_research_rejects_unknown_research_document_id(admin_client, db_session, storage):
    seed = _seed_product(db_session)
    with patch("app.importers.document.extract_text", return_value=_STUB_EXTRACTED_TEXT):
        r = admin_client.post(
            f"/admin/products/{seed['product'].id}/import/from-documents-with-research",
            files={"files": ("crm.pdf", b"%PDF-1.4 fake", "application/pdf")},
            data={"research_document_ids": str(uuid.uuid4())},
        )
    assert r.status_code == 422
