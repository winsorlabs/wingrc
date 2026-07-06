"""Framework catalog endpoint.

GET /frameworks  — list all seeded frameworks so the frontend can resolve
                   a framework_id when starting an assessment.
"""
from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_session
from ..models import Framework

router = APIRouter(prefix="/frameworks", tags=["frameworks"])


class FrameworkOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    key: str
    name: str
    version: str
    published_at: date | None = None


@router.get("", response_model=list[FrameworkOut])
def list_frameworks(session: Session = Depends(get_session)) -> list[FrameworkOut]:
    frameworks = session.scalars(select(Framework).order_by(Framework.key)).all()
    return [FrameworkOut.model_validate(f) for f in frameworks]
