"""Integration tests for the audit events I.1 adds to routers/users.py.

Covers:
  - role change writes exactly one user.role_change row with correct
    before/after (and an unchanged role writes none)
  - activation change writes user.activation_change
  - deactivation writes user.deactivate
  - token create/revoke write their rows
  - no audit row anywhere contains a raw secret: neither the invite token
    (user.invite) nor the API token (api_token.create) ever leaks into
    before_value/after_value/context
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.auth import get_current_user
from app.db import get_session
from app.main import app
from app.models import AuditLog, Organization, User
from tests.conftest import _app_session, _authed


@pytest.fixture
def client(db_session, fake_msp_admin):
    app.dependency_overrides[get_session] = _app_session(db_session)
    app.dependency_overrides[get_current_user] = _authed(db_session, fake_msp_admin)
    yield TestClient(app)
    app.dependency_overrides.clear()


def _seed_org(db_session, org_id: uuid.UUID) -> Organization:
    org = Organization(id=org_id, name=f"AuditTestOrg-{uuid.uuid4().hex[:8]}")
    db_session.add(org)
    db_session.flush()
    return org


def _seed_user(db_session, *, org_id: uuid.UUID, role: str = "customer_poc") -> User:
    user = User(
        org_id=org_id,
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        display_name="Target User",
        login_method="local",
        role=role,
        is_active=True,
    )
    db_session.add(user)
    db_session.flush()
    return user


def _seed_matching_admin(db_session, fake_msp_admin) -> User:
    """Seed a real User row at fake_msp_admin's exact id/org_id.

    fake_msp_admin is a bare CurrentUser dataclass, not a DB row. A
    self-issued (no user_id in the body) ApiToken.user_id FKs to user.id,
    so self-issue token creation needs a real row at that id or the INSERT
    violates the FK constraint.
    """
    user = User(
        id=fake_msp_admin.id,
        org_id=fake_msp_admin.org_id,
        email=fake_msp_admin.email,
        display_name=fake_msp_admin.display_name,
        login_method=fake_msp_admin.login_method,
        role=fake_msp_admin.role,
        is_active=fake_msp_admin.is_active,
    )
    db_session.add(user)
    db_session.flush()
    return user


def _rows(db_session, org_id: uuid.UUID, action: str, entity_id: uuid.UUID | None = None):
    stmt = select(AuditLog).where(AuditLog.org_id == org_id, AuditLog.action == action)
    if entity_id is not None:
        stmt = stmt.where(AuditLog.entity_id == entity_id)
    return db_session.scalars(stmt).all()


# ---------------------------------------------------------------------------
# Role change
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_role_change_writes_one_row_with_before_after(client, db_session, fake_msp_admin):
    _seed_org(db_session, fake_msp_admin.org_id)
    target = _seed_user(db_session, org_id=fake_msp_admin.org_id, role="customer_poc")

    r = client.patch(
        f"/orgs/{fake_msp_admin.org_id}/users/{target.id}",
        json={"role": "msp_engineer"},
    )
    assert r.status_code == 200

    rows = _rows(db_session, fake_msp_admin.org_id, "user.role_change", target.id)
    assert len(rows) == 1
    assert rows[0].before_value == {"role": "customer_poc"}
    assert rows[0].after_value == {"role": "msp_engineer"}


@pytest.mark.integration
def test_role_unchanged_writes_no_row(client, db_session, fake_msp_admin):
    _seed_org(db_session, fake_msp_admin.org_id)
    target = _seed_user(db_session, org_id=fake_msp_admin.org_id, role="customer_poc")

    r = client.patch(
        f"/orgs/{fake_msp_admin.org_id}/users/{target.id}",
        json={"role": "customer_poc"},
    )
    assert r.status_code == 200
    assert _rows(db_session, fake_msp_admin.org_id, "user.role_change", target.id) == []


# ---------------------------------------------------------------------------
# Activation change / deactivation
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_activation_change_writes_one_row(client, db_session, fake_msp_admin):
    _seed_org(db_session, fake_msp_admin.org_id)
    target = _seed_user(db_session, org_id=fake_msp_admin.org_id)

    r = client.patch(
        f"/orgs/{fake_msp_admin.org_id}/users/{target.id}",
        json={"is_active": False},
    )
    assert r.status_code == 200

    rows = _rows(db_session, fake_msp_admin.org_id, "user.activation_change", target.id)
    assert len(rows) == 1
    assert rows[0].before_value == {"is_active": True}
    assert rows[0].after_value == {"is_active": False}


@pytest.mark.integration
def test_deactivate_writes_one_row(client, db_session, fake_msp_admin):
    _seed_org(db_session, fake_msp_admin.org_id)
    target = _seed_user(db_session, org_id=fake_msp_admin.org_id)

    r = client.delete(f"/orgs/{fake_msp_admin.org_id}/users/{target.id}")
    assert r.status_code == 200

    rows = _rows(db_session, fake_msp_admin.org_id, "user.deactivate", target.id)
    assert len(rows) == 1
    assert rows[0].before_value == {"is_active": True}
    assert rows[0].after_value == {"is_active": False}


# ---------------------------------------------------------------------------
# API tokens
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_api_token_create_writes_one_row(client, db_session, fake_msp_admin):
    _seed_org(db_session, fake_msp_admin.org_id)
    _seed_matching_admin(db_session, fake_msp_admin)

    r = client.post(
        f"/orgs/{fake_msp_admin.org_id}/api-tokens",
        json={"name": "CI", "role": "customer_poc"},
    )
    assert r.status_code == 201
    token_id = uuid.UUID(r.json()["id"])

    rows = _rows(db_session, fake_msp_admin.org_id, "api_token.create", token_id)
    assert len(rows) == 1
    assert rows[0].after_value["name"] == "CI"
    assert rows[0].after_value["role"] == "customer_poc"
    assert rows[0].after_value["on_behalf_of"] is False


@pytest.mark.integration
def test_api_token_revoke_writes_one_row(client, db_session, fake_msp_admin):
    _seed_org(db_session, fake_msp_admin.org_id)
    _seed_matching_admin(db_session, fake_msp_admin)

    created = client.post(
        f"/orgs/{fake_msp_admin.org_id}/api-tokens",
        json={"name": "CI", "role": "customer_poc"},
    ).json()
    token_id = uuid.UUID(created["id"])

    r = client.delete(f"/orgs/{fake_msp_admin.org_id}/api-tokens/{token_id}")
    assert r.status_code == 200

    rows = _rows(db_session, fake_msp_admin.org_id, "api_token.revoke", token_id)
    assert len(rows) == 1
    assert rows[0].before_value == {"revoked_at": None}
    assert rows[0].after_value["revoked_at"] is not None


# ---------------------------------------------------------------------------
# Secret-leak guard — no raw secret ever lands in an audit row
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_no_raw_api_token_leaks_into_audit_log(client, db_session, fake_msp_admin):
    _seed_org(db_session, fake_msp_admin.org_id)
    _seed_matching_admin(db_session, fake_msp_admin)

    r = client.post(
        f"/orgs/{fake_msp_admin.org_id}/api-tokens",
        json={"name": "CI", "role": "customer_poc"},
    )
    raw_token = r.json()["token"]
    assert raw_token  # sanity: we actually got a secret to search for

    rows = db_session.scalars(
        select(AuditLog).where(AuditLog.org_id == fake_msp_admin.org_id)
    ).all()
    assert rows, "expected at least the api_token.create row to exist"
    for row in rows:
        for blob in (row.before_value, row.after_value, row.context):
            assert raw_token not in str(blob)


@pytest.mark.integration
def test_no_raw_invite_token_leaks_into_audit_log(client, db_session, fake_msp_admin):
    _seed_org(db_session, fake_msp_admin.org_id)

    r = client.post(
        f"/orgs/{fake_msp_admin.org_id}/users",
        json={
            "email": f"{uuid.uuid4().hex[:8]}@example.com",
            "display_name": "Invitee",
            "role": "customer_poc",
        },
    )
    assert r.status_code == 201
    raw_invite_token = r.json()["invite_token"]
    assert raw_invite_token  # sanity: we actually got a secret to search for

    rows = db_session.scalars(
        select(AuditLog).where(
            AuditLog.org_id == fake_msp_admin.org_id, AuditLog.action == "user.invite"
        )
    ).all()
    assert rows, "expected the user.invite row to exist"
    for row in rows:
        for blob in (row.before_value, row.after_value, row.context):
            assert raw_invite_token not in str(blob)
