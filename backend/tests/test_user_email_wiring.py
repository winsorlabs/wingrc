"""Integration tests for the invite/reset-password -> email wiring in
routers/users.py (the "Build outbound email" slice's §4).

Covers the fallback contract that is the whole point of this wiring:
manual (token-in-response) delivery must keep working in every case --
WINGRC_PUBLIC_URL unset, SMTP not configured, and SMTP configured but the
send genuinely fails -- and email_sent/email_error on the response must
tell the admin honestly which happened. Also covers the content rule (no
compliance content, link not bare token) and that email.send audit rows
and application logs never carry the raw token.

Single-use redemption of the invite/reset token itself is already covered
by test_password_lifecycle.py's test_reset_token_single_use /
test_reset_issues_working_one_time_token -- not duplicated here.

Run in-container:
    docker compose exec backend pytest tests/test_user_email_wiring.py -m integration -v
"""

from __future__ import annotations

import json
import logging
import socket
import uuid

import pytest
from aiosmtpd.controller import Controller
from aiosmtpd.handlers import Sink
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.auth import get_current_user
from app.config import get_settings
from app.crypto import encrypt_credential
from app.db import get_session
from app.main import app
from app.models import AuditLog, IntegrationConnection, Organization
from tests.conftest import _app_session, _authed, _grant, _make_fake_user

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _credential_key(monkeypatch):
    monkeypatch.setenv(
        "WINGRC_CREDENTIAL_ENCRYPTION_KEYS", f"test:{Fernet.generate_key().decode()}"
    )
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def fake_msp_admin():
    return _make_fake_user()


@pytest.fixture
def client(db_session, fake_msp_admin):
    app.dependency_overrides[get_session] = _app_session(db_session)
    app.dependency_overrides[get_current_user] = _authed(db_session, fake_msp_admin)
    yield TestClient(app)
    app.dependency_overrides.clear()


def _seed_own_org(db_session, fake_msp_admin) -> Organization:
    org = Organization(id=fake_msp_admin.org_id, name=f"EmailWiringOrg-{uuid.uuid4().hex[:8]}")
    db_session.add(org)
    db_session.flush()
    _grant(db_session, fake_msp_admin)
    return org


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _RecordingHandler(Sink):
    def __init__(self):
        self.messages: list[bytes] = []

    async def handle_DATA(self, server, session, envelope):
        self.messages.append(envelope.content)
        return "250 Message accepted for delivery"


@pytest.fixture
def fake_server():
    handler = _RecordingHandler()
    port = _free_port()
    controller = Controller(handler, hostname="127.0.0.1", port=port)
    controller.start()
    yield handler, port
    controller.stop()


def _configure_smtp(db_session, *, port: int) -> None:
    ciphertext, key_version = encrypt_credential(json.dumps({"username": "", "password": ""}))
    db_session.add(
        IntegrationConnection(
            connector_key="smtp",
            config={
                "host": "127.0.0.1",
                "port": str(port),
                "encryption_mode": "none",
                "from_address": "noreply@example.com",
                "from_name": "WinGRC",
                "verify_cert": "false",
            },
            encrypted_credential=ciphertext,
            credential_key_version=key_version,
            credential_hint=None,
        )
    )
    db_session.flush()


# ---------------------------------------------------------------------------
# Fallback: no WINGRC_PUBLIC_URL configured -- most common case today
# ---------------------------------------------------------------------------


def test_invite_without_public_url_still_returns_token(client, db_session, fake_msp_admin):
    _seed_own_org(db_session, fake_msp_admin)
    r = client.post(
        f"/orgs/{fake_msp_admin.org_id}/users",
        json={"email": "newbie@example.com", "display_name": "New User", "role": "customer_poc"},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["invite_token"]
    assert body["email_sent"] is False
    assert "WINGRC_PUBLIC_URL" in body["email_error"]


def test_reset_password_without_public_url_still_returns_token(
    client, db_session, fake_msp_admin
):
    _seed_own_org(db_session, fake_msp_admin)
    invite = client.post(
        f"/orgs/{fake_msp_admin.org_id}/users",
        json={"email": "target@example.com", "display_name": "Target", "role": "customer_poc"},
    ).json()

    r = client.post(f"/orgs/{fake_msp_admin.org_id}/users/{invite['id']}/reset-password")
    assert r.status_code == 200
    body = r.json()
    assert body["reset_token"]
    assert body["email_sent"] is False
    assert body["email_error"]


# ---------------------------------------------------------------------------
# WINGRC_PUBLIC_URL set, SMTP not configured
# ---------------------------------------------------------------------------


def test_invite_with_public_url_but_no_smtp_still_returns_token(
    client, db_session, fake_msp_admin, monkeypatch
):
    monkeypatch.setenv("WINGRC_PUBLIC_URL", "https://wingrc.example.com")
    get_settings.cache_clear()
    _seed_own_org(db_session, fake_msp_admin)

    r = client.post(
        f"/orgs/{fake_msp_admin.org_id}/users",
        json={"email": "newbie2@example.com", "display_name": "New User 2", "role": "customer_poc"},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["invite_token"]
    assert body["email_sent"] is False
    assert "not configured" in body["email_error"].lower()
    get_settings.cache_clear()


# ---------------------------------------------------------------------------
# WINGRC_PUBLIC_URL + SMTP both configured -- real send against fake server
# ---------------------------------------------------------------------------


def test_invite_sends_real_email_with_link_and_ttl_no_compliance_content(
    client, db_session, fake_msp_admin, fake_server, monkeypatch
):
    handler, port = fake_server
    monkeypatch.setenv("WINGRC_PUBLIC_URL", "https://wingrc.example.com")
    get_settings.cache_clear()
    _seed_own_org(db_session, fake_msp_admin)
    _configure_smtp(db_session, port=port)

    r = client.post(
        f"/orgs/{fake_msp_admin.org_id}/users",
        json={"email": "invitee@example.com", "display_name": "Invitee", "role": "customer_poc"},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["email_sent"] is True
    assert body["email_error"] is None

    assert len(handler.messages) == 1
    delivered = handler.messages[0].decode()
    assert "invitee@example.com" in delivered
    assert f"?invite_token={body['invite_token']}" in delivered
    assert "48" in delivered  # TTL stated in the body
    # Content rule: no compliance-domain vocabulary anywhere in the email.
    for forbidden in ("control", "SPRS", "finding", "evidence", "assessment", "score"):
        assert forbidden.lower() not in delivered.lower()

    get_settings.cache_clear()


def test_reset_password_sends_real_email(
    client, db_session, fake_msp_admin, fake_server, monkeypatch
):
    handler, port = fake_server
    monkeypatch.setenv("WINGRC_PUBLIC_URL", "https://wingrc.example.com")
    get_settings.cache_clear()
    _seed_own_org(db_session, fake_msp_admin)

    invite = client.post(
        f"/orgs/{fake_msp_admin.org_id}/users",
        json={"email": "resettarget@example.com", "display_name": "Target", "role": "customer_poc"},
    ).json()
    handler.messages.clear()  # drop the invite email sent above

    _configure_smtp(db_session, port=port)
    r = client.post(f"/orgs/{fake_msp_admin.org_id}/users/{invite['id']}/reset-password")
    assert r.status_code == 200
    body = r.json()
    assert body["email_sent"] is True

    assert len(handler.messages) == 1
    delivered = handler.messages[0].decode()
    assert f"?invite_token={body['reset_token']}" in delivered

    get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Token/secret hygiene: never in logs, never in the audit row
# ---------------------------------------------------------------------------


def test_email_send_audit_row_never_contains_the_token(
    client, db_session, fake_msp_admin, fake_server, monkeypatch
):
    handler, port = fake_server
    monkeypatch.setenv("WINGRC_PUBLIC_URL", "https://wingrc.example.com")
    get_settings.cache_clear()
    _seed_own_org(db_session, fake_msp_admin)
    _configure_smtp(db_session, port=port)

    r = client.post(
        f"/orgs/{fake_msp_admin.org_id}/users",
        json={"email": "audited@example.com", "display_name": "Audited", "role": "customer_poc"},
    )
    body = r.json()
    token = body["invite_token"]

    rows = db_session.scalars(
        select(AuditLog).where(
            AuditLog.org_id == fake_msp_admin.org_id, AuditLog.action == "email.send"
        )
    ).all()
    assert len(rows) == 1
    dumped = json.dumps(
        {
            "after": rows[0].after_value,
            "before": rows[0].before_value,
            "context": rows[0].context,
        }
    )
    assert token not in dumped
    assert rows[0].after_value["to"] == "audited@example.com"
    assert rows[0].after_value["template"] == "user_invite"
    assert rows[0].after_value["sent"] is True

    get_settings.cache_clear()


def test_invite_token_never_appears_in_logs(
    client, db_session, fake_msp_admin, fake_server, monkeypatch, caplog
):
    handler, port = fake_server
    monkeypatch.setenv("WINGRC_PUBLIC_URL", "https://wingrc.example.com")
    get_settings.cache_clear()
    _seed_own_org(db_session, fake_msp_admin)
    _configure_smtp(db_session, port=port)

    with caplog.at_level(logging.DEBUG):
        r = client.post(
            f"/orgs/{fake_msp_admin.org_id}/users",
            json={
                "email": "logcheck@example.com",
                "display_name": "Log Check",
                "role": "customer_poc",
            },
        )
    token = r.json()["invite_token"]
    log_text = "\n".join(rec.getMessage() for rec in caplog.records)
    assert token not in log_text

    get_settings.cache_clear()
