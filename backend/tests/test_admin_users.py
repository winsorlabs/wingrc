"""Integration tests for routers/admin_users.py (ADR 0009 M.7 / G.11): the
deployment-wide user directory and the designated-MSP-org lookup.

Run in-container:
    docker compose exec backend pytest tests/test_admin_users.py -m integration -v
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.auth import get_current_user
from app.db import get_session
from app.main import app
from app.models import DeploymentSettings, Organization, OrgMembership, User
from tests.conftest import _app_session, _authed, _grant, _make_fake_user

pytestmark = pytest.mark.integration


@pytest.fixture
def admin_client(db_session, fake_msp_admin):
    app.dependency_overrides[get_session] = _app_session(db_session)
    app.dependency_overrides[get_current_user] = _authed(db_session, fake_msp_admin)
    yield TestClient(app)
    app.dependency_overrides.clear()


def _client_as(db_session, user):
    app.dependency_overrides[get_session] = _app_session(db_session)
    app.dependency_overrides[get_current_user] = _authed(db_session, user)
    return TestClient(app)


def _seed_org(db_session, *, name: str | None = None) -> Organization:
    org = Organization(name=name or f"DirOrg-{uuid.uuid4().hex[:8]}")
    db_session.add(org)
    db_session.flush()
    return org


def _seed_user(db_session, *, org_id, **overrides) -> User:
    defaults = dict(
        home_org_id=org_id,
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        display_name="Directory Test User",
        login_method="local",
        role="customer_poc",
        is_active=True,
    )
    defaults.update(overrides)
    user = User(**defaults)
    db_session.add(user)
    db_session.flush()
    return user


class TestDirectory:
    def test_lists_users_whose_home_org_differs_from_caller(
        self, admin_client, db_session, fake_msp_admin
    ):
        home_org = Organization(id=fake_msp_admin.org_id, name=f"CallerOrg-{uuid.uuid4().hex[:8]}")
        other_org = _seed_org(db_session)
        db_session.add(home_org)
        db_session.flush()
        _grant(db_session, fake_msp_admin)

        other_user = _seed_user(db_session, org_id=other_org.id, role="customer_poc")
        db_session.add(
            OrgMembership(user_id=other_user.id, org_id=other_org.id, role="customer_poc")
        )
        db_session.flush()

        r = admin_client.get("/admin/users")
        assert r.status_code == 200
        emails = {row["email"] for row in r.json()}
        # The whole point of the SECURITY DEFINER bypass: a user whose
        # home org isn't app.current_org (the caller's own org) still
        # shows up -- a plain RLS-scoped query would silently drop this.
        assert other_user.email in emails

    def test_shows_membership_list_not_a_bare_role_column(
        self, admin_client, db_session, fake_msp_admin
    ):
        home_org = Organization(id=fake_msp_admin.org_id, name=f"CallerOrg-{uuid.uuid4().hex[:8]}")
        second_org = _seed_org(db_session)
        db_session.add(home_org)
        db_session.flush()
        _grant(db_session, fake_msp_admin)

        target = _seed_user(db_session, org_id=home_org.id, role="msp_engineer")
        db_session.add(OrgMembership(user_id=target.id, org_id=home_org.id, role="msp_engineer"))
        db_session.add(OrgMembership(user_id=target.id, org_id=second_org.id, role="customer_poc"))
        db_session.flush()

        r = admin_client.get("/admin/users")
        assert r.status_code == 200
        row = next(row for row in r.json() if row["id"] == str(target.id))
        assert "role" not in row  # deliberately dropped -- see admin_users.py's own docstring
        memberships = {(m["org_id"], m["role"]) for m in row["memberships"]}
        assert memberships == {
            (str(home_org.id), "msp_engineer"),
            (str(second_org.id), "customer_poc"),
        }
        assert row["home_org_name"] == home_org.name

    def test_consultant_admin_gets_403(self, db_session):
        # require_role gates on current_user.role alone -- no org_id in
        # this router's paths, so no org/membership seeding is needed to
        # reach the check being tested.
        client = _client_as(db_session, _make_fake_user(role="consultant_admin"))
        try:
            r = client.get("/admin/users")
        finally:
            app.dependency_overrides.clear()
        assert r.status_code == 403

    def test_customer_poc_gets_403(self, db_session):
        client = _client_as(db_session, _make_fake_user(role="customer_poc"))
        try:
            r = client.get("/admin/users")
        finally:
            app.dependency_overrides.clear()
        assert r.status_code == 403


class TestMspOrg:
    def test_returns_none_when_not_designated(self, admin_client, db_session, fake_msp_admin):
        home_org = Organization(id=fake_msp_admin.org_id, name=f"CallerOrg-{uuid.uuid4().hex[:8]}")
        db_session.add(home_org)
        db_session.flush()
        _grant(db_session, fake_msp_admin)
        # No DeploymentSettings row at all -- the fresh-deployment case.

        r = admin_client.get("/admin/users/msp-org")
        assert r.status_code == 200
        assert r.json() is None

    def test_returns_the_designated_org(self, admin_client, db_session, fake_msp_admin):
        home_org = Organization(id=fake_msp_admin.org_id, name=f"CallerOrg-{uuid.uuid4().hex[:8]}")
        msp_org = _seed_org(db_session, name="The Real MSP")
        db_session.add(home_org)
        db_session.flush()
        _grant(db_session, fake_msp_admin)
        db_session.add(DeploymentSettings(id=1, msp_org_id=msp_org.id))
        db_session.flush()

        r = admin_client.get("/admin/users/msp-org")
        assert r.status_code == 200
        body = r.json()
        assert body["org_id"] == str(msp_org.id)
        assert body["org_name"] == "The Real MSP"
