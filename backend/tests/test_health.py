"""The /health endpoint, which is the instrument a deploy is judged by.

It used to return a static dict. That is how an `alembic upgrade head`
which ran, logged success, exited 0 and silently rolled back left a
completely empty database while `docker compose ps` reported the backend
healthy the whole time. Nothing in the check touched a table, so nothing
noticed.

These tests exist because the check is now load-bearing for the
`wingrc_app` cutover: the two failure modes there are "the app cannot
authenticate" and "RLS returns nothing", and a healthcheck that touches no
table reports healthy through both.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db import get_session
from app.main import _expected_migration_head, app

pytestmark = pytest.mark.integration


@pytest.fixture
def client(db_session):
    app.dependency_overrides[get_session] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_health_reports_ok_with_a_live_database(client):
    r = client.get("/health")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"


def test_health_reports_the_applied_migration(client):
    """Not decoration: this is the field that makes an empty or
    half-migrated database visible at a glance."""
    body = client.get("/health").json()
    assert body["migration"] == _expected_migration_head()


def test_health_is_unhealthy_when_the_schema_does_not_match_the_build(client, db_session):
    """The rollback incident, reproduced.

    A database whose `alembic_version` does not match the head this image
    ships must report 503, because the running code does not match the
    schema it is talking to. Rolled back, so the row is restored.
    """
    original = db_session.execute(text("SELECT version_num FROM alembic_version")).scalar()
    db_session.execute(
        text("UPDATE alembic_version SET version_num = :v"), {"v": "0001_initial"}
    )

    r = client.get("/health")
    assert r.status_code == 503, r.text
    body = r.json()
    assert body["status"] == "error"
    assert body["migration"] == "0001_initial"
    assert body["migration_expected"] == _expected_migration_head()

    db_session.execute(
        text("UPDATE alembic_version SET version_num = :v"), {"v": original}
    )
    assert client.get("/health").status_code == 200


def test_health_is_unhealthy_when_the_database_is_unreachable():
    """The other cutover failure mode: a credential that cannot connect.

    Simulated by a session whose every statement raises, which is what a
    refused login looks like from the endpoint's side.
    """

    class _DeadSession:
        def execute(self, *_args, **_kwargs):
            raise RuntimeError("connection refused")

    app.dependency_overrides[get_session] = lambda: _DeadSession()
    try:
        r = TestClient(app).get("/health")
        assert r.status_code == 503, r.text
        assert r.json()["status"] == "error"
        assert "unreachable" in r.json()["database"]
    finally:
        app.dependency_overrides.clear()


def test_expected_head_is_resolvable_in_this_build():
    """If the script directory cannot be read the head check silently
    skips, which would quietly disarm it. Assert it actually resolves.
    """
    assert _expected_migration_head() is not None
    assert _expected_migration_head().startswith("00")
