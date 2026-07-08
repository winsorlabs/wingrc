"""Evidence upload, list, download, and delete endpoints.

Endpoints:
  POST   /orgs/{org_id}/assessments/{assessment_id}/control-states/{cs_id}/evidence
  GET    /orgs/{org_id}/assessments/{assessment_id}/control-states/{cs_id}/evidence
  GET    /orgs/{org_id}/evidence/{evidence_id}/download
  DELETE /orgs/{org_id}/assessments/{assessment_id}/control-states/{cs_id}/evidence/{evidence_id}

Invariant: none of these endpoints modify control_state.status.  Evidence is
candidate material.  The engineer marks status explicitly after reviewing it.
"""
from __future__ import annotations

import mimetypes
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_session
from ..models import Assessment, ControlState, Evidence, EvidenceStateLink
from ..storage import StorageClient, get_storage_client

router = APIRouter(prefix="/orgs/{org_id}", tags=["evidence"])

_VALID_ARTIFACT_TYPES = frozenset({"screenshot", "export", "document", "link", "policy"})
_MAX_FILE_BYTES = 50 * 1024 * 1024  # 50 MB


class EvidenceOut(BaseModel):
    id: uuid.UUID
    title: str
    artifact_type: str
    mime_type: str | None
    file_size_bytes: int | None
    collected_at: datetime
    download_url: str


def _check_assessment(
    session: Session, org_id: uuid.UUID, assessment_id: uuid.UUID
) -> Assessment:
    a = session.get(Assessment, assessment_id)
    if a is None or a.org_id != org_id:
        raise HTTPException(status_code=404, detail="Assessment not found")
    return a


def _check_cs(
    session: Session, assessment_id: uuid.UUID, cs_id: uuid.UUID
) -> ControlState:
    cs = session.get(ControlState, cs_id)
    if cs is None or cs.assessment_id != assessment_id:
        raise HTTPException(status_code=404, detail="Control state not found")
    return cs


def _safe_filename(raw: str) -> str:
    return raw.replace("\\", "/").rsplit("/", 1)[-1] or "upload"


@router.post(
    "/assessments/{assessment_id}/control-states/{cs_id}/evidence",
    response_model=EvidenceOut,
    status_code=201,
)
async def upload_evidence(
    org_id: uuid.UUID,
    assessment_id: uuid.UUID,
    cs_id: uuid.UUID,
    file: UploadFile = File(...),
    artifact_type: str = Form("document"),
    title: str | None = Form(None),
    session: Session = Depends(get_session),
    storage: StorageClient = Depends(get_storage_client),
) -> EvidenceOut:
    _check_assessment(session, org_id, assessment_id)
    cs = _check_cs(session, assessment_id, cs_id)

    if artifact_type not in _VALID_ARTIFACT_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"artifact_type must be one of: {sorted(_VALID_ARTIFACT_TYPES)}",
        )

    data = await file.read()
    if len(data) > _MAX_FILE_BYTES:
        raise HTTPException(status_code=413, detail="File exceeds 50 MB limit")

    filename = _safe_filename(file.filename or "upload")
    mime = (
        file.content_type
        or mimetypes.guess_type(filename)[0]
        or "application/octet-stream"
    )
    display_title = title or filename

    evidence_id = uuid.uuid4()
    storage_key = f"{org_id}/evidence/{evidence_id}/{filename}"

    storage.upload_file(storage_key, data, mime)

    ev = Evidence(
        id=evidence_id,
        org_id=org_id,
        title=display_title,
        artifact_type=artifact_type,
        storage_key=storage_key,
        mime_type=mime,
        file_size_bytes=len(data),
        collected_at=datetime.now(UTC),
    )
    session.add(ev)
    session.flush()

    session.add(EvidenceStateLink(evidence_id=ev.id, control_state_id=cs.id))
    session.commit()

    return EvidenceOut(
        id=ev.id,
        title=ev.title,
        artifact_type=ev.artifact_type,
        mime_type=ev.mime_type,
        file_size_bytes=ev.file_size_bytes,
        collected_at=ev.collected_at,
        download_url=storage.presigned_url(storage_key),
    )


@router.get(
    "/assessments/{assessment_id}/control-states/{cs_id}/evidence",
    response_model=list[EvidenceOut],
)
def list_evidence(
    org_id: uuid.UUID,
    assessment_id: uuid.UUID,
    cs_id: uuid.UUID,
    session: Session = Depends(get_session),
    storage: StorageClient = Depends(get_storage_client),
) -> list[EvidenceOut]:
    _check_assessment(session, org_id, assessment_id)
    cs = _check_cs(session, assessment_id, cs_id)

    ev_list = session.scalars(
        select(Evidence)
        .join(EvidenceStateLink, EvidenceStateLink.evidence_id == Evidence.id)
        .where(EvidenceStateLink.control_state_id == cs.id)
        .order_by(Evidence.collected_at)
    ).all()

    return [
        EvidenceOut(
            id=ev.id,
            title=ev.title,
            artifact_type=ev.artifact_type,
            mime_type=ev.mime_type,
            file_size_bytes=ev.file_size_bytes,
            collected_at=ev.collected_at,
            download_url=storage.presigned_url(ev.storage_key) if ev.storage_key else "",
        )
        for ev in ev_list
    ]


@router.get("/evidence/{evidence_id}/download")
def download_evidence(
    org_id: uuid.UUID,
    evidence_id: uuid.UUID,
    session: Session = Depends(get_session),
    storage: StorageClient = Depends(get_storage_client),
) -> RedirectResponse:
    ev = session.get(Evidence, evidence_id)
    if ev is None or ev.org_id != org_id:
        raise HTTPException(status_code=404, detail="Evidence not found")
    if not ev.storage_key:
        raise HTTPException(status_code=404, detail="No file stored for this evidence item")

    url = storage.presigned_url(ev.storage_key)
    if not url:
        raise HTTPException(status_code=404, detail="Storage not configured")

    return RedirectResponse(url=url, status_code=302)


@router.delete(
    "/assessments/{assessment_id}/control-states/{cs_id}/evidence/{evidence_id}",
    status_code=204,
)
def delete_evidence(
    org_id: uuid.UUID,
    assessment_id: uuid.UUID,
    cs_id: uuid.UUID,
    evidence_id: uuid.UUID,
    session: Session = Depends(get_session),
    storage: StorageClient = Depends(get_storage_client),
) -> None:
    _check_assessment(session, org_id, assessment_id)
    _check_cs(session, assessment_id, cs_id)

    ev = session.get(Evidence, evidence_id)
    if ev is None or ev.org_id != org_id:
        raise HTTPException(status_code=404, detail="Evidence not found")

    link = session.scalars(
        select(EvidenceStateLink).where(
            EvidenceStateLink.evidence_id == evidence_id,
            EvidenceStateLink.control_state_id == cs_id,
        )
    ).first()
    if link is None:
        raise HTTPException(status_code=404, detail="Evidence not linked to this control state")

    session.delete(link)
    session.flush()

    # Clean up the artifact itself if no remaining links
    remaining = session.scalars(
        select(EvidenceStateLink).where(EvidenceStateLink.evidence_id == evidence_id)
    ).first()
    if remaining is None:
        if ev.storage_key:
            storage.delete_file(ev.storage_key)
        session.delete(ev)

    session.commit()
