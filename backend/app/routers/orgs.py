"""Organization CRUD endpoints.

POST /orgs           Create an organization; returns the new record with its id.
GET  /orgs           List all organizations ordered by name.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_session
from ..models import Organization

router = APIRouter(prefix="/orgs", tags=["orgs"])


class OrgIn(BaseModel):
    name: str


class OrgOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    created_at: datetime


@router.post("", response_model=OrgOut, status_code=201)
def create_org(body: OrgIn, session: Session = Depends(get_session)) -> OrgOut:
    existing = session.scalars(
        select(Organization).where(Organization.name == body.name)
    ).first()
    if existing is not None:
        raise HTTPException(status_code=409, detail=f"Organization {body.name!r} already exists")
    org = Organization(name=body.name)
    session.add(org)
    session.commit()
    session.refresh(org)
    return OrgOut.model_validate(org)


@router.get("", response_model=list[OrgOut])
def list_orgs(session: Session = Depends(get_session)) -> list[OrgOut]:
    orgs = session.scalars(select(Organization).order_by(Organization.name)).all()
    return [OrgOut.model_validate(o) for o in orgs]
