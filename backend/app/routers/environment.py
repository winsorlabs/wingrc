"""The environment dashboard: what is in this org's boundary, and what is
waiting on a person (roadmap Q.0).

GET /orgs/{org_id}/environment

Org-scoped, deliberately, and a second endpoint rather than more fields on
the assessment dashboard (routers/dashboard.py):

- Which devices and people are in the boundary has nothing to do with
  which assessment is open, and a new tenant with no assessment at all
  still has an environment worth showing. Threading an assessment id
  through to reach this data would misstate what it depends on.
- The assessment dashboard's one-payload shape is a decision its own
  docstring defends. Growing it by lists it does not scope would undo
  that decision by accretion.
- One screen already makes more than one call: recent activity reads
  GET /orgs/{org_id}/audit-log under its own gate.

The engineer's view (frontend OrgDashboard, `environment`) calls this, and
the assessment dashboard as well when an assessment exists, for the SPRS
score, the family radar and open evidence tasks, which are assessment data.

**Every list is capped and every total is its own COUNT(*)** under the
same `scope_filter` clauses as the rows, so a tenant with more entities
than the cap still reads its real total (test_environment_dashboard.py
holds this with a cap below the population).

**"In boundary" means `status = 'active'` and `in_boundary` true.**
`status` alone is not the question: a rejected entity stays
`status=active, in_boundary=False` (domain.EntityStatus), so a list of
"active" devices would show what a person excluded as if it had been
accepted. Pending approval is its own list, and active-but-out-of-boundary
entities are counted here and listed by GET /orgs/{org_id}/scope with
`in_boundary=false`, so a rejected entity stays reachable.

A GET under the router's ordinary gates, so every role with access to the
org reads it, c3pao_assessor included: this is the boundary inventory and
the org profile, both evidence an assessor examines.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..auth import require_org_access, require_write
from ..db import get_session
from ..domain import EntityStatus, EntityType
from ..models import Assessment, Framework, Organization, ScopeEntity
from .dashboard import DocumentReviewWidget, _document_reviews
from .scope import scope_filter

router = APIRouter(
    prefix="/orgs/{org_id}",
    tags=["dashboard"],
    dependencies=[Depends(require_org_access()), Depends(require_write())],
)

ENVIRONMENT_LIST_CAP = 50

# CAGE code and UEI are mandatory SSP header fields for a DoD submission
# (migration 0011), so the panel says when they are missing rather than
# leaving a blank that reads as nothing to do.
SSP_REQUIRED_FIELDS = ("cage_code", "uei")


class ScopeListRow(BaseModel):
    id: uuid.UUID
    entity_type: str
    natural_key: str
    display_name: str
    sub_label: str | None
    scope_category: str | None
    source: str


class ScopeListWidget(BaseModel):
    """A capped list and the true size of what it was cut from.

    `total` is COUNT(*) under the same filter as `rows`, never len(rows):
    for a tenant with more than `cap` matching entities the two differ, and
    the total is the one a reader takes away.
    """

    entity_type: str | None
    status: str
    in_boundary: bool | None
    total: int
    cap: int
    rows: list[ScopeListRow]


class OrgInfo(BaseModel):
    name: str
    cage_code: str | None
    uei: str | None
    address_line1: str | None
    address_line2: str | None
    city: str | None
    state_or_province: str | None
    postal_code: str | None
    country: str | None
    phone_primary: str | None
    website: str | None
    missing_ssp_fields: list[str]


class AssessmentRef(BaseModel):
    """What the Org Info panel shows as the assessment's level. The level
    is a property of the framework, not a column on the assessment, so it
    is the framework's name and version beside the assessment's type."""

    id: uuid.UUID
    name: str
    assessment_type: str
    status: str
    framework_name: str
    framework_version: str


class EnvironmentOut(BaseModel):
    org: OrgInfo
    assessments: list[AssessmentRef]
    devices_in_boundary: ScopeListWidget
    people_in_boundary: ScopeListWidget
    pending_approval: ScopeListWidget
    devices_out_of_boundary: int
    people_out_of_boundary: int
    document_reviews: DocumentReviewWidget


def _text(attrs: dict, *keys: str) -> str | None:
    for key in keys:
        value = attrs.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _row(entity: ScopeEntity) -> ScopeListRow:
    attrs = entity.attributes or {}
    name = _text(attrs, "display_name")
    if name is None and entity.entity_type == EntityType.PERSON.value:
        first = _text(attrs, "First Name", "FirstName", "first_name")
        last = _text(attrs, "Last Name", "LastName", "last_name")
        name = " ".join(p for p in (first, last) if p) or None
    display = name or entity.natural_key
    sub = _text(attrs, "hostname") if entity.entity_type == EntityType.DEVICE.value else None
    if sub is None or sub == display:
        sub = entity.natural_key if display != entity.natural_key else None
    return ScopeListRow(
        id=entity.id,
        entity_type=entity.entity_type,
        natural_key=entity.natural_key,
        display_name=display,
        sub_label=sub,
        scope_category=entity.scope_category,
        source=entity.source,
    )


def _count(session: Session, clauses: list) -> int:
    return session.scalar(select(func.count()).select_from(ScopeEntity).where(*clauses)) or 0


def scope_list(
    session: Session,
    org_id: uuid.UUID,
    *,
    entity_type: str | None,
    status: str,
    in_boundary: bool | None,
) -> ScopeListWidget:
    cap = ENVIRONMENT_LIST_CAP
    clauses = scope_filter(org_id, entity_type=entity_type, status=status, in_boundary=in_boundary)
    rows = session.scalars(
        select(ScopeEntity)
        .where(*clauses)
        .order_by(ScopeEntity.natural_key, ScopeEntity.id)
        .limit(cap)
    ).all()
    return ScopeListWidget(
        entity_type=entity_type,
        status=status,
        in_boundary=in_boundary,
        total=_count(session, clauses),
        cap=cap,
        rows=[_row(r) for r in rows],
    )


def _org_info(org: Organization) -> OrgInfo:
    return OrgInfo(
        name=org.name,
        cage_code=org.cage_code,
        uei=org.uei,
        address_line1=org.address_line1,
        address_line2=org.address_line2,
        city=org.city,
        state_or_province=org.state_or_province,
        postal_code=org.postal_code,
        country=org.country,
        phone_primary=org.phone_primary,
        website=org.website,
        missing_ssp_fields=[f for f in SSP_REQUIRED_FIELDS if not (getattr(org, f) or "").strip()],
    )


def _assessments(session: Session, org_id: uuid.UUID) -> list[AssessmentRef]:
    rows = session.execute(
        select(Assessment, Framework.name, Framework.version)
        .join(Framework, Framework.id == Assessment.framework_id)
        .where(Assessment.org_id == org_id)
        .order_by(Assessment.started_at.desc(), Assessment.id)
    ).all()
    return [
        AssessmentRef(
            id=a.id,
            name=a.name,
            assessment_type=a.assessment_type,
            status=a.status,
            framework_name=fw_name,
            framework_version=fw_version,
        )
        for a, fw_name, fw_version in rows
    ]


@router.get("/environment", response_model=EnvironmentOut)
def get_environment(
    org_id: uuid.UUID,
    session: Session = Depends(get_session),
) -> EnvironmentOut:
    org = session.get(Organization, org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found")

    active = EntityStatus.ACTIVE.value
    device = EntityType.DEVICE.value
    person = EntityType.PERSON.value
    return EnvironmentOut(
        org=_org_info(org),
        assessments=_assessments(session, org_id),
        devices_in_boundary=scope_list(
            session, org_id, entity_type=device, status=active, in_boundary=True
        ),
        people_in_boundary=scope_list(
            session, org_id, entity_type=person, status=active, in_boundary=True
        ),
        pending_approval=scope_list(
            session, org_id, entity_type=None,
            status=EntityStatus.PENDING_APPROVAL.value, in_boundary=None,
        ),
        devices_out_of_boundary=_count(
            session, scope_filter(org_id, entity_type=device, status=active, in_boundary=False)
        ),
        people_out_of_boundary=_count(
            session, scope_filter(org_id, entity_type=person, status=active, in_boundary=False)
        ),
        document_reviews=_document_reviews(session, org_id),
    )
