"""Integration tests for the D.1 Integrations router.

Covers: RBAC (msp_admin only), the credential is never returned/echoed
anywhere (response bodies, error messages), ciphertext (not plaintext) is
what actually lands in the DB, fail-closed behavior when the encryption
key is missing, and that set/test/delete each write an audit event without
ever putting the credential value into it.

test_connection itself is monkeypatched at the connector-spec level so
these tests never make a real network call — connectors/liongard.py's own
HTTP-parsing logic isn't this file's concern.

Run in-container:
    docker compose exec backend pytest tests/test_integrations.py -m integration -v
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.connectors import REGISTRY, ConnectorTestResult
from app.crypto import decrypt_credential
from app.db import get_session
from app.main import app
from app.models import AuditLog, IntegrationConnection
from tests.conftest import _app_session, _authed, _make_fake_user

pytestmark = pytest.mark.integration

_CRED_BODY = {
    "config": {"instance_url": "https://myinstance.app.liongard.com"},
    "credential": {"access_key_id": "AKIDEXAMPLE", "access_key_secret": "s3cr3t-value-9f8e7d"},
}


@pytest.fixture
def admin_client(db_session: Session, fake_msp_admin):
    app.dependency_overrides[get_session] = _app_session(db_session)
    app.dependency_overrides[get_current_user] = _authed(db_session, fake_msp_admin)
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def engineer_client(db_session: Session):
    engineer = _make_fake_user(role="msp_engineer")
    app.dependency_overrides[get_session] = _app_session(db_session)
    app.dependency_overrides[get_current_user] = _authed(db_session, engineer)
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def _stub_liongard_test_connection(monkeypatch):
    """Replace the real HTTP call with a controllable stub so these tests
    never hit the network. Individual tests still override the module-level
    _result to exercise the failure path."""
    state = {
        "result": ConnectorTestResult(ok=True, message="Connected — 3 environment(s) visible.")
    }

    def _fake(config, credential):
        return state["result"]

    monkeypatch.setattr(REGISTRY["liongard"], "test_connection", _fake)
    return state


def test_list_integrations_shows_liongard_never_connected(admin_client):
    r = admin_client.get("/integrations")
    assert r.status_code == 200
    rows = r.json()
    liongard = next(row for row in rows if row["connector_key"] == "liongard")
    assert liongard["configured"] is False
    assert liongard["credential_hint"] is None
    assert liongard["last_test_ok"] is None


def test_non_admin_cannot_set_credential(engineer_client):
    r = engineer_client.put("/integrations/liongard/credential", json=_CRED_BODY)
    assert r.status_code == 403


def test_non_admin_cannot_list(engineer_client):
    r = engineer_client.get("/integrations")
    assert r.status_code == 403


def test_set_credential_never_returns_plaintext(admin_client):
    r = admin_client.put("/integrations/liongard/credential", json=_CRED_BODY)
    assert r.status_code == 200
    body = r.json()
    assert "s3cr3t-value-9f8e7d" not in r.text
    assert "AKIDEXAMPLE" not in r.text
    assert body["configured"] is True
    assert body["credential_hint"] == "s3cr3t-value-9f8e7d"[-4:]
    assert body["config"] == {"instance_url": "https://myinstance.app.liongard.com"}


def test_credential_stored_as_ciphertext_not_plaintext(admin_client, db_session):
    admin_client.put("/integrations/liongard/credential", json=_CRED_BODY)
    row = db_session.scalars(
        select(IntegrationConnection).where(IntegrationConnection.connector_key == "liongard")
    ).first()
    assert row is not None
    assert row.encrypted_credential is not None
    assert "s3cr3t-value-9f8e7d" not in row.encrypted_credential
    assert "AKIDEXAMPLE" not in row.encrypted_credential
    # Round-trips back through crypto.py to the real values -- proves it's
    # genuinely encrypted (not e.g. base64'd) and genuinely recoverable.
    import json

    decrypted = json.loads(decrypt_credential(row.encrypted_credential))
    assert decrypted == _CRED_BODY["credential"]


def test_set_credential_missing_field_rejected(admin_client):
    bad = {
        "config": {"instance_url": ""},
        "credential": {"access_key_id": "x", "access_key_secret": "y"},
    }
    r = admin_client.put("/integrations/liongard/credential", json=bad)
    assert r.status_code == 400
    assert "instance_url" in r.text


def test_set_credential_writes_audit_event_without_the_value(admin_client, db_session):
    admin_client.put("/integrations/liongard/credential", json=_CRED_BODY)
    entry = db_session.scalars(
        select(AuditLog).where(AuditLog.action == "integration_connection.credential_set")
    ).first()
    assert entry is not None
    assert "s3cr3t-value-9f8e7d" not in str(entry.after_value)
    assert "AKIDEXAMPLE" not in str(entry.after_value)
    assert entry.after_value["credential_hint"] == "s3cr3t-value-9f8e7d"[-4:]
    assert entry.after_value["connector_key"] == "liongard"


def test_test_connection_success_updates_status(admin_client):
    admin_client.put("/integrations/liongard/credential", json=_CRED_BODY)
    r = admin_client.post("/integrations/liongard/test")
    assert r.status_code == 200
    body = r.json()
    assert body["last_test_ok"] is True
    assert body["last_test_error"] is None


def test_test_connection_surfaces_real_failure_message(
    admin_client, _stub_liongard_test_connection
):
    admin_client.put("/integrations/liongard/credential", json=_CRED_BODY)
    _stub_liongard_test_connection["result"] = ConnectorTestResult(
        ok=False,
        message=(
            "Liongard rejected the key (HTTP 401) — check that it's active "
            "and has at least Reader access."
        ),
    )
    r = admin_client.post("/integrations/liongard/test")
    assert r.status_code == 200
    body = r.json()
    assert body["last_test_ok"] is False
    assert "401" in body["last_test_error"]
    assert "Reader" in body["last_test_error"]


def test_test_connection_without_credential_rejected(admin_client):
    r = admin_client.post("/integrations/liongard/test")
    assert r.status_code == 400


def test_test_connection_never_echoes_credential(admin_client):
    admin_client.put("/integrations/liongard/credential", json=_CRED_BODY)
    r = admin_client.post("/integrations/liongard/test")
    assert "s3cr3t-value-9f8e7d" not in r.text
    assert "AKIDEXAMPLE" not in r.text


def test_delete_credential_clears_state(admin_client, db_session):
    admin_client.put("/integrations/liongard/credential", json=_CRED_BODY)
    r = admin_client.delete("/integrations/liongard/credential")
    assert r.status_code == 204

    row = db_session.scalars(
        select(IntegrationConnection).where(IntegrationConnection.connector_key == "liongard")
    ).first()
    assert row.encrypted_credential is None
    assert row.credential_hint is None

    listed = admin_client.get("/integrations").json()
    liongard = next(x for x in listed if x["connector_key"] == "liongard")
    assert liongard["configured"] is False


def test_delete_without_credential_404s(admin_client):
    r = admin_client.delete("/integrations/liongard/credential")
    assert r.status_code == 404


def test_delete_writes_audit_event(admin_client, db_session):
    admin_client.put("/integrations/liongard/credential", json=_CRED_BODY)
    admin_client.delete("/integrations/liongard/credential")
    entry = db_session.scalars(
        select(AuditLog).where(AuditLog.action == "integration_connection.credential_delete")
    ).first()
    assert entry is not None


def test_unknown_connector_404s(admin_client):
    r = admin_client.get("/integrations")
    assert r.status_code == 200
    r2 = admin_client.put(
        "/integrations/not-a-real-connector/credential",
        json={"config": {}, "credential": {}},
    )
    assert r2.status_code == 404


def test_fails_closed_when_encryption_key_unset(admin_client, monkeypatch):
    from app.config import get_settings

    monkeypatch.delenv("WINGRC_CREDENTIAL_ENCRYPTION_KEYS", raising=False)
    get_settings.cache_clear()
    try:
        r = admin_client.put("/integrations/liongard/credential", json=_CRED_BODY)
        assert r.status_code == 500
        assert "s3cr3t-value-9f8e7d" not in r.text
    finally:
        get_settings.cache_clear()
