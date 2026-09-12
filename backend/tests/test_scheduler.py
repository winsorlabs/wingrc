"""Integration tests for scheduler.py -- the job scheduler (D.3's second
infrastructure prerequisite, after outbound email).

These tests point scheduler.py's SessionLocal/_lock_engine at the real,
dedicated wingrc_test database (via db_engine's own DSN) rather than
db_session's savepoint-per-test fixture, and use a throwaway job
registered just for this file rather than the real registry. Both choices
are load-bearing: the properties under test here -- two genuinely separate
connections racing pg_try_advisory_lock, a row that must still be visible
after this test's own transaction ends -- are exactly what a shared,
rolled-back-at-teardown session can't exercise faithfully. Rows this file
commits are cleaned up explicitly in a fixture teardown, not left for the
next test's rollback to hide.

Not covered here (see the outbound-email/job-scheduler task writeup for
the honest reason): a live measurement of API responsiveness under a real
concurrent HTTP load while a real `worker` container runs a real slow job
-- that requires the actual multi-container bench stack (separate
uvicorn/worker OS processes), which no pytest process can reproduce.
test_slow_job_does_not_block_other_database_activity below is a weaker,
necessary-but-not-sufficient regression guard: it proves scheduler.py
itself holds no lock/resource for a run's duration that would block an
unrelated query, which the container-separation argument depends on but
does not by itself prove end to end.

Run in-container:
    docker compose exec backend pytest tests/test_scheduler.py -m integration -v
"""

from __future__ import annotations

import threading
import time
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from app import scheduler
from app.models import JobRun, Organization, User

pytestmark = pytest.mark.integration

_TEST_JOB_NAME = f"test_job_{uuid.uuid4().hex[:8]}"


@pytest.fixture
def scheduler_env(monkeypatch, db_engine):
    """Point scheduler.py's SessionLocal/_lock_engine at the real test
    database (db_engine's own DSN) instead of the savepoint-wrapped
    db_session fixture -- see module docstring. Cleans up every job_run
    row this test's job name produced, regardless of outcome.
    """
    test_url = db_engine.url

    def _session_factory():
        return Session(db_engine, autoflush=False, expire_on_commit=False)

    def _lock_engine():
        return create_engine(test_url, poolclass=NullPool, future=True)

    monkeypatch.setattr(scheduler, "SessionLocal", _session_factory)
    monkeypatch.setattr(scheduler, "_lock_engine", _lock_engine)
    yield
    with db_engine.connect() as conn:
        conn.execute(text("DELETE FROM job_run WHERE job_name = :n"), {"n": _TEST_JOB_NAME})
        conn.commit()


def _register(monkeypatch, run_fn, interval=timedelta(hours=1)):
    spec = scheduler.JobSpec(name=_TEST_JOB_NAME, interval=interval, run=run_fn)
    monkeypatch.setattr(scheduler, "JOB_REGISTRY", {_TEST_JOB_NAME: spec})
    return spec


def _rows(db_engine) -> list[JobRun]:
    with Session(db_engine) as s:
        return list(
            s.scalars(
                select(JobRun)
                .where(JobRun.job_name == _TEST_JOB_NAME)
                .order_by(JobRun.created_at)
            )
        )


# ---------------------------------------------------------------------------
# Basic run + record + admin visibility
# ---------------------------------------------------------------------------


def test_due_job_runs_and_records_success(scheduler_env, monkeypatch, db_engine):
    _register(monkeypatch, lambda session: {"ok": True})

    outcomes = scheduler.run_due_jobs()

    assert outcomes == [{"job": _TEST_JOB_NAME, "outcome": "succeeded", "result": {"ok": True}}]
    rows = _rows(db_engine)
    assert len(rows) == 1
    assert rows[0].status == "succeeded"
    assert rows[0].result == {"ok": True}
    assert rows[0].finished_at is not None
    assert rows[0].worker_id == scheduler.WORKER_ID


def test_visible_via_last_run_for_the_admin_panel(scheduler_env, monkeypatch, db_engine):
    _register(monkeypatch, lambda session: {"expired_count": 2})
    scheduler.run_due_jobs()

    with Session(db_engine) as s:
        last = scheduler.last_run_for(s, _TEST_JOB_NAME)

    assert last is not None
    assert last.status == "succeeded"
    assert last.result == {"expired_count": 2}


def test_not_due_job_is_skipped_without_running(scheduler_env, monkeypatch, db_engine):
    calls = []
    _register(monkeypatch, lambda session: calls.append(1) or {}, interval=timedelta(days=1))
    scheduler.run_due_jobs()  # first call: no prior row, due immediately
    calls.clear()

    outcomes = scheduler.run_due_jobs()  # second call, right away: not due

    assert outcomes == [{"job": _TEST_JOB_NAME, "outcome": "not_due"}]
    assert calls == []
    assert len(_rows(db_engine)) == 1  # only the first run's row exists


# ---------------------------------------------------------------------------
# Failure handling
# ---------------------------------------------------------------------------


def test_failing_job_records_failure_and_next_tick_still_runs(
    scheduler_env, monkeypatch, db_engine
):
    attempt = {"n": 0}

    def flaky(session):
        attempt["n"] += 1
        if attempt["n"] == 1:
            raise ValueError("boom")
        return {"ok": True}

    _register(monkeypatch, flaky, interval=timedelta(seconds=0))

    first = scheduler.run_due_jobs()
    assert first == [{"job": _TEST_JOB_NAME, "outcome": "failed", "error": "boom"}]

    second = scheduler.run_due_jobs()
    assert second == [{"job": _TEST_JOB_NAME, "outcome": "succeeded", "result": {"ok": True}}]

    rows = _rows(db_engine)
    assert [r.status for r in rows] == ["failed", "succeeded"]
    assert rows[0].error == "boom"
    assert rows[0].finished_at is not None


# ---------------------------------------------------------------------------
# No double-runs (concurrency) and overlap policy
# ---------------------------------------------------------------------------


def test_concurrent_attempts_run_the_job_exactly_once(scheduler_env, monkeypatch, db_engine):
    entered = threading.Event()
    proceed = threading.Event()
    run_count = {"n": 0}

    def slow_job(session):
        run_count["n"] += 1
        entered.set()
        assert proceed.wait(timeout=5), "test setup error: proceed was never set"
        return {"ok": True}

    spec = _register(monkeypatch, slow_job)
    results: dict[str, dict] = {}

    def run_first():
        results["first"] = scheduler._run_one(spec, datetime.now(UTC))

    t1 = threading.Thread(target=run_first)
    t1.start()
    assert entered.wait(timeout=5), "first attempt never entered the job body"

    # A second attempt while the first genuinely still holds the lock
    # (blocked on proceed.wait()) -- this is the property under test.
    second = scheduler._run_one(spec, datetime.now(UTC))
    assert second == {"job": _TEST_JOB_NAME, "outcome": "skipped_running_elsewhere"}

    proceed.set()
    t1.join(timeout=5)

    assert results["first"]["outcome"] == "succeeded"
    assert run_count["n"] == 1  # the job body itself only ran once

    rows = _rows(db_engine)
    assert len(rows) == 1
    assert rows[0].status == "succeeded"


def test_long_running_job_does_not_overlap_itself_across_ticks(
    scheduler_env, monkeypatch, db_engine
):
    """Same property as the concurrency test, exercised through
    run_due_jobs() itself (the real scheduling entry point) rather than
    the private _run_one -- a slow job's next scheduled tick must skip,
    not queue behind it."""
    entered = threading.Event()
    proceed = threading.Event()

    def slow_job(session):
        entered.set()
        assert proceed.wait(timeout=5), "test setup error: proceed was never set"
        return {"ok": True}

    _register(monkeypatch, slow_job, interval=timedelta(seconds=0))

    tick_results: dict[str, list[dict]] = {}

    def first_tick():
        tick_results["first"] = scheduler.run_due_jobs()

    t1 = threading.Thread(target=first_tick)
    t1.start()
    assert entered.wait(timeout=5)

    # The next tick fires while the first is still in flight -- it must
    # see the job as already running and skip, not pile up behind it.
    second_tick = scheduler.run_due_jobs()
    assert second_tick == [{"job": _TEST_JOB_NAME, "outcome": "skipped_running_elsewhere"}]

    proceed.set()
    t1.join(timeout=5)

    assert tick_results["first"] == [
        {"job": _TEST_JOB_NAME, "outcome": "succeeded", "result": {"ok": True}}
    ]
    assert len(_rows(db_engine)) == 1


# ---------------------------------------------------------------------------
# Crash recovery
# ---------------------------------------------------------------------------


def test_orphaned_running_row_is_reconciled_on_the_next_run(scheduler_env, monkeypatch, db_engine):
    """Simulates a worker killed mid-job: a 'running' row with no
    finished_at, and -- critically -- nobody currently holding its
    advisory lock, matching exactly what a real process death leaves
    behind (Postgres releases a session-level advisory lock the instant
    the connection dies)."""
    _register(monkeypatch, lambda session: {"ok": True}, interval=timedelta(minutes=1))

    orphan_id = uuid.uuid4()
    with Session(db_engine) as s:
        s.add(
            JobRun(
                id=orphan_id,
                job_name=_TEST_JOB_NAME,
                scheduled_for=datetime.now(UTC) - timedelta(hours=2),
                started_at=datetime.now(UTC) - timedelta(hours=2),
                status="running",
                worker_id="dead-worker:999",
            )
        )
        s.commit()

    outcomes = scheduler.run_due_jobs()
    assert outcomes == [{"job": _TEST_JOB_NAME, "outcome": "succeeded", "result": {"ok": True}}]

    rows = {r.id: r for r in _rows(db_engine)}
    assert len(rows) == 2
    orphan = rows[orphan_id]
    assert orphan.status == "failed"
    assert orphan.finished_at is not None
    assert "Orphaned" in orphan.error

    fresh = [r for rid, r in rows.items() if rid != orphan_id][0]
    assert fresh.status == "succeeded"


# ---------------------------------------------------------------------------
# Does not block other database activity (see module docstring's caveat)
# ---------------------------------------------------------------------------


def test_slow_job_does_not_block_other_database_activity(scheduler_env, monkeypatch, db_engine):
    entered = threading.Event()
    finish = threading.Event()

    def slow_job(session):
        entered.set()
        assert finish.wait(timeout=5), "test setup error: finish was never set"
        return {"ok": True}

    _register(monkeypatch, slow_job)

    t = threading.Thread(target=scheduler.run_due_jobs)
    t.start()
    assert entered.wait(timeout=5)

    start = time.monotonic()
    with Session(db_engine) as s:
        assert s.execute(text("SELECT 1")).scalar_one() == 1
    elapsed = time.monotonic() - start

    finish.set()
    t.join(timeout=5)

    assert elapsed < 1.0, f"an unrelated query took {elapsed:.2f}s while a job was running"


# ---------------------------------------------------------------------------
# RLS: the SECURITY DEFINER mechanism (migration 0042) works without a
# broader bypass, and touches exactly what it claims to.
# ---------------------------------------------------------------------------


def test_expire_stale_invites_works_under_restricted_role(db_session):
    """Proves the SECURITY DEFINER mechanism this slice's proof job relies
    on for its cross-org sweep is genuinely usable by wingrc_app (the
    RLS-restricted role a future runtime cutover would run the worker
    as) with no broader grant, and that it clears exactly the stale row's
    two columns -- never a still-valid token, never anything else."""
    org = Organization(name=f"SchedTestOrg-{uuid.uuid4().hex[:8]}")
    db_session.add(org)
    db_session.flush()

    stale = User(
        home_org_id=org.id,
        email=f"stale-{uuid.uuid4().hex[:6]}@example.com",
        display_name="Stale Invite",
        login_method="local",
        role="customer_poc",
        is_active=False,
        invite_token_hash="deadbeef",
        invite_expires_at=datetime.now(UTC) - timedelta(hours=1),
    )
    fresh = User(
        home_org_id=org.id,
        email=f"fresh-{uuid.uuid4().hex[:6]}@example.com",
        display_name="Fresh Invite",
        login_method="local",
        role="customer_poc",
        is_active=False,
        invite_token_hash="cafebabe",
        invite_expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    db_session.add_all([stale, fresh])
    db_session.commit()

    conn = db_session.connection()
    conn.execute(text("SET ROLE wingrc_app"))
    try:
        count = conn.execute(text("SELECT auth.expire_stale_invites()")).scalar_one()
    finally:
        conn.execute(text("RESET ROLE"))

    assert count == 1
    db_session.refresh(stale)
    db_session.refresh(fresh)
    assert stale.invite_token_hash is None
    assert stale.invite_expires_at is None
    assert fresh.invite_token_hash == "cafebabe"
    assert fresh.invite_expires_at is not None
