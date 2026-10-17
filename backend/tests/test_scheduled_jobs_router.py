"""Integration tests for routers/scheduled_jobs.py: RBAC (msp_admin only,
NOT consultant_admin -- unlike Integrations/Tools) and the response shape
admin visibility depends on.

scheduler.py's own behavior (due-check, locking, crash recovery) is
covered in test_scheduler.py; this file only covers the read endpoint
built on top of it.

Run in-container:
    docker compose exec backend pytest tests/test_scheduled_jobs_router.py -m integration -v
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.auth import get_current_user
from app.db import get_session
from app.main import app
from app.models import JobRun
from app.scheduler import JOB_REGISTRY
from tests.conftest import _app_session, _authed, _make_fake_user

pytestmark = pytest.mark.integration

_JOB_NAME = next(iter(JOB_REGISTRY))  # "expire_stale_invites" today


@pytest.fixture
def admin_client(db_session, fake_msp_admin):
    app.dependency_overrides[get_session] = _app_session(db_session)
    app.dependency_overrides[get_current_user] = _authed(db_session, fake_msp_admin)
    yield TestClient(app)
    app.dependency_overrides.clear()


def _client_as(db_session, role: str):
    user = _make_fake_user(role=role)
    app.dependency_overrides[get_session] = _app_session(db_session)
    app.dependency_overrides[get_current_user] = _authed(db_session, user)
    return TestClient(app)


def test_msp_admin_can_list(admin_client):
    r = admin_client.get("/admin/scheduled-jobs")
    assert r.status_code == 200
    names = [j["job_name"] for j in r.json()]
    assert _JOB_NAME in names


@pytest.mark.parametrize("role", ["msp_engineer", "consultant_admin", "customer_poc"])
def test_other_roles_are_refused(db_session, role):
    r = _client_as(db_session, role).get("/admin/scheduled-jobs")
    assert r.status_code == 403


def test_job_that_never_ran_shows_null_last_run_and_due_now(admin_client):
    r = admin_client.get("/admin/scheduled-jobs")
    entry = next(j for j in r.json() if j["job_name"] == _JOB_NAME)
    assert entry["last_run"] is None
    assert entry["next_due_at"] is None
    assert entry["interval_seconds"] > 0


def test_job_with_a_recorded_run_surfaces_it_and_computes_next_due(admin_client, db_session):
    started = datetime.now(UTC) - timedelta(minutes=5)
    finished = started + timedelta(seconds=2)
    db_session.add(
        JobRun(
            id=uuid.uuid4(),
            job_name=_JOB_NAME,
            scheduled_for=started,
            started_at=started,
            finished_at=finished,
            status="succeeded",
            result={"expired_count": 3},
            worker_id="worker-1:123",
        )
    )
    db_session.commit()

    r = admin_client.get("/admin/scheduled-jobs")
    entry = next(j for j in r.json() if j["job_name"] == _JOB_NAME)
    assert entry["last_run"]["status"] == "succeeded"
    assert entry["last_run"]["result"] == {"expired_count": 3}
    assert entry["last_run"]["error"] is None
    # next_due_at = finished_at + interval, not started_at + interval.
    interval = JOB_REGISTRY[_JOB_NAME].interval
    expected_next_due = finished + interval
    actual_next_due = datetime.fromisoformat(entry["next_due_at"])
    assert abs((actual_next_due - expected_next_due).total_seconds()) < 1


def test_failed_run_surfaces_its_error(admin_client, db_session):
    started = datetime.now(UTC) - timedelta(minutes=5)
    db_session.add(
        JobRun(
            id=uuid.uuid4(),
            job_name=_JOB_NAME,
            scheduled_for=started,
            started_at=started,
            finished_at=started + timedelta(seconds=1),
            status="failed",
            error="Something went wrong",
            worker_id="worker-1:123",
        )
    )
    db_session.commit()

    r = admin_client.get("/admin/scheduled-jobs")
    entry = next(j for j in r.json() if j["job_name"] == _JOB_NAME)
    assert entry["last_run"]["status"] == "failed"
    assert entry["last_run"]["error"] == "Something went wrong"


# ── GET /admin/scheduled-jobs/tls-expiry: the banner's data ───────────────

_TLS_URL = "/admin/scheduled-jobs/tls-expiry"


@pytest.fixture
def tls_configured(monkeypatch):
    from types import SimpleNamespace

    from app.routers import scheduled_jobs

    monkeypatch.setattr(scheduled_jobs, "get_settings", lambda: SimpleNamespace(
        public_url="https://dev.example.test", storage_public_endpoint=None,
    ))


def _tls_run(db_session, *, status, finished_at, not_after=None):
    db_session.add(JobRun(
        id=uuid.uuid4(), job_name="tls_expiry_check", scheduled_for=finished_at,
        started_at=finished_at - timedelta(seconds=1), finished_at=finished_at,
        status=status, worker_id="worker-1:123",
        result=None if status != "succeeded" else {"certs": [{
            "name": "dev.example.test",
            "not_after": not_after.isoformat() if not_after else None,
            "error": None if not_after else "TimeoutError: timed out",
        }]},
    ))
    db_session.commit()


def test_tls_expiry_is_a_registered_job():
    assert JOB_REGISTRY["tls_expiry_check"].interval <= timedelta(hours=24)


@pytest.mark.parametrize("role", ["msp_engineer", "consultant_admin", "customer_poc"])
def test_tls_expiry_is_msp_admin_only(db_session, role):
    assert _client_as(db_session, role).get(_TLS_URL).status_code == 403


def test_tls_expiry_never_run_needs_attention(admin_client, tls_configured):
    body = admin_client.get(_TLS_URL).json()
    assert body["configured"] is True
    assert body["stale"] is True and body["attention"] is True


def test_tls_expiry_reports_the_last_successful_reading(admin_client, db_session, tls_configured):
    now = datetime.now(UTC)
    _tls_run(db_session, status="succeeded", finished_at=now - timedelta(hours=7),
             not_after=now + timedelta(days=12))
    # A later failed run has no reading; it must not hide the one above.
    _tls_run(db_session, status="failed", finished_at=now - timedelta(hours=1))
    body = admin_client.get(_TLS_URL).json()
    assert body["attention"] is True and body["stale"] is False
    assert body["window_days"] == 30
    [cert] = body["certs"]
    assert cert["name"] == "dev.example.test"
    assert cert["verdict"] == "expiring"
    assert cert["days_left"] in (11, 12)


def test_tls_expiry_fresh_and_fine_is_quiet(admin_client, db_session, tls_configured):
    now = datetime.now(UTC)
    _tls_run(db_session, status="succeeded", finished_at=now - timedelta(hours=1),
             not_after=now + timedelta(days=80))
    body = admin_client.get(_TLS_URL).json()
    assert body["attention"] is False
    assert body["certs"][0]["verdict"] == "ok"


def test_tls_expiry_without_an_https_public_url_is_quiet(admin_client, monkeypatch):
    from types import SimpleNamespace

    from app.routers import scheduled_jobs

    monkeypatch.setattr(scheduled_jobs, "get_settings", lambda: SimpleNamespace(
        public_url="http://localhost:5173", storage_public_endpoint=None,
    ))
    body = admin_client.get(_TLS_URL).json()
    assert body["configured"] is False and body["attention"] is False
