"""Integration tests for POST /orgs/{org_id}/users/api (API-user creation)
and the user_id-targeted (on-behalf-of) extension to POST .../api-tokens.

Covers:
  - happy path: create an API user, get a token, actually authenticate a
    real request with it (proves the account is usable, not just insertable)
  - msp_engineer attempting on-behalf-of token creation -> 403
  - user_id pointing at a real user in a different org -> 404
  - msp_admin minting a token above the target user's own role -> 403
  - invite_user still rejects login_method="api" (_VALID_METHODS)
  - I.3: a token minted at one role clamps to the user's current role on
    demotion, and does not escalate on promotion (auth.py's
    _resolve_api_token, via effective_role = min(token role, user role))
  - I.3: patch_user(is_active=False) revokes the user's live sessions,
    same as deactivate_user
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.auth import CurrentUser, create_session, get_current_user
from app.db import get_session
from app.main import app
from app.models import Organization, User
from tests.conftest import _app_session, _authed


@pytest.fixture
def client(db_session, fake_msp_admin):
    app.dependency_overrides[get_session] = _app_session(db_session)
    app.dependency_overrides[get_current_user] = _authed(db_session, fake_msp_admin)
    yield TestClient(app)
    app.dependency_overrides.clear()


def _seed_org(db_session, org_id: uuid.UUID) -> Organization:
    org = Organization(id=org_id, name=f"ApiTokenTestOrg-{uuid.uuid4().hex[:8]}")
    db_session.add(org)
    db_session.flush()
    return org


def _seed_user(db_session, *, org_id: uuid.UUID, role: str) -> User:
    user = User(
        home_org_id=org_id,
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        display_name="Seeded User",
        login_method="local",
        role=role,
        is_active=True,
    )
    db_session.add(user)
    db_session.flush()
    return user


def _as_role(role: str, *, org_id: uuid.UUID) -> CurrentUser:
    return CurrentUser(
        id=uuid.uuid4(),
        org_id=org_id,
        email=f"{role}@example.com",
        display_name=role,
        role=role,
        is_active=True,
        login_method="local",
        mfa_enrolled=True,
    )


# ---------------------------------------------------------------------------
# 1. Happy path: create API user, then actually authenticate with its token
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_api_user_token_actually_authenticates(client, db_session, fake_msp_admin):
    _seed_org(db_session, fake_msp_admin.org_id)

    r = client.post(
        f"/orgs/{fake_msp_admin.org_id}/users/api",
        json={"display_name": "CI Bot", "role": "customer_poc"},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["role"] == "customer_poc"
    token = body["token"]

    # Drop the get_current_user override so the real Bearer-token resolution
    # path (get_current_user -> _resolve_api_token) actually runs against
    # the token just minted, instead of the usual test-fixture bypass.
    del app.dependency_overrides[get_current_user]
    r2 = client.get(
        f"/orgs/{fake_msp_admin.org_id}/users",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r2.status_code == 200


# ---------------------------------------------------------------------------
# 2. msp_engineer attempting on-behalf-of token creation -> 403
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_create_api_token_on_behalf_of_by_engineer_403(client, db_session, fake_msp_admin):
    _seed_org(db_session, fake_msp_admin.org_id)
    target = _seed_user(db_session, org_id=fake_msp_admin.org_id, role="msp_engineer")

    app.dependency_overrides[get_current_user] = lambda: _as_role(
        "msp_engineer", org_id=fake_msp_admin.org_id
    )
    r = client.post(
        f"/orgs/{fake_msp_admin.org_id}/api-tokens",
        json={"name": "x", "role": "msp_engineer", "user_id": str(target.id)},
    )
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# 3. user_id in a different org -> 404 (exercised through the endpoint)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_create_api_token_on_behalf_of_wrong_org_404(client, db_session, fake_msp_admin):
    other_org = Organization(name=f"OtherOrg-{uuid.uuid4().hex[:8]}")
    db_session.add(other_org)
    db_session.flush()
    target = _seed_user(db_session, org_id=other_org.id, role="msp_engineer")

    r = client.post(
        f"/orgs/{fake_msp_admin.org_id}/api-tokens",
        json={"name": "x", "role": "msp_engineer", "user_id": str(target.id)},
    )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# 4. msp_admin minting a token above the target user's own role -> 403
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_create_api_token_on_behalf_of_exceeds_target_role_403(client, db_session, fake_msp_admin):
    _seed_org(db_session, fake_msp_admin.org_id)
    target = _seed_user(db_session, org_id=fake_msp_admin.org_id, role="customer_poc")

    r = client.post(
        f"/orgs/{fake_msp_admin.org_id}/api-tokens",
        json={"name": "x", "role": "msp_admin", "user_id": str(target.id)},
    )
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# 5. invite_user still rejects login_method="api"
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_invite_user_rejects_api_login_method(client, fake_msp_admin):
    payload = {
        "email": f"{uuid.uuid4().hex[:8]}@example.com",
        "display_name": "Sneaky",
        "role": "customer_poc",
        "login_method": "api",
    }
    r = client.post(f"/orgs/{fake_msp_admin.org_id}/users", json=payload)
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# I.3: token/role coherence — a token cannot outlive the privilege of the
# user behind it, and promotion does not retroactively escalate one already
# minted at a lower role.
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_api_token_role_clamps_after_demotion(client, db_session, fake_msp_admin):
    _seed_org(db_session, fake_msp_admin.org_id)
    target = _seed_user(db_session, org_id=fake_msp_admin.org_id, role="msp_admin")

    minted = client.post(
        f"/orgs/{fake_msp_admin.org_id}/api-tokens",
        json={"name": "x", "role": "msp_admin", "user_id": str(target.id)},
    )
    assert minted.status_code == 201
    token = minted.json()["token"]

    patched = client.patch(
        f"/orgs/{fake_msp_admin.org_id}/users/{target.id}",
        json={"role": "customer_poc"},
    )
    assert patched.status_code == 200

    # Drop the fixture override so the token resolves through the real
    # get_current_user -> _resolve_api_token path instead of the test bypass.
    del app.dependency_overrides[get_current_user]

    me = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["role"] == "customer_poc"

    r = client.post(
        f"/orgs/{fake_msp_admin.org_id}/users",
        json={
            "email": f"{uuid.uuid4().hex[:8]}@example.com",
            "display_name": "Should be blocked",
            "role": "customer_poc",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 403


@pytest.mark.integration
def test_api_token_role_not_escalated_after_promotion(client, db_session, fake_msp_admin):
    _seed_org(db_session, fake_msp_admin.org_id)
    target = _seed_user(db_session, org_id=fake_msp_admin.org_id, role="customer_poc")

    minted = client.post(
        f"/orgs/{fake_msp_admin.org_id}/api-tokens",
        json={"name": "x", "role": "customer_poc", "user_id": str(target.id)},
    )
    assert minted.status_code == 201
    token = minted.json()["token"]

    patched = client.patch(
        f"/orgs/{fake_msp_admin.org_id}/users/{target.id}",
        json={"role": "msp_admin"},
    )
    assert patched.status_code == 200

    del app.dependency_overrides[get_current_user]

    me = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["role"] == "customer_poc"


# ---------------------------------------------------------------------------
# I.3: patch_user(is_active=False) revokes live sessions, same as
# deactivate_user
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_patch_user_is_active_false_revokes_sessions(client, db_session, fake_msp_admin):
    _seed_org(db_session, fake_msp_admin.org_id)
    target = _seed_user(db_session, org_id=fake_msp_admin.org_id, role="customer_poc")

    session_row, _raw = create_session(db_session, target)
    db_session.flush()
    assert session_row.revoked_at is None

    r = client.patch(
        f"/orgs/{fake_msp_admin.org_id}/users/{target.id}",
        json={"is_active": False},
    )
    assert r.status_code == 200

    db_session.refresh(session_row)
    assert session_row.revoked_at is not None
