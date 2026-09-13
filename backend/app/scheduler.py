"""Postgres-backed job scheduler -- D.3's second infrastructure
prerequisite (after outbound email; see email_service.py). D.3 (daily
Liongard sync + onboarding approval workflow) needs both before it can be
built; neither is built here.

**Architecture, and why.** This deployment has already suffered real
event-loop starvation: at concurrency 50 the single uvicorn worker froze
entirely and needed a manual restart (see config.py's db-pool comment for
the threadpool math that incident is measured against). A scheduled job
that blocks in-process, in that same worker, would take the API down with
it -- that is the single strongest constraint here, and it's empirical,
not theoretical. So scheduled work runs in a genuinely separate process:
a dedicated `worker` container (docker-compose.yml), running the exact
same image as `backend` with a different command (`wingrc worker`,
cli.py). It cannot starve the API's event loop because it isn't in it.

Coordination is a Postgres job table plus session-level advisory locks
(`pg_try_advisory_lock`) -- not a broker (Celery/RQ/Arq). This deployment
is self-hosted by MSPs via plain Docker Compose; Postgres is already
there and already the coordination point for everything else (RLS,
`SET LOCAL app.current_org`, the SPRS-recompute row lock). Adding Redis
or another broker so a background job can run is a real adoption cost
for an open-source self-hosted tool that a Postgres-only design avoids
entirely. In-process APScheduler was considered and rejected outright --
it reintroduces exactly the starvation risk this design exists to avoid.
Host cron calling `wingrc jobs-run-due` was also considered; it's not
rejected, it's *supported* -- see below.

**Two call paths, one function.** `run_due_jobs()` is the entire
scheduling decision (what's due, and running it exactly once) and is the
only thing that decides that. `wingrc worker` (cli.py) calls it in a
sleep loop for operators who want the extra container; `wingrc
jobs-run-due` calls it once and exits, for operators who'd rather point
host cron at `docker compose exec backend wingrc jobs-run-due` and skip
the extra container entirely. Both are the same code path -- there is
exactly one definition of "due" and "ran," never a second, cron-flavored
implementation that could drift from the container's. An operator who
runs neither loses scheduled work only -- the API has no dependency on
this module at request time, so it stays fully functional; the admin
"Scheduled Jobs" panel (routers/scheduled_jobs.py) then honestly shows
every registered job as never having run, rather than silently pretending
otherwise.

**No double-runs.** `pg_try_advisory_lock(key)` is a session-level lock:
it's tied to the physical Postgres backend connection, not to any
application-level "job" concept, and Postgres releases it unconditionally
the moment that connection terminates -- including an ungraceful one
(the backend process dying, the container being killed). That's exactly
the crash-recovery property needed, for free, provided the lock is held
on a connection this module fully controls the lifetime of. It is:
`_lock_engine()` below builds a dedicated engine with `poolclass=NullPool`
just for this, so "acquire, run, release, close" is one real TCP
connection open-to-close, never a pooled connection that could be
recycled for something else while a lock is still logically (but not
physically) attached to it. Two worker containers -- or a worker
restarting mid-job -- calling `pg_try_advisory_lock` on the same key can
never both succeed.

**Crash recovery.** If a job is killed mid-run, its advisory lock is
released automatically as above -- so the *next* attempt at that job is
never blocked. But the `job_run` row that attempt left behind is stuck at
status='running' with no finished_at, forever, unless something notices.
Rather than a separate periodic sweep or a heartbeat/expiry timestamp,
this reconciles per-job at the moment the *next* run acquires that same
job's lock (`_reconcile_orphan`): holding the lock is proof nobody else is
running this job right now, so any row still marked 'running' for it must
be an orphan from a run that never reached its own finally block --
reconciled to 'failed' with an explanatory error before the fresh run's
own row is inserted. This is a lazy, per-job startup sweep, not a
timestamp-expiry guess about how long is "too long."

**Overlap policy: skip, don't queue.** If a job is still running when its
next tick comes due, `pg_try_advisory_lock` simply fails for that tick and
the caller moves on (`outcome: "skipped_running_elsewhere"`) -- no
retry-in-place, no pile-up. For both jobs registered as of this writing,
skipping a tick has no consequence beyond a slightly later effective
run: `expire_stale_invites` just sweeps a little later, and
`sprs_annual_reminder`'s own idempotency (migration 0044 -- keyed to the
submission, not the tick) means a skipped daily due-check simply gets
picked up on the very next one, never a lost or duplicated reminder. A
future job where a skipped tick is NOT harmless should say so explicitly
when it's added.

**Timezone: UTC, fixed intervals, no local-time schedules -- yet.**
`JobSpec.interval` is a plain `timedelta` measured against `job_run.
created_at` (a `timestamptz`, always UTC internally). There is
deliberately no "run at 3am local" / cron-expression concept in this
slice: `sprs_annual_reminder`'s interval is `timedelta(hours=24)` (a
JobSpec-level "check daily"), not "fire on the anniversary" -- the actual
12-month business cadence lives in migration 0044's SQL as a due-check
condition evaluated on every daily tick, which is a fine substitute for
"fire once a year, on the day" for a reminder (a day's slop either side
of the true anniversary is immaterial) but would NOT be for something
needing an exact local calendar date. Neither registered job needs true
cron scheduling, and getting that right (storing an IANA zone name, not
a raw UTC offset, so DST doesn't silently walk the run time) is real
design work that has no job to justify it yet. D.3's daily Liongard sync
will likely want exactly that; add it there, against a real requirement,
not speculatively here.

**RLS.** `job_run` itself is deployment-wide, like
`integration_connection`/`deployment_settings` -- no org_id, no RLS (see
models.py:JobRun). The harder question is what a job's own *body* may do.
The rule: a job that needs to act across every org in one operation goes
through a purpose-built `SECURITY DEFINER` function, exactly like
`auth.msp_role_users()`/`auth.all_users_directory()` -- see
`auth.expire_stale_invites()` (migration 0042) and
`auth.orgs_due_for_sprs_reminder()`/`auth.mark_sprs_reminder_sent()`/
`auth.msp_staff_emails()` (migration 0044) for this codebase's running
set of examples. A future job that needs *per-org* scoped work (D.3's Liongard
sync, most likely) must instead loop over orgs and issue `SET LOCAL
app.current_org = ...` before each org's portion, exactly like a request
handler does -- never a blanket bypass, and never `BYPASSRLS` granted to
any role for this. Nothing in this module grants broader database access
than a CLI command already has today (both connect via the same
`db.py:SessionLocal`, the owner role) -- this design is what keeps that
true as new jobs are added, not a new privilege boundary being introduced
alongside them.
"""

from __future__ import annotations

import hashlib
import logging
import os
import socket
import struct
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine, select, text, update
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from . import email_service, review_cycles
from .audit import log_event
from .config import get_settings
from .db import SessionLocal
from .models import JobRun, ReviewCycle, ReviewCycleReviewer
from .storage import get_storage_client

logger = logging.getLogger(__name__)

WORKER_ID = f"{socket.gethostname()}:{os.getpid()}"


@dataclass(frozen=True)
class JobSpec:
    name: str
    interval: timedelta
    # Takes a live Session (already inside this run's own transaction) and
    # returns a small JSON-serializable summary for job_run.result. Must
    # raise on failure rather than return a falsy/error value -- that's
    # what run_due_jobs() catches to record a failed run.
    run: Callable[[Session], dict]


def _expire_stale_invites(session: Session) -> dict:
    """The proof job: sweep invite_token_hash/invite_expires_at for any
    user row whose token has already expired. See migration 0042's own
    docstring for why this is hygiene, not a security fix (an expired
    token is already unredeemable), and why it goes through a
    SECURITY DEFINER function rather than a plain cross-org UPDATE here.
    """
    count = session.execute(text("SELECT auth.expire_stale_invites()")).scalar_one()
    session.commit()
    return {"expired_count": count}


_SPRS_REMINDER_SUBJECT = "An annual SPRS submission is coming due"
_SPRS_REMINDER_BODY = (
    "An annual SPRS submission is coming due for one or more organizations "
    "you manage in WinGRC.\n\n"
    "Sign in to WinGRC to review: {link}\n"
)


def _sprs_annual_reminder(session: Session) -> dict:
    """The first scheduler job with actual product meaning (expire_stale_
    invites above is a hygiene proof). Checks daily whether any org's
    current SPRS submission has passed its 12-month anniversary with no
    reminder sent yet for that submission -- see migration 0044's
    auth.orgs_due_for_sprs_reminder() for the due-check and idempotency
    query itself, and models.py:SprsReminderLog for why idempotency is
    keyed to the submission, not the org (a new submission resets the
    clock automatically).

    Clock starts at the ATTESTED SUBMISSION DATE ONLY -- an org with no
    sprs_submission row has no clock, full stop; this function never
    substitutes an onboarding date, an assessment start date, or any
    other proxy. That's enforced by construction: the SECURITY DEFINER
    query only ever considers orgs that have a submission at all.

    Content rule (email_service.py's own docstring, restated because this
    is the first call site added since it was written): the email names
    no org, no score, no due date -- the exact same body regardless of
    how many orgs are due, or which ones. If two or ten orgs are all due
    on the same day, this still sends exactly one email per recipient,
    not one per org -- multiplying by org count would be exactly the
    "cries wolf" pattern this slice's own cadence decision (a single
    12-month reminder, not a 60/30/7-day ramp) is trying to avoid.

    Recipients default to MSP staff only (every active msp_admin/
    msp_engineer, deployment-wide, via auth.msp_staff_emails() --
    consultant_admin excluded, matching auth.msp_role_users()'s existing
    role set). Whether a customer contact at the client org should ever
    receive this is an open product question Jarrod has not decided --
    see docs/roadmap.md's entry for this slice. Do not widen this without
    that decision.

    Never raises on a send failure (matching email_service.send()'s own
    contract) -- an unconfigured or failing SMTP setup must not crash the
    job or the daily due-check; it just means no reminder gets marked
    sent, so the same orgs are retried on tomorrow's tick. A due org's
    reminder is marked sent only if at least one recipient email actually
    went out; if every recipient send fails, nothing is marked and every
    due org is retried tomorrow.
    """
    due = session.execute(
        text("SELECT org_id, submission_id FROM auth.orgs_due_for_sprs_reminder()")
    ).all()
    if not due:
        return {"orgs_due": 0, "recipients_emailed": 0, "email_sent": False}

    recipients = [
        r[0] for r in session.execute(text("SELECT email FROM auth.msp_staff_emails()")).all()
    ]
    if not recipients:
        return {"orgs_due": len(due), "recipients_emailed": 0, "email_sent": False}

    public_url = (get_settings().public_url or "").strip()
    link = public_url.rstrip("/") if public_url else "the WinGRC application"
    body = _SPRS_REMINDER_BODY.format(link=link)

    sent_count = 0
    for email in recipients:
        result = email_service.send(
            session, to=email, subject=_SPRS_REMINDER_SUBJECT, body=body,
            template="sprs_annual_reminder",
        )
        if result.sent:
            sent_count += 1

    if sent_count == 0:
        return {"orgs_due": len(due), "recipients_emailed": 0, "email_sent": False}

    for org_id, submission_id in due:
        session.execute(
            text("SELECT auth.mark_sprs_reminder_sent(:org_id, :submission_id)"),
            {"org_id": org_id, "submission_id": submission_id},
        )
    session.commit()
    return {"orgs_due": len(due), "recipients_emailed": sent_count, "email_sent": True}


_REVIEW_REQUEST_SUBJECT = "Review needed: authorized users & devices"
_REVIEW_REQUEST_BODY = (
    "A periodic review of authorized users and devices is awaiting your "
    "review in WinGRC.\n\nSign in to review: {link}\n"
)
_REVIEW_REMINDER_SUBJECT = "Reminder: review needed in WinGRC"
_REVIEW_REMINDER_BODY = (
    "This is a reminder that a periodic review is still awaiting your "
    "attention in WinGRC.\n\nSign in to review: {link}\n"
)


def _review_link() -> str:
    public_url = (get_settings().public_url or "").strip()
    return public_url.rstrip("/") if public_url else "the WinGRC application"


def _review_cycle_open(session: Session) -> dict:
    """Opens a due review cycle for every org whose cadence has elapsed
    (auth.orgs_due_for_review_cycle_open(), migration 0046) and emails
    every snapshotted reviewer -- MSP AND client side both, per the
    design task's explicit recipient decision (unlike
    sprs_annual_reminder, which is MSP-only: the client's acknowledgement
    IS the artifact this feature produces, so they must be in the loop).
    The email itself carries no list, no item detail -- see
    email_service.py's content rule and review_cycles.py's own docstring.
    """
    due = session.execute(
        text("SELECT org_id, cadence_months FROM auth.orgs_due_for_review_cycle_open()")
    ).all()
    if not due:
        return {"cycles_opened": 0}

    link = _review_link()
    opened = 0
    for org_id, _cadence in due:
        session.execute(text("SET LOCAL app.current_org = :org_id"), {"org_id": str(org_id)})
        cycle, reviewers = review_cycles.open_cycle(session, org_id=org_id, opened_by="scheduler")
        log_event(
            session, org_id=org_id, action="review_cycle.open", entity_type="review_cycle",
            entity_id=cycle.id,
            after_value={"reviewer_count": len(reviewers), "due_at": cycle.due_at.isoformat()},
            context={"via": "scheduler"}, actor="system", actor_type="system",
        )
        for r in reviewers:
            email_service.send(
                session, to=r.reviewer_email, subject=_REVIEW_REQUEST_SUBJECT,
                body=_REVIEW_REQUEST_BODY.format(link=link), template="review_cycle_request",
            )
        session.commit()
        opened += 1
    return {"cycles_opened": opened}


def _review_cycle_sweep(session: Session) -> dict:
    """Daily sweep of every 'open' cycle: sends due reminders (idempotent
    per reviewer per reminder number, review_cycle_reminder_log) and
    force-closes any cycle whose due_at has passed as
    'closed_unattested' -- the non-response record §3 of the design task
    calls the most valuable part of this feature. A cycle that reaches
    full attestation before its due_at is closed immediately by
    review_cycles.attest() itself, not by this job -- this job only ever
    sees cycles still open because at least one reviewer hasn't responded
    yet.
    """
    open_cycles = session.execute(
        text("SELECT cycle_id, org_id FROM auth.review_cycles_due_for_sweep()")
    ).all()
    if not open_cycles:
        return {"reminders_sent": 0, "cycles_closed": 0}

    storage = get_storage_client()
    link = _review_link()
    now = datetime.now(UTC)
    reminders_sent = 0
    cycles_closed = 0

    for cycle_id, org_id in open_cycles:
        session.execute(text("SET LOCAL app.current_org = :org_id"), {"org_id": str(org_id)})
        cycle = session.get(ReviewCycle, cycle_id)
        reviewers = list(
            session.scalars(
                select(ReviewCycleReviewer).where(ReviewCycleReviewer.cycle_id == cycle_id)
            )
        )

        if now >= cycle.due_at:
            review_cycles.close_cycle(session, storage, cycle=cycle, status="closed_unattested")
            log_event(
                session, org_id=org_id, action="review_cycle.closed_unattested",
                entity_type="review_cycle", entity_id=cycle_id,
                after_value={
                    "no_response": sum(1 for r in reviewers if r.status != "attested"),
                },
                context={"via": "scheduler"}, actor="system", actor_type="system",
            )
            session.commit()
            cycles_closed += 1
            continue

        for r in reviewers:
            already = {
                row[0]
                for row in session.execute(
                    text(
                        "SELECT reminder_number FROM review_cycle_reminder_log "
                        "WHERE reviewer_id = :rid"
                    ),
                    {"rid": r.id},
                ).all()
            }
            number = review_cycles.due_reminder_number(r, now=now, already_sent=already)
            if number is None:
                continue
            result = email_service.send(
                session, to=r.reviewer_email, subject=_REVIEW_REMINDER_SUBJECT,
                body=_REVIEW_REMINDER_BODY.format(link=link), template="review_cycle_reminder",
            )
            if result.sent:
                review_cycles.record_reminder_sent(
                    session, cycle_id=cycle_id, reviewer_id=r.id, reminder_number=number
                )
                reminders_sent += 1
        session.commit()

    return {"reminders_sent": reminders_sent, "cycles_closed": cycles_closed}


JOB_REGISTRY: dict[str, JobSpec] = {
    spec.name: spec
    for spec in (
        JobSpec(
            name="expire_stale_invites",
            interval=timedelta(hours=1),
            run=_expire_stale_invites,
        ),
        JobSpec(
            name="review_cycle_open",
            # Opening is a cadence check (a due-check against each org's
            # own review_cadence_months, in months), so daily is plenty
            # granular -- see module docstring's Timezone section for why
            # a daily due-check substitutes for "fire on the exact
            # anniversary" here.
            interval=timedelta(hours=24),
            run=_review_cycle_open,
        ),
        JobSpec(
            name="review_cycle_sweep",
            interval=timedelta(hours=24),
            run=_review_cycle_sweep,
        ),
        JobSpec(
            name="sprs_annual_reminder",
            # Fixed UTC intervals are the only schedule type this
            # scheduler supports today (see module docstring's Timezone
            # section) -- fine for a yearly business cadence, but that
            # means "check daily whether anything is due," not "fire on
            # the anniversary." The 12-month business rule itself lives
            # in migration 0044's SQL, not here.
            interval=timedelta(hours=24),
            run=_sprs_annual_reminder,
        ),
    )
}


def _lock_key(job_name: str) -> int:
    """Deterministic signed-64-bit key for pg_try_advisory_lock(bigint),
    derived in Python rather than via a Postgres hash function -- stable
    across Postgres versions/collations, and testable without a database.
    """
    digest = hashlib.sha256(job_name.encode()).digest()[:8]
    return struct.unpack(">q", digest)[0]


def _lock_engine():
    """A fresh, dedicated engine per call -- deliberately NOT the shared
    pooled engine db.py:engine uses, and deliberately not cached at module
    level either (so a test that points WINGRC_DATABASE_URL at a different
    database between runs is never handed a stale connection to the wrong
    one). See this module's own docstring on why the advisory lock's
    connection lifetime must be fully controlled here (NullPool: every
    checkout is a brand-new physical connection, every close is a real
    disconnect, so Postgres always releases the lock, including on an
    ungraceful process kill). Jobs run at most a few times an hour, so
    building an Engine per call costs nothing that matters."""
    return create_engine(get_settings().database_url, poolclass=NullPool, future=True)


def last_run_for(session: Session, job_name: str) -> JobRun | None:
    return session.scalars(
        select(JobRun)
        .where(JobRun.job_name == job_name)
        .order_by(JobRun.created_at.desc())
        .limit(1)
    ).first()


def _is_due(session: Session, spec: JobSpec, now: datetime) -> bool:
    last = last_run_for(session, spec.name)
    if last is None:
        return True
    reference = last.finished_at or last.started_at
    return now - reference >= spec.interval


def _reconcile_orphan(session: Session, job_name: str) -> None:
    """Called only while this process holds job_name's advisory lock --
    that's proof nothing else can be running it right now, so any row
    still marked 'running' for it is left over from a run that was killed
    before reaching its own finally block. See module docstring's "Crash
    recovery" section."""
    session.execute(
        update(JobRun)
        .where(JobRun.job_name == job_name, JobRun.status == "running")
        .values(
            status="failed",
            finished_at=datetime.now(UTC),
            error="Orphaned: worker process terminated without completing this run "
            "(detected when the next run acquired the job's lock).",
        )
    )
    session.commit()


def _run_one(spec: JobSpec, scheduled_for: datetime) -> dict:
    """Try to acquire spec's advisory lock and, if acquired, run it end to
    end: reconcile any orphan, insert the 'running' row, execute the job
    body, and record success or failure. Never raises -- a job's own
    exception is caught here and recorded, not propagated, so one job
    failing can never stop run_due_jobs() from trying the rest.
    """
    key = _lock_key(spec.name)
    lock_engine = _lock_engine()
    lock_conn = lock_engine.connect()
    got = False
    try:
        got = lock_conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": key}).scalar()
        if not got:
            return {"job": spec.name, "outcome": "skipped_running_elsewhere"}

        session = SessionLocal()
        run_id = uuid.uuid4()
        try:
            _reconcile_orphan(session, spec.name)

            started_at = datetime.now(UTC)
            session.add(
                JobRun(
                    id=run_id,
                    job_name=spec.name,
                    scheduled_for=scheduled_for,
                    started_at=started_at,
                    status="running",
                    worker_id=WORKER_ID,
                )
            )
            session.commit()

            try:
                result = spec.run(session)
            except Exception as e:  # noqa: BLE001 -- recorded, never re-raised
                session.rollback()
                session.execute(
                    update(JobRun)
                    .where(JobRun.id == run_id)
                    .values(status="failed", finished_at=datetime.now(UTC), error=str(e))
                )
                session.commit()
                logger.warning("Job failed: name=%s error=%s", spec.name, e)
                return {"job": spec.name, "outcome": "failed", "error": str(e)}

            session.execute(
                update(JobRun)
                .where(JobRun.id == run_id)
                .values(status="succeeded", finished_at=datetime.now(UTC), result=result)
            )
            session.commit()
            logger.info("Job succeeded: name=%s result=%s", spec.name, result)
            return {"job": spec.name, "outcome": "succeeded", "result": result}
        finally:
            session.close()
    finally:
        try:
            if got:
                lock_conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": key})
        finally:
            lock_conn.close()
            lock_engine.dispose()


def run_due_jobs() -> list[dict]:
    """The scheduling decision, in full: for every registered job, check
    whether it's due and, if so, try to run it (which may still yield
    "skipped_running_elsewhere" if another worker beat this one to the
    lock). Returns one outcome dict per registered job, for CLI/log
    output. Called by `wingrc worker`'s loop and `wingrc jobs-run-due`'s
    one-shot command -- see module docstring; there is no other entry
    point that decides "due."
    """
    now = datetime.now(UTC)
    outcomes: list[dict] = []
    for spec in JOB_REGISTRY.values():
        session = SessionLocal()
        try:
            due = _is_due(session, spec, now)
        finally:
            session.close()

        if not due:
            outcomes.append({"job": spec.name, "outcome": "not_due"})
            continue

        outcomes.append(_run_one(spec, now))
    return outcomes
