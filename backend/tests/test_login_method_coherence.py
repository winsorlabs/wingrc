"""Integration tests for I.6 — login_method coherence.

test_invite_user_rejects_api_login_method (test_api_tokens.py) covers
rejection at invite time. This covers enforcement at the actual
authentication boundary: local_login must refuse a user whose
login_method isn't "local", for every other value the column allows
(entra, api) — and still accept one whose login_method genuinely is
"local".

Run in-container:
    docker compose exec backend pytest tests/test_login_method_coherence.py -m integration -v
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.auth import hash_password
from app.db import get_session
from app.main import app
from app.models import Organization, User
from tests.conftest import _app_session

_PASSWORD = "correct-horse-battery-staple-and-then-some"


@pytest.fixture
def client(db_session):
    app.dependency_overrides[get_session] = _app_session(db_session)
    yield TestClient(app)
    app.dependency_overrides.clear()


def _seed_user(db_session, *, login_method: str) -> User:
    org = Organization(name=f"LoginMethodOrg-{uuid.uuid4().hex[:8]}")
    db_session.add(org)
    db_session.flush()
    user = User(
        org_id=org.id,
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        display_name="Login Method Test User",
        login_method=login_method,
        role="customer_poc",
        is_active=True,
        password_hash=hash_password(_PASSWORD) if login_method == "local" else None,
    )
    db_session.add(user)
    db_session.flush()
    return user


@pytest.mark.integration
@pytest.mark.parametrize("login_method", ["entra", "api"])
def test_local_login_rejects_non_local_login_method(client, db_session, login_method):
    user = _seed_user(db_session, login_method=login_method)
    r = client.post("/auth/login", json={"email": user.email, "password": "whatever-they-guess"})
    assert r.status_code == 401


@pytest.mark.integration
def test_local_login_accepts_local_login_method(client, db_session):
    user = _seed_user(db_session, login_method="local")
    r = client.post("/auth/login", json={"email": user.email, "password": _PASSWORD})
    # Not MFA-enrolled yet -> proceeds to {"next": "enroll"} rather than
    # being rejected for its login_method.
    assert r.status_code == 200
    assert r.json()["next"] == "enroll"
