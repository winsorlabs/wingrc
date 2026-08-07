"""Integration tests for M.1 — org_membership + deployment_settings schema.

Schema-only slice (docs/adr/0009-multi-org-user-access.md): nothing in the
application reads or writes these tables at request time yet.
require_org_access() still gates on User.org_id/User.role exclusively —
that's M.4. This file covers exactly what M.1 claims: the tables exist
with the right constraints, and manage.py bootstrap-admin populates them
correctly.

Run in-container:
    docker compose exec backend pytest tests/test_org_membership.py -m integration -v
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.manage import _bootstrap_admin_core
from app.models import DeploymentSettings, Organization, OrgMembership, User

_STRONG_PASSWORD = "correct-horse-battery-staple-and-then-some"


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
        org_id=org_id,
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
