"""Integration tests for org_membership + deployment_settings.

M.1 (docs/adr/0009-multi-org-user-access.md): schema constraints and
manage.py bootstrap-admin populating both tables correctly.

M.2: auto-provisioning. Neither create_org()/invite_user() nor migration
0024's backfill pass 2 is exercised by wl-util-1's own data (a single-org
deployment can't exhibit the multi-org case at all) — every multi-org
scenario below is seeded synthetically for exactly that reason.

The tests below cover M.1/M.2 (schema + auto-provisioning) as of the
session that wrote them — require_org_access() did not yet consult
org_membership at that point. **M.4 has since landed** (2026-08-17):
require_org_access() now reads org_membership directly and is
authoritative for access — see OrgMembership's model docstring and
test_org_access_guard.py's own module docstring for the real coverage of
that cutover. (This file's docstring used to assert the pre-M.4 state as
still current; corrected here as part of the same M.7/M.8 pass that
found the identical stale claim in models.py and org_membership.py.)

Run in-container:
    docker compose exec backend pytest tests/test_org_membership.py -m integration -v
"""
from __future__ import annotations

import uuid

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.auth import get_current_user
from app.db import get_session
from app.main import app
from app.manage import _bootstrap_admin_core
from app.models import DeploymentSettings, Organization, OrgMembership, User
from app.org_membership import provision_new_org_memberships, provision_new_user_memberships
from tests.conftest import _app_session, _authed, _grant, _make_fake_user

_STRONG_PASSWORD = "correct-horse-battery-staple-and-then-some"


@pytest.fixture
def client(db_session, fake_msp_admin):
    app.dependency_overrides[get_session] = _app_session(db_session)
    app.dependency_overrides[get_current_user] = _authed(db_session, fake_msp_admin)
    yield TestClient(app)
    app.dependency_overrides.clear()


def _client_for(db_session, user) -> TestClient:
    """A TestClient authenticated as `user` -- for the handful of M.8
    tests that need to act as the *target* of a grant/revoke, not just
    fake_msp_admin. Caller is responsible for app.dependency_overrides.
    clear() afterward (the `client` fixture above does this via its own
    teardown; this bare helper doesn't have one to hook into)."""
    app.dependency_overrides[get_session] = _app_session(db_session)
    app.dependency_overrides[get_current_user] = _authed(db_session, user)
    return TestClient(app)


def _load_backfill_pass_2_sql() -> str:
    """Load migration 0024's exact backfill SQL via Alembic's own dynamic
    module loading (the same ScriptDirectory mechanism test_migrations.py
    already uses) rather than a hand-kept copy — nothing here can drift
    out of sync with what the migration actually runs."""
    cfg = Config("alembic.ini")
    script = ScriptDirectory.from_config(cfg)
    revision = script.get_revision("0024_msp_membership_backfill")
    return revision.module.BACKFILL_PASS_2_SQL


def _seed_org(db_session) -> Organization:
    org = Organization(name=f"MembershipOrg-{uuid.uuid4().hex[:8]}")
    db_session.add(org)
    db_session.flush()
    return org


def _membership_role(db_session, *, user_id: uuid.UUID, org_id: uuid.UUID) -> str | None:
    return db_session.scalar(
        select(OrgMembership.role).where(
            OrgMembership.user_id == user_id, OrgMembership.org_id == org_id
        )
    )


def _seed_user(db_session, *, org_id: uuid.UUID, **overrides) -> User:
    defaults = dict(
        home_org_id=org_id,
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        display_name="Membership Test User",
        login_method="local",
        role="msp_admin",
        is_active=True,
    )
    defaults.update(overrides)
    user = User(**defaults)
    db_session.add(user)
    db_session.flush()
    return user


# ---------------------------------------------------------------------------
# Schema constraints
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_org_membership_unique_per_user_org(db_session):
    org = _seed_org(db_session)
    user = _seed_user(db_session, org_id=org.id)

    db_session.add(OrgMembership(user_id=user.id, org_id=org.id, role="msp_admin"))
    db_session.flush()

    db_session.add(OrgMembership(user_id=user.id, org_id=org.id, role="msp_engineer"))
    with pytest.raises(IntegrityError):
        db_session.flush()


@pytest.mark.integration
def test_org_membership_allows_same_user_multiple_orgs_different_roles(db_session):
    """The core capability this whole ADR exists to add: one person, two
    orgs, two independently-chosen roles."""
    org_a = _seed_org(db_session)
    org_b = _seed_org(db_session)
    user = _seed_user(db_session, org_id=org_a.id, role="msp_admin")

    db_session.add(OrgMembership(user_id=user.id, org_id=org_a.id, role="msp_admin"))
    db_session.add(OrgMembership(user_id=user.id, org_id=org_b.id, role="msp_engineer"))
    db_session.flush()  # must not raise

    rows = {
        m.org_id: m.role
        for m in db_session.scalars(
            select(OrgMembership).where(OrgMembership.user_id == user.id)
        ).all()
    }
    assert rows == {org_a.id: "msp_admin", org_b.id: "msp_engineer"}


@pytest.mark.integration
def test_org_membership_rejects_invalid_role(db_session):
    org = _seed_org(db_session)
    user = _seed_user(db_session, org_id=org.id)

    db_session.add(OrgMembership(user_id=user.id, org_id=org.id, role="superuser"))
    with pytest.raises(IntegrityError):
        db_session.flush()


@pytest.mark.integration
def test_deployment_settings_singleton_rejects_second_row(db_session):
    org_a = _seed_org(db_session)
    org_b = _seed_org(db_session)

    db_session.add(DeploymentSettings(id=1, msp_org_id=org_a.id))
    db_session.flush()

    db_session.add(DeploymentSettings(id=1, msp_org_id=org_b.id))
    with pytest.raises(IntegrityError):
        db_session.flush()


@pytest.mark.integration
def test_deployment_settings_rejects_non_singleton_id(db_session):
    org = _seed_org(db_session)
    db_session.add(DeploymentSettings(id=2, msp_org_id=org.id))
    with pytest.raises(IntegrityError):
        db_session.flush()


# ---------------------------------------------------------------------------
# manage.py bootstrap-admin
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_bootstrap_admin_grants_membership_and_anchors_deployment_settings(db_session):
    """First-ever bootstrap on this database: creates org + user, grants
    org_membership, anchors deployment_settings."""
    result = _bootstrap_admin_core(
        db_session,
        org=f"BootstrapOrg-{uuid.uuid4().hex[:8]}",
        email="admin@example.com",
        display_name="Admin User",
        role="msp_admin",
        password=_STRONG_PASSWORD,
    )
    db_session.flush()

    assert result.org_created is True
    assert result.deployment_settings_anchored is True

    role = _membership_role(db_session, user_id=result.user_id, org_id=result.org_id)
    assert role == "msp_admin"

    settings = db_session.get(DeploymentSettings, 1)
    assert settings is not None
    assert settings.msp_org_id == result.org_id


@pytest.mark.integration
def test_bootstrap_admin_second_run_does_not_reanchor_deployment_settings(db_session):
    """A second bootstrap (new org, new user) must not move
    deployment_settings — it's set once, ever, per ADR 0009's Boundary
    section, not silently re-pointed by a later run."""
    first = _bootstrap_admin_core(
        db_session,
        org=f"FirstOrg-{uuid.uuid4().hex[:8]}",
        email="first-admin@example.com",
        display_name="First Admin",
        role="msp_admin",
        password=_STRONG_PASSWORD,
    )
    db_session.flush()

    second = _bootstrap_admin_core(
        db_session,
        org=f"SecondOrg-{uuid.uuid4().hex[:8]}",
        email="second-admin@example.com",
        display_name="Second Admin",
        role="msp_admin",
        password=_STRONG_PASSWORD,
    )
    db_session.flush()

    assert second.deployment_settings_anchored is False
    settings = db_session.get(DeploymentSettings, 1)
    assert settings.msp_org_id == first.org_id
    assert settings.msp_org_id != second.org_id

    # Both users still get their own membership, regardless of the anchor.
    role = _membership_role(db_session, user_id=second.user_id, org_id=second.org_id)
    assert role == "msp_admin"


@pytest.mark.integration
def test_bootstrap_admin_reuses_existing_org_by_name(db_session):
    org_name = f"ReusedOrg-{uuid.uuid4().hex[:8]}"
    first = _bootstrap_admin_core(
        db_session, org=org_name, email="one@example.com",
        display_name="One", role="msp_admin", password=_STRONG_PASSWORD,
    )
    db_session.flush()

    second = _bootstrap_admin_core(
        db_session, org=org_name, email="two@example.com",
        display_name="Two", role="msp_engineer", password=_STRONG_PASSWORD,
    )
    db_session.flush()

    assert second.org_created is False
    assert second.org_id == first.org_id
    role = _membership_role(db_session, user_id=second.user_id, org_id=first.org_id)
    assert role == "msp_engineer"


@pytest.mark.integration
def test_bootstrap_admin_rejects_duplicate_email_in_same_org(db_session):
    org_name = f"DupeOrg-{uuid.uuid4().hex[:8]}"
    _bootstrap_admin_core(
        db_session, org=org_name, email="dupe@example.com",
        display_name="Original", role="msp_admin", password=_STRONG_PASSWORD,
    )
    db_session.flush()

    with pytest.raises(ValueError, match="already exists"):
        _bootstrap_admin_core(
            db_session, org=org_name, email="dupe@example.com",
            display_name="Impersonator", role="msp_admin", password=_STRONG_PASSWORD,
        )


# ---------------------------------------------------------------------------
# M.2 — provision_new_org_memberships (a new org grants existing MSP users)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_provision_new_org_memberships_grants_existing_msp_users_at_own_role(db_session):
    org_a = _seed_org(db_session)
    admin = _seed_user(db_session, org_id=org_a.id, role="msp_admin")
    engineer = _seed_user(db_session, org_id=org_a.id, role="msp_engineer")
    poc = _seed_user(db_session, org_id=org_a.id, role="customer_poc")

    org_new = _seed_org(db_session)
    granted = provision_new_org_memberships(db_session, org_new.id)
    db_session.flush()

    assert granted == 2  # admin + engineer -- not poc
    assert _membership_role(db_session, user_id=admin.id, org_id=org_new.id) == "msp_admin"
    assert _membership_role(db_session, user_id=engineer.id, org_id=org_new.id) == "msp_engineer"
    assert _membership_role(db_session, user_id=poc.id, org_id=org_new.id) is None


@pytest.mark.integration
def test_provision_new_org_memberships_is_idempotent(db_session):
    org_a = _seed_org(db_session)
    _seed_user(db_session, org_id=org_a.id, role="msp_admin")
    org_new = _seed_org(db_session)

    first = provision_new_org_memberships(db_session, org_new.id)
    db_session.flush()
    second = provision_new_org_memberships(db_session, org_new.id)
    db_session.flush()  # must not raise IntegrityError

    assert first == 1
    assert second == 0


# ---------------------------------------------------------------------------
# M.2 — provision_new_user_memberships (a new user spans orgs by role)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_provision_new_user_memberships_msp_role_spans_every_existing_org(db_session):
    org_a = _seed_org(db_session)
    org_b = _seed_org(db_session)
    org_c = _seed_org(db_session)
    new_user = _seed_user(db_session, org_id=org_a.id, role="msp_engineer")

    granted = provision_new_user_memberships(
        db_session, user_id=new_user.id, org_id=org_a.id, role="msp_engineer"
    )
    db_session.flush()

    assert granted == 3
    for org in (org_a, org_b, org_c):
        role = _membership_role(db_session, user_id=new_user.id, org_id=org.id)
        assert role == "msp_engineer"


@pytest.mark.integration
def test_provision_new_user_memberships_customer_role_stays_single_org(db_session):
    org_a = _seed_org(db_session)
    org_b = _seed_org(db_session)  # exists, must NOT get a grant
    new_user = _seed_user(db_session, org_id=org_a.id, role="customer_poc")

    granted = provision_new_user_memberships(
        db_session, user_id=new_user.id, org_id=org_a.id, role="customer_poc"
    )
    db_session.flush()

    assert granted == 1
    assert _membership_role(db_session, user_id=new_user.id, org_id=org_a.id) == "customer_poc"
    assert _membership_role(db_session, user_id=new_user.id, org_id=org_b.id) is None


# ---------------------------------------------------------------------------
# M.2 — migration 0024 backfill pass 2 (the multi-org case wl-util-1's own
# data can't exercise — single-org deployment, backfill is a no-op there)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_backfill_pass_2_sql_spans_every_org_for_msp_roles(db_session):
    org_a = _seed_org(db_session)
    org_b = _seed_org(db_session)
    org_c = _seed_org(db_session)

    admin = _seed_user(db_session, org_id=org_a.id, role="msp_admin")
    engineer = _seed_user(db_session, org_id=org_b.id, role="msp_engineer")
    poc = _seed_user(db_session, org_id=org_c.id, role="customer_poc")

    # Simulate M.1's pass-1 state: each user already has exactly one
    # membership, matching their own org, before pass 2 runs.
    db_session.add(OrgMembership(user_id=admin.id, org_id=org_a.id, role="msp_admin"))
    db_session.add(OrgMembership(user_id=engineer.id, org_id=org_b.id, role="msp_engineer"))
    db_session.add(OrgMembership(user_id=poc.id, org_id=org_c.id, role="customer_poc"))
    db_session.flush()

    db_session.execute(text(_load_backfill_pass_2_sql()))
    db_session.flush()

    # msp_admin and msp_engineer now span all three orgs, at their own role.
    for org in (org_a, org_b, org_c):
        assert _membership_role(db_session, user_id=admin.id, org_id=org.id) == "msp_admin"
        assert _membership_role(db_session, user_id=engineer.id, org_id=org.id) == "msp_engineer"

    # customer_poc is untouched -- still only their own org.
    assert _membership_role(db_session, user_id=poc.id, org_id=org_a.id) is None
    assert _membership_role(db_session, user_id=poc.id, org_id=org_b.id) is None
    assert _membership_role(db_session, user_id=poc.id, org_id=org_c.id) == "customer_poc"


@pytest.mark.integration
def test_backfill_pass_2_sql_is_idempotent(db_session):
    org_a = _seed_org(db_session)
    org_b = _seed_org(db_session)
    admin = _seed_user(db_session, org_id=org_a.id, role="msp_admin")
    db_session.add(OrgMembership(user_id=admin.id, org_id=org_a.id, role="msp_admin"))
    db_session.flush()

    sql = _load_backfill_pass_2_sql()
    db_session.execute(text(sql))
    db_session.flush()
    db_session.execute(text(sql))  # must not raise -- NOT EXISTS guard
    db_session.flush()

    assert _membership_role(db_session, user_id=admin.id, org_id=org_b.id) == "msp_admin"


# ---------------------------------------------------------------------------
# M.2 — through the real endpoints (POST /orgs/{org_id}/users)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_invite_msp_user_spans_every_existing_org(client, db_session, fake_msp_admin):
    home_org = Organization(id=fake_msp_admin.org_id, name=f"HomeOrg-{uuid.uuid4().hex[:8]}")
    other_org = _seed_org(db_session)
    db_session.add(home_org)
    db_session.flush()
    _grant(db_session, fake_msp_admin)

    r = client.post(
        f"/orgs/{home_org.id}/users",
        json={
            "email": f"{uuid.uuid4().hex[:8]}@example.com",
            "display_name": "New Engineer",
            "role": "msp_engineer",
        },
    )
    assert r.status_code == 201
    new_user_id = uuid.UUID(r.json()["id"])

    for org_id in (home_org.id, other_org.id):
        role = _membership_role(db_session, user_id=new_user_id, org_id=org_id)
        assert role == "msp_engineer"


@pytest.mark.integration
def test_invite_customer_poc_stays_single_org(client, db_session, fake_msp_admin):
    home_org = Organization(id=fake_msp_admin.org_id, name=f"HomeOrg-{uuid.uuid4().hex[:8]}")
    other_org = _seed_org(db_session)
    db_session.add(home_org)
    db_session.flush()
    _grant(db_session, fake_msp_admin)

    r = client.post(
        f"/orgs/{home_org.id}/users",
        json={
            "email": f"{uuid.uuid4().hex[:8]}@example.com",
            "display_name": "New POC",
            "role": "customer_poc",
        },
    )
    assert r.status_code == 201
    new_user_id = uuid.UUID(r.json()["id"])

    assert _membership_role(db_session, user_id=new_user_id, org_id=home_org.id) == "customer_poc"
    assert _membership_role(db_session, user_id=new_user_id, org_id=other_org.id) is None


@pytest.mark.integration
def test_create_api_user_grants_membership(client, db_session, fake_msp_admin):
    """POST /orgs/{org_id}/users/api (create_api_user) previously never
    called provision_new_user_memberships at all -- an API user created
    this way had zero org_membership rows, including none at its own home
    org. Harmless under M.2 (nothing read org_membership yet), but would
    break that account's authentication entirely once M.4 makes
    org_membership authoritative. Fixed to match invite_user()'s existing
    call."""
    home_org = Organization(id=fake_msp_admin.org_id, name=f"HomeOrg-{uuid.uuid4().hex[:8]}")
    db_session.add(home_org)
    db_session.flush()
    _grant(db_session, fake_msp_admin)

    r = client.post(
        f"/orgs/{home_org.id}/users/api",
        json={"display_name": "CI Bot", "role": "customer_poc"},
    )
    assert r.status_code == 201
    new_user_id = uuid.UUID(r.json()["id"])

    assert _membership_role(db_session, user_id=new_user_id, org_id=home_org.id) == "customer_poc"


@pytest.mark.integration
def test_patch_user_role_change_updates_org_membership(client, db_session, fake_msp_admin):
    """PATCH /orgs/{org_id}/users/{user_id} previously only wrote
    User.role -- org_membership.role for that same (user, org) pair was
    never touched. Harmless under M.2 (nothing read org_membership yet),
    but once M.4 makes org_membership authoritative, a role change here
    would appear to succeed (200) while silently not taking effect."""
    home_org = Organization(id=fake_msp_admin.org_id, name=f"HomeOrg-{uuid.uuid4().hex[:8]}")
    db_session.add(home_org)
    db_session.flush()
    _grant(db_session, fake_msp_admin)
    target = _seed_user(db_session, org_id=home_org.id, role="customer_poc")
    db_session.add(OrgMembership(user_id=target.id, org_id=home_org.id, role="customer_poc"))
    db_session.flush()

    r = client.patch(
        f"/orgs/{home_org.id}/users/{target.id}",
        json={"role": "msp_engineer"},
    )
    assert r.status_code == 200
    assert _membership_role(db_session, user_id=target.id, org_id=home_org.id) == "msp_engineer"


# ---------------------------------------------------------------------------
# M.8 — POST/DELETE /orgs/{org_id}/memberships (admin-initiated grant/revoke)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_grant_membership_creates_exactly_one_row(client, db_session, fake_msp_admin):
    home_org = Organization(id=fake_msp_admin.org_id, name=f"HomeOrg-{uuid.uuid4().hex[:8]}")
    other_org = _seed_org(db_session)
    db_session.add(home_org)
    db_session.flush()
    _grant(db_session, fake_msp_admin)
    # fake_msp_admin must itself hold msp_admin on other_org to grant
    # into it -- M.8's own design ("the target org's own admin grants
    # access into it"), enforced by require_org_access before this
    # endpoint's own logic ever runs.
    _grant(db_session, fake_msp_admin, org_id=other_org.id)
    target = _seed_user(db_session, org_id=home_org.id, role="customer_poc")
    db_session.add(OrgMembership(user_id=target.id, org_id=home_org.id, role="customer_poc"))
    db_session.flush()

    r = client.post(
        f"/orgs/{other_org.id}/memberships",
        json={"user_id": str(target.id), "role": "customer_poc"},
    )
    assert r.status_code == 200
    assert r.json()["granted"] is True
    assert _membership_role(db_session, user_id=target.id, org_id=other_org.id) == "customer_poc"


@pytest.mark.integration
def test_grant_membership_is_idempotent(client, db_session, fake_msp_admin):
    home_org = Organization(id=fake_msp_admin.org_id, name=f"HomeOrg-{uuid.uuid4().hex[:8]}")
    other_org = _seed_org(db_session)
    db_session.add(home_org)
    db_session.flush()
    _grant(db_session, fake_msp_admin)
    _grant(db_session, fake_msp_admin, org_id=other_org.id)
    target = _seed_user(db_session, org_id=home_org.id, role="customer_poc")
    db_session.add(OrgMembership(user_id=target.id, org_id=home_org.id, role="customer_poc"))
    db_session.flush()

    body = {"user_id": str(target.id), "role": "customer_poc"}
    first = client.post(f"/orgs/{other_org.id}/memberships", json=body)
    second = client.post(f"/orgs/{other_org.id}/memberships", json=body)

    assert first.status_code == 200 and first.json()["granted"] is True
    assert second.status_code == 200 and second.json()["granted"] is False
    rows = db_session.execute(
        select(OrgMembership).where(
            OrgMembership.user_id == target.id, OrgMembership.org_id == other_org.id
        )
    ).scalars().all()
    assert len(rows) == 1


@pytest.mark.integration
def test_granted_user_can_actually_reach_the_org(client, db_session, fake_msp_admin):
    """§8's explicit ask: verify against the real endpoint, not by
    reasoning about the grant in isolation. M.4 makes this testable --
    before it, org_membership could be perfectly correct and
    require_org_access would still 403 every cross-org request."""
    home_org = Organization(id=fake_msp_admin.org_id, name=f"HomeOrg-{uuid.uuid4().hex[:8]}")
    other_org = _seed_org(db_session)
    db_session.add(home_org)
    db_session.flush()
    _grant(db_session, fake_msp_admin)
    _grant(db_session, fake_msp_admin, org_id=other_org.id)
    target = _seed_user(db_session, org_id=home_org.id, role="customer_poc")
    db_session.add(OrgMembership(user_id=target.id, org_id=home_org.id, role="customer_poc"))
    db_session.flush()

    grant_resp = client.post(
        f"/orgs/{other_org.id}/memberships",
        json={"user_id": str(target.id), "role": "customer_poc"},
    )
    assert grant_resp.status_code == 200

    target_identity = _make_fake_user(
        id=target.id, org_id=home_org.id, role="customer_poc", email=target.email,
        display_name=target.display_name,
    )
    target_client = _client_for(db_session, target_identity)
    try:
        r = target_client.get(f"/orgs/{other_org.id}/users")
    finally:
        app.dependency_overrides.clear()
    assert r.status_code == 200


@pytest.mark.integration
def test_grant_membership_rejects_invalid_role(client, db_session, fake_msp_admin):
    home_org = Organization(id=fake_msp_admin.org_id, name=f"HomeOrg-{uuid.uuid4().hex[:8]}")
    other_org = _seed_org(db_session)
    db_session.add(home_org)
    db_session.flush()
    _grant(db_session, fake_msp_admin)
    _grant(db_session, fake_msp_admin, org_id=other_org.id)
    target = _seed_user(db_session, org_id=home_org.id, role="customer_poc")
    db_session.add(OrgMembership(user_id=target.id, org_id=home_org.id, role="customer_poc"))
    db_session.flush()

    r = client.post(
        f"/orgs/{other_org.id}/memberships",
        json={"user_id": str(target.id), "role": "superuser"},
    )
    assert r.status_code == 422


@pytest.mark.integration
def test_grant_membership_unknown_user_404(client, db_session, fake_msp_admin):
    home_org = Organization(id=fake_msp_admin.org_id, name=f"HomeOrg-{uuid.uuid4().hex[:8]}")
    other_org = _seed_org(db_session)
    db_session.add(home_org)
    db_session.flush()
    _grant(db_session, fake_msp_admin)
    _grant(db_session, fake_msp_admin, org_id=other_org.id)

    r = client.post(
        f"/orgs/{other_org.id}/memberships",
        json={"user_id": str(uuid.uuid4()), "role": "customer_poc"},
    )
    assert r.status_code == 404


@pytest.mark.integration
def test_revoke_membership_removes_access_for_real(client, db_session, fake_msp_admin):
    home_org = Organization(id=fake_msp_admin.org_id, name=f"HomeOrg-{uuid.uuid4().hex[:8]}")
    other_org = _seed_org(db_session)
    db_session.add(home_org)
    db_session.flush()
    _grant(db_session, fake_msp_admin)
    _grant(db_session, fake_msp_admin, org_id=other_org.id)
    target = _seed_user(db_session, org_id=home_org.id, role="customer_poc")
    db_session.add(OrgMembership(user_id=target.id, org_id=home_org.id, role="customer_poc"))
    db_session.add(OrgMembership(user_id=target.id, org_id=other_org.id, role="customer_poc"))
    db_session.flush()

    r = client.delete(f"/orgs/{other_org.id}/memberships/{target.id}")
    assert r.status_code == 200
    assert _membership_role(db_session, user_id=target.id, org_id=other_org.id) is None

    target_identity = _make_fake_user(
        id=target.id, org_id=home_org.id, role="customer_poc", email=target.email,
        display_name=target.display_name,
    )
    target_client = _client_for(db_session, target_identity)
    try:
        r2 = target_client.get(f"/orgs/{other_org.id}/users")
    finally:
        app.dependency_overrides.clear()
    assert r2.status_code == 403


@pytest.mark.integration
def test_revoke_last_msp_admin_refused(db_session, fake_msp_admin):
    """Calls revoke_membership() directly rather than through the HTTP
    client: the endpoint's own require_org_access("msp_admin") gate means
    any HTTP caller authorized to reach it already holds msp_admin on
    other_org, so a *different* target can never actually be "the last
    admin" through that path (the caller is always a second one still
    standing) -- see revoke_membership's own docstring note. Calling the
    function directly, with a current_user who does NOT hold membership
    on other_org at all, is the only way to exercise this guard against a
    target that genuinely is the org's sole msp_admin.
    """
    from app.routers.users import revoke_membership

    other_org = _seed_org(db_session)
    lone_admin = _seed_user(db_session, org_id=other_org.id, role="msp_admin")
    db_session.add(OrgMembership(user_id=lone_admin.id, org_id=other_org.id, role="msp_admin"))
    db_session.flush()

    with pytest.raises(HTTPException) as exc_info:
        revoke_membership(
            org_id=other_org.id, user_id=lone_admin.id, db=db_session, current_user=fake_msp_admin
        )
    assert exc_info.value.status_code == 409
    assert _membership_role(db_session, user_id=lone_admin.id, org_id=other_org.id) == "msp_admin"


@pytest.mark.integration
def test_revoke_last_msp_admin_allowed_when_another_admin_remains(db_session, fake_msp_admin):
    from app.routers.users import revoke_membership

    other_org = _seed_org(db_session)
    admin_one = _seed_user(db_session, org_id=other_org.id, role="msp_admin")
    admin_two = _seed_user(db_session, org_id=other_org.id, role="msp_admin")
    db_session.add(OrgMembership(user_id=admin_one.id, org_id=other_org.id, role="msp_admin"))
    db_session.add(OrgMembership(user_id=admin_two.id, org_id=other_org.id, role="msp_admin"))
    db_session.flush()

    result = revoke_membership(
        org_id=other_org.id, user_id=admin_one.id, db=db_session, current_user=fake_msp_admin
    )
    assert result == {"ok": True}
    assert _membership_role(db_session, user_id=admin_one.id, org_id=other_org.id) is None
    assert _membership_role(db_session, user_id=admin_two.id, org_id=other_org.id) == "msp_admin"


@pytest.mark.integration
def test_revoke_self_refused(client, db_session, fake_msp_admin):
    home_org = Organization(id=fake_msp_admin.org_id, name=f"HomeOrg-{uuid.uuid4().hex[:8]}")
    db_session.add(home_org)
    db_session.flush()
    _grant(db_session, fake_msp_admin)

    r = client.delete(f"/orgs/{home_org.id}/memberships/{fake_msp_admin.id}")
    assert r.status_code == 400
    role = _membership_role(db_session, user_id=fake_msp_admin.id, org_id=home_org.id)
    assert role == "msp_admin"


@pytest.mark.integration
def test_revoke_nonexistent_membership_404(client, db_session, fake_msp_admin):
    home_org = Organization(id=fake_msp_admin.org_id, name=f"HomeOrg-{uuid.uuid4().hex[:8]}")
    other_org = _seed_org(db_session)
    db_session.add(home_org)
    db_session.flush()
    _grant(db_session, fake_msp_admin)
    _grant(db_session, fake_msp_admin, org_id=other_org.id)
    stranger = _seed_user(db_session, org_id=home_org.id, role="customer_poc")
    db_session.flush()  # no membership row on other_org for this user

    r = client.delete(f"/orgs/{other_org.id}/memberships/{stranger.id}")
    assert r.status_code == 404
