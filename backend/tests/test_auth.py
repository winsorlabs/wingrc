"""Unit + integration tests for auth password hashing, policy, and HTTP routing."""
from __future__ import annotations

import hashlib

import pytest
from fastapi.testclient import TestClient

from app.auth import hash_password, validate_password_policy, verify_password
from app.main import app


# ---------------------------------------------------------------------------
# Unit: password hashing
# ---------------------------------------------------------------------------


def test_hash_verify_round_trip():
    pw = "correct-horse-battery-staple-and-then-some"
    assert verify_password(pw, hash_password(pw))


def test_verify_wrong_password():
    stored = hash_password("correct-horse-battery-staple-and-then-some")
    assert not verify_password("wrong-password", stored)


def test_verify_empty_stored():
    assert not verify_password("anything", "")


def test_verify_malformed_stored():
    assert not verify_password("anything", "bad$hash")


def test_hash_uses_pbkdf2_sha256():
    pw = "correct-horse-battery-staple-and-then-some"
    stored = hash_password(pw)
    iterations_str, salt_hex, expected_hex = stored.split("$")
    key = hashlib.pbkdf2_hmac(
        "sha256",
        pw.encode(),
        bytes.fromhex(salt_hex),
        int(iterations_str),
    )
    assert key.hex() == expected_hex


# ---------------------------------------------------------------------------
# Unit: password policy
# ---------------------------------------------------------------------------


def test_policy_too_short():
    assert any("15" in e for e in validate_password_policy("short"))


def test_policy_minimum_ok():
    assert not validate_password_policy("a" * 15)


def test_policy_too_long():
    assert any("128" in e for e in validate_password_policy("a" * 129))


# ---------------------------------------------------------------------------
# Integration: HTTP routing (no DB)
# ---------------------------------------------------------------------------


@pytest.fixture
def http_client():
    return TestClient(app, raise_server_exceptions=False)


def test_health_is_ungated(http_client):
    assert http_client.get("/health").status_code == 200


def test_me_without_session_returns_401(http_client):
    assert http_client.get("/auth/me").status_code == 401


def test_local_login_missing_body_returns_422(http_client):
    assert http_client.post("/auth/login", json={}).status_code == 422
