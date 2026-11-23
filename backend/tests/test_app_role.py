"""The role the application connects as (app/app_role.py): its password,
set as the backend starts, and the reading /health reports.

Until 2026-11-23 compose's default connected the app as the owner, a
superuser, so every policy and migration 0077's WR001 were inert on a
fresh install, and nothing said so (docs/roadmap.md, "A fresh install
runs the app as a superuser").

Two of these tests are run as the owner on purpose, to show the
app-role tests would pass vacuously on the old default: the owner reads
another tenant's row, and reads with no org set without raising.
"""
from __future__ import annotations

import secrets
import uuid

import pytest
from fastapi.testclient import TestClient
from psycopg import sql
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.pool import NullPool

from app import app_role
from app.app_role import APP_ROLE, ensure_app_role_password, row_level_security
from app.models import Contact, Organization

# --- the /health reading, its cases written out ---------------------------


def test_a_superuser_reads_as_bypassed_and_says_so():
    r = row_level_security("wingrc", True, True, 60)
    assert r["row_level_security"].startswith("bypassed: wingrc is a superuser")
    assert r["superuser"] is True


def test_bypassrls_reads_as_bypassed():
    r = row_level_security("someone", False, True, 0)
    assert r["row_level_security"].startswith("bypassed: someone has BYPASSRLS")


def test_a_table_owner_reads_as_bypassed_with_the_count():
    r = row_level_security("owner", False, False, 3)
    assert r["row_level_security"].startswith("bypassed: owner owns 3 tables")


def test_a_role_with_none_of_the_three_reads_as_enforced():
    r = row_level_security(APP_ROLE, False, False, 0)
    assert r == {
        "role": APP_ROLE,
        "superuser": False,
        "bypassrls": False,
        "owner_of_rls_tables": 0,
        "row_level_security": "enforced",
    }


@pytest.mark.integration
def test_the_reading_is_taken_from_the_connection_not_the_configuration(db_session):
    """Run as the owner, then as wingrc_app on the same connection: the
    reading follows the role actually in force."""
    owner = db_session.execute(app_role.ROLE_SQL).one()
    as_owner = row_level_security(*owner)
    assert as_owner["row_level_security"].startswith("bypassed:"), as_owner
    nested = db_session.begin_nested()
    db_session.execute(text(f"SET LOCAL ROLE {APP_ROLE}"))
    app = db_session.execute(app_role.ROLE_SQL).one()
    nested.rollback()
    assert row_level_security(*app) == {
        "role": APP_ROLE,
        "superuser": False,
        "bypassrls": False,
        "owner_of_rls_tables": 0,
        "row_level_security": "enforced",
    }


@pytest.mark.integration
def test_health_reports_the_role_it_answers_on(db_session):
    """The suite's requests run as wingrc_app (conftest._app_session SETs the
    role for each request), so /health reads enforced, while the test body,
    on the owner, reads bypassed: the reading is the request's own."""
    from app.db import get_session
    from app.main import app
    from tests.conftest import _app_session

    app.dependency_overrides[get_session] = _app_session(db_session)
    try:
        body = TestClient(app).get("/health").json()
    finally:
        app.dependency_overrides.clear()
    assert db_session.execute(text("SELECT current_user")).scalar() != APP_ROLE, (
        "fixture's own connection must be the owner, or this test proves nothing"
    )
    assert body["database_role"] == {
        "role": APP_ROLE,
        "superuser": False,
        "bypassrls": False,
        "owner_of_rls_tables": 0,
        "row_level_security": "enforced",
    }


# --- what the app role can and cannot read --------------------------------


@pytest.fixture
def two_tenants(db_session):
    """Two orgs with a contact each, read below by text SQL only, so no
    answer can come from the session's identity map (#139)."""
    rows = []
    for tag in ("A", "B"):
        org = Organization(name=f"Role-{tag}-{uuid.uuid4().hex[:8]}")
        db_session.add(org)
        db_session.flush()
        c = Contact(org_id=org.id, name=f"Tenant {tag}", email=f"{tag.lower()}@example.com",
                    affiliation="customer")
        db_session.add(c)
        db_session.flush()
        rows.append((org.id, c.id))
    return rows


def _read_contact(db_session, contact_id, *, role: str | None, org_id) -> object:
    nested = db_session.begin_nested()
    try:
        if role:
            db_session.execute(text(f"SET LOCAL ROLE {role}"))
        db_session.execute(text("SELECT set_config('app.current_org', :o, true)"),
                           {"o": str(org_id) if org_id else ""})
        return db_session.execute(
            text("SELECT id FROM contact WHERE id = :id"), {"id": contact_id}
        ).scalar_one_or_none()
    finally:
        nested.rollback()


@pytest.mark.integration
def test_the_app_role_cannot_read_another_tenants_row(db_session, two_tenants):
    (org_a, contact_a), (_, contact_b) = two_tenants
    assert _read_contact(db_session, contact_a, role=APP_ROLE, org_id=org_a) == contact_a
    assert _read_contact(db_session, contact_b, role=APP_ROLE, org_id=org_a) is None


@pytest.mark.integration
def test_the_owner_reads_another_tenants_row_which_is_what_the_old_default_served_as(
    db_session, two_tenants
):
    (org_a, _), (_, contact_b) = two_tenants
    assert _read_contact(db_session, contact_b, role=None, org_id=org_a) == contact_b


@pytest.mark.integration
def test_the_owner_reads_with_no_org_set_so_wr001_never_fired_on_the_old_default(
    db_session, two_tenants
):
    """test_rls_unset_org's WR001 test, run as the owner: no error. On the
    old default that guard was silent."""
    (_, contact_a), _ = two_tenants
    assert _read_contact(db_session, contact_a, role=None, org_id=None) == contact_a
    with pytest.raises(DBAPIError) as caught:
        _read_contact(db_session, contact_a, role=APP_ROLE, org_id=None)
    assert caught.value.orig.sqlstate == "WR001"


# --- setting the password as the backend starts ---------------------------


def test_a_url_for_another_role_is_left_alone_without_connecting():
    """The host does not resolve: had it tried to connect, this would raise."""
    said = ensure_app_role_password(
        "postgresql+psycopg://wingrc:pw@no-such-host.invalid:5432/wingrc", None
    )
    assert "not 'wingrc_app': nothing to set" in said


def test_the_app_role_with_no_password_refuses_by_name():
    with pytest.raises(SystemExit) as caught:
        ensure_app_role_password(
            "postgresql+psycopg://wingrc_app@no-such-host.invalid:5432/wingrc", None
        )
    assert "WINGRC_APP_DB_PASSWORD" in str(caught.value)


@pytest.fixture
def owner_url(db_engine) -> str:
    return db_engine.url.render_as_string(hide_password=False)


@pytest.fixture
def saved_app_password(owner_url):
    """Changes wingrc_app's password, which is cluster-wide, and puts the
    verifier back afterwards, so a bench backend connected as the role
    keeps working once this finishes."""
    engine = create_engine(owner_url, poolclass=NullPool)
    with engine.connect() as conn:
        superuser = conn.execute(
            text("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")
        ).scalar()
        assert superuser, (
            "fixture must connect as a superuser to save the verifier, or this test proves nothing"
        )
        saved = conn.execute(
            text("SELECT rolpassword FROM pg_authid WHERE rolname = :r"), {"r": APP_ROLE}
        ).scalar()
    yield
    with engine.begin() as conn:
        raw = conn.connection.driver_connection
        with raw.cursor() as cur:
            cur.execute(sql.SQL("ALTER ROLE {} PASSWORD {}").format(
                sql.Identifier(APP_ROLE), sql.Literal(saved)
            ))
    engine.dispose()


@pytest.mark.integration
def test_the_start_sets_the_app_roles_password_once_and_then_leaves_it(
    owner_url, saved_app_password
):
    app_url = make_url(owner_url).set(username=APP_ROLE, password=secrets.token_urlsafe(24))
    app_url_s = app_url.render_as_string(hide_password=False)
    assert app_role._authenticates(app_url_s) is not None, (
        "fixture must be a cluster that checks passwords, or this test proves nothing"
    )

    first = ensure_app_role_password(app_url_s, owner_url)
    assert "set its password" in first, first
    second = ensure_app_role_password(app_url_s, owner_url)
    assert second == f"{APP_ROLE} authenticates: nothing set."

    engine = create_engine(app_url_s, poolclass=NullPool)
    with engine.connect() as conn:
        got = conn.execute(app_role.ROLE_SQL).one()
    engine.dispose()
    assert row_level_security(*got)["row_level_security"] == "enforced", got


@pytest.mark.integration
def test_the_start_refuses_by_name_when_there_is_no_owner_url(owner_url):
    app_url = make_url(owner_url).set(username=APP_ROLE, password=secrets.token_urlsafe(24))
    with pytest.raises(SystemExit) as caught:
        ensure_app_role_password(app_url.render_as_string(hide_password=False), None)
    assert "WINGRC_MIGRATION_DATABASE_URL" in str(caught.value)
