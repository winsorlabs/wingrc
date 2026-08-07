"""Integration tests for I.6 — session fixation.

Confirms create_session() mints a fresh session token after MFA step-up
(both the already-enrolled login path and the first-time enrollment path)
rather than reusing/promoting the pre-auth wingrc_mfa_pending (and, for
enrollment, wingrc_mfa_setup) state cookie, and that those pending cookies
are actually cleared on success. This is the classic session-fixation
shape: an attacker who somehow captured the pre-auth state cookie must not
gain anything usable once the victim completes authentication.

Run in-container:
    docker compose exec backend pytest tests/test_session_fixation.py -m integration -v
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta

import pyotp
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.auth import hash_password
from app.db import get_session
from app.main import app
from app.models import Organization, User, UserSession
from tests.conftest import _app_session, _is_cleared, _set_cookie_value


@pytest.fixture
def client(db_session):
    app.dependency_overrides[get_session] = _app_session(db_session)
    yield TestClient(app)
    app.dependency_overrides.clear()


def _seed_org(db_session) -> Organization:
    org = Organization(name=f"FixationTestOrg-{uuid.uuid4().hex[:8]}")
    db_session.add(org)
    db_session.flush()
    return org


# ---------------------------------------------------------------------------
# Already-enrolled login: POST /auth/login -> POST /auth/mfa/verify
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_mfa_verify_mints_fresh_session_and_clears_pending_cookie(client, db_session):
    org = _seed_org(db_session)
    secret = pyotp.random_base32()
    password = "correct-horse-battery-staple-and-then-some"
    user = User(
        home_org_id=org.id,
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        display_name="Fixation Test User",
        login_method="local",
        role="customer_poc",
        is_active=True,
        password_hash=hash_password(password),
        mfa_enrolled=True,
        totp_secret=secret,
    )
    db_session.add(user)
    db_session.flush()

    login_resp = client.post("/auth/login", json={"email": user.email, "password": password})
    assert login_resp.status_code == 200
    assert login_resp.json()["next"] == "verify"

    pending_cookie = _set_cookie_value(login_resp, "wingrc_mfa_pending")
    assert pending_cookie, "login should have set a wingrc_mfa_pending state cookie"
    # No session cookie exists yet at this point — nothing to fixate onto pre-MFA.
    assert _set_cookie_value(login_resp, "wingrc_session") is None

    code = pyotp.TOTP(secret).now()
    verify_resp = client.post(
        "/auth/mfa/verify",
        json={"code": code},
        cookies={"wingrc_mfa_pending": pending_cookie},
    )
    assert verify_resp.status_code == 200

    session_cookie = _set_cookie_value(verify_resp, "wingrc_session")
    assert session_cookie, "mfa/verify should mint a session cookie"
    # The post-auth token is not the pre-auth identifier reused or promoted.
    assert session_cookie != pending_cookie

    assert _is_cleared(verify_resp, "wingrc_mfa_pending"), (
        "pre-MFA pending cookie must be cleared once authentication succeeds"
    )

    # And it's a real, freshly created session row — not a derivative of
    # anything that existed before this request.
    token_hash = hashlib.sha256(session_cookie.encode()).hexdigest()
    rows = db_session.scalars(select(UserSession).where(UserSession.user_id == user.id)).all()
    assert len(rows) == 1
    assert rows[0].token_hash == token_hash
    assert rows[0].revoked_at is None


# ---------------------------------------------------------------------------
# First-time enrollment: set-password -> mfa/enroll -> mfa/enroll/confirm
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_mfa_enroll_confirm_mints_fresh_session_and_clears_pending_cookies(client, db_session):
    org = _seed_org(db_session)
    raw_invite_token = f"invite-{uuid.uuid4().hex}"
    user = User(
        home_org_id=org.id,
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        display_name="Fixation Enroll User",
        login_method="local",
        role="customer_poc",
        is_active=False,
        invite_token_hash=hashlib.sha256(raw_invite_token.encode()).hexdigest(),
        # auth.find_user_for_invite requires invite_expires_at > now(); a
        # NULL expiry silently excludes the row (NULL > now() is NULL, not
        # true), which is what made this test's set-password call 400.
        invite_expires_at=datetime.now(UTC) + timedelta(hours=48),
    )
    db_session.add(user)
    db_session.flush()

    set_pw_resp = client.post(
        "/auth/set-password",
        json={
            "token": raw_invite_token,
            "password": "correct-horse-battery-staple-and-then-some",
        },
    )
    assert set_pw_resp.status_code == 200
    pending_cookie = _set_cookie_value(set_pw_resp, "wingrc_mfa_pending")
    assert pending_cookie

    enroll_resp = client.post("/auth/mfa/enroll", cookies={"wingrc_mfa_pending": pending_cookie})
    assert enroll_resp.status_code == 200
    secret = enroll_resp.json()["secret"]
    setup_cookie = _set_cookie_value(enroll_resp, "wingrc_mfa_setup")
    assert setup_cookie

    code = pyotp.TOTP(secret).now()
    confirm_resp = client.post(
        "/auth/mfa/enroll/confirm",
        json={"code": code},
        cookies={"wingrc_mfa_pending": pending_cookie, "wingrc_mfa_setup": setup_cookie},
    )
    assert confirm_resp.status_code == 200

    session_cookie = _set_cookie_value(confirm_resp, "wingrc_session")
    assert session_cookie
    assert session_cookie not in (pending_cookie, setup_cookie)

    assert _is_cleared(confirm_resp, "wingrc_mfa_pending")
    assert _is_cleared(confirm_resp, "wingrc_mfa_setup")

    token_hash = hashlib.sha256(session_cookie.encode()).hexdigest()
    rows = db_session.scalars(select(UserSession).where(UserSession.user_id == user.id)).all()
    assert len(rows) == 1
    assert rows[0].token_hash == token_hash
    assert rows[0].revoked_at is None
