"""Read-only admin visibility into the job scheduler (scheduler.py) --
D.3's second infrastructure prerequisite. Manual "run now" and enable/
disable are NOT here: they're their own slice, with their own
authorization questions (see scheduler.py's module docstring for the
mechanism this surfaces status for).

GET /admin/scheduled-jobs  Every registered job: name, interval, last run
                            (started/finished/status/error/result), and
                            when it's next due.

Deployment-wide, not org-scoped -- job_run carries no org_id (see
models.py:JobRun). Gated to msp_admin only, matching admin_users.py's
reasoning rather than integrations.py's (msp_admin + consultant_admin):
this is operational/identity-adjacent infrastructure status (is the
worker container even running, did a background sweep crash), not
compliance-data configuration. Worth revisiting once D.3 adds per-org
sync jobs, whose visibility might belong closer to Integrations instead.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..auth import require_role
from ..db import get_session
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
