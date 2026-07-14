"""Unit + integration tests for auth.py.

Unit tests (no DB required):
  - password hashing round-trip
  - verify_password rejects wrong passwords
  - validate_password_policy length enforcement
  - state cookie sign/verify round-trip
  - state cookie rejects tampered signature
  - state cookie rejects expired payload
  - _lockout_duration_minutes caps at 8h

Integration tests (no DB — just checks request routing):
  - GET /health is still ungated → 200
  - GET /auth/me without cookie → 401
  - POST /auth/login with missing body → 422
"""
from __future__ import annotations

import time
import uuid

from fastapi.testclient import TestClient

from app.auth import (
    _lockout_duration_minutes,
    hash_password,
    make_state_payload,
    sign_state_cookie,
    validate_password_policy,
    verify_password,
    verify_state_cookie,
)
from app.main import app


# ---------------------------------------------------------------------------
# Unit: password hashing
# ---------------------------------------------------------------------------

def test_hash_verify_round_trip():
    pw = "correct-horse-battery-staple-and-then-some"
    stored = hash_password(pw)
    assert verify_password(pw, stored)


def test_verify_wrong_password():
    pw = "correct-horse-battery-staple-and-then-some"
    stored = hash_password(pw)
    assert not verify_password("wrong-password", stored)


def test_verify_empty_stored():
    assert not verify_password("anything", "")


def test_verify_malformed_stored():
    assert not verify_password("anything", "not$a$real$hash$with$too$many$parts")


# ---------------------------------------------------------------------------
# Unit: password policy
# ---------------------------------------------------------------------------

def test_policy_too_short():
    errors = validate_password_policy("short")
    assert any("15" in e for e in errors)


def test_policy_exactly_minimum():
    errors = validate_password_policy("a" * 15)
    assert not errors


def test_policy_too_long():
    errors = validate_password_policy("a" * 129)
    assert any("128" in e for e in errors)


# ---------------------------------------------------------------------------
# Unit: state cookie signing
# ---------------------------------------------------------------------------

def test_state_cookie_round_trip():
    payload = make_state_payload({"user_id": str(uuid.uuid4()), "phase": "verify"})
    signed = sign_state_cookie(payload)
    result = verify_state_cookie(signed)
    assert result is not None
    assert result["phase"] == "verify"


def test_state_cookie_tampered_signature():
    payload = make_state_payload({"x": "y"})
    signed = sign_state_cookie(payload)
    tampered = signed[:-4] + "XXXX"
    assert verify_state_cookie(tampered) is None


def test_state_cookie_expired():
    payload = {"x": "y", "exp": int(time.time()) - 1}
    signed = sign_state_cookie(payload)
    assert verify_state_cookie(signed) is None


# ---------------------------------------------------------------------------
# Unit: lockout duration
# ---------------------------------------------------------------------------

def test_lockout_first_lockout():
    assert _lockout_duration_minutes(1) == 15


def test_lockout_second_lockout():
    assert _lockout_duration_minutes(2) == 30


def test_lockout_caps_at_8_hours():
    assert _lockout_duration_minutes(100) == 480


# ---------------------------------------------------------------------------
# Integration: HTTP layer basics (no DB — just checks request routing)
# ---------------------------------------------------------------------------

_client = TestClient(app, raise_server_exceptions=False)


def test_health_is_ungated():
    r = _client.get("/health")
    assert r.status_code == 200


def test_me_without_session_returns_401():
    r = _client.get("/auth/me")
    assert r.status_code == 401


def test_local_login_missing_body_returns_422():
    r = _client.post("/auth/login", json={})
    assert r.status_code == 422
