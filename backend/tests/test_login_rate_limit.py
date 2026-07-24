"""Integration tests for I.6 — login rate limit by source IP.

Distinct from per-account lockout (apply_failed_login): that gates one
account after repeated failures against it, which alone permits spraying
one attempt each across many accounts from a single source since no
individual account ever reaches its own threshold. This gates the source
address instead, independent of which account(s) it targets.

Run in-container:
    docker compose exec backend pytest tests/test_login_rate_limit.py -m integration -v
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.auth import _LOGIN_RATE_LIMIT, _login_attempts
from app.db import get_session
from app.main import app
from tests.conftest import _app_session


@pytest.fixture(autouse=True)
def _clear_rate_limiter():
    """The counter is module-level in-memory state, shared across every
    test in the process — clear it before and after each test so one
    test's attempts can't push another over the limit."""
    _login_attempts.clear()
    yield
    _login_attempts.clear()


@pytest.fixture
def client(db_session):
    app.dependency_overrides[get_session] = _app_session(db_session)
    yield TestClient(app)
    app.dependency_overrides.clear()


def _login(client: TestClient, ip: str, email: str | None = None):
    return client.post(
        "/auth/login",
        json={
            "email": email or f"{uuid.uuid4().hex[:8]}@example.com",
            "password": "wrong-password",
        },
        headers={"X-Real-IP": ip},
    )


@pytest.mark.integration
def test_under_limit_returns_normal_auth_error(client):
    ip = "203.0.113.10"
    for _ in range(_LOGIN_RATE_LIMIT):
        r = _login(client, ip)
        assert r.status_code == 401


@pytest.mark.integration
def test_over_limit_from_one_ip_returns_429_even_spraying_different_accounts(client):
    """The exact scenario the plan doc calls out: each attempt here targets
    a different, nonexistent account, so no single account's failure
    counter ever climbs — apply_failed_login never fires. Only the
    IP-scoped limiter can catch this."""
    ip = "203.0.113.11"
    for _ in range(_LOGIN_RATE_LIMIT):
        r = _login(client, ip, email=f"{uuid.uuid4().hex[:8]}@example.com")
        assert r.status_code == 401
    r = _login(client, ip, email=f"{uuid.uuid4().hex[:8]}@example.com")
    assert r.status_code == 429


@pytest.mark.integration
def test_limit_is_scoped_per_ip(client):
    ip_a = "203.0.113.12"
    ip_b = "203.0.113.13"
    for _ in range(_LOGIN_RATE_LIMIT):
        _login(client, ip_a)
    assert _login(client, ip_a).status_code == 429
    # A different source address has its own independent counter.
    assert _login(client, ip_b).status_code == 401
