"""tls_expiry.py: reading the certificate a server actually presents, and
the verdict the banner shows. No database: the reading is exercised
against a real TLS server on localhost, serving certificates made here,
because "configured to check" is not "checks" -- the July certificate
lapsed with nothing watching it."""

from __future__ import annotations

import socket
import ssl
import threading
from datetime import UTC, datetime, timedelta

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from app import tls_expiry

NOW = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)


def _cert_files(tmp_path, not_after: datetime) -> tuple[str, str]:
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "dev.example.test")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(not_after - timedelta(days=90))
        .not_valid_after(not_after)
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = tmp_path / "cert.pem", tmp_path / "key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ))
    return str(cert_path), str(key_path)


@pytest.fixture
def tls_server(tmp_path):
    """Serve one handshake with a certificate expiring at the given time;
    yields the port."""
    servers = []

    def start(not_after: datetime) -> int:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(*_cert_files(tmp_path, not_after))
        listener = socket.create_server(("127.0.0.1", 0))
        servers.append(listener)

        def serve():
            try:
                conn, _ = listener.accept()
                with ctx.wrap_socket(conn, server_side=True) as tls:
                    tls.recv(1)
            except OSError:
                pass

        threading.Thread(target=serve, daemon=True).start()
        return listener.getsockname()[1]

    yield start
    for s in servers:
        s.close()


def _free_port() -> int:
    with socket.create_server(("127.0.0.1", 0)) as s:
        return s.getsockname()[1]


def test_targets_are_the_https_public_hosts_once_each():
    assert tls_expiry.targets(
        "https://dev.example.test/", "https://storage.dev.example.test", "https://dev.example.test"
    ) == ["dev.example.test", "storage.dev.example.test"]
    assert tls_expiry.targets("http://dev.example.test", None, "") == [], (
        "a deployment without https serves no certificate of ours to check"
    )


def test_reads_the_expiry_of_the_certificate_the_server_presents(tls_server):
    not_after = (NOW + timedelta(days=12)).replace(microsecond=0)
    port = tls_server(not_after)
    r = tls_expiry.read_served("dev.example.test", "127.0.0.1", port)
    assert r.error is None
    assert r.not_after == not_after


def test_an_expired_certificate_is_still_read_not_refused(tls_server):
    # Verification off is the point: an expired certificate is exactly
    # the one that has to be reported.
    not_after = datetime(2026, 10, 15, 0, 4, 38, tzinfo=UTC)
    r = tls_expiry.read_served("dev.example.test", "127.0.0.1", tls_server(not_after))
    assert r.not_after == not_after


def test_nothing_listening_is_an_error_reading_not_an_exception():
    r = tls_expiry.read_served("dev.example.test", "127.0.0.1", _free_port(), timeout=2)
    assert r.not_after is None
    assert r.error


def _status(certs, checked_at=NOW - timedelta(hours=1), configured=True):
    result = tls_expiry.to_result([
        tls_expiry.Reading(name=n, not_after=a, error=e) for n, a, e in certs
    ])
    return tls_expiry.status(
        configured=configured, result=result, checked_at=checked_at, now=NOW
    )


def test_a_fresh_reading_outside_the_window_needs_no_attention():
    s = _status([("dev.example.test", NOW + timedelta(days=31), None)])
    assert s["attention"] is False
    assert s["certs"][0]["verdict"] == "ok"
    assert s["certs"][0]["days_left"] == 31


@pytest.mark.parametrize(("delta", "verdict"), [
    (timedelta(days=30), "expiring"),
    (timedelta(days=1), "expiring"),
    (timedelta(seconds=0), "expired"),
    (timedelta(days=-2), "expired"),
])
def test_inside_the_window_or_past_it_needs_attention(delta, verdict):
    s = _status([
        ("dev.example.test", NOW + timedelta(days=80), None),
        ("storage.dev.example.test", NOW + delta, None),
    ])
    assert s["attention"] is True
    assert [c["verdict"] for c in s["certs"]] == ["ok", verdict]


def test_an_unreadable_certificate_needs_attention():
    s = _status([("dev.example.test", None, "ConnectionRefusedError: refused")])
    assert s["attention"] is True
    assert s["certs"][0]["verdict"] == "unreadable"
    assert s["certs"][0]["error"] == "ConnectionRefusedError: refused"


def test_a_check_that_has_not_run_recently_needs_attention_whatever_it_last_said():
    fine = [("dev.example.test", NOW + timedelta(days=80), None)]
    assert _status(fine, checked_at=NOW - timedelta(hours=47))["attention"] is False, (
        "fixture must be fresh-and-fine at 47 hours or the next assert proves nothing"
    )
    s = _status(fine, checked_at=NOW - timedelta(hours=49))
    assert s["stale"] is True
    assert s["attention"] is True


def test_never_run_needs_attention():
    s = tls_expiry.status(configured=True, result=None, checked_at=None, now=NOW)
    assert s["stale"] is True
    assert s["attention"] is True


def test_a_reading_with_no_certificates_needs_attention():
    s = _status([])
    assert s["attention"] is True


def test_not_configured_is_quiet():
    s = tls_expiry.status(configured=False, result=None, checked_at=None, now=NOW)
    assert s == {"configured": False, "attention": False, "checked_at": None,
                 "stale": False, "certs": []}
