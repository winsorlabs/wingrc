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

# Marked per-test, not per-module: two of these need no database at all, and
# a module-level marker would drop them from a `-m "not integration"` run.
# The unreachable-database test is specifically about what happens WITHOUT
# one.


@pytest.fixture
def client(db_session):
    app.dependency_overrides[get_session] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.mark.integration
def test_health_reports_ok_with_a_live_database(client):
    r = client.get("/health")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"


@pytest.mark.integration
def test_health_reports_the_applied_migration(client):
    """Not decoration: this is the field that makes an empty or
    half-migrated database visible at a glance."""
    body = client.get("/health").json()
    assert body["migration"] == _expected_migration_head()


@pytest.mark.integration
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


def test_engine_sets_a_connect_timeout():
    """A readiness probe must fail fast, so the engine must bound its connect.

    No database needed, and no timing assertion -- this is a configuration
    check on purpose. The defect it guards was measured, not theorised:
    with no `connect_timeout`, `/health` took 260.03s to return 503 against
    an unreachable Postgres, and the four no-database tests that call it
    accounted for 1043s of a 1054s suite run.

    The bound is per resolved address, not per connection attempt: libpq
    applies it to each A/AAAA record in turn, so `localhost` (127.0.0.1 and
    ::1) doubles it -- a 300s setting was measured taking 600.64s. A
    multi-homed database host multiplies this value, which is why it is
    small rather than merely finite.

    Checks `build_connect_args()` rather than the live engine: `connect_args`
    is captured in the pool's creator closure and cannot be read back off an
    `Engine`. The single `connect_args=build_connect_args()` line in db.py is
    the wiring, and it is visible in review; this pins the value.
    """
    from app.config import get_settings
    from app.db import build_connect_args

    configured = build_connect_args()
    assert "connect_timeout" in configured, (
        "the engine must pass connect_timeout to libpq -- without it an "
        "unreachable database hangs every connection attempt for minutes, "
        "including the readiness probe whose job is to answer immediately"
    )
    timeout = int(configured["connect_timeout"])
    # libpq silently raises anything below 2 to 2, so 2 is the real floor.
    assert 2 <= timeout <= 15, (
        f"connect_timeout={timeout} is outside the useful range: below 2 libpq "
        "ignores it, and a large value reintroduces the slow-probe defect once "
        "multiplied by the number of addresses the host resolves to"
    )
    assert timeout == get_settings().db_connect_timeout


def test_health_reports_the_commit_the_image_was_built_from(health_probe_client, monkeypatch):
    """docker-compose.yml no longer bind-mounts a checkout under the live
    backend, so the running code is the image's -- and /health says which
    commit that image was built from (deploy/deploy.sh waits for it)."""
    monkeypatch.setenv("WINGRC_BUILD_SHA", "0123abcd")
    assert health_probe_client.get("/health").json()["build"] == "0123abcd"
    monkeypatch.delenv("WINGRC_BUILD_SHA")
    assert health_probe_client.get("/health").json()["build"] == "unknown"
