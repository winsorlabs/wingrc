"""Unit tests for the HMAC-SHA256 signed state-cookie helpers in auth.py."""
from __future__ import annotations

from app.auth import make_state_payload, sign_state_cookie, verify_state_cookie

_EXPIRED_TS = 0  # Unix epoch — always in the past


def test_state_cookie_round_trip():
    payload = make_state_payload({"user_id": "00000000-0000-0000-0000-000000000001",
                                  "phase": "verify"})
    result = verify_state_cookie(sign_state_cookie(payload))
    assert result is not None
    assert result["phase"] == "verify"


def test_state_cookie_tampered():
    signed = sign_state_cookie(make_state_payload({"x": "y"}))
    assert verify_state_cookie(signed[:-4] + "XXXX") is None


def test_state_cookie_expired():
    assert verify_state_cookie(sign_state_cookie({"x": "y", "exp": _EXPIRED_TS})) is None
