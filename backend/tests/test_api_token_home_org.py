"""API tokens outside the user's home org (2026-10-08).

The `user` RLS policy is home_org_id = app.current_org, and
_resolve_api_token sets the org to the token's org before loading the
user. So a token issued in any org other than its user's home org can never
authenticate. Two small pieces landed ahead of the auth plan's full fix:
creation is refused instead of minting a dead credential, and the error for
one that already exists says what is true instead of "Account deactivated".
It fails closed -- a usability defect, not a security hole.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.auth import generate_secret, get_current_user
from app.db import get_session
from app.main import app
from app.models import ApiToken, Organization, User
from tests.conftest import _app_session, _authed, _grant, _make_fake_user

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


def _two_orgs_one_engineer(db_session):
    home = Organization(name=f"TokHome-{uuid.uuid4().hex[:8]}")
    other = Organization(name=f"TokOther-{uuid.uuid4().hex[:8]}")
    db_session.add_all([home, other])
    db_session.flush()
    user = _make_fake_user(
        role="msp_engineer", org_id=home.id, email=f"{uuid.uuid4().hex[:8]}@example.com"
    )
    _grant(db_session, user)
    _grant(db_session, user, org_id=other.id, role="msp_engineer")
    app.dependency_overrides[get_session] = _app_session(db_session)
    app.dependency_overrides[get_current_user] = _authed(db_session, user)
    return TestClient(app), user, home, other


def test_self_issue_in_a_non_home_org_is_refused_with_the_reason(db_session):
    client, _, _, other = _two_orgs_one_engineer(db_session)
    r = client.post(f"/orgs/{other.id}/api-tokens", json={"name": "ci", "role": "customer_poc"})
    assert r.status_code == 422
    assert "home organization" in r.json()["detail"]
    assert db_session.query(ApiToken).filter(ApiToken.org_id == other.id).count() == 0


def test_self_issue_in_the_home_org_still_works_and_authenticates(db_session):
    client, _, home, _ = _two_orgs_one_engineer(db_session)
    r = client.post(f"/orgs/{home.id}/api-tokens", json={"name": "ci", "role": "customer_poc"})
    assert r.status_code == 201, r.text
    del app.dependency_overrides[get_current_user]
    me = client.get("/auth/me", headers={"Authorization": f"Bearer {r.json()['token']}"})
    assert me.status_code == 200


def test_an_existing_cross_org_token_says_why_it_fails(db_session):
    """A token minted before creation was refused still exists in the wild;
    it must not claim the account is deactivated."""
    client, user, _, other = _two_orgs_one_engineer(db_session)
    raw, token_hash = generate_secret("wingrc_")
    db_session.add(
        ApiToken(
            org_id=other.id,
            user_id=user.id,
            name="legacy cross-org",
            token_hash=token_hash,
            role="customer_poc",
        )
    )
    db_session.flush()

    del app.dependency_overrides[get_current_user]
    r = client.get("/auth/me", headers={"Authorization": f"Bearer {raw}"})
    assert r.status_code == 403
    detail = r.json()["detail"]
    assert "other than its user's home organization" in detail
    assert "deactivated" not in detail.lower()


def test_a_deactivated_user_is_still_reported_as_deactivated(db_session):
    client, user, home, _ = _two_orgs_one_engineer(db_session)
    raw, token_hash = generate_secret("wingrc_")
    db_session.add(
        ApiToken(
            org_id=home.id, user_id=user.id, name="t", token_hash=token_hash, role="customer_poc"
        )
    )
    db_session.get(User, user.id).is_active = False
    db_session.flush()

    del app.dependency_overrides[get_current_user]
    r = client.get("/auth/me", headers={"Authorization": f"Bearer {raw}"})
    assert r.status_code == 403
    assert r.json()["detail"] == "Account deactivated"
