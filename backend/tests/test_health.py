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


# --- Which code is running, per component (app/health.py) -----------------
#
# A single `build` field (the backend's) said `ok` through a split deploy on
# 2026-10-26. Three matching commits reading `ok` proves nothing -- the old
# field passed that too -- so these pin the cases where they do not match.

A = "a" * 40
B = "b" * 40


def _probe(client, monkeypatch, *, backend=A, nginx=A, workers=(A,)):
    from tests.conftest import HealthProbeSession

    if backend is None:
        monkeypatch.delenv("WINGRC_BUILD_SHA", raising=False)
    else:
        monkeypatch.setenv("WINGRC_BUILD_SHA", backend)
    monkeypatch.setattr(
        HealthProbeSession,
        "worker_application_names",
        [f"wingrc-worker@{w}" for w in workers],
    )
    headers = {"X-WinGRC-Nginx-Build": nginx} if nginx is not None else {}
    r = client.get("/health", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def test_three_matching_commits_read_ok(health_probe_client, monkeypatch):
    body = _probe(health_probe_client, monkeypatch)
    assert body["status"] == "ok"
    assert body["components"] == {"backend": A, "nginx": A, "worker": A}
    assert "detail" not in body
    assert "build" not in body, "one field cannot describe three components; it was removed"


@pytest.mark.parametrize("odd", ["backend", "nginx", "worker"])
def test_one_component_on_another_commit_is_not_ok_and_names_both(
    health_probe_client, monkeypatch, odd
):
    """The disagreement case, for each component in turn. Mutation-checked
    2026-10-26: with the verdict's loop narrowed to backend and nginx, the
    `worker` case went red."""
    kwargs = {"backend": A, "nginx": A, "workers": (A,)}
    kwargs["workers" if odd == "worker" else odd] = (B,) if odd == "worker" else B
    body = _probe(health_probe_client, monkeypatch, **kwargs)
    assert body["status"] == "split", body
    assert body["components"][odd] == B
    assert A in body["detail"] and B in body["detail"], body["detail"]
    assert f"{odd} {B}" in body["detail"], body["detail"]


def test_two_workers_on_different_commits_is_split(health_probe_client, monkeypatch):
    body = _probe(health_probe_client, monkeypatch, workers=(A, B))
    assert body["status"] == "split"
    assert body["components"]["worker"] == f"{A}, {B}"


def test_a_component_nobody_reported_is_unknown_never_the_backends(
    health_probe_client, monkeypatch
):
    """No nginx header and no worker connection: both read `unknown`, with
    why, and the backend's commit appears nowhere but the backend's."""
    body = _probe(health_probe_client, monkeypatch, nginx=None, workers=())
    assert body["status"] == "incomplete"
    assert body["components"] == {"backend": A, "nginx": "unknown", "worker": "unknown"}
    assert "did not come through nginx" in body["detail"]
    assert "no worker is connected" in body["detail"]


def test_the_containers_own_healthcheck_sees_incomplete_not_ok(
    health_probe_client, monkeypatch
):
    """`/health`'s docstring says a direct call to backend:8000 -- the
    container's healthcheck -- always sees `incomplete`, and stays 200 so
    the container is not restarted over something a restart cannot fix."""
    body = _probe(health_probe_client, monkeypatch, nginx=None)
    assert body["status"] == "incomplete"
    assert body["components"]["nginx"] == "unknown"


def test_an_unstamped_image_is_unknown_with_its_own_reason(health_probe_client, monkeypatch):
    body = _probe(health_probe_client, monkeypatch, backend=None, nginx="unknown")
    assert body["status"] == "incomplete"
    assert body["components"]["backend"] == "unknown"
    assert "nginx image built without WINGRC_BUILD_SHA" in body["detail"]


def test_the_verdict_counts_a_component_left_out_of_its_input_as_unknown():
    """The verdict iterates the deployment's components, not whatever it
    was handed, so it cannot be computed from fewer of them."""
    from app.health import deployment_verdict

    status, detail = deployment_verdict({"backend": [A], "nginx": [A]}, {})
    assert status == "incomplete"
    assert "worker unknown" in detail


def test_the_worker_lookup_cannot_find_the_backends_own_connection():
    """No check that matches its own invocation: /health's connection is
    not named `wingrc-worker@`, and only names with that prefix count."""
    from app.health import WORKER_APP_NAME_PREFIX, WORKER_BUILDS_SQL, worker_builds

    assert f"LIKE '{WORKER_APP_NAME_PREFIX}%'" in WORKER_BUILDS_SQL
    assert worker_builds(["", "psql", "wingrc-backend@x", f"{WORKER_APP_NAME_PREFIX}{A}"]) == [A]


def test_the_worker_names_its_connections_with_its_own_commit(monkeypatch):
    """`cli.worker` sets PGAPPNAME before its first connection; stopped at
    its first call to run_due_jobs, before any database work."""
    import os

    from typer.testing import CliRunner

    import app.scheduler
    from app.cli import app as cli_app

    seen = {}

    def _stop():
        seen["name"] = os.environ.get("PGAPPNAME")
        raise SystemExit(0)

    monkeypatch.setenv("WINGRC_BUILD_SHA", B)
    monkeypatch.delenv("PGAPPNAME", raising=False)
    monkeypatch.setattr(app.scheduler, "run_due_jobs", _stop)
    try:
        CliRunner().invoke(cli_app, ["worker"])
    finally:
        os.environ.pop("PGAPPNAME", None)
    assert seen["name"] == f"wingrc-worker@{B}"


@pytest.mark.integration
def test_health_sees_a_worker_connected_to_the_real_database(client, monkeypatch):
    """Against a real pg_stat_activity: a connection named as a worker is
    reported with its commit, and gone once it closes."""
    import os

    from sqlalchemy import create_engine

    monkeypatch.setenv("WINGRC_BUILD_SHA", A)
    headers = {"X-WinGRC-Nginx-Build": A}
    engine = create_engine(
        os.environ["WINGRC_TEST_DATABASE_URL"],
        connect_args={"application_name": f"wingrc-worker@{B}"},
    )
    conn = engine.connect()
    try:
        body = client.get("/health", headers=headers).json()
        assert body["components"]["worker"] == B, body
        assert body["status"] == "split"
    finally:
        conn.close()
        engine.dispose()
    body = client.get("/health", headers=headers).json()
    assert body["components"]["worker"] == "unknown", body
