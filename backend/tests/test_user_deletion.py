"""Integration tests for ADR 0006 — user deletion vs. the immutable audit trail.

Two-tier model (see docs/adr/0006-user-deletion-vs-immutable-audit-trail.md):
  - Zero audit_log footprint -> POST .../delete performs a real
    DELETE FROM "user", cascading user_session/mfa_backup_code/api_token/
    password_history via their ON DELETE CASCADE FKs.
  - Any audit_log history -> POST .../delete is blocked with 409 and the
    admin must choose POST .../anonymize as a separate, deliberate action.
    anonymize scrubs PII, sets deleted_at, and never touches audit_log.

Both paths require the target to already be inactive (deactivate-first
gate) and never operate on the caller's own account.

Run in-container:
    docker compose exec backend pytest tests/test_user_deletion.py -m integration -v
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.auth import get_current_user
from app.db import get_session
from app.main import app
from app.models import (
    ApiToken,
    AuditLog,
    MfaBackupCode,
    Organization,
    PasswordHistory,
    User,
    UserSession,
)
from tests.conftest import _app_session, _authed, _grant, _make_fake_user

_STRONG_PASSWORD_HASH = "not-a-real-pbkdf2-hash-just-a-fixture-value"


@pytest.fixture
def client(db_session, fake_msp_admin):
    app.dependency_overrides[get_session] = _app_session(db_session)
    app.dependency_overrides[get_current_user] = _authed(db_session, fake_msp_admin)
    yield TestClient(app)
    app.dependency_overrides.clear()


def _seed_org(db_session, org_id: uuid.UUID) -> Organization:
    org = Organization(id=org_id, name=f"DeletionOrg-{uuid.uuid4().hex[:8]}")
    db_session.add(org)
    db_session.flush()
    return org


def _seed_own_org(db_session, fake_msp_admin) -> Organization:
    """_seed_org at fake_msp_admin's own org_id, plus the org_membership
    grant require_org_access now needs there (ADR 0009 M.4)."""
    org = _seed_org(db_session, fake_msp_admin.org_id)
    _grant(db_session, fake_msp_admin)
    return org


def _seed_user(db_session, *, org_id: uuid.UUID, **overrides) -> User:
    defaults = dict(
        home_org_id=org_id,
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        display_name="Target User",
        login_method="local",
        role="customer_poc",
        is_active=False,
    )
    defaults.update(overrides)
    user = User(**defaults)
    db_session.add(user)
    db_session.flush()
    return user


def _seed_audit_row(
    db_session, *, org_id: uuid.UUID, user_id: uuid.UUID, action="user.role_change"
) -> AuditLog:
    row = AuditLog(
        org_id=org_id,
        actor="some-other-admin-id",
        actor_type="user",
        action=action,
        entity_type="user",
        entity_id=user_id,
        before_value={"role": "customer_poc"},
        after_value={"role": "msp_engineer"},
    )
    db_session.add(row)
    db_session.flush()
    return row


def _seed_auth_artifacts(db_session, *, org_id: uuid.UUID, user_id: uuid.UUID) -> None:
    """One row in each of the four tables ADR 0006 says cascade on deletion."""
    db_session.add(
        UserSession(
            user_id=user_id,
            org_id=org_id,
            token_hash=uuid.uuid4().hex,
            expires_at=datetime.now(UTC) + timedelta(hours=8),
        )
    )
    db_session.add(MfaBackupCode(user_id=user_id, code_hash=uuid.uuid4().hex))
    db_session.add(
        ApiToken(
            org_id=org_id,
            user_id=user_id,
            name="fixture token",
            token_hash=uuid.uuid4().hex,
            role="customer_poc",
        )
    )
    db_session.add(PasswordHistory(user_id=user_id, password_hash=_STRONG_PASSWORD_HASH))
    db_session.flush()


def _counts(db_session, user_id: uuid.UUID) -> dict[str, int]:
    return {
        "user": len(db_session.scalars(select(User).where(User.id == user_id)).all()),
        "user_session": len(
            db_session.scalars(select(UserSession).where(UserSession.user_id == user_id)).all()
        ),
        "mfa_backup_code": len(
            db_session.scalars(select(MfaBackupCode).where(MfaBackupCode.user_id == user_id)).all()
        ),
        "api_token": len(
            db_session.scalars(select(ApiToken).where(ApiToken.user_id == user_id)).all()
        ),
        "password_history": len(
            db_session.scalars(
                select(PasswordHistory).where(PasswordHistory.user_id == user_id)
            ).all()
        ),
    }


# ---------------------------------------------------------------------------
# Prerequisite: delete/anonymize only on an already-deactivated user
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_delete_blocked_on_active_user(client, db_session, fake_msp_admin):
    org = _seed_own_org(db_session, fake_msp_admin)
    user = _seed_user(db_session, org_id=org.id, is_active=True)

    r = client.post(f"/orgs/{fake_msp_admin.org_id}/users/{user.id}/delete")
    assert r.status_code == 400
    assert "deactivat" in r.json()["detail"].lower()

    db_session.refresh(user)
    assert user.deleted_at is None


@pytest.mark.integration
def test_anonymize_blocked_on_active_user(client, db_session, fake_msp_admin):
    org = _seed_own_org(db_session, fake_msp_admin)
    user = _seed_user(db_session, org_id=org.id, is_active=True)

    r = client.post(f"/orgs/{fake_msp_admin.org_id}/users/{user.id}/anonymize")
    assert r.status_code == 400
    assert "deactivat" in r.json()["detail"].lower()

    db_session.refresh(user)
    assert user.deleted_at is None
    assert "@example.com" in user.email, "PII must not be touched when the call is rejected"


# ---------------------------------------------------------------------------
# Self-protection: mirrors deactivate_user's existing self-check
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_delete_blocked_on_self(client, db_session, fake_msp_admin):
    _seed_own_org(db_session, fake_msp_admin)
    r = client.post(f"/orgs/{fake_msp_admin.org_id}/users/{fake_msp_admin.id}/delete")
    assert r.status_code == 400
    assert "own account" in r.json()["detail"].lower()


@pytest.mark.integration
def test_anonymize_blocked_on_self(client, db_session, fake_msp_admin):
    _seed_own_org(db_session, fake_msp_admin)
    r = client.post(f"/orgs/{fake_msp_admin.org_id}/users/{fake_msp_admin.id}/anonymize")
    assert r.status_code == 400
    assert "own account" in r.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Delete blocked when audit history exists
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_delete_blocked_when_history_exists_singular(client, db_session, fake_msp_admin):
    org = _seed_own_org(db_session, fake_msp_admin)
    user = _seed_user(db_session, org_id=org.id)
    _seed_audit_row(db_session, org_id=org.id, user_id=user.id)

    r = client.post(f"/orgs/{fake_msp_admin.org_id}/users/{user.id}/delete")
    assert r.status_code == 409
    detail = r.json()["detail"].lower()
    assert "1 audit log entry" in detail
    assert "anonymize" in detail

    db_session.refresh(user)
    assert user.deleted_at is None, "a blocked delete must not fall back to anonymizing"


@pytest.mark.integration
def test_delete_blocked_when_history_exists_plural(client, db_session, fake_msp_admin):
    org = _seed_own_org(db_session, fake_msp_admin)
    user = _seed_user(db_session, org_id=org.id)
    _seed_audit_row(db_session, org_id=org.id, user_id=user.id, action="user.role_change")
    _seed_audit_row(db_session, org_id=org.id, user_id=user.id, action="user.activation_change")
    _seed_audit_row(db_session, org_id=org.id, user_id=user.id, action="user.unlock")

    r = client.post(f"/orgs/{fake_msp_admin.org_id}/users/{user.id}/delete")
    assert r.status_code == 409
    assert "3 audit log entries" in r.json()["detail"].lower()


@pytest.mark.integration
def test_delete_blocked_when_user_is_the_actor_not_just_the_entity(
    client, db_session, fake_msp_admin
):
    """History also counts rows where this user acted on something else —
    not only rows where they were the target (ADR 0006's OR clause).
    """
    org = _seed_own_org(db_session, fake_msp_admin)
    user = _seed_user(db_session, org_id=org.id)
    other = _seed_user(db_session, org_id=org.id)
    db_session.add(
        AuditLog(
            org_id=org.id,
            actor=str(user.id),
            actor_type="user",
            action="user.unlock",
            entity_type="user",
            entity_id=other.id,
        )
    )
    db_session.flush()

    r = client.post(f"/orgs/{fake_msp_admin.org_id}/users/{user.id}/delete")
    assert r.status_code == 409


# ---------------------------------------------------------------------------
# Hard delete: zero-history cascade
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_delete_succeeds_and_cascades_for_zero_history_user(client, db_session, fake_msp_admin):
    org = _seed_own_org(db_session, fake_msp_admin)
    user = _seed_user(db_session, org_id=org.id)
    _seed_auth_artifacts(db_session, org_id=org.id, user_id=user.id)
    assert _counts(db_session, user.id) == {
        "user": 1, "user_session": 1, "mfa_backup_code": 1, "api_token": 1, "password_history": 1,
    }

    r = client.post(f"/orgs/{fake_msp_admin.org_id}/users/{user.id}/delete")
    assert r.status_code == 200
    assert r.json() == {"ok": True, "deleted": True}

    assert _counts(db_session, user.id) == {
        "user": 0, "user_session": 0, "mfa_backup_code": 0, "api_token": 0, "password_history": 0,
    }


@pytest.mark.integration
def test_delete_writes_its_own_audit_row(client, db_session, fake_msp_admin):
    org = _seed_own_org(db_session, fake_msp_admin)
    user = _seed_user(db_session, org_id=org.id)
    user_id = user.id

    r = client.post(f"/orgs/{fake_msp_admin.org_id}/users/{user_id}/delete")
    assert r.status_code == 200

    rows = db_session.scalars(
        select(AuditLog).where(AuditLog.action == "user.delete", AuditLog.entity_id == user_id)
    ).all()
    assert len(rows) == 1
    assert rows[0].after_value == {"deleted": True}, "the delete event itself must carry no PII"


@pytest.mark.integration
def test_delete_already_anonymized_user_is_rejected(client, db_session, fake_msp_admin):
    org = _seed_own_org(db_session, fake_msp_admin)
    user = _seed_user(db_session, org_id=org.id, deleted_at=datetime.now(UTC))

    r = client.post(f"/orgs/{fake_msp_admin.org_id}/users/{user.id}/delete")
    assert r.status_code == 400
    assert "already been anonymized" in r.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Anonymize: scrubs PII, preserves audit_log byte-for-byte, cascades auth tables
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_anonymize_scrubs_pii_and_sets_deleted_at(client, db_session, fake_msp_admin):
    org = _seed_own_org(db_session, fake_msp_admin)
    user = _seed_user(
        db_session,
        org_id=org.id,
        email="realname@example.com",
        display_name="Real Name",
        entra_oid="entra-oid-123",
        totp_secret="JBSWY3DPEHPK3PXP",
        mfa_enrolled=True,
        password_hash=_STRONG_PASSWORD_HASH,
    )
    user_id = user.id

    r = client.post(f"/orgs/{fake_msp_admin.org_id}/users/{user_id}/anonymize")
    assert r.status_code == 200
    body = r.json()
    assert body["deleted_at"] is not None
    assert body["display_name"] == "Deleted user"
    assert body["email"] == f"deleted-{user_id}@wingrc.invalid"

    db_session.refresh(user)
    assert user.email == f"deleted-{user_id}@wingrc.invalid"
    assert user.display_name == "Deleted user"
    assert user.entra_oid is None
    assert user.totp_secret is None
    assert user.mfa_enrolled is False
    assert user.password_hash is None
    assert user.deleted_at is not None
    assert user.is_active is False


@pytest.mark.integration
def test_anonymize_preserves_audit_rows_byte_for_byte(client, db_session, fake_msp_admin):
    org = _seed_own_org(db_session, fake_msp_admin)
    user = _seed_user(db_session, org_id=org.id, email="realname@example.com")
    history_row = _seed_audit_row(db_session, org_id=org.id, user_id=user.id)
    original_before = dict(history_row.before_value)
    original_after = dict(history_row.after_value)
    original_actor = history_row.actor
    original_created_at = history_row.created_at

    r = client.post(f"/orgs/{fake_msp_admin.org_id}/users/{user.id}/anonymize")
    assert r.status_code == 200

    db_session.refresh(history_row)
    assert history_row.before_value == original_before
    assert history_row.after_value == original_after
    assert history_row.actor == original_actor
    assert history_row.entity_id == user.id, "row must still resolve to the anonymized user"
    assert history_row.created_at == original_created_at

    new_rows = db_session.scalars(
        select(AuditLog).where(AuditLog.action == "user.anonymize", AuditLog.entity_id == user.id)
    ).all()
    assert len(new_rows) == 1
    assert new_rows[0].after_value == {"anonymized": True}
    assert "realname" not in str(new_rows[0].after_value), "no PII in the anonymize event itself"


@pytest.mark.integration
def test_anonymize_cascades_auth_tables(client, db_session, fake_msp_admin):
    org = _seed_own_org(db_session, fake_msp_admin)
    user = _seed_user(db_session, org_id=org.id)
    _seed_audit_row(db_session, org_id=org.id, user_id=user.id)
    _seed_auth_artifacts(db_session, org_id=org.id, user_id=user.id)
    assert _counts(db_session, user.id) == {
        "user": 1, "user_session": 1, "mfa_backup_code": 1, "api_token": 1, "password_history": 1,
    }

    r = client.post(f"/orgs/{fake_msp_admin.org_id}/users/{user.id}/anonymize")
    assert r.status_code == 200

    counts = _counts(db_session, user.id)
    assert counts["user"] == 1, "the user row itself must survive anonymization"
    assert counts["user_session"] == 0
    assert counts["mfa_backup_code"] == 0
    assert counts["api_token"] == 0
    assert counts["password_history"] == 0


@pytest.mark.integration
def test_anonymize_twice_is_rejected(client, db_session, fake_msp_admin):
    org = _seed_own_org(db_session, fake_msp_admin)
    user = _seed_user(db_session, org_id=org.id)

    first = client.post(f"/orgs/{fake_msp_admin.org_id}/users/{user.id}/anonymize")
    assert first.status_code == 200

    second = client.post(f"/orgs/{fake_msp_admin.org_id}/users/{user.id}/anonymize")
    assert second.status_code == 400
    assert "already been anonymized" in second.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Reactivation must never succeed once deleted_at is set
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_patch_is_active_true_rejected_after_anonymize(client, db_session, fake_msp_admin):
    org = _seed_own_org(db_session, fake_msp_admin)
    user = _seed_user(db_session, org_id=org.id)

    anon = client.post(f"/orgs/{fake_msp_admin.org_id}/users/{user.id}/anonymize")
    assert anon.status_code == 200

    r = client.patch(f"/orgs/{fake_msp_admin.org_id}/users/{user.id}", json={"is_active": True})
    assert r.status_code == 409
    assert "anonymized" in r.json()["detail"].lower()

    db_session.refresh(user)
    assert user.is_active is False


# ---------------------------------------------------------------------------
# Authorization: non-admin / assessor 403 on both endpoints (I.2 gate)
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.parametrize("role", ["customer_poc", "msp_engineer", "c3pao_assessor"])
def test_non_admin_403_on_delete_and_anonymize(db_session, role):
    org_id = uuid.uuid4()
    org = _seed_org(db_session, org_id)
    non_admin = _make_fake_user(org_id=org.id, role=role)
    _grant(db_session, non_admin)
    target = _seed_user(db_session, org_id=org.id)

    app.dependency_overrides[get_session] = _app_session(db_session)
    app.dependency_overrides[get_current_user] = _authed(db_session, non_admin)
    try:
        c = TestClient(app)
        assert c.post(f"/orgs/{org.id}/users/{target.id}/delete").status_code == 403
        assert c.post(f"/orgs/{org.id}/users/{target.id}/anonymize").status_code == 403
    finally:
        app.dependency_overrides.clear()
