"""Read-only admin visibility into the job scheduler (scheduler.py) --
D.3's second infrastructure prerequisite. Manual "run now" and enable/
disable are NOT here: they're their own slice, with their own
authorization questions (see scheduler.py's module docstring for the
mechanism this surfaces status for).

GET /admin/scheduled-jobs  Every registered job: name, interval, last run
                            (started/finished/status/error/result), and
                            when it's next due.
GET /admin/scheduled-jobs/tls-expiry
                            The served certificates' expiry, from the last
                            successful tls_expiry_check -- the banner's data.

Deployment-wide, not org-scoped -- job_run carries no org_id (see
models.py:JobRun). Gated to msp_admin only, matching admin_users.py's
reasoning rather than integrations.py's (msp_admin + consultant_admin):
this is operational/identity-adjacent infrastructure status (is the
worker container even running, did a background sweep crash), not
compliance-data configuration. Worth revisiting once D.3 adds per-org
sync jobs, whose visibility might belong closer to Integrations instead.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import tls_expiry
from ..auth import require_role
from ..config import get_settings
from ..db import get_session
from ..models import JobRun
from ..scheduler import JOB_REGISTRY, last_run_for

router = APIRouter(
    prefix="/admin/scheduled-jobs",
    tags=["scheduled-jobs"],
    dependencies=[Depends(require_role("msp_admin"))],
)


class LastRunOut(BaseModel):
    started_at: datetime
    finished_at: datetime | None
    status: str
    error: str | None
    result: dict | None
    worker_id: str


class ScheduledJobOut(BaseModel):
    job_name: str
    interval_seconds: int
    last_run: LastRunOut | None
    # None means "never run -- due now," not "no schedule."
    next_due_at: datetime | None


@router.get("", response_model=list[ScheduledJobOut])
def list_scheduled_jobs(db: Session = Depends(get_session)) -> list[ScheduledJobOut]:
    out: list[ScheduledJobOut] = []
    for spec in JOB_REGISTRY.values():
        last = last_run_for(db, spec.name)
        last_out = None
        next_due_at = None
        if last is not None:
            last_out = LastRunOut(
                started_at=last.started_at,
                finished_at=last.finished_at,
                status=last.status,
                error=last.error,
                result=last.result,
                worker_id=last.worker_id,
            )
            reference = last.finished_at or last.started_at
            next_due_at = reference + spec.interval
        out.append(
            ScheduledJobOut(
                job_name=spec.name,
                interval_seconds=int(spec.interval.total_seconds()),
                last_run=last_out,
                next_due_at=next_due_at,
            )
        )
    return out


class TlsCertOut(BaseModel):
    name: str
    not_after: datetime | None
    days_left: int | None
    verdict: str  # ok | expiring | expired | unreadable
    error: str | None


class TlsExpiryOut(BaseModel):
    configured: bool
    attention: bool
    checked_at: datetime | None
    stale: bool
    window_days: int
    certs: list[TlsCertOut]


@router.get("/tls-expiry", response_model=TlsExpiryOut)
def tls_expiry_status(db: Session = Depends(get_session)) -> TlsExpiryOut:
    # The last run that finished: a failed or still-running one has no
    # reading, and must not hide the previous one. If none finished
    # recently, status() says the check is stale.
    last = db.scalars(
        select(JobRun)
        .where(JobRun.job_name == "tls_expiry_check", JobRun.status == "succeeded")
        .order_by(JobRun.finished_at.desc(), JobRun.id.desc())
        .limit(1)
    ).first()
    out = tls_expiry.status(
        configured=bool(tls_expiry.configured_targets(get_settings())),
        result=last.result if last else None,
        checked_at=last.finished_at if last else None,
        now=datetime.now(UTC),
    )
    return TlsExpiryOut(window_days=tls_expiry.WINDOW_DAYS, **out)
