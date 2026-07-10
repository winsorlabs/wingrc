"""Organization endpoints.

POST   /orgs                              Create org
GET    /orgs                              List orgs
GET    /orgs/{org_id}                     Get one org (basic)
GET    /orgs/{org_id}/profile             Full SSP profile (includes logo_url)
PATCH  /orgs/{org_id}/profile             Partial-update profile fields
POST   /orgs/{org_id}/logo               Upload logo image
GET    /orgs/{org_id}/system-description  System description (404 until first PUT)
PUT    /orgs/{org_id}/system-description  Upsert system description
GET    /orgs/{org_id}/onboarding-status  Completion indicators (never gates access)
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import log_event
from ..db import get_session
from ..models import (
    Contact,
    ContactDocumentationRole,
    Organization,
    SystemDescription,
)
from ..storage import StorageClient, get_storage_client

router = APIRouter(prefix="/orgs", tags=["orgs"])

# ---------------------------------------------------------------------------
# Image upload constraints for logo (image-only subset of evidence upload)
# ---------------------------------------------------------------------------

_IMAGE_MIME_TO_EXT: dict[str, str] = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
}
_IMAGE_MAX_BYTES = 10 * 1024 * 1024  # 10 MB

# Profile fields required for "profile complete" in onboarding status
_PROFILE_REQUIRED = [
    "industry",
    "address_line1",
    "city",
    "state_or_province",
    "postal_code",
    "phone_primary",
]

_VALID_SYSTEM_TYPES = frozenset(
    {"major_application", "general_support_system", "minor_application"}
)
_VALID_OP_STATUSES = frozenset(
    {"operational", "under_development", "undergoing_major_modification"}
)


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class OrgIn(BaseModel):
    name: str


class OrgOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    created_at: datetime


class OrgProfileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    created_at: datetime
    updated_at: datetime | None = None
    cage_code: str | None = None
    uei: str | None = None
    year_established: int | None = None
    industry: str | None = None
    address_line1: str | None = None
    address_line2: str | None = None
    city: str | None = None
    state_or_province: str | None = None
    postal_code: str | None = None
    country: str | None = None
    phone_primary: str | None = None
    phone_secondary: str | None = None
    website: str | None = None
    logo_storage_key: str | None = None
    logo_url: str | None = None  # presigned; not on the model, computed per-request


class OrgProfilePatch(BaseModel):
    cage_code: str | None = None
    uei: str | None = None
    year_established: int | None = None
    industry: str | None = None
    address_line1: str | None = None
    address_line2: str | None = None
    city: str | None = None
    state_or_province: str | None = None
    postal_code: str | None = None
    country: str | None = None
    phone_primary: str | None = None
    phone_secondary: str | None = None
    website: str | None = None


class SystemDescriptionIn(BaseModel):
    system_name: str
    system_type: str
    operational_status: str
    system_description: str | None = None
    cui_categories: list[str] = []
    cui_storage_locations: list[dict[str, Any]] = []
    authorization_boundary_description: str | None = None
    external_connections: list[dict[str, Any]] = []
    cui_flow_description: str | None = None

    @field_validator("system_type")
    @classmethod
    def validate_system_type(cls, v: str) -> str:
        if v not in _VALID_SYSTEM_TYPES:
            raise ValueError(f"system_type must be one of: {sorted(_VALID_SYSTEM_TYPES)}")
        return v

    @field_validator("operational_status")
    @classmethod
    def validate_operational_status(cls, v: str) -> str:
        if v not in _VALID_OP_STATUSES:
            raise ValueError(f"operational_status must be one of: {sorted(_VALID_OP_STATUSES)}")
        return v


class SystemDescriptionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    org_id: uuid.UUID
    system_name: str
    system_type: str
    operational_status: str
    system_description: str | None = None
    cui_categories: list[str] = []
    cui_storage_locations: list[dict[str, Any]] = []
    authorization_boundary_description: str | None = None
    external_connections: list[dict[str, Any]] = []
    cui_flow_description: str | None = None
    created_at: datetime
    updated_at: datetime


class ProfileCompletionStatus(BaseModel):
    complete: bool
    missing_fields: list[str]


class SectionCompletionStatus(BaseModel):
    complete: bool


class PersonnelCompletionStatus(BaseModel):
    complete: bool
    contact_count: int
    roles_covered: list[str]


class OnboardingStatus(BaseModel):
    profile: ProfileCompletionStatus
    system_description: SectionCompletionStatus
    personnel: PersonnelCompletionStatus


class LogoUploadOut(BaseModel):
    logo_storage_key: str
    logo_url: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_org(session: Session, org_id: uuid.UUID) -> Organization:
    org = session.get(Organization, org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found")
    return org


def _verify_image_bytes(data: bytes, mime: str) -> bool:
    if mime == "image/png":
        return data[:4] == b"\x89PNG"
    if mime == "image/jpeg":
        return data[:3] == b"\xff\xd8\xff"
    if mime == "image/gif":
        return data[:6] in (b"GIF87a", b"GIF89a")
    if mime == "image/webp":
        return data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    return False


def _build_profile_out(org: Organization, storage: StorageClient | None) -> OrgProfileOut:
    out = OrgProfileOut.model_validate(org)
    if org.logo_storage_key and storage is not None:
        out.logo_url = storage.presigned_url(org.logo_storage_key)
    return out


# ---------------------------------------------------------------------------
# Basic org endpoints
# ---------------------------------------------------------------------------


@router.post("", response_model=OrgOut, status_code=201)
def create_org(body: OrgIn, session: Session = Depends(get_session)) -> OrgOut:
    existing = session.scalars(
        select(Organization).where(Organization.name == body.name)
    ).first()
    if existing is not None:
        raise HTTPException(
            status_code=409, detail=f"Organization {body.name!r} already exists"
        )
    org = Organization(name=body.name)
    session.add(org)
    session.commit()
    session.refresh(org)
    return OrgOut.model_validate(org)


@router.get("", response_model=list[OrgOut])
def list_orgs(session: Session = Depends(get_session)) -> list[OrgOut]:
    orgs = session.scalars(select(Organization).order_by(Organization.name)).all()
    return [OrgOut.model_validate(o) for o in orgs]


@router.get("/{org_id}", response_model=OrgOut)
def get_org(org_id: uuid.UUID, session: Session = Depends(get_session)) -> OrgOut:
    org = _get_org(session, org_id)
    return OrgOut.model_validate(org)


# ---------------------------------------------------------------------------
# Profile endpoints
# ---------------------------------------------------------------------------


@router.get("/{org_id}/profile", response_model=OrgProfileOut)
def get_profile(
    org_id: uuid.UUID,
    session: Session = Depends(get_session),
    storage: StorageClient = Depends(get_storage_client),
) -> OrgProfileOut:
    org = _get_org(session, org_id)
    return _build_profile_out(org, storage)


@router.patch("/{org_id}/profile", response_model=OrgProfileOut)
def patch_profile(
    org_id: uuid.UUID,
    body: OrgProfilePatch,
    session: Session = Depends(get_session),
    storage: StorageClient = Depends(get_storage_client),
) -> OrgProfileOut:
    org = _get_org(session, org_id)

    # Only touch fields explicitly present in the request body (PATCH semantics)
    update_data = body.model_dump(include=body.model_fields_set)
    if not update_data:
        return _build_profile_out(org, storage)

    before: dict[str, Any] = {k: getattr(org, k) for k in update_data}
    for field, value in update_data.items():
        setattr(org, field, value)

    session.flush()
    log_event(
        session,
        org_id=org_id,
        action="org.profile.update",
        entity_type="organization",
        entity_id=org.id,
        before_value=before,
        after_value=update_data,
        context={"via": "api"},
    )
    session.commit()
    session.refresh(org)
    return _build_profile_out(org, storage)


@router.post("/{org_id}/logo", response_model=LogoUploadOut)
async def upload_logo(
    org_id: uuid.UUID,
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
    storage: StorageClient = Depends(get_storage_client),
) -> LogoUploadOut:
    org = _get_org(session, org_id)

    mime = file.content_type or ""
    if mime not in _IMAGE_MIME_TO_EXT:
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported image type {mime!r}. Allowed: {sorted(_IMAGE_MIME_TO_EXT)}",
        )

    data = await file.read()
    if len(data) > _IMAGE_MAX_BYTES:
        raise HTTPException(
            status_code=422,
            detail=f"Logo exceeds {_IMAGE_MAX_BYTES // (1024 * 1024)} MB limit",
        )
    if not _verify_image_bytes(data, mime):
        raise HTTPException(
            status_code=422, detail="File content does not match declared MIME type"
        )

    ext = _IMAGE_MIME_TO_EXT[mime]
    new_key = f"{org_id}/logos/{uuid.uuid4()}{ext}"

    old_key = org.logo_storage_key
    storage.upload_file(new_key, data, mime)

    org.logo_storage_key = new_key
    session.flush()
    log_event(
        session,
        org_id=org_id,
        action="org.logo.upload",
        entity_type="organization",
        entity_id=org.id,
        before_value={"logo_storage_key": old_key},
        after_value={"logo_storage_key": new_key},
        context={"via": "api"},
    )
    session.commit()

    if old_key:
        try:
            storage.delete_file(old_key)
        except Exception:
            pass  # best-effort; old key is orphaned but not critical

    return LogoUploadOut(
        logo_storage_key=new_key,
        logo_url=storage.presigned_url(new_key),
    )


# ---------------------------------------------------------------------------
# System description endpoints
# ---------------------------------------------------------------------------


@router.get("/{org_id}/system-description", response_model=SystemDescriptionOut)
def get_system_description(
    org_id: uuid.UUID, session: Session = Depends(get_session)
) -> SystemDescriptionOut:
    _get_org(session, org_id)
    sd = session.scalars(
        select(SystemDescription).where(SystemDescription.org_id == org_id)
    ).first()
    if sd is None:
        raise HTTPException(status_code=404, detail="System description not yet created")
    return SystemDescriptionOut.model_validate(sd)


@router.put("/{org_id}/system-description", response_model=SystemDescriptionOut)
def upsert_system_description(
    org_id: uuid.UUID,
    body: SystemDescriptionIn,
    session: Session = Depends(get_session),
) -> SystemDescriptionOut:
    _get_org(session, org_id)
    sd = session.scalars(
        select(SystemDescription).where(SystemDescription.org_id == org_id)
    ).first()

    data = body.model_dump()
    if sd is None:
        sd = SystemDescription(org_id=org_id, **data)
        session.add(sd)
        is_new = True
        before: dict[str, Any] | None = None
    else:
        before = {k: getattr(sd, k) for k in data}
        for k, v in data.items():
            setattr(sd, k, v)
        is_new = False

    session.flush()
    log_event(
        session,
        org_id=org_id,
        action="system_description.upsert",
        entity_type="system_description",
        entity_id=sd.id,
        before_value=before,
        after_value=data,
        context={"via": "api", "created": is_new},
    )
    session.commit()
    session.refresh(sd)
    return SystemDescriptionOut.model_validate(sd)


# ---------------------------------------------------------------------------
# Onboarding status (read-only; never gates access)
# ---------------------------------------------------------------------------


@router.get("/{org_id}/onboarding-status", response_model=OnboardingStatus)
def get_onboarding_status(
    org_id: uuid.UUID, session: Session = Depends(get_session)
) -> OnboardingStatus:
    org = _get_org(session, org_id)

    # Profile completeness
    missing = [f for f in _PROFILE_REQUIRED if getattr(org, f) is None]
    profile_status = ProfileCompletionStatus(
        complete=len(missing) == 0, missing_fields=missing
    )

    # System description completeness
    sd = session.scalars(
        select(SystemDescription).where(SystemDescription.org_id == org_id)
    ).first()
    sd_status = SectionCompletionStatus(complete=sd is not None)

    # Personnel completeness
    contact_rows = session.scalars(
        select(Contact).where(Contact.org_id == org_id)
    ).all()
    contact_ids = [c.id for c in contact_rows]

    roles_covered: list[str] = []
    has_any_role = False
    if contact_ids:
        role_rows = session.scalars(
            select(ContactDocumentationRole).where(
                ContactDocumentationRole.contact_id.in_(contact_ids)
            )
        ).all()
        roles_covered = sorted({r.role for r in role_rows})
        has_any_role = len(role_rows) > 0

    personnel_status = PersonnelCompletionStatus(
        complete=has_any_role,
        contact_count=len(contact_rows),
        roles_covered=roles_covered,
    )

    return OnboardingStatus(
        profile=profile_status,
        system_description=sd_status,
        personnel=personnel_status,
    )
