"""Evidence upload, reference-add, list, download, delete, and manifest endpoints.

Endpoints:
  POST   /orgs/{org_id}/assessments/{assessment_id}/control-states/{cs_id}/evidence
             Upload a file artifact to MinIO.
  POST   /orgs/{org_id}/assessments/{assessment_id}/control-states/{cs_id}/evidence/references
             Add one or more location references (URL / UNC path / drive path).
  GET    /orgs/{org_id}/assessments/{assessment_id}/control-states/{cs_id}/evidence
             List all evidence (files + references) linked to a control state.
  GET    /orgs/{org_id}/evidence/{evidence_id}/download
             Redirect to a presigned download URL (file-kind only).
  DELETE /orgs/{org_id}/assessments/{assessment_id}/control-states/{cs_id}/evidence/{evidence_id}
             Unlink evidence from this control state; delete artifact if no links remain.
  GET    /orgs/{org_id}/assessments/{assessment_id}/evidence-manifest
             Point-in-time manifest of every objective's evidence for bundle export.

Invariants:
  - None of these endpoints modify control_state.status.
  - Any control_state can have evidence regardless of responsibility value
    (customer_owns does NOT block attachment).
  - Removing evidence never changes status either.
"""
from __future__ import annotations

import hashlib
import mimetypes
import os
import re
import uuid
from collections import OrderedDict
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import log_event
from ..auth import require_org_access
from ..db import get_session
from ..models import (
    Assessment,
    AssessmentObjective,
    Control,
    ControlState,
    Evidence,
    EvidenceStateLink,
    EvidenceTask,
    EvidenceTaskStateLink,
)
from ..storage import StorageClient, download_filename, get_storage_client

router = APIRouter(
    prefix="/orgs/{org_id}",
    tags=["evidence"],
    dependencies=[Depends(require_org_access())],
)

# ---------------------------------------------------------------------------
# Upload validation constants
# ---------------------------------------------------------------------------

_ALLOWED_MIME_TYPES = frozenset({
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/webp",
    "application/pdf",
    "application/msword",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "text/plain",
    "text/csv",
    "application/zip",
})

_ALLOWED_EXTENSIONS = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".webp",
    ".pdf",
    ".doc", ".docx",
    ".xls", ".xlsx",
    ".txt", ".csv",
    ".zip",
})

_MAX_FILE_BYTES = 50 * 1024 * 1024  # 50 MB

# Reference location: must start with a network protocol, UNC path,
# Windows drive letter, or Unix absolute path.
_LOCATION_RE = re.compile(
    r"^("
    r"https?://|ftp://|sftp://|smb://|"
    r"\\\\|"               # UNC: \\server\share
    r"[A-Za-z]:[/\\]|"    # Windows drive: C:\ or C:/
    r"/"                   # Unix absolute
    r")"
)

_MAX_LOCATION_LEN = 2000
_MAX_REFS_PER_CALL = 20

_REFERENCE_NOTE = "Location only — not a stored copy"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe_filename(raw: str) -> str:
    return raw.replace("\\", "/").rsplit("/", 1)[-1] or "upload"


def _verify_magic_bytes(data: bytes, mime: str) -> bool:
    """Return False when bytes contradict the declared MIME for detectable types."""
    if mime == "image/png":
        return data[:4] == b"\x89PNG"
    if mime == "image/jpeg":
        return data[:3] == b"\xff\xd8\xff"
    if mime == "image/gif":
        return data[:6] in (b"GIF87a", b"GIF89a")
    if mime == "image/webp":
        return data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    if mime == "application/pdf":
        return data[:4] == b"%PDF"
    # ZIP-based office formats and plain zip: magic is PK\x03\x04 but we
    # cannot distinguish docx from xlsx from zip by header alone — trust
    # declared MIME + extension for these.
    return True


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


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class EvidenceOut(BaseModel):
    id: uuid.UUID
    kind: str                      # 'file' | 'reference'
    title: str
    artifact_type: str
    mime_type: str | None          # file only
    file_size_bytes: int | None    # file only
    download_url: str | None       # file only (presigned URL); None for references
    reference_location: str | None  # reference only
    note: str | None               # set to _REFERENCE_NOTE for references
    collected_at: datetime


class ReferenceIn(BaseModel):
    title: str
    location: str
    artifact_type: str

    @field_validator("title")
    @classmethod
    def title_nonempty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("title must not be empty")
        return v

    @field_validator("location")
    @classmethod
    def location_valid(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("location must not be empty")
        if len(v) > _MAX_LOCATION_LEN:
            raise ValueError(f"location exceeds {_MAX_LOCATION_LEN} characters")
        if not _LOCATION_RE.match(v):
            raise ValueError(
                "location must be a URL (https://...) or absolute path "
                r"(\\server\share, C:\folder, /mnt/share)"
            )
        return v

    @field_validator("artifact_type")
    @classmethod
    def artifact_type_valid(cls, v: str) -> str:
        valid = {"screenshot", "export", "document", "link", "policy"}
        if v not in valid:
            raise ValueError(f"artifact_type must be one of: {sorted(valid)}")
        return v


# ---------------------------------------------------------------------------
# Manifest schemas (designed for bundle-export consumption)
# ---------------------------------------------------------------------------


class ManifestEvidenceItem(BaseModel):
    evidence_id: uuid.UUID
    kind: str
    title: str
    artifact_type: str
    # file-only
    storage_key: str | None
    mime_type: str | None
    file_size_bytes: int | None
    # reference-only
    location: str | None
    note: str | None
    collected_at: datetime


class ManifestObjective(BaseModel):
    control_id: str        # human-readable: "AU.L2-3.3.1"
    family: str            # "AU"
    objective_key: str     # "a"
    control_state_id: uuid.UUID
    status: str
    responsibility: str
    evidence: list[ManifestEvidenceItem]


class EvidenceManifest(BaseModel):
    assessment_id: uuid.UUID
    org_id: uuid.UUID
    generated_at: datetime
    objectives: list[ManifestObjective]


# ---------------------------------------------------------------------------
# File upload
# ---------------------------------------------------------------------------


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

    if artifact_type not in {"screenshot", "export", "document", "link", "policy"}:
        raise HTTPException(
            status_code=422,
            detail="artifact_type must be one of: document, export, link, policy, screenshot",
        )

    data = await file.read()
    if len(data) > _MAX_FILE_BYTES:
        raise HTTPException(status_code=413, detail="File exceeds 50 MB limit")

    raw_name = _safe_filename(file.filename or "upload")
    ext = os.path.splitext(raw_name)[1].lower()

    if ext not in _ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=415,
            detail=f"File extension {ext!r} not permitted. "
                   f"Allowed: {sorted(_ALLOWED_EXTENSIONS)}",
        )

    mime = (
        file.content_type
        or mimetypes.guess_type(raw_name)[0]
        or "application/octet-stream"
    )

    if mime not in _ALLOWED_MIME_TYPES:
        raise HTTPException(
            status_code=415,
            detail=f"Content-Type {mime!r} not permitted. "
                   f"Allowed: {sorted(_ALLOWED_MIME_TYPES)}",
        )

    if not _verify_magic_bytes(data, mime):
        raise HTTPException(
            status_code=415,
            detail=f"File bytes do not match declared Content-Type {mime!r}",
        )

    display_title = title or raw_name
    evidence_id = uuid.uuid4()
    # Store under UUID to prevent path traversal; original name kept in title.
    storage_key = f"{org_id}/evidence/{evidence_id}/{evidence_id}{ext}"

    # SHA-256 per DoD-CIO-00008 CMMC artifact hashing requirement.  Algorithm is
    # CMVP-approved; the FIPS-validated crypto boundary (UBI 9 / BoringCrypto,
    # see docs/fips.md) is roadmap item #2 — FIPS deployment profile — and is
    # not yet deployed.  Do not read this as FIPS-compliant today.
    file_sha256 = hashlib.sha256(data).hexdigest()

    storage.upload_file(storage_key, data, mime)

    ev = Evidence(
        id=evidence_id,
        org_id=org_id,
        kind="file",
        title=display_title,
        artifact_type=artifact_type,
        storage_key=storage_key,
        mime_type=mime,
        file_size_bytes=len(data),
        sha256_hash=file_sha256,
        collected_at=datetime.now(UTC),
    )
    session.add(ev)
    session.flush()

    session.add(EvidenceStateLink(evidence_id=ev.id, control_state_id=cs.id))
    session.commit()

    return EvidenceOut(
        id=ev.id,
        kind="file",
        title=ev.title,
        artifact_type=ev.artifact_type,
        mime_type=ev.mime_type,
        file_size_bytes=ev.file_size_bytes,
        download_url=storage.presigned_url(
            storage_key, download_filename=download_filename(display_title, ext)
        ),
        reference_location=None,
        note=None,
        collected_at=ev.collected_at,
    )


# ---------------------------------------------------------------------------
# Reference add (batch)
# ---------------------------------------------------------------------------


@router.post(
    "/assessments/{assessment_id}/control-states/{cs_id}/evidence/references",
    response_model=list[EvidenceOut],
    status_code=201,
)
def add_references(
    org_id: uuid.UUID,
    assessment_id: uuid.UUID,
    cs_id: uuid.UUID,
    refs: list[ReferenceIn],
    session: Session = Depends(get_session),
) -> list[EvidenceOut]:
    _check_assessment(session, org_id, assessment_id)
    cs = _check_cs(session, assessment_id, cs_id)

    if not refs:
        raise HTTPException(status_code=422, detail="At least one reference required")
    if len(refs) > _MAX_REFS_PER_CALL:
        raise HTTPException(
            status_code=422,
            detail=f"At most {_MAX_REFS_PER_CALL} references per call",
        )

    now = datetime.now(UTC)
    result: list[EvidenceOut] = []

    for ref in refs:
        ev = Evidence(
            org_id=org_id,
            kind="reference",
            title=ref.title,
            artifact_type=ref.artifact_type,
            reference_location=ref.location,
            collected_at=now,
        )
        session.add(ev)
        session.flush()
        session.add(EvidenceStateLink(evidence_id=ev.id, control_state_id=cs.id))
        result.append(EvidenceOut(
            id=ev.id,
            kind="reference",
            title=ev.title,
            artifact_type=ev.artifact_type,
            mime_type=None,
            file_size_bytes=None,
            download_url=None,
            reference_location=ref.location,
            note=_REFERENCE_NOTE,
            collected_at=ev.collected_at,
        ))

    session.commit()
    return result


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


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
            kind=ev.kind,
            title=ev.title,
            artifact_type=ev.artifact_type,
            mime_type=ev.mime_type,
            file_size_bytes=ev.file_size_bytes,
            download_url=(
                storage.presigned_url(
                    ev.storage_key,
                    download_filename=download_filename(
                        ev.title, os.path.splitext(ev.storage_key)[1]
                    ),
                )
                if ev.kind == "file" and ev.storage_key
                else None
            ),
            reference_location=ev.reference_location,
            note=_REFERENCE_NOTE if ev.kind == "reference" else None,
            collected_at=ev.collected_at,
        )
        for ev in ev_list
    ]


# ---------------------------------------------------------------------------
# Download (file-kind only)
# ---------------------------------------------------------------------------


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
    if ev.kind == "reference" or not ev.storage_key:
        raise HTTPException(
            status_code=404,
            detail="No stored file for this evidence item — it is a location reference",
        )

    url = storage.presigned_url(
        ev.storage_key,
        download_filename=download_filename(ev.title, os.path.splitext(ev.storage_key)[1]),
    )
    if not url:
        raise HTTPException(status_code=404, detail="Storage not configured")

    return RedirectResponse(url=url, status_code=302)


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------


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

    remaining = session.scalars(
        select(EvidenceStateLink).where(EvidenceStateLink.evidence_id == evidence_id)
    ).first()
    if remaining is None:
        if ev.kind == "file" and ev.storage_key:
            storage.delete_file(ev.storage_key)
        session.delete(ev)

    session.commit()


# ---------------------------------------------------------------------------
# Evidence manifest
# ---------------------------------------------------------------------------


@router.get(
    "/assessments/{assessment_id}/evidence-manifest",
    response_model=EvidenceManifest,
)
def get_evidence_manifest(
    org_id: uuid.UUID,
    assessment_id: uuid.UUID,
    session: Session = Depends(get_session),
) -> EvidenceManifest:
    assessment = session.get(Assessment, assessment_id)
    if assessment is None or assessment.org_id != org_id:
        raise HTTPException(status_code=404, detail="Assessment not found")

    rows = session.execute(
        select(
            Control.control_id,
            Control.family,
            Control.sequence_order,
            AssessmentObjective.objective_key,
            ControlState.id.label("cs_id"),
            ControlState.status,
            ControlState.responsibility,
            Evidence.id.label("ev_id"),
            Evidence.kind,
            Evidence.title.label("ev_title"),
            Evidence.artifact_type,
            Evidence.storage_key,
            Evidence.mime_type,
            Evidence.file_size_bytes,
            Evidence.reference_location,
            Evidence.collected_at,
        )
        .select_from(ControlState)
        .join(AssessmentObjective, AssessmentObjective.id == ControlState.objective_id)
        .join(Control, Control.id == AssessmentObjective.control_id)
        .outerjoin(EvidenceStateLink, EvidenceStateLink.control_state_id == ControlState.id)
        .outerjoin(Evidence, Evidence.id == EvidenceStateLink.evidence_id)
        .where(ControlState.assessment_id == assessment_id)
        .order_by(
            Control.family,
            Control.sequence_order,
            AssessmentObjective.objective_key,
            Evidence.collected_at,
        )
    ).all()

    # Aggregate rows: one dict per objective, evidence list built up per row.
    # Use OrderedDict so family/sequence ordering from the query is preserved.
    objectives_map: OrderedDict[uuid.UUID, dict] = OrderedDict()

    for row in rows:
        cs_id = row.cs_id
        if cs_id not in objectives_map:
            objectives_map[cs_id] = {
                "control_id": row.control_id,
                "family": row.family,
                "objective_key": row.objective_key,
                "control_state_id": cs_id,
                "status": row.status,
                "responsibility": row.responsibility,
                "evidence": [],
            }

        if row.ev_id is None:
            continue

        if row.kind == "file":
            item = ManifestEvidenceItem(
                evidence_id=row.ev_id,
                kind="file",
                title=row.ev_title,
                artifact_type=row.artifact_type,
                storage_key=row.storage_key,
                mime_type=row.mime_type,
                file_size_bytes=row.file_size_bytes,
                location=None,
                note=None,
                collected_at=row.collected_at,
            )
        else:
            item = ManifestEvidenceItem(
                evidence_id=row.ev_id,
                kind="reference",
                title=row.ev_title,
                artifact_type=row.artifact_type,
                storage_key=None,
                mime_type=None,
                file_size_bytes=None,
                location=row.reference_location,
                note=_REFERENCE_NOTE,
                collected_at=row.collected_at,
            )

        objectives_map[cs_id]["evidence"].append(item)

    return EvidenceManifest(
        assessment_id=assessment_id,
        org_id=org_id,
        generated_at=datetime.now(UTC),
        objectives=[ManifestObjective(**obj) for obj in objectives_map.values()],
    )


# ---------------------------------------------------------------------------
# Task-based evidence collection (fan-out to all linked control states)
# ---------------------------------------------------------------------------


def _get_task_for_collect(
    session: Session,
    org_id: uuid.UUID,
    assessment_id: uuid.UUID,
    task_id: uuid.UUID,
) -> EvidenceTask:
    """Load and validate an evidence task for a collect operation."""
    _check_assessment(session, org_id, assessment_id)
    task = session.get(EvidenceTask, task_id)
    if task is None or task.assessment_id != assessment_id or task.org_id != org_id:
        raise HTTPException(status_code=404, detail="Evidence task not found")
    if task.is_archived:
        raise HTTPException(status_code=422, detail="Cannot collect evidence for an archived task")
    return task


def _fan_out_evidence(
    session: Session,
    task: EvidenceTask,
    ev: Evidence,
) -> int:
    """Create EvidenceStateLink for every control_state the task covers.

    Returns the number of links created. Does NOT touch ControlState.status —
    evidence attachment is a candidate; a human confirms Met separately.
    """
    task_links = session.scalars(
        select(EvidenceTaskStateLink).where(EvidenceTaskStateLink.task_id == task.id)
    ).all()

    created = 0
    for lnk in task_links:
        # UNIQUE constraint guards duplicates; skip if already linked
        existing = session.scalars(
            select(EvidenceStateLink).where(
                EvidenceStateLink.evidence_id == ev.id,
                EvidenceStateLink.control_state_id == lnk.control_state_id,
            )
        ).first()
        if existing is None:
            session.add(EvidenceStateLink(evidence_id=ev.id, control_state_id=lnk.control_state_id))
            created += 1

    # Mark task collected — only change to task state; ControlState is untouched
    task.status = "collected"
    task.completed_evidence_id = ev.id
    return created


@router.post(
    "/assessments/{assessment_id}/evidence-tasks/{task_id}/collect",
    response_model=EvidenceOut,
    status_code=201,
)
async def collect_task_evidence_file(
    org_id: uuid.UUID,
    assessment_id: uuid.UUID,
    task_id: uuid.UUID,
    file: UploadFile = File(...),
    artifact_type: str = Form("document"),
    title: str | None = Form(None),
    session: Session = Depends(get_session),
    storage: StorageClient = Depends(get_storage_client),
) -> EvidenceOut:
    """Upload a file to satisfy an evidence task.

    Creates one Evidence row, links it to every control_state the task covers
    (fan-out via EvidenceStateLink), and marks the task 'collected'.

    Invariant: ControlState.status is never modified here — evidence is a
    candidate; an engineer confirms Met separately in the control drawer.
    """
    task = _get_task_for_collect(session, org_id, assessment_id, task_id)

    if artifact_type not in {"screenshot", "export", "document", "link", "policy"}:
        raise HTTPException(
            status_code=422,
            detail="artifact_type must be one of: document, export, link, policy, screenshot",
        )

    data = await file.read()
    if len(data) > _MAX_FILE_BYTES:
        raise HTTPException(status_code=413, detail="File exceeds 50 MB limit")

    raw_name = _safe_filename(file.filename or "upload")
    ext = os.path.splitext(raw_name)[1].lower()

    if ext not in _ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=415,
            detail=f"File extension {ext!r} not permitted. Allowed: {sorted(_ALLOWED_EXTENSIONS)}",
        )

    mime = (
        file.content_type
        or mimetypes.guess_type(raw_name)[0]
        or "application/octet-stream"
    )

    if mime not in _ALLOWED_MIME_TYPES:
        raise HTTPException(
            status_code=415,
            detail=f"Content-Type {mime!r} not permitted. Allowed: {sorted(_ALLOWED_MIME_TYPES)}",
        )

    if not _verify_magic_bytes(data, mime):
        raise HTTPException(
            status_code=415,
            detail=f"File bytes do not match declared Content-Type {mime!r}",
        )

    display_title = title or raw_name
    evidence_id = uuid.uuid4()
    storage_key = f"{org_id}/evidence/{evidence_id}/{evidence_id}{ext}"
    file_sha256 = hashlib.sha256(data).hexdigest()
    storage.upload_file(storage_key, data, mime)

    ev = Evidence(
        id=evidence_id,
        org_id=org_id,
        kind="file",
        title=display_title,
        artifact_type=artifact_type,
        storage_key=storage_key,
        mime_type=mime,
        file_size_bytes=len(data),
        sha256_hash=file_sha256,
        collected_at=datetime.now(UTC),
    )
    session.add(ev)
    session.flush()

    links_created = _fan_out_evidence(session, task, ev)

    log_event(
        session,
        org_id=org_id,
        action="evidence_task.collect",
        entity_type="evidence_task",
        entity_id=task.id,
        after_value={
            "evidence_id": str(ev.id),
            "kind": "file",
            "title": display_title,
            "links_created": links_created,
        },
        context={"via": "api"},
    )
    session.commit()

    return EvidenceOut(
        id=ev.id,
        kind="file",
        title=ev.title,
        artifact_type=ev.artifact_type,
        mime_type=ev.mime_type,
        file_size_bytes=ev.file_size_bytes,
        download_url=storage.presigned_url(
            storage_key, download_filename=download_filename(display_title, ext)
        ),
        reference_location=None,
        note=None,
        collected_at=ev.collected_at,
    )


@router.post(
    "/assessments/{assessment_id}/evidence-tasks/{task_id}/collect/reference",
    response_model=EvidenceOut,
    status_code=201,
)
def collect_task_evidence_reference(
    org_id: uuid.UUID,
    assessment_id: uuid.UUID,
    task_id: uuid.UUID,
    ref: ReferenceIn,
    session: Session = Depends(get_session),
) -> EvidenceOut:
    """Add a location reference to satisfy an evidence task.

    Creates one Evidence row (kind='reference'), links it to every control_state
    the task covers, and marks the task 'collected'.

    Invariant: ControlState.status is never modified here.
    """
    task = _get_task_for_collect(session, org_id, assessment_id, task_id)

    now = datetime.now(UTC)
    ev = Evidence(
        org_id=org_id,
        kind="reference",
        title=ref.title,
        artifact_type=ref.artifact_type,
        reference_location=ref.location,
        collected_at=now,
    )
    session.add(ev)
    session.flush()

    links_created = _fan_out_evidence(session, task, ev)

    log_event(
        session,
        org_id=org_id,
        action="evidence_task.collect",
        entity_type="evidence_task",
        entity_id=task.id,
        after_value={
            "evidence_id": str(ev.id),
            "kind": "reference",
            "title": ref.title,
            "links_created": links_created,
        },
        context={"via": "api"},
    )
    session.commit()

    return EvidenceOut(
        id=ev.id,
        kind="reference",
        title=ev.title,
        artifact_type=ev.artifact_type,
        mime_type=None,
        file_size_bytes=None,
        download_url=None,
        reference_location=ref.location,
        note=_REFERENCE_NOTE,
        collected_at=ev.collected_at,
    )
