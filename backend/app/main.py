"""WinGRC API.

Scope-graph endpoints (read, manual CRUD, workbook import/export) live in
routers/scope.py (G.5). This file wires up the FastAPI app, middleware, and
router registration, plus the two endpoints with no dedicated router of
their own: /health and /catalog/views (read-only catalog metadata, not
scoped to any one org).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from fastapi import Depends, FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.orm import Session

from .audit import set_current_ip
from .auth import CurrentUser, get_client_ip, get_current_user
from .catalog import ALL_VIEWS
from .config import get_settings
from .db import get_session
from .routers import (
    admin_products,
    admin_users,
    assessments,
    audit_log,
    bundle,
    contacts,
    dashboard,
    documents,
    evidence,
    frameworks,
    integrations,
    liongard_sync,
    objectives,
    orgs,
    raci,
    review_cycles,
    scheduled_jobs,
    scope,
    sprs_submissions,
)
from .routers import auth as auth_router
from .routers import users as users_router

settings = get_settings()
app = FastAPI(title=settings.app_name, version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def _stamp_audit_ip(request: Request, call_next):
    """Make the resolved client IP available to audit.log_event() for the
    duration of this request, without threading it through every call site
    (see audit.py's module docstring for why). Runs before routing, so it
    covers every endpoint including the ones with no Request in their own
    signature.
    """
    set_current_ip(get_client_ip(request))
    return await call_next(request)


app.include_router(auth_router.router)
app.include_router(frameworks.router)
app.include_router(orgs.router)
app.include_router(contacts.router)
app.include_router(scope.router)
app.include_router(assessments.router)
app.include_router(evidence.router)
app.include_router(bundle.router)
app.include_router(users_router.router)
app.include_router(audit_log.router)
app.include_router(dashboard.router)
app.include_router(raci.router)
app.include_router(integrations.router)
app.include_router(objectives.router)
app.include_router(admin_products.router)
app.include_router(admin_users.router)
app.include_router(scheduled_jobs.router)
app.include_router(sprs_submissions.router)
app.include_router(review_cycles.router)
app.include_router(liongard_sync.router)
app.include_router(documents.router)


@lru_cache(maxsize=1)
def _expected_migration_head() -> str | None:
    """The revision this image's code expects, read once from the script
    directory. Fixed for the life of the process, so it is cached rather
    than re-scanned on every healthcheck.

    Returns None if the script directory cannot be read, which makes the
    head comparison skip rather than report unhealthy -- a packaging
    problem should not look like a database problem.
    """
    try:
        from alembic.config import Config as AlembicConfig
        from alembic.script import ScriptDirectory

        ini = Path(__file__).resolve().parents[1] / "alembic.ini"
        return ScriptDirectory.from_config(AlembicConfig(str(ini))).get_current_head()
    except Exception:  # noqa: BLE001 -- see docstring; never fail the check here
        return None


@app.get("/health")
def health(response: Response, db: Session = Depends(get_session)) -> dict:
    """Liveness AND readiness, because this is the instrument a deploy is
    judged by and it used to prove nothing.

    It previously returned a static dict. That mattered: an
    `alembic upgrade head` that ran, logged success, exited 0 and rolled
    back left a completely empty database, and `docker compose ps` reported
    the backend healthy throughout. Nothing here touched a table, so
    nothing noticed. See docs/roadmap.md's RLS-track entries for the
    incident.

    Two checks, both deliberately cheap enough to run every 30 seconds:

    1. **`SELECT 1` as the application's own role.** Proves the connection
       authenticates and the database answers. This is the check that
       matters for the `wingrc_app` cutover -- a bad password or a revoked
       grant shows up here rather than as a wall of 500s.
    2. **`alembic_version` matches the head this image ships.** This is the
       one that would have caught the rollback incident directly. It reads
       one row from a one-row table and compares it to a cached string.

    On (2) being a readiness condition rather than a warning: a deploy IS
    unhealthy while the schema does not match the code, and saying so is
    the point. There is no window where that costs anything in this
    deployment shape -- the backend's own command is
    `alembic upgrade head && exec uvicorn`, so uvicorn never starts until
    migrations finish, and the worker has healthchecks disabled. A
    multi-replica rollout would see a genuine window, and being marked
    unhealthy during it is still the correct answer.

    `alembic_version` carries no RLS policy and is readable by
    `wingrc_app`, so this works identically before and after the cutover.
    """
    detail: dict = {"status": "ok", "app": settings.app_name, "version": "0.1.0"}

    try:
        db.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 -- surfaced to the caller below
        response.status_code = 503
        return {**detail, "status": "error", "database": f"unreachable: {type(exc).__name__}"}

    expected = _expected_migration_head()
    if expected is not None:
        applied = db.execute(text("SELECT version_num FROM alembic_version")).scalar()
        detail["migration"] = applied
        if applied != expected:
            response.status_code = 503
            return {
                **detail,
                "status": "error",
                "migration_expected": expected,
                "database": "schema does not match this build",
            }

    detail["database"] = "ok"
    return detail


@app.get("/catalog/views")
def catalog_views(_auth: CurrentUser = Depends(get_current_user)) -> list[dict]:
    return [
        {
            "id": v.id,
            "title": v.title,
            "control_ids": list(v.control_ids),
            "entity_type": v.entity_type.value,
            "columns": [d for _, d in v.columns],
        }
        for v in ALL_VIEWS
    ]
