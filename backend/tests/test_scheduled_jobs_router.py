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
