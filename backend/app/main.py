"""WinGRC API.

The endpoints here expose the scope module: read the scope graph, dry-run an
import to get a reconcile diff, and render a CMMC list. Side-effecting apply is
deliberately a separate, explicit step — imports never mutate scope without a
confirmed diff.
"""

from __future__ import annotations

import tempfile
import uuid
from pathlib import Path

from fastapi import Depends, FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from . import repo
from .auth import CurrentUser, get_current_user
from .catalog import ALL_VIEWS, VIEWS_BY_ID
from .config import get_settings
from .db import get_session
from .domain import EntityType
from .importers.workbook import parse_workbook
from .reconcile import reconcile
from .render import render_view
from .routers import assessments, bundle, contacts, evidence, frameworks, orgs
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
app.include_router(auth_router.router)
app.include_router(frameworks.router)
app.include_router(orgs.router)
app.include_router(contacts.router)
app.include_router(assessments.router)
app.include_router(evidence.router)
app.include_router(bundle.router)
app.include_router(users_router.router)


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


@app.get("/orgs/{org_id}/scope")
def get_scope(
    org_id: uuid.UUID,
    entity_type: str | None = Query(default=None),
    session: Session = Depends(get_session),
    _auth: CurrentUser = Depends(get_current_user),
) -> list[dict]:
    et = EntityType(entity_type) if entity_type else None
    entities = repo.list_entities(session, org_id, et)
    return [
        {
            "entity_type": e.entity_type.value,
            "natural_key": e.natural_key,
            "scope_category": e.scope_category.value if e.scope_category else None,
            "status": e.status.value,
            "attributes": e.attributes,
        }
        for e in entities
    ]


@app.post("/orgs/{org_id}/imports/workbook/dry-run")
async def import_dry_run(
    org_id: uuid.UUID,
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
    _auth: CurrentUser = Depends(get_current_user),
) -> dict:
    """Parse an uploaded workbook and return the reconcile diff. No writes."""
    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name
    try:
        incoming = parse_workbook(tmp_path)
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    current = repo.list_entities(session, org_id)
    result = reconcile(current, incoming)
    return {
        "summary": result.summary(),
        "changes": [
            {
                "change_type": c.change_type.value,
                "entity_type": c.entity_type.value,
                "natural_key": c.natural_key,
                "field_diffs": {k: list(v) for k, v in c.field_diffs.items()},
            }
            for c in result.changes
            if c.change_type.value != "unchanged"
        ],
    }


@app.post("/orgs/{org_id}/exports/{view_id}")
def export_view(
    org_id: uuid.UUID,
    view_id: str,
    session: Session = Depends(get_session),
    _auth: CurrentUser = Depends(get_current_user),
) -> FileResponse:
    view = VIEWS_BY_ID.get(view_id)
    if view is None:
        raise HTTPException(status_code=404, detail=f"Unknown view {view_id!r}")
    entities = repo.list_entities(session, org_id, view.entity_type)
    out = Path(tempfile.gettempdir()) / f"{view_id}.xlsx"
    render_view(view, entities, out)
    return FileResponse(out, filename=f"{view_id}.xlsx")
