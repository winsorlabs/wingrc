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
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from . import repo
from .catalog import ALL_VIEWS, VIEWS_BY_ID
from .config import get_settings
from .db import get_session
from .domain import EntityType
from .importers.workbook import parse_workbook
from .reconcile import reconcile
from .render import render_view
from .routers import assessments

settings = get_settings()
app = FastAPI(title=settings.app_name, version="0.1.0")
app.include_router(assessments.router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "app": settings.app_name, "version": "0.1.0"}


@app.get("/catalog/views")
def catalog_views() -> list[dict]:
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
) -> FileResponse:
    view = VIEWS_BY_ID.get(view_id)
    if view is None:
        raise HTTPException(status_code=404, detail=f"Unknown view {view_id!r}")
    entities = repo.list_entities(session, org_id, view.entity_type)
    out = Path(tempfile.gettempdir()) / f"{view_id}.xlsx"
    render_view(view, entities, out)
    return FileResponse(out, filename=f"{view_id}.xlsx")
