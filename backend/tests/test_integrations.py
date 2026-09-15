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

import dataclasses

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.connectors import REGISTRY, ConnectorTestResult
from app.crypto import decrypt_credential, encrypt_credential
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

    def _fake(config, credential, test_input=None):
        return state["result"]

    # ConnectorSpec is a frozen dataclass (deliberately -- see connectors/
    # __init__.py), so the stub replaces the whole registry entry rather
    # than mutating a field in place.
    monkeypatch.setitem(
        REGISTRY, "liongard", dataclasses.replace(REGISTRY["liongard"], test_connection=_fake)
    )
    return state


_SMTP_CRED_BODY = {
    "config": {
        "host": "mail.example.com", "port": "2525", "encryption_mode": "starttls",
        "from_address": "noreply@example.com", "from_name": "WinGRC",
    },
    "credential": {"username": "smtpuser", "password": "s3cr3t-smtp-pass"},
}


@pytest.fixture(autouse=True)
def _stub_smtp_test_connection(monkeypatch):
    """Same shape as the Liongard stub above, but also records the
    test_input each call received, so router-level tests can assert the
    recipient was actually passed through rather than silently dropped."""
    state = {
        "result": ConnectorTestResult(ok=True, message="Connected to mail.example.com:2525."),
        "calls": [],
    }

    def _fake(config, credential, test_input=None):
        state["calls"].append(test_input)
        return state["result"]

    monkeypatch.setitem(
        REGISTRY, "smtp", dataclasses.replace(REGISTRY["smtp"], test_connection=_fake)
    )
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


# ---------------------------------------------------------------------------
# test_input passthrough (2026-09-14) -- SMTP: a real test-message
# recipient. Router-level: connectors/smtp.py's own send-message behavior
# is covered by test_smtp_connector.py; this covers that the router
# actually passes the recipient through and audit-logs it, and that the
# pre-existing "no body at all" call shape (every test above) still works
# unchanged now that the endpoint accepts an optional body.
# ---------------------------------------------------------------------------


def test_existing_config_saved_under_old_shape_still_loads(admin_client, db_session):
    """The stored `config` JSON was always a plain {field_name: value}
    dict -- ConfigField only changes how the *schema* is described to the
    frontend, never the storage format. This directly proves a row
    written before this change (as Jarrod's real SMTP2GO credential on
    wl-util-1 was) still reads back correctly and needs no re-entry."""
    import json

    ciphertext, key_version = encrypt_credential(
        json.dumps({"username": "smtpuser", "password": "s3cr3t-smtp-pass"})
    )
    row = IntegrationConnection(
        connector_key="smtp",
        config={
            "host": "mail.smtp2go.com", "port": "2525", "encryption_mode": "starttls",
            "from_address": "noreply@example.com", "from_name": "WinGRC", "verify_cert": "true",
        },
        encrypted_credential=ciphertext,
        credential_key_version=key_version,
        credential_hint="pass"[-4:],
    )
    db_session.add(row)
    db_session.commit()

    r = admin_client.get("/integrations")
    assert r.status_code == 200
    smtp = next(x for x in r.json() if x["connector_key"] == "smtp")
    assert smtp["configured"] is True
    assert smtp["config"]["host"] == "mail.smtp2go.com"
    assert smtp["config"]["encryption_mode"] == "starttls"
    assert smtp["credential_hint"] == "pass"[-4:]
    # The password itself was never re-entered -- it still decrypts to
    # exactly what was stored under the old code.
    decrypted = json.loads(decrypt_credential(row.encrypted_credential))
    assert decrypted == {"username": "smtpuser", "password": "s3cr3t-smtp-pass"}
    # And the new descriptor metadata is present alongside the untouched
    # stored values -- both shapes coexist correctly.
    encryption_field = next(f for f in smtp["config_fields"] if f["name"] == "encryption_mode")
    assert encryption_field["type"] == "select"
    assert any(o["value"] == "starttls" for o in encryption_field["options"])


def test_test_connection_with_no_body_behaves_as_before(admin_client, _stub_smtp_test_connection):
    """The exact call shape every test above already uses
    (`client.post(".../test")`, no body) must keep working now that the
    endpoint accepts an optional JSON body -- this is the regression this
    slice must not cause."""
    admin_client.put("/integrations/smtp/credential", json=_SMTP_CRED_BODY)
    r = admin_client.post("/integrations/smtp/test")
    assert r.status_code == 200
    assert _stub_smtp_test_connection["calls"] == [None]


def test_test_connection_passes_recipient_through(admin_client, _stub_smtp_test_connection):
    admin_client.put("/integrations/smtp/credential", json=_SMTP_CRED_BODY)
    r = admin_client.post("/integrations/smtp/test", json={"test_input": "ops@example.com"})
    assert r.status_code == 200
    assert _stub_smtp_test_connection["calls"] == ["ops@example.com"]


def test_test_connection_recipient_rejected_reports_distinctly(
    admin_client, _stub_smtp_test_connection
):
    admin_client.put("/integrations/smtp/credential", json=_SMTP_CRED_BODY)
    _stub_smtp_test_connection["result"] = ConnectorTestResult(
        ok=False,
        message=(
            "mail.example.com rejected the from-address 'noreply@example.com' "
            "(SMTP 550): sender domain not verified. This usually means the "
            "sending domain isn't verified with the provider (SPF/DKIM)."
        ),
    )
    r = admin_client.post("/integrations/smtp/test", json={"test_input": "ops@example.com"})
    assert r.status_code == 200
    body = r.json()
    assert body["last_test_ok"] is False
    assert "from-address" in body["last_test_error"]
    assert "SPF/DKIM" in body["last_test_error"]


def test_test_send_writes_audit_event_with_recipient(admin_client, db_session):
    admin_client.put("/integrations/smtp/credential", json=_SMTP_CRED_BODY)
    admin_client.post("/integrations/smtp/test", json={"test_input": "ops@example.com"})
    entry = db_session.scalars(
        select(AuditLog).where(
            AuditLog.action == "integration_connection.test",
            AuditLog.entity_type == "integration_connection",
        )
    ).first()
    assert entry is not None
    assert entry.after_value["test_recipient"] == "ops@example.com"
    assert entry.after_value["connector_key"] == "smtp"
    assert entry.after_value["ok"] is True
    # Actor is stamped automatically from the authenticated request, same
    # as every other event this router writes -- not "system".
    assert entry.actor is not None
    assert entry.actor != "system"


def test_connect_only_test_does_not_record_a_recipient_in_audit_log(admin_client, db_session):
    """A plain connect-only test (no test_input) must not gain a
    test_recipient key at all -- not None, not missing-but-implied,
    genuinely absent, so a log reader can't mistake it for "sent to
    nobody" versus "wasn't a send.\""""
    admin_client.put("/integrations/smtp/credential", json=_SMTP_CRED_BODY)
    admin_client.post("/integrations/smtp/test")
    entry = db_session.scalars(
        select(AuditLog).where(
            AuditLog.action == "integration_connection.test",
            AuditLog.entity_type == "integration_connection",
        )
    ).first()
    assert entry is not None
    assert "test_recipient" not in entry.after_value


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


# ---------------------------------------------------------------------------
# AI provider connector (connectors/ai.py, 2026-09-19) -- router-level
# coverage only, mirroring the Liongard tests above (same generic
# router, same RBAC gate, same encrypt-at-rest guarantees). run_completion's
# own exception categorization is test_ai_connector.py's job, not this
# file's -- these tests stub test_connection exactly like Liongard's/SMTP's
# own fixtures above, so no real Anthropic call happens here either.
# ---------------------------------------------------------------------------

_AI_CRED_BODY = {
    "config": {"provider": "anthropic", "model": "claude-sonnet-4-6"},
    "credential": {"api_key": "sk-ant-test-key-9f8e7d"},
}


@pytest.fixture(autouse=True)
def _stub_ai_test_connection(monkeypatch):
    state = {
        "result": ConnectorTestResult(
            ok=True, message="Connected to Anthropic (claude-sonnet-4-6)."
        ),
    }

    def _fake(config, credential, test_input=None):
        return state["result"]

    monkeypatch.setitem(REGISTRY, "ai", dataclasses.replace(REGISTRY["ai"], test_connection=_fake))
    return state


def test_list_integrations_shows_ai_never_connected(admin_client):
    r = admin_client.get("/integrations")
    assert r.status_code == 200
    rows = r.json()
    ai = next(row for row in rows if row["connector_key"] == "ai")
    assert ai["configured"] is False
    assert ai["credential_hint"] is None
    assert ai["last_test_ok"] is None
    assert ai["kind"] == "ai"


def test_ai_non_admin_cannot_set_credential(engineer_client):
    r = engineer_client.put("/integrations/ai/credential", json=_AI_CRED_BODY)
    assert r.status_code == 403


def test_ai_set_credential_never_returns_plaintext(admin_client):
    r = admin_client.put("/integrations/ai/credential", json=_AI_CRED_BODY)
    assert r.status_code == 200
    body = r.json()
    assert "sk-ant-test-key-9f8e7d" not in r.text
    assert body["configured"] is True
    assert body["credential_hint"] == "sk-ant-test-key-9f8e7d"[-4:]
    assert body["config"] == {"provider": "anthropic", "model": "claude-sonnet-4-6"}


def test_ai_credential_stored_as_ciphertext_not_plaintext(admin_client, db_session):
    admin_client.put("/integrations/ai/credential", json=_AI_CRED_BODY)
    row = db_session.scalars(
        select(IntegrationConnection).where(IntegrationConnection.connector_key == "ai")
    ).first()
    assert row is not None
    assert row.encrypted_credential is not None
    assert "sk-ant-test-key-9f8e7d" not in row.encrypted_credential

    import json

    decrypted = json.loads(decrypt_credential(row.encrypted_credential))
    assert decrypted == _AI_CRED_BODY["credential"]


def test_ai_test_connection_success_updates_status(admin_client):
    admin_client.put("/integrations/ai/credential", json=_AI_CRED_BODY)
    r = admin_client.post("/integrations/ai/test")
    assert r.status_code == 200
    body = r.json()
    assert body["last_test_ok"] is True
    assert body["last_test_error"] is None


def test_ai_test_connection_surfaces_real_failure_message(admin_client, _stub_ai_test_connection):
    admin_client.put("/integrations/ai/credential", json=_AI_CRED_BODY)
    _stub_ai_test_connection["result"] = ConnectorTestResult(
        ok=False,
        message=(
            "Anthropic rejected the API key (HTTP 401) -- check it's active "
            "and correctly entered."
        ),
    )
    r = admin_client.post("/integrations/ai/test")
    assert r.status_code == 200
    body = r.json()
    assert body["last_test_ok"] is False
    assert "401" in body["last_test_error"]


def test_ai_test_connection_without_credential_rejected(admin_client):
    r = admin_client.post("/integrations/ai/test")
    assert r.status_code == 400


def test_ai_test_connection_never_echoes_credential(admin_client):
    admin_client.put("/integrations/ai/credential", json=_AI_CRED_BODY)
    r = admin_client.post("/integrations/ai/test")
    assert "sk-ant-test-key-9f8e7d" not in r.text


def test_ai_delete_credential_clears_state(admin_client, db_session):
    admin_client.put("/integrations/ai/credential", json=_AI_CRED_BODY)
    r = admin_client.delete("/integrations/ai/credential")
    assert r.status_code == 204

    row = db_session.scalars(
        select(IntegrationConnection).where(IntegrationConnection.connector_key == "ai")
    ).first()
    assert row.encrypted_credential is None
    assert row.credential_hint is None

    listed = admin_client.get("/integrations").json()
    ai = next(x for x in listed if x["connector_key"] == "ai")
    assert ai["configured"] is False
