"""CORS middleware tests.

No database required — exercises the middleware layer only via /health.
These run in the standard (non-integration) test suite.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

ALLOWED = "http://localhost:5173"
OTHER_ALLOWED = "http://10.10.24.35:5173"
UNKNOWN = "http://evil.example.com"


def test_allowed_origin_reflected():
    r = client.get("/health", headers={"Origin": ALLOWED})
    assert r.status_code == 200
    assert r.headers.get("access-control-allow-origin") == ALLOWED


def test_second_allowed_origin_reflected():
    r = client.get("/health", headers={"Origin": OTHER_ALLOWED})
    assert r.status_code == 200
    assert r.headers.get("access-control-allow-origin") == OTHER_ALLOWED


def test_unknown_origin_not_reflected():
    r = client.get("/health", headers={"Origin": UNKNOWN})
    assert r.status_code == 200
    assert r.headers.get("access-control-allow-origin") != UNKNOWN


def test_preflight_allowed_origin():
    r = client.options(
        "/health",
        headers={
            "Origin": ALLOWED,
            "Access-Control-Request-Method": "GET",
        },
    )
    assert r.status_code == 200
    assert r.headers.get("access-control-allow-origin") == ALLOWED
