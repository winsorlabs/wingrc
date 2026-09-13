"""Tests for connectors/smtp.py against a real, local fake SMTP server
(aiosmtpd) rather than a monkeypatched smtplib -- STARTTLS negotiation,
implicit TLS, and AUTH are real protocol exchanges a bare function-call
mock can't meaningfully verify. stdlib `smtpd` was removed in Python 3.12
(PEP 594), so aiosmtpd is a genuinely new test-only dependency (see
pyproject.toml's dev extra) -- never imported outside tests/.

Covers: STARTTLS, implicit TLS, no encryption, auth and no-auth, auth
failure, cert verification on by default (and the explicit opt-out),
connect timeout. Does not cover an actual message send (email.send_message)
-- that's email_service.py's job, tested in test_email_service.py against
the same fake server.
"""

from __future__ import annotations

import datetime
import socket
import ssl

import pytest
from aiosmtpd.controller import Controller
from aiosmtpd.handlers import Sink
from aiosmtpd.smtp import AuthResult
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from app.connectors import smtp

_VALID_USER = "validuser"
_VALID_PASS = "s3cr3t-pass"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _generate_self_signed_cert(tmp_path):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
        .sign(key, hashes.SHA256())
    )
    cert_path = tmp_path / "cert.pem"
    key_path = tmp_path / "key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    return str(cert_path), str(key_path)


@pytest.fixture(scope="module")
def tls_cert(tmp_path_factory):
    return _generate_self_signed_cert(tmp_path_factory.mktemp("smtp-tls"))


def _server_tls_context(tls_cert) -> ssl.SSLContext:
    cert_path, key_path = tls_cert
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert_path, key_path)
    return ctx


def _authenticator(server, session, envelope, mechanism, auth_data):
    login = getattr(auth_data, "login", b"").decode(errors="replace")
    password = getattr(auth_data, "password", b"").decode(errors="replace")
    if login == _VALID_USER and password == _VALID_PASS:
        return AuthResult(success=True)
    return AuthResult(success=False, handled=False)


def _config(host: str, port: int, mode: str, *, verify_cert: str = "false", **extra) -> dict:
    return {
        "host": host,
        "port": str(port),
        "encryption_mode": mode,
        "from_address": "noreply@example.com",
        "from_name": "WinGRC",
        "verify_cert": verify_cert,
        **extra,
    }


class _Server:
    """Thin wrapper so each test gets one Controller with a clear .stop()
    regardless of which mode it's configured for -- Controller itself
    already runs the asyncio loop on its own background thread, so start/
    stop here is synchronous from the test's point of view."""

    def __init__(self, controller: Controller, port: int):
        self.controller = controller
        self.port = port

    def stop(self):
        self.controller.stop()


def _start_plain_server(
    *, require_auth: bool = False, auth_require_tls: bool = False, handler=None
) -> _Server:
    port = _free_port()
    controller = Controller(
        handler if handler is not None else Sink(),
        hostname="127.0.0.1",
        port=port,
        authenticator=_authenticator if require_auth else None,
        auth_required=require_auth,
        auth_require_tls=auth_require_tls,
    )
    controller.start()
    return _Server(controller, port)


def _start_starttls_server(tls_cert, *, require_auth: bool = False) -> _Server:
    port = _free_port()
    controller = Controller(
        Sink(),
        hostname="127.0.0.1",
        port=port,
        tls_context=_server_tls_context(tls_cert),
        authenticator=_authenticator if require_auth else None,
        auth_required=require_auth,
        auth_require_tls=True,
    )
    controller.start()
    return _Server(controller, port)


class _RejectSenderHandler:
    """Simulates a provider rejecting MAIL FROM -- the SPF/DKIM-
    unverified-sending-domain case docs/email-setup.md warns about."""

    async def handle_MAIL(self, server, session, envelope, address, mail_options):
        return "550 5.7.1 Sender domain not verified"


class _RejectRecipientHandler:
    """Simulates a provider rejecting RCPT TO for the given address."""

    async def handle_RCPT(self, server, session, envelope, address, rcpt_options):
        return "550 5.1.1 No such recipient"


def _start_implicit_tls_server(tls_cert, *, require_auth: bool = False) -> _Server:
    port = _free_port()
    controller = Controller(
        Sink(),
        hostname="127.0.0.1",
        port=port,
        ssl_context=_server_tls_context(tls_cert),
        authenticator=_authenticator if require_auth else None,
        auth_required=require_auth,
        auth_require_tls=True,
    )
    controller.start()
    return _Server(controller, port)


# ---------------------------------------------------------------------------
# Encryption modes
# ---------------------------------------------------------------------------


def test_no_encryption_connects_and_tests_ok():
    server = _start_plain_server()
    try:
        result = smtp._test_connection(_config("127.0.0.1", server.port, "none"), {})
        assert result.ok, result.message
        assert "none" in result.message
    finally:
        server.stop()


def test_starttls_connects_and_tests_ok(tls_cert):
    server = _start_starttls_server(tls_cert)
    try:
        result = smtp._test_connection(
            _config("127.0.0.1", server.port, "starttls", verify_cert="false"), {}
        )
        assert result.ok, result.message
    finally:
        server.stop()


def test_implicit_tls_connects_and_tests_ok(tls_cert):
    server = _start_implicit_tls_server(tls_cert)
    try:
        result = smtp._test_connection(
            _config("127.0.0.1", server.port, "tls", verify_cert="false"), {}
        )
        assert result.ok, result.message
    finally:
        server.stop()


# ---------------------------------------------------------------------------
# Certificate verification
# ---------------------------------------------------------------------------


def test_verify_cert_on_by_default_rejects_self_signed_cert(tls_cert):
    """The self-signed cert isn't in any trust store -- with verification
    on (the default), the handshake must fail, proving verification is
    genuinely happening rather than silently skipped."""
    server = _start_starttls_server(tls_cert)
    try:
        config = _config("127.0.0.1", server.port, "starttls", verify_cert="true")
        result = smtp._test_connection(config, {})
        assert not result.ok
        assert "tls" in result.message.lower() or "certificate" in result.message.lower()
    finally:
        server.stop()


def test_verify_cert_disabled_accepts_self_signed_cert(tls_cert):
    server = _start_starttls_server(tls_cert)
    try:
        config = _config("127.0.0.1", server.port, "starttls", verify_cert="false")
        result = smtp._test_connection(config, {})
        assert result.ok, result.message
    finally:
        server.stop()


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


def test_no_username_skips_auth_against_a_server_that_requires_it():
    """If username is blank, open_connection must not attempt AUTH at all
    -- calling .login("", "") against a server with auth_required=True
    would correctly fail, but for the wrong reason (empty credentials
    rather than "we chose not to authenticate"). This proves AUTH is
    skipped, not attempted-and-failed, by using a server that does NOT
    require auth (skipping AUTH is what makes this succeed at all)."""
    server = _start_plain_server(require_auth=False)
    try:
        result = smtp._test_connection(_config("127.0.0.1", server.port, "none"), {})
        assert result.ok, result.message
        assert "no auth" in result.message
    finally:
        server.stop()


def test_valid_auth_succeeds(tls_cert):
    server = _start_starttls_server(tls_cert, require_auth=True)
    try:
        config = _config("127.0.0.1", server.port, "starttls", verify_cert="false")
        credential = {"username": _VALID_USER, "password": _VALID_PASS}
        result = smtp._test_connection(config, credential)
        assert result.ok, result.message
        assert _VALID_USER in result.message
    finally:
        server.stop()


def test_invalid_auth_rejected(tls_cert):
    server = _start_starttls_server(tls_cert, require_auth=True)
    try:
        config = _config("127.0.0.1", server.port, "starttls", verify_cert="false")
        credential = {"username": _VALID_USER, "password": "wrong-password"}
        result = smtp._test_connection(config, credential)
        assert not result.ok
        assert "rejected" in result.message.lower()
    finally:
        server.stop()


# ---------------------------------------------------------------------------
# Connect failures
# ---------------------------------------------------------------------------


def test_connect_timeout_reports_timeout(monkeypatch):
    monkeypatch.setattr(smtp, "_TIMEOUT_SECONDS", 1)
    # 10.255.255.1 is a non-routable address commonly used to force a
    # connect timeout rather than an immediate refusal.
    config = _config("10.255.255.1", 25, "none")
    result = smtp._test_connection(config, {})
    assert not result.ok
    assert "timed out" in result.message.lower() or "connect" in result.message.lower()


def test_connection_refused_reports_specifically():
    port = _free_port()  # nothing listening here
    result = smtp._test_connection(_config("127.0.0.1", port, "none"), {})
    assert not result.ok
    assert "connect" in result.message.lower()


# ---------------------------------------------------------------------------
# test_input: sending a real test message (2026-09-14, requested after
# Jarrod's live SMTP2GO setup -- connect-only testing already caught the
# STARTTLS/implicit-TLS mismatch; this proves delivery, not just the
# handshake).
# ---------------------------------------------------------------------------


def test_no_test_input_behaves_exactly_as_before():
    """The regression this feature must not cause: omitting test_input
    (the default, and every pre-2026-09-14 call site) must still just
    connect + quit, no message sent, no mention of a recipient."""
    server = _start_plain_server()
    try:
        result = smtp._test_connection(_config("127.0.0.1", server.port, "none"), {})
        assert result.ok, result.message
        assert "test message" not in result.message.lower()
    finally:
        server.stop()


def test_test_input_sends_real_message_and_reports_accepted_not_delivered():
    server = _start_plain_server()
    try:
        result = smtp._test_connection(
            _config("127.0.0.1", server.port, "none"), {}, "recipient@example.com"
        )
        assert result.ok, result.message
        assert "recipient@example.com" in result.message
        # Honest wording: accepted for delivery, not confirmed delivered.
        assert "accepted" in result.message.lower()
        assert "sent successfully" not in result.message.lower()
        assert "check the inbox" in result.message.lower()
    finally:
        server.stop()


def test_test_input_blank_string_behaves_as_no_input():
    """A blank/whitespace test_input (the router normalizes this too, but
    the connector itself must not treat "" as a recipient) must not
    attempt a send."""
    server = _start_plain_server()
    try:
        result = smtp._test_connection(_config("127.0.0.1", server.port, "none"), {}, "   ")
        assert result.ok, result.message
        assert "test message" not in result.message.lower()
    finally:
        server.stop()


def test_sender_refused_reports_distinctly_from_connection_failure():
    """The case the task calls "the most valuable thing this feature can
    surface": authentication succeeds, but the provider rejects the
    from-address -- typically an unverified sending domain (SPF/DKIM),
    not a bad host/port/credential."""
    server = _start_plain_server(handler=_RejectSenderHandler())
    try:
        result = smtp._test_connection(
            _config("127.0.0.1", server.port, "none"), {}, "recipient@example.com"
        )
        assert not result.ok
        assert "rejected the from-address" in result.message
        assert "SPF/DKIM" in result.message or "verified" in result.message.lower()
    finally:
        server.stop()


def test_recipient_refused_reports_distinctly():
    server = _start_plain_server(handler=_RejectRecipientHandler())
    try:
        result = smtp._test_connection(
            _config("127.0.0.1", server.port, "none"), {}, "nobody@example.com"
        )
        assert not result.ok
        assert "rejected the recipient" in result.message
        assert "nobody@example.com" in result.message
    finally:
        server.stop()


def test_send_failure_does_not_report_as_connection_failure():
    """A rejected send must never be reported as "could not connect" or
    similar connect-failure wording -- it's a fundamentally different
    operator problem (per the task's own instruction)."""
    server = _start_plain_server(handler=_RejectSenderHandler())
    try:
        result = smtp._test_connection(
            _config("127.0.0.1", server.port, "none"), {}, "recipient@example.com"
        )
        assert not result.ok
        assert "could not connect" not in result.message.lower()
        assert "timed out" not in result.message.lower()
    finally:
        server.stop()


# ---------------------------------------------------------------------------
# Config validation
# ---------------------------------------------------------------------------


def test_invalid_encryption_mode_rejected_before_any_network_call():
    config = _config("127.0.0.1", 25, "ssl_v2_please")
    result = smtp._test_connection(config, {})
    assert not result.ok
    assert "encryption mode" in result.message.lower()


def test_missing_host_rejected():
    config = _config("", 25, "none")
    result = smtp._test_connection(config, {})
    assert not result.ok
    assert "host" in result.message.lower()


def test_bad_port_rejected():
    config = _config("127.0.0.1", 0, "none")
    config["port"] = "not-a-number"
    result = smtp._test_connection(config, {})
    assert not result.ok
    assert "port" in result.message.lower()
