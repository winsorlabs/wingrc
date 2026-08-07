"""Integration tests for I.5 — password lifecycle (unlock, reset, reuse).

Covers the spec's own list (docs/PLAN-auth-rbac-completion.md, I.5):
  - reuse of any of the last N passwords is rejected; the (N+1)th prior is accepted
  - unlock clears lockout state and leaves totp_secret/mfa_enrolled intact
  - reset issues a working one-time token; the token is single-use; expired
    tokens are rejected
  - reset revokes live sessions
  - non-admin gets 403 on both endpoints; assessor gets 403 (I.2 gate)

Plus the three implementation-time deviations from the written spec:
  - /set-password responds next="verify" (not "enroll") when the target is
    already MFA-enrolled, since reset reuses the same endpoint as invite
    redemption but the target is an existing, not brand-new, user
  - auth.find_user_for_invite (0015) no longer requires is_active = FALSE —
    it silently matched zero rows for a reset against an active user before
    the 0019 migration fix; this is the regression test for that fix
  - _user_out() exposes locked_until/lockout_count (frontend-facing, not
    re-tested here beyond confirming the fields are present)

Run in-container:
    docker compose exec backend pytest tests/test_password_lifecycle.py -m integration -v
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.auth import (
    check_password_reuse,
    get_current_user,
    hash_password,
    record_password,
)
from app.db import get_session
from app.main import app
from app.models import AuditLog, Organization, PasswordHistory, User, UserSession
from tests.conftest import _app_session, _authed, _make_fake_user

_PASSWORD_HISTORY_GENERATIONS = 5
_STRONG_PASSWORD = "correct-horse-battery-staple-and-then-some"


@pytest.fixture
def client(db_session, fake_msp_admin):
    app.dependency_overrides[get_session] = _app_session(db_session)
    app.dependency_overrides[get_current_user] = _authed(db_session, fake_msp_admin)
    yield TestClient(app)
    app.dependency_overrides.clear()


def _seed_org(db_session, org_id: uuid.UUID) -> Organization:
    org = Organization(id=org_id, name=f"PwLifecycleOrg-{uuid.uuid4().hex[:8]}")
    db_session.add(org)
    db_session.flush()
    return org


def _seed_local_user(db_session, *, org_id: uuid.UUID, **overrides) -> User:
    defaults = dict(
        home_org_id=org_id,
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        display_name="Target User",
        login_method="local",
        role="customer_poc",
        is_active=True,
    )
    defaults.update(overrides)
    user = User(**defaults)
    db_session.add(user)
    db_session.flush()
    return user


# ---------------------------------------------------------------------------
# Password reuse (auth.check_password_reuse / record_password)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_reuse_rejects_any_of_last_n_passwords(db_session):
    org = _seed_org(db_session, uuid.uuid4())
    user = _seed_local_user(db_session, org_id=org.id)

    passwords = [f"{_STRONG_PASSWORD}-{i}" for i in range(_PASSWORD_HISTORY_GENERATIONS)]
    for pw in passwords:
        record_password(db_session, user.id, hash_password(pw))

    for pw in passwords:
        assert check_password_reuse(
            db_session, user.id, pw, _PASSWORD_HISTORY_GENERATIONS
        ), f"{pw!r} should be rejected as reuse of one of the last {_PASSWORD_HISTORY_GENERATIONS}"


@pytest.mark.integration
def test_reuse_allows_password_beyond_generation_window(db_session):
    org = _seed_org(db_session, uuid.uuid4())
    user = _seed_local_user(db_session, org_id=org.id)

    oldest = f"{_STRONG_PASSWORD}-oldest"
    record_password(db_session, user.id, hash_password(oldest))
    # Push `oldest` beyond the retained window with N more distinct passwords.
    for i in range(_PASSWORD_HISTORY_GENERATIONS):
        record_password(db_session, user.id, hash_password(f"{_STRONG_PASSWORD}-{i}"))

    assert not check_password_reuse(
        db_session, user.id, oldest, _PASSWORD_HISTORY_GENERATIONS
    )
    rows = db_session.scalars(
        select(PasswordHistory).where(PasswordHistory.user_id == user.id)
    ).all()
    assert len(rows) == _PASSWORD_HISTORY_GENERATIONS, "record_password must trim beyond N"


@pytest.mark.integration
def test_set_password_rejects_reuse_via_http(client, db_session, fake_msp_admin):
    org = _seed_org(db_session, fake_msp_admin.org_id)
    user = _seed_local_user(
        db_session, org_id=org.id, password_hash=hash_password(_STRONG_PASSWORD)
    )
    record_password(db_session, user.id, user.password_hash)

    raw_token = client.post(
        f"/orgs/{fake_msp_admin.org_id}/users/{user.id}/reset-password"
    ).json()["reset_token"]

    r = client.post(
        "/auth/set-password", json={"token": raw_token, "password": _STRONG_PASSWORD}
    )
    assert r.status_code == 422
    assert "last" in r.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Unlock
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_unlock_clears_lockout_preserves_mfa(client, db_session, fake_msp_admin):
    org = _seed_org(db_session, fake_msp_admin.org_id)
    user = _seed_local_user(
        db_session,
        org_id=org.id,
        failed_login_count=3,
        lockout_count=2,
        locked_until=datetime.now(UTC) + timedelta(minutes=30),
        requires_admin_reset=False,
        mfa_enrolled=True,
        totp_secret="JBSWY3DPEHPK3PXP",
    )

    r = client.post(f"/orgs/{fake_msp_admin.org_id}/users/{user.id}/unlock")
    assert r.status_code == 200

    db_session.refresh(user)
    assert user.locked_until is None
    assert user.failed_login_count == 0
    assert user.lockout_count == 0
    assert user.requires_admin_reset is False
    assert user.mfa_enrolled is True, "unlock must not touch MFA enrollment"
    assert user.totp_secret == "JBSWY3DPEHPK3PXP", "unlock must not touch the TOTP secret"

    rows = db_session.scalars(
        select(AuditLog).where(
            AuditLog.org_id == fake_msp_admin.org_id,
            AuditLog.action == "user.unlock",
            AuditLog.entity_id == user.id,
        )
    ).all()
    assert len(rows) == 1
    assert rows[0].before_value["lockout_count"] == 2
    assert rows[0].after_value["lockout_count"] == 0


@pytest.mark.integration
def test_unlock_does_not_touch_password(client, db_session, fake_msp_admin):
    org = _seed_org(db_session, fake_msp_admin.org_id)
    stored_hash = hash_password(_STRONG_PASSWORD)
    user = _seed_local_user(
        db_session,
        org_id=org.id,
        password_hash=stored_hash,
        locked_until=datetime.now(UTC) + timedelta(minutes=15),
    )

    r = client.post(f"/orgs/{fake_msp_admin.org_id}/users/{user.id}/unlock")
    assert r.status_code == 200

    db_session.refresh(user)
    assert user.password_hash == stored_hash


# ---------------------------------------------------------------------------
# Reset-password: token mint + redemption
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_reset_issues_working_one_time_token(client, db_session, fake_msp_admin):
    org = _seed_org(db_session, fake_msp_admin.org_id)
    user = _seed_local_user(
        db_session, org_id=org.id, password_hash=hash_password(_STRONG_PASSWORD)
    )

    r = client.post(f"/orgs/{fake_msp_admin.org_id}/users/{user.id}/reset-password")
    assert r.status_code == 200
    raw_token = r.json()["reset_token"]
    assert raw_token

    set_resp = client.post(
        "/auth/set-password",
        json={"token": raw_token, "password": "a-brand-new-password-entirely"},
    )
    assert set_resp.status_code == 200

    db_session.refresh(user)
    assert user.invite_token_hash is None, "token must be consumed on redemption"
    assert user.invite_expires_at is None


@pytest.mark.integration
def test_reset_token_single_use(client, db_session, fake_msp_admin):
    org = _seed_org(db_session, fake_msp_admin.org_id)
    user = _seed_local_user(
        db_session, org_id=org.id, password_hash=hash_password(_STRONG_PASSWORD)
    )
    raw_token = client.post(
        f"/orgs/{fake_msp_admin.org_id}/users/{user.id}/reset-password"
    ).json()["reset_token"]

    first = client.post(
        "/auth/set-password",
        json={"token": raw_token, "password": "first-replacement-password-here"},
    )
    assert first.status_code == 200

    second = client.post(
        "/auth/set-password",
        json={"token": raw_token, "password": "second-replacement-password-x"},
    )
    assert second.status_code == 400


@pytest.mark.integration
def test_reset_token_expired_rejected(client, db_session, fake_msp_admin):
    org = _seed_org(db_session, fake_msp_admin.org_id)
    user = _seed_local_user(
        db_session, org_id=org.id, password_hash=hash_password(_STRONG_PASSWORD)
    )
    client.post(f"/orgs/{fake_msp_admin.org_id}/users/{user.id}/reset-password")

    # Backdate the expiry directly — the raw token itself never left the
    # response above in a form we can reuse, so mint again and immediately
    # expire that one instead.
    raw_token = client.post(
        f"/orgs/{fake_msp_admin.org_id}/users/{user.id}/reset-password"
    ).json()["reset_token"]
    user.invite_expires_at = datetime.now(UTC) - timedelta(minutes=1)
    db_session.flush()

    r = client.post(
        "/auth/set-password",
        json={"token": raw_token, "password": "irrelevant-password-value-here"},
    )
    assert r.status_code == 400


@pytest.mark.integration
def test_reset_revokes_live_sessions(client, db_session, fake_msp_admin):
    org = _seed_org(db_session, fake_msp_admin.org_id)
    user = _seed_local_user(
        db_session, org_id=org.id, password_hash=hash_password(_STRONG_PASSWORD)
    )
    live_session = UserSession(
        user_id=user.id,
        org_id=org.id,
        token_hash=uuid.uuid4().hex,
        expires_at=datetime.now(UTC) + timedelta(hours=8),
    )
    db_session.add(live_session)
    db_session.flush()

    r = client.post(f"/orgs/{fake_msp_admin.org_id}/users/{user.id}/reset-password")
    assert r.status_code == 200

    db_session.refresh(live_session)
    assert live_session.revoked_at is not None


# ---------------------------------------------------------------------------
# Deviation: /set-password's next value depends on mfa_enrolled, not hardcoded
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_reset_of_already_enrolled_user_responds_verify_not_enroll(
    client, db_session, fake_msp_admin
):
    org = _seed_org(db_session, fake_msp_admin.org_id)
    user = _seed_local_user(
        db_session,
        org_id=org.id,
        password_hash=hash_password(_STRONG_PASSWORD),
        mfa_enrolled=True,
        totp_secret="JBSWY3DPEHPK3PXP",
    )
    raw_token = client.post(
        f"/orgs/{fake_msp_admin.org_id}/users/{user.id}/reset-password"
    ).json()["reset_token"]

    r = client.post(
        "/auth/set-password",
        json={"token": raw_token, "password": "a-completely-different-password-1"},
    )
    assert r.status_code == 200
    assert r.json()["next"] == "verify"

    db_session.refresh(user)
    assert user.mfa_enrolled is True
    assert user.totp_secret == "JBSWY3DPEHPK3PXP", "reset must not force re-enrollment"


@pytest.mark.integration
def test_fresh_invite_still_responds_enroll(client, db_session, fake_msp_admin):
    """Regression guard: the mfa_enrolled branch must not change invite behavior."""
    org = _seed_org(db_session, fake_msp_admin.org_id)
    raw_invite_token = f"invite-{uuid.uuid4().hex}"
    user = _seed_local_user(
        db_session,
        org_id=org.id,
        is_active=False,
        invite_token_hash=hashlib.sha256(raw_invite_token.encode()).hexdigest(),
        invite_expires_at=datetime.now(UTC) + timedelta(hours=48),
    )

    r = client.post(
        "/auth/set-password",
        json={"token": raw_invite_token, "password": _STRONG_PASSWORD},
    )
    assert r.status_code == 200
    assert r.json()["next"] == "enroll"
    assert user.mfa_enrolled is False


# ---------------------------------------------------------------------------
# Deviation: auth.find_user_for_invite must match an already-active user
# (0019 dropped the old `is_active = FALSE` predicate — this is the
# regression test proving a reset token actually redeems for a real,
# already-active account instead of silently 400ing).
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_reset_token_redeems_for_already_active_user(client, db_session, fake_msp_admin):
    org = _seed_org(db_session, fake_msp_admin.org_id)
    user = _seed_local_user(
        db_session,
        org_id=org.id,
        is_active=True,
        password_hash=hash_password(_STRONG_PASSWORD),
    )
    assert user.is_active is True

    raw_token = client.post(
        f"/orgs/{fake_msp_admin.org_id}/users/{user.id}/reset-password"
    ).json()["reset_token"]

    r = client.post(
        "/auth/set-password",
        json={"token": raw_token, "password": "yet-another-new-password-here"},
    )
    assert r.status_code == 200, (
        "reset redemption must succeed for an active user — "
        "auth.find_user_for_invite must not require is_active = FALSE"
    )


# ---------------------------------------------------------------------------
# Authorization: non-admin / assessor 403 on both endpoints
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.parametrize("role", ["customer_poc", "msp_engineer", "c3pao_assessor"])
def test_non_admin_403_on_unlock_and_reset_password(db_session, role):
    org_id = uuid.uuid4()
    org = _seed_org(db_session, org_id)
    non_admin = _make_fake_user(org_id=org.id, role=role)
    target = _seed_local_user(db_session, org_id=org.id)

    app.dependency_overrides[get_session] = _app_session(db_session)
    app.dependency_overrides[get_current_user] = _authed(db_session, non_admin)
    try:
        c = TestClient(app)
        assert c.post(f"/orgs/{org.id}/users/{target.id}/unlock").status_code == 403
        assert c.post(f"/orgs/{org.id}/users/{target.id}/reset-password").status_code == 403
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# _user_out() exposes the fields the unlock UI needs (deviation)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_user_list_exposes_locked_until_and_lockout_count(client, db_session, fake_msp_admin):
    org = _seed_org(db_session, fake_msp_admin.org_id)
    locked_until = datetime.now(UTC) + timedelta(minutes=45)
    _seed_local_user(
        db_session,
        org_id=org.id,
        locked_until=locked_until,
        lockout_count=1,
        requires_admin_reset=False,
    )

    r = client.get(f"/orgs/{fake_msp_admin.org_id}/users")
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 1
    assert rows[0]["locked_until"] is not None
    assert rows[0]["lockout_count"] == 1
    # This is exactly the case the gap covers: locked without having
    # tripped requires_admin_reset yet.
    assert rows[0]["requires_admin_reset"] is False
