"""Bundle export endpoint — downloadable assessor package."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from ..audit import log_event
from ..auth import require_org_access, require_write
from ..bundle_service import render_bundle, snapshot_bundle
from ..db import get_session
from ..models import Assessment, Organization
from ..storage import StorageClient, get_storage_client

router = APIRouter(
    prefix="/orgs/{org_id}",
    tags=["bundle"],
    dependencies=[Depends(require_org_access()), Depends(require_write())],
)


@router.get("/assessments/{assessment_id}/bundle")
def export_bundle(
    org_id: uuid.UUID,
    assessment_id: uuid.UUID,
    list_id: list[uuid.UUID] = Query(default=[]),
    session: Session = Depends(get_session),
    storage: StorageClient = Depends(get_storage_client),
) -> Response:
    """Generate and download a point-in-time assessor bundle (ZIP).

    Recomputes the SPRS score, assembles all SSP content, embeds evidence file
    bytes, and returns a structured ZIP.  An audit record is written on each
    export.

    `list_id` (repeatable) names the library lists to embed (roadmap L.3).
    None by default: the bundle carries the lists the operator chose, each
    as its current version by id, and lists/index.html says so. A list
    this org does not have is a 404, never silently dropped.
    """
    org = session.get(Organization, org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found")

    assessment = session.get(Assessment, assessment_id)
    if assessment is None or assessment.org_id != org_id:
        raise HTTPException(status_code=404, detail="Assessment not found")

    try:
        snapshot = snapshot_bundle(session, storage, org_id, assessment_id, list_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="List not found") from None
    zip_bytes, filename, artifact_log_filename, artifact_log_hash = render_bundle(snapshot)

    log_event(
        session,
        org_id=org_id,
        action="bundle.export",
        entity_type="assessment",
        entity_id=assessment_id,
        before_value=None,
        after_value={
            "sprs_score": snapshot.sprs_score,
            "generated_at": snapshot.generated_at.isoformat(),
            "hashed_data_list": artifact_log_filename,
            "hash_value": artifact_log_hash,
            "list_versions": [
                {"list_id": s.list_id, "version_id": s.version_id,
                 "version_number": s.version_number}
                for s in snapshot.lists
            ],
        },
        context={"filename": filename},
    )
    session.commit()

    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
