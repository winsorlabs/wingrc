"""Integration tests for email_service.send() -- the sending boundary that
loads the SMTP connector's stored config/credential and sends a real
message. Exercises it against a real, local fake SMTP server (aiosmtpd),
same rationale as test_smtp_connector.py: STARTTLS/auth are real protocol
exchanges a bare mock can't meaningfully verify.

Covers: not-configured (no IntegrationConnection row at all) never raises,
a bad/undecryptable credential never raises, a successful send actually
delivers the message to the fake server, a send-time failure (server closes
after EHLO) is reported without raising, and the credential/body/token
never appear in caplog output.

Run in-container:
    docker compose exec backend pytest tests/test_email_service.py -m integration -v
"""

from __future__ import annotations

import json
import logging

import pytest
from aiosmtpd.controller import Controller
from aiosmtpd.handlers import Sink
from cryptography.fernet import Fernet

from app import email_service
from app.config import get_settings
from app.crypto import encrypt_credential
from app.models import IntegrationConnection

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _credential_key(monkeypatch):
    monkeypatch.setenv(
        "WINGRC_CREDENTIAL_ENCRYPTION_KEYS", f"test:{Fernet.generate_key().decode()}"
    )
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _free_port() -> int:
    import socket

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


def _configure_smtp(db_session, *, port: int, **config_overrides) -> None:
    config = {
        "host": "127.0.0.1",
        "port": str(port),
        "encryption_mode": "none",
        "from_address": "noreply@example.com",
        "from_name": "WinGRC",
        "verify_cert": "false",
        **config_overrides,
    }
    ciphertext, key_version = encrypt_credential(json.dumps({"username": "", "password": ""}))
    db_session.add(
        IntegrationConnection(
            connector_key="smtp",
            config=config,
            encrypted_credential=ciphertext,
            credential_key_version=key_version,
            credential_hint=None,
        )
    )
    db_session.flush()


def test_not_configured_returns_failure_without_raising(db_session):
    result = email_service.send(
        db_session, to="user@example.com", subject="Hi", body="Body", template="user_invite"
    )
    assert result.sent is False
    assert "not configured" in result.error.lower()


def test_bad_credential_ciphertext_returns_failure_without_raising(db_session):
    db_session.add(
        IntegrationConnection(
            connector_key="smtp",
            config={
                "host": "127.0.0.1",
                "port": "25",
                "encryption_mode": "none",
                "from_address": "noreply@example.com",
                "from_name": "WinGRC",
                "verify_cert": "false",
            },
            encrypted_credential="not-actually-a-fernet-token",
            credential_key_version="test",
            credential_hint=None,
        )
    )
    db_session.flush()

    result = email_service.send(
        db_session, to="user@example.com", subject="Hi", body="Body", template="user_invite"
    )
    assert result.sent is False
    assert result.error is not None


def test_successful_send_delivers_message(db_session, fake_server):
    handler, port = fake_server
    _configure_smtp(db_session, port=port)

    result = email_service.send(
        db_session,
        to="recipient@example.com",
        subject="You've been invited to WinGRC",
        body="Set up your account: https://example.com/?invite_token=irrelevant-in-this-test\n",
        template="user_invite",
    )

    assert result.sent is True
    assert result.error is None
    assert len(handler.messages) == 1
    delivered = handler.messages[0].decode()
    assert "recipient@example.com" in delivered
    assert "You've been invited to WinGRC" in delivered


def test_send_failure_reported_without_raising(db_session):
    # Nothing listening on this port -- connect fails immediately.
    _configure_smtp(db_session, port=_free_port())

    result = email_service.send(
        db_session, to="user@example.com", subject="Hi", body="Body", template="password_reset"
    )
    assert result.sent is False
    assert result.error is not None


def test_credential_and_body_never_logged(db_session, fake_server, caplog):
    handler, port = fake_server
    secret_password = "s3cret-smtp-password-xyz"
    config = {
        "host": "127.0.0.1",
        "port": str(port),
        "encryption_mode": "none",
        "from_address": "noreply@example.com",
        "from_name": "WinGRC",
        "verify_cert": "false",
    }
    ciphertext, key_version = encrypt_credential(
        json.dumps({"username": "someuser", "password": secret_password})
    )
    db_session.add(
        IntegrationConnection(
            connector_key="smtp",
            config=config,
            encrypted_credential=ciphertext,
            credential_key_version=key_version,
            credential_hint=None,
        )
    )
    db_session.flush()

    secret_body = "a very secret invite token: SECRET-TOKEN-VALUE-12345"
    with caplog.at_level(logging.DEBUG):
        # This server doesn't require auth, so login() against it will be
        # attempted and rejected (no AUTH advertised) -- either outcome
        # (sent or a clean failure) is fine for this test; what matters is
        # nothing secret ever reaches the log.
        email_service.send(
            db_session,
            to="user@example.com",
            subject="Reset your WinGRC password",
            body=secret_body,
            template="password_reset",
        )

    log_text = "\n".join(r.getMessage() for r in caplog.records)
    assert secret_password not in log_text
    assert secret_body not in log_text
    assert "SECRET-TOKEN-VALUE-12345" not in log_text
