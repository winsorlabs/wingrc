"""A route that waits on something outside the database holds no pool
connection while it waits (db.py:end_transaction).

Four routes wait: three on a model call, which runs for minutes, and
the research fetch on up to ten other sites. Each reads first, and a
read opens a transaction that holds a pool connection until a commit or
close. Before 2026-11-19 they held it through the wait
(`docs/roadmap.md`, "Generating a baseline froze every request").

These tests do not use `admin_client`. Its session joins the test's outer
transaction, and its commit wrapper runs a statement straight after every
commit, so under it a route always looks mid-transaction. Here each
request gets a session made by production's own `SessionLocal` settings,
on an engine of its own, so a commit really returns the connection. The
probe reads the pool at the moment of the slow call.

What this does not show: that a generate holds no threadpool token. It
does, for the whole call; ending the transaction frees the connection,
not the token.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.ai import get_ai_provider as real_get_ai_provider
from app.ai.base import AIProvider
from app.auth import get_current_user
from app.db import SessionLocal, end_transaction, get_session
from app.main import app
from app.models import Product
from app.storage import NullStorageClient, get_storage_client
from tests.conftest import _test_db_url

_TEXT = "Extracted vendor document text, long enough to clear the minimum guard. " * 3


@pytest.fixture
def probe_engine(db_engine):
    # db_engine is requested only for its skip and its test-database guard.
    engine = create_engine(_test_db_url(), pool_size=5, max_overflow=0, future=True)
    yield engine
    engine.dispose()


@pytest.fixture
def client(probe_engine, fake_msp_admin):
    factory = sessionmaker(**{**SessionLocal.kw, "bind": probe_engine})
    sessions = []

    def _session():
        s = factory()
        sessions.append(s)
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_current_user] = lambda: fake_msp_admin
    app.dependency_overrides[get_storage_client] = lambda: NullStorageClient()
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


class _Probe(AIProvider):
    """Stands in for the provider. At each call it records whether the
    session is in a transaction and how many connections are checked out,
    then stops the route."""

    def __init__(self, engine) -> None:
        self.engine = engine
        self.session = None
        self.read_held: list[int] = []
        self.at_call: list[tuple[bool, int]] = []

    def provider_for(self, session):
        # The real lookup runs, so the read before the call is production's.
        real_get_ai_provider(session)
        self.session = session
        self.read_held.append(self.engine.pool.checkedout())
        return self

    def complete(self, system, user, *, max_tokens=8192):
        self.at_call.append((self.session.in_transaction(), self.engine.pool.checkedout()))
        raise RuntimeError("probe: the call is where this test stops")

    @property
    def identity(self) -> str:
        return "probe"


def _product_read(probe: _Probe):
    """`_get_product` replaced by a real read that needs no seeded row
    committed where this engine can see it."""

    def _get(session, product_id):
        session.execute(select(Product.id).limit(1))
        probe.read_held.append(probe.engine.pool.checkedout())
        return SimpleNamespace(name="Probe Tool", provider="Vendor", key="probe-tool")

    return _get


def _assert_released(probe: _Probe, baseline: int) -> None:
    assert probe.read_held and probe.read_held[0] == baseline + 1, (
        "the route's read must hold a connection before the call, or this "
        f"test proves nothing (held {probe.read_held}, baseline {baseline})"
    )
    assert probe.at_call, "the probe's call never ran, so this test proves nothing"
    for in_txn, checked_out in probe.at_call:
        assert not in_txn, "the session was still in a transaction at the slow call"
        assert checked_out == baseline, (
            f"{checked_out - baseline} connection(s) held through the slow call"
        )


@pytest.mark.integration
def test_import_from_documents_holds_no_connection_through_the_model_call(
    client, probe_engine
):
    probe = _Probe(probe_engine)
    baseline = probe_engine.pool.checkedout()
    with (
        patch("app.routers.admin_products.get_ai_provider", side_effect=probe.provider_for),
        patch("app.importers.document.extract_text", return_value=_TEXT),
    ):
        r = client.post(
            "/admin/products/import/from-documents",
            files={"files": ("crm.pdf", b"%PDF-1.4 probe", "application/pdf")},
            data={"product_key": "probe-tool"},
        )
    assert r.status_code == 422, r.text
    _assert_released(probe, baseline)


@pytest.mark.integration
def test_suggest_research_urls_holds_no_connection_through_the_model_call(
    client, probe_engine
):
    probe = _Probe(probe_engine)
    baseline = probe_engine.pool.checkedout()
    with (
        patch("app.routers.admin_products._get_product", side_effect=_product_read(probe)),
        patch("app.routers.admin_products.get_ai_provider", side_effect=probe.provider_for),
    ):
        r = client.post(f"/admin/products/{uuid.uuid4()}/research/suggest-urls")
    assert r.status_code == 422, r.text
    _assert_released(probe, baseline)


@pytest.mark.integration
def test_import_with_research_holds_no_connection_through_the_model_call(
    client, probe_engine
):
    probe = _Probe(probe_engine)
    baseline = probe_engine.pool.checkedout()
    with (
        patch("app.routers.admin_products._get_product", side_effect=_product_read(probe)),
        patch("app.routers.admin_products.get_ai_provider", side_effect=probe.provider_for),
        patch("app.importers.document.extract_text", return_value=_TEXT),
    ):
        r = client.post(
            f"/admin/products/{uuid.uuid4()}/import/from-documents-with-research",
            files={"files": ("crm.pdf", b"%PDF-1.4 probe", "application/pdf")},
        )
    assert r.status_code == 422, r.text
    _assert_released(probe, baseline)


@pytest.mark.integration
def test_research_fetch_holds_no_connection_through_the_fetches(client, probe_engine):
    probe = _Probe(probe_engine)
    baseline = probe_engine.pool.checkedout()

    def _fetch(url):
        probe.at_call.append((probe.session.in_transaction(), probe_engine.pool.checkedout()))
        return SimpleNamespace(ok=False, content=None, final_url=url, error="probe")

    def _get(session, product_id):
        probe.session = session
        return _product_read(probe)(session, product_id)

    with (
        patch("app.routers.admin_products._get_product", side_effect=_get),
        patch("app.routers.admin_products.fetch_url_safely", side_effect=_fetch),
    ):
        r = client.post(
            f"/admin/products/{uuid.uuid4()}/research/fetch",
            json={"urls": ["https://example.com/a", "https://example.com/b"]},
        )
    assert r.status_code == 200, r.text
    assert len(probe.at_call) == 2, "both fetches must run, or this test proves nothing"
    _assert_released(probe, baseline)


def test_end_transaction_refuses_unwritten_changes():
    """No database: the refusal is decided before anything is sent."""
    session = SessionLocal()
    try:
        session.add(Product(key="unwritten", name="Unwritten"))
        with pytest.raises(RuntimeError, match="unwritten changes"):
            end_transaction(session)
    finally:
        session.expunge_all()
        session.close()
