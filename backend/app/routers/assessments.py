"""Assessment API endpoints.

Three endpoints:
  POST /orgs/{org_id}/assessments
      Start a new assessment. Seeds control_state for all framework objectives
      and fires the magic loop for any already-active org_products.

  POST /orgs/{org_id}/assessments/{assessment_id}/products/{product_id}/activate
      Mark a product in-use and fire the magic loop for this assessment.

  GET  /orgs/{org_id}/assessments/{assessment_id}/control-states
      Return per-objective compliance state, optionally filtered by control family.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_session
from ..engine import activate_org_product, start_assessment
from ..models import Assessment, AssessmentObjective, Control, ControlState, ImplementationStatement

router = APIRouter(prefix="/orgs/{org_id}", tags=["assessments"])


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------


class StartAssessmentIn(BaseModel):
    framework_id: uuid.UUID
    name: str
    assessment_type: str = "self"


class AssessmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    org_id: uuid.UUID
    framework_id: uuid.UUID
    name: str
    assessment_type: str
    status: str
    started_at: datetime
    sprs_score: int | None = None


class ActivateIn(BaseModel):
    configuration_notes: str | None = None


class ActivateOut(BaseModel):
    objectives_updated: int
    tasks_created: int


class ControlStateOut(BaseModel):
    id: uuid.UUID
    objective_id: uuid.UUID
    control_id: str
    family: str
    control_title: str
    objective_key: str
    objective_text: str
    status: str
    responsibility: str
    sourced_from_product_id: uuid.UUID | None = None
    statement_status: str | None = None
    evidence_count: int = 0


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/assessments", response_model=list[AssessmentOut])
def list_assessments(
    org_id: uuid.UUID,
    session: Session = Depends(get_session),
) -> list[AssessmentOut]:
    assessments = session.scalars(
        select(Assessment)
        .where(Assessment.org_id == org_id)
        .order_by(Assessment.started_at.desc())
    ).all()
    return [AssessmentOut.model_validate(a) for a in assessments]


@router.post("/assessments", response_model=AssessmentOut, status_code=201)
def create_assessment(
    org_id: uuid.UUID,
    body: StartAssessmentIn,
    session: Session = Depends(get_session),
) -> AssessmentOut:
    assessment = start_assessment(
        session,
        org_id=org_id,
        framework_id=body.framework_id,
        name=body.name,
        assessment_type=body.assessment_type,
    )
    session.commit()
    return AssessmentOut.model_validate(assessment)


@router.post(
    "/assessments/{assessment_id}/products/{product_id}/activate",
    response_model=ActivateOut,
)
def activate_product(
    org_id: uuid.UUID,
    assessment_id: uuid.UUID,
    product_id: uuid.UUID,
    body: ActivateIn = ActivateIn(),
    session: Session = Depends(get_session),
) -> ActivateOut:
    # Verify the assessment belongs to this org
    assessment = session.get(Assessment, assessment_id)
    if assessment is None or assessment.org_id != org_id:
        raise HTTPException(status_code=404, detail="Assessment not found")

    result = activate_org_product(
        session,
        org_id=org_id,
        product_id=product_id,
        assessment_id=assessment_id,
        configuration_notes=body.configuration_notes,
    )
    session.commit()
    return ActivateOut(**result)


@router.get(
    "/assessments/{assessment_id}/control-states",
    response_model=list[ControlStateOut],
)
def list_control_states(
    org_id: uuid.UUID,
    assessment_id: uuid.UUID,
    family: str | None = Query(default=None, description="Filter by control family (e.g. AC, AU)"),
    session: Session = Depends(get_session),
) -> list[ControlStateOut]:
    assessment = session.get(Assessment, assessment_id)
    if assessment is None or assessment.org_id != org_id:
        raise HTTPException(status_code=404, detail="Assessment not found")

    stmt = (
        select(ControlState, AssessmentObjective, Control, ImplementationStatement)
        .join(AssessmentObjective, ControlState.objective_id == AssessmentObjective.id)
        .join(Control, AssessmentObjective.control_id == Control.id)
        .outerjoin(
            ImplementationStatement,
            (ImplementationStatement.control_id == Control.id)
            & (ImplementationStatement.assessment_id == assessment_id),
        )
        .where(ControlState.assessment_id == assessment_id)
        .where(ControlState.org_id == org_id)
        .order_by(Control.sequence_order, AssessmentObjective.objective_key)
    )
    if family:
        stmt = stmt.where(Control.family == family.upper())

    return [
        ControlStateOut(
            id=cs.id,
            objective_id=cs.objective_id,
            control_id=ctrl.control_id,
            family=ctrl.family,
            control_title=ctrl.title,
            objective_key=obj.objective_key,
            objective_text=obj.text,
            status=cs.status,
            responsibility=cs.responsibility,
            sourced_from_product_id=cs.sourced_from_product_id,
            statement_status=imp_stmt.status if imp_stmt is not None else None,
            evidence_count=0,
        )
        for cs, obj, ctrl, imp_stmt in session.execute(stmt).all()
    ]
