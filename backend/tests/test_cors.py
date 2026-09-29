"""CORS middleware tests.

No database required — exercises the middleware layer only via /health.
These run in the standard (non-integration) test suite.

/health is a readiness probe as of the wingrc_app cutover: it returns 503
without a database. These tests are about CORS reflection, not health, so
they use conftest's `health_probe_client`, whose /health can answer. Using
a real 200 rather than tolerating a 503 keeps the assertion honest -- a
route that 503s still gets CORS headers, so accepting either would have
made these tests pass on a broken app.
"""
from __future__ import annotations

ALLOWED = "http://localhost:5173"
OTHER_ALLOWED = "http://10.10.24.35:5173"
UNKNOWN = "http://evil.example.com"


def test_allowed_origin_reflected(health_probe_client):
    r = health_probe_client.get("/health", headers={"Origin": ALLOWED})
    assert r.status_code == 200
    assert r.headers.get("access-control-allow-origin") == ALLOWED


def test_second_allowed_origin_reflected(health_probe_client):
    r = health_probe_client.get("/health", headers={"Origin": OTHER_ALLOWED})
    assert r.status_code == 200
    assert r.headers.get("access-control-allow-origin") == OTHER_ALLOWED


def test_unknown_origin_not_reflected(health_probe_client):
    r = health_probe_client.get("/health", headers={"Origin": UNKNOWN})
    assert r.status_code == 200
    assert r.headers.get("access-control-allow-origin") != UNKNOWN


def test_preflight_allowed_origin(health_probe_client):
    r = health_probe_client.options(
        "/health",
        headers={
            "Origin": ALLOWED,
            "Access-Control-Request-Method": "GET",
        },
    )
    assert r.status_code == 200
    assert r.headers.get("access-control-allow-origin") == ALLOWED
