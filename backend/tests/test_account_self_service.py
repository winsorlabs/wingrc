"""Integration tests for I.9 — self-service account management.

Covers change-password, MFA re-enrollment (step-up gated), backup-code
regeneration, and session listing/revoke-all — see
docs/PLAN-auth-rbac-completion.md, I.9.

Every endpoint here acts on the caller's own account via
Depends(get_current_user), so tests build a CurrentUser whose fields match
a real seeded User row exactly (unlike role-gating tests elsewhere, these
endpoints do db.get(User, current_user.id) and need a resolvable row).

Run in-container:
    docker compose exec backend pytest tests/test_account_self_service.py -m integration -v
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pyotp
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.auth import CurrentUser, create_session, get_current_user, hash_password, verify_password
from app.db import get_session
from app.main import app
from app.models import MfaBackupCode, Organization, User
from tests.conftest import _app_session, _authed

_STRONG_PASSWORD = "correct-horse-battery-staple-and-then-some"


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


def _seed_org(db_session) -> Organization:
    org = Organization(name=f"SelfServiceOrg-{uuid.uuid4().hex[:8]}")
    db_session.add(org)
    db_session.flush()
    return org


def _seed_local_user(db_session, *, org_id: uuid.UUID, **overrides) -> User:
    defaults = dict(
        org_id=org_id,
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        display_name="Self Service User",
        login_method="local",
        role="customer_poc",
        is_active=True,
    )
    defaults.update(overrides)
    user = User(**defaults)
    db_session.add(user)
    db_session.flush()
    return user


def _current_user_for(user: User) -> CurrentUser:
    return CurrentUser(
        id=user.id,
        org_id=user.org_id,
        email=user.email,
        display_name=user.display_name,
        role=user.role,
        is_active=user.is_active,
        login_method=user.login_method,
        mfa_enrolled=user.mfa_enrolled,
    )


def _client_as(db_session, user: User) -> TestClient:
    app.dependency_overrides[get_session] = _app_session(db_session)
    app.dependency_overrides[get_current_user] = _authed(db_session, _current_user_for(user))
    return TestClient(app)


def _set_cookie_value(response, name: str) -> str | None:
    """Pull a cookie's raw value directly out of Set-Cookie headers.

    State cookies (and wingrc_session) are scoped to path=/api or
    path=/api/auth (see auth.py's set_state_cookie/set_session_cookie),
    which doesn't match the bare /auth path TestClient hits directly
    against the FastAPI app — jar-based auto-propagation across requests
    can't be relied on here (same reasoning as test_session_fixation.py's
    identical helper).
    """
    for raw in response.headers.get_list("set-cookie"):
        if raw.startswith(f"{name}="):
            first_segment = raw.split(";", 1)[0]
            value = first_segment.split("=", 1)[1]
            return value or None
    return None


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Change password
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_change_password_happy_path(db_session):
    org = _seed_org(db_session)
    user = _seed_local_user(
        db_session, org_id=org.id, password_hash=hash_password(_STRONG_PASSWORD)
    )
    client = _client_as(db_session, user)

    r = client.post(
        "/auth/change-password",
        json={"current_password": _STRONG_PASSWORD, "new_password": _STRONG_PASSWORD + "-new"},
    )
    assert r.status_code == 200

    db_session.refresh(user)
    assert verify_password(_STRONG_PASSWORD + "-new", user.password_hash)
    assert not verify_password(_STRONG_PASSWORD, user.password_hash)


@pytest.mark.integration
def test_change_password_rejects_wrong_current_password(db_session):
    org = _seed_org(db_session)
    user = _seed_local_user(
        db_session, org_id=org.id, password_hash=hash_password(_STRONG_PASSWORD)
    )
    client = _client_as(db_session, user)

    r = client.post(
        "/auth/change-password",
        json={"current_password": "totally-wrong", "new_password": _STRONG_PASSWORD + "-new"},
    )
    assert r.status_code == 401

    db_session.refresh(user)
    assert verify_password(_STRONG_PASSWORD, user.password_hash)


@pytest.mark.integration
def test_change_password_rejects_weak_new_password(db_session):
    org = _seed_org(db_session)
    user = _seed_local_user(
        db_session, org_id=org.id, password_hash=hash_password(_STRONG_PASSWORD)
    )
    client = _client_as(db_session, user)

    r = client.post(
        "/auth/change-password",
        json={"current_password": _STRONG_PASSWORD, "new_password": "short"},
    )
    assert r.status_code == 422


@pytest.mark.integration
def test_change_password_rejects_sso_account(db_session):
    org = _seed_org(db_session)
    user = _seed_local_user(db_session, org_id=org.id, login_method="sso", password_hash=None)
    client = _client_as(db_session, user)

    r = client.post(
        "/auth/change-password",
        json={"current_password": "whatever", "new_password": _STRONG_PASSWORD},
    )
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# MFA re-enrollment (step-up gated)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_mfa_reenroll_with_password_step_up_rotates_secret_and_backup_codes(db_session):
    org = _seed_org(db_session)
    old_secret = pyotp.random_base32()
    user = _seed_local_user(
        db_session,
        org_id=org.id,
        password_hash=hash_password(_STRONG_PASSWORD),
        totp_secret=old_secret,
        mfa_enrolled=True,
    )
    old_code_hash_count = 3
    for _ in range(old_code_hash_count):
        db_session.add(MfaBackupCode(user_id=user.id, code_hash=uuid.uuid4().hex))
    db_session.flush()

    client = _client_as(db_session, user)

    r = client.post("/auth/mfa/reenroll", json={"current_password": _STRONG_PASSWORD})
    assert r.status_code == 200
    data = r.json()
    new_secret = data["secret"]
    assert new_secret != old_secret
    assert data["qr_data_uri"].startswith("data:image/svg+xml")
    assert "qrserver" not in data["qr_data_uri"]

    reenroll_cookie = _set_cookie_value(r, "wingrc_mfa_reenroll")
    assert reenroll_cookie

    code = pyotp.TOTP(new_secret).now()
    confirm = client.post(
        "/auth/mfa/reenroll/confirm",
        json={"code": code},
        cookies={"wingrc_mfa_reenroll": reenroll_cookie},
    )
    assert confirm.status_code == 200
    backup_codes = confirm.json()["backup_codes"]
    assert len(backup_codes) == 10

    # Confirming must not mint or touch a session — the caller is already
    # authenticated; unlike pre-auth /mfa/enroll/confirm this never issues
    # a wingrc_session cookie.
    assert _set_cookie_value(confirm, "wingrc_session") is None

    db_session.refresh(user)
    assert user.totp_secret == new_secret
    assert user.mfa_enrolled is True

    remaining_codes = db_session.scalars(
        select(MfaBackupCode).where(MfaBackupCode.user_id == user.id)
    ).all()
    assert len(remaining_codes) == 10, "old backup codes must be replaced, not appended to"


@pytest.mark.integration
def test_mfa_reenroll_with_totp_step_up_succeeds(db_session):
    org = _seed_org(db_session)
    old_secret = pyotp.random_base32()
    user = _seed_local_user(
        db_session,
        org_id=org.id,
        password_hash=hash_password(_STRONG_PASSWORD),
        totp_secret=old_secret,
        mfa_enrolled=True,
    )
    client = _client_as(db_session, user)

    code = pyotp.TOTP(old_secret).now()
    r = client.post("/auth/mfa/reenroll", json={"totp_code": code})
    assert r.status_code == 200


@pytest.mark.integration
def test_mfa_reenroll_rejects_missing_step_up(db_session):
    org = _seed_org(db_session)
    user = _seed_local_user(
        db_session,
        org_id=org.id,
        password_hash=hash_password(_STRONG_PASSWORD),
        totp_secret=pyotp.random_base32(),
        mfa_enrolled=True,
    )
    client = _client_as(db_session, user)

    r = client.post("/auth/mfa/reenroll", json={})
    assert r.status_code == 401


@pytest.mark.integration
def test_mfa_reenroll_rejects_wrong_step_up(db_session):
    org = _seed_org(db_session)
    user = _seed_local_user(
        db_session,
        org_id=org.id,
        password_hash=hash_password(_STRONG_PASSWORD),
        totp_secret=pyotp.random_base32(),
        mfa_enrolled=True,
    )
    client = _client_as(db_session, user)

    r = client.post("/auth/mfa/reenroll", json={"current_password": "wrong"})
    assert r.status_code == 401


@pytest.mark.integration
def test_mfa_reenroll_rejects_sso_account(db_session):
    org = _seed_org(db_session)
    user = _seed_local_user(db_session, org_id=org.id, login_method="sso", password_hash=None)
    client = _client_as(db_session, user)

    r = client.post("/auth/mfa/reenroll", json={"current_password": "n/a"})
    assert r.status_code == 400


@pytest.mark.integration
def test_mfa_reenroll_confirm_rejects_mismatched_user_cookie(db_session):
    """The staged cookie's user_id must match the confirming session's own
    id — a stolen/replayed re-enrollment cookie from a different account
    must not be honored just because it's a validly-signed state cookie."""
    org = _seed_org(db_session)
    secret_a = pyotp.random_base32()
    user_a = _seed_local_user(
        db_session,
        org_id=org.id,
        password_hash=hash_password(_STRONG_PASSWORD),
        totp_secret=secret_a,
        mfa_enrolled=True,
    )
    user_b = _seed_local_user(
        db_session,
        org_id=org.id,
        password_hash=hash_password(_STRONG_PASSWORD),
        totp_secret=pyotp.random_base32(),
        mfa_enrolled=True,
    )

    client_a = _client_as(db_session, user_a)
    r = client_a.post("/auth/mfa/reenroll", json={"current_password": _STRONG_PASSWORD})
    reenroll_cookie = _set_cookie_value(r, "wingrc_mfa_reenroll")
    new_secret = r.json()["secret"]

    client_b = _client_as(db_session, user_b)
    code = pyotp.TOTP(new_secret).now()
    confirm = client_b.post(
        "/auth/mfa/reenroll/confirm",
        json={"code": code},
        cookies={"wingrc_mfa_reenroll": reenroll_cookie},
    )
    assert confirm.status_code == 400


# ---------------------------------------------------------------------------
# Backup code regeneration
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_regenerate_backup_codes_replaces_existing_codes(db_session):
    org = _seed_org(db_session)
    user = _seed_local_user(
        db_session,
        org_id=org.id,
        password_hash=hash_password(_STRONG_PASSWORD),
        totp_secret=pyotp.random_base32(),
        mfa_enrolled=True,
    )
    db_session.add(MfaBackupCode(user_id=user.id, code_hash=uuid.uuid4().hex))
    db_session.flush()

    client = _client_as(db_session, user)
    r = client.post(
        "/auth/mfa/backup-codes/regenerate", json={"current_password": _STRONG_PASSWORD}
    )
    assert r.status_code == 200
    codes = r.json()["backup_codes"]
    assert len(codes) == 10

    remaining = db_session.scalars(
        select(MfaBackupCode).where(MfaBackupCode.user_id == user.id)
    ).all()
    assert len(remaining) == 10


@pytest.mark.integration
def test_regenerate_backup_codes_requires_mfa_enrolled(db_session):
    org = _seed_org(db_session)
    user = _seed_local_user(
        db_session, org_id=org.id, password_hash=hash_password(_STRONG_PASSWORD)
    )
    client = _client_as(db_session, user)

    r = client.post(
        "/auth/mfa/backup-codes/regenerate", json={"current_password": _STRONG_PASSWORD}
    )
    assert r.status_code == 400


@pytest.mark.integration
def test_regenerate_backup_codes_rejects_missing_step_up(db_session):
    org = _seed_org(db_session)
    user = _seed_local_user(
        db_session,
        org_id=org.id,
        password_hash=hash_password(_STRONG_PASSWORD),
        totp_secret=pyotp.random_base32(),
        mfa_enrolled=True,
    )
    client = _client_as(db_session, user)

    r = client.post("/auth/mfa/backup-codes/regenerate", json={})
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# Sessions: list + revoke-all
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_list_sessions_returns_only_this_users_active_sessions(db_session):
    org = _seed_org(db_session)
    user = _seed_local_user(db_session, org_id=org.id)
    other_user = _seed_local_user(db_session, org_id=org.id)

    _row1, _raw1 = create_session(db_session, user)
    _row2, _raw2 = create_session(db_session, user)
    create_session(db_session, other_user)
    db_session.flush()

    client = _client_as(db_session, user)
    r = client.get("/auth/sessions")
    assert r.status_code == 200
    sessions = r.json()
    assert len(sessions) == 2
    required_fields = {"last_activity_at", "created_at", "expires_at"}
    assert all(required_fields <= s.keys() for s in sessions)


@pytest.mark.integration
def test_list_sessions_excludes_revoked(db_session):
    org = _seed_org(db_session)
    user = _seed_local_user(db_session, org_id=org.id)

    row, _raw = create_session(db_session, user)
    row.revoked_at = datetime.now(UTC)
    db_session.flush()

    client = _client_as(db_session, user)
    r = client.get("/auth/sessions")
    assert r.json() == []


@pytest.mark.integration
def test_revoke_all_sessions_revokes_every_live_session(db_session):
    org = _seed_org(db_session)
    user = _seed_local_user(db_session, org_id=org.id)

    row1, _raw1 = create_session(db_session, user)
    row2, _raw2 = create_session(db_session, user)
    db_session.flush()

    client = _client_as(db_session, user)
    r = client.post("/auth/sessions/revoke-all")
    assert r.status_code == 200
    # Response must clear the session cookie, same as logout -- the
    # frontend should treat this exactly like being signed out.
    assert _set_cookie_value(r, "wingrc_session") == ""

    db_session.refresh(row1)
    db_session.refresh(row2)
    assert row1.revoked_at is not None
    assert row2.revoked_at is not None


@pytest.mark.integration
def test_revoke_all_sessions_does_not_touch_other_users(db_session):
    org = _seed_org(db_session)
    user = _seed_local_user(db_session, org_id=org.id)
    other_user = _seed_local_user(db_session, org_id=org.id)

    row_other, _raw = create_session(db_session, other_user)
    db_session.flush()

    client = _client_as(db_session, user)
    client.post("/auth/sessions/revoke-all")

    db_session.refresh(row_other)
    assert row_other.revoked_at is None
