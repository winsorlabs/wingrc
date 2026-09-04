"""WinGRC API.

Scope-graph endpoints (read, manual CRUD, workbook import/export) live in
routers/scope.py (G.5). This file wires up the FastAPI app, middleware, and
router registration, plus the two endpoints with no dedicated router of
their own: /health and /catalog/views (read-only catalog metadata, not
scoped to any one org).
"""

from __future__ import annotations

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from .audit import set_current_ip
from .auth import CurrentUser, get_client_ip, get_current_user
from .catalog import ALL_VIEWS
from .config import get_settings
from .routers import (
    assessments,
    audit_log,
    bundle,
    contacts,
    dashboard,
    evidence,
    frameworks,
    orgs,
    scope,
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


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "app": settings.app_name, "version": "0.1.0"}


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
