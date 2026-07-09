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
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_session
from ..engine import activate_org_product, start_assessment
from ..models import (
    Assessment,
    AssessmentObjective,
    BaselineControl,
    Control,
    ControlState,
    ControlStateHistory,
    EvidenceStateLink,
    ImplementationStatement,
    OrgProduct,
    Product,
)

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


class ProductOut(BaseModel):
    id: uuid.UUID
    key: str
    name: str
    provider: str
    category: str
    role: str
    is_active: bool
    activated_at: datetime | None = None
    provider_satisfies_count: int
    shared_count: int
    customer_owns_count: int
    customer_system_count: int
    assists_count: int
    platform_only_count: int


_VALID_STATUSES = frozenset(
    {"met", "not_met", "partial", "pending_evidence", "not_applicable", "inherited"}
)
_VALID_STMT_STATUSES = frozenset({"draft", "reviewed", "approved"})


class PatchControlStateIn(BaseModel):
    status: str


class PatchControlStateOut(BaseModel):
    id: uuid.UUID
    status: str


class StatementOut(BaseModel):
    id: uuid.UUID | None = None
    objective_id: uuid.UUID
    control_state_id: uuid.UUID | None = None
    objective_key: str
    objective_text: str
    objective_guidance: str | None = None
    body: str
    status: str | None = None
    control_discussion: str | None = None


class UpsertStatementIn(BaseModel):
    objective_id: uuid.UUID
    body: str
    status: str = "draft"


class ControlStateOut(BaseModel):
    id: uuid.UUID
    objective_id: uuid.UUID
    control_id: str
    control_db_id: uuid.UUID
    family: str
    control_title: str
    objective_key: str
    objective_text: str
    status: str
    responsibility: str
    sourced_from_product_id: uuid.UUID | None = None
    sourced_from_product_key: str | None = None
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
    "/assessments/{assessment_id}/products",
    response_model=list[ProductOut],
)
def list_products_for_assessment(
    org_id: uuid.UUID,
    assessment_id: uuid.UUID,
    session: Session = Depends(get_session),
) -> list[ProductOut]:
    assessment = session.get(Assessment, assessment_id)
    if assessment is None or assessment.org_id != org_id:
        raise HTTPException(status_code=404, detail="Assessment not found")

    products = session.scalars(
        select(Product)
        .where(Product.framework_id == assessment.framework_id)
        .order_by(Product.name)
    ).all()

    if not products:
        return []

    product_ids = [p.id for p in products]

    # Aggregate classification counts per product in one query
    coverage_rows = session.execute(
        select(
            BaselineControl.product_id,
            BaselineControl.classification,
            func.count().label("cnt"),
        )
        .where(BaselineControl.product_id.in_(product_ids))
        .group_by(BaselineControl.product_id, BaselineControl.classification)
    ).all()
    coverage: dict[uuid.UUID, dict[str, int]] = {}
    for row in coverage_rows:
        coverage.setdefault(row.product_id, {})[row.classification] = row.cnt

    # Aggregate coverage_basis counts (non-customer_owns only — basis is
    # only meaningful for controls where the vendor claims involvement)
    basis_rows = session.execute(
        select(
            BaselineControl.product_id,
            BaselineControl.coverage_basis,
            func.count().label("cnt"),
        )
        .where(BaselineControl.product_id.in_(product_ids))
        .where(BaselineControl.classification != "customer_owns")
        .group_by(BaselineControl.product_id, BaselineControl.coverage_basis)
    ).all()
    basis: dict[uuid.UUID, dict[str, int]] = {}
    for row in basis_rows:
        basis.setdefault(row.product_id, {})[row.coverage_basis] = row.cnt

    # Per-org activation status
    org_products = {
        op.product_id: op
        for op in session.scalars(
            select(OrgProduct).where(
                OrgProduct.org_id == org_id,
                OrgProduct.product_id.in_(product_ids),
            )
        ).all()
    }

    out: list[ProductOut] = []
    for p in products:
        op = org_products.get(p.id)
        c = coverage.get(p.id, {})
        b = basis.get(p.id, {})
        out.append(
            ProductOut(
                id=p.id,
                key=p.key,
                name=p.name,
                provider=p.provider,
                category=p.category,
                role=p.role,
                is_active=op is not None and op.status == "active",
                activated_at=op.activated_at if op is not None else None,
                provider_satisfies_count=c.get("provider_satisfies", 0),
                shared_count=c.get("shared", 0),
                customer_owns_count=c.get("customer_owns", 0),
                customer_system_count=b.get("customer_system", 0),
                assists_count=b.get("assists", 0),
                platform_only_count=b.get("platform_only", 0),
            )
        )
    return out


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

    ev_count_sq = (
        select(func.count(EvidenceStateLink.id))
        .where(EvidenceStateLink.control_state_id == ControlState.id)
        .correlate(ControlState)
        .scalar_subquery()
    )

    stmt = (
        select(
            ControlState,
            AssessmentObjective,
            Control,
            ImplementationStatement,
            Product,
            ev_count_sq.label("evidence_count"),
        )
        .join(AssessmentObjective, ControlState.objective_id == AssessmentObjective.id)
        .join(Control, AssessmentObjective.control_id == Control.id)
        .outerjoin(
            ImplementationStatement,
            (ImplementationStatement.objective_id == AssessmentObjective.id)
            & (ImplementationStatement.assessment_id == assessment_id),
        )
        .outerjoin(Product, ControlState.sourced_from_product_id == Product.id)
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
            control_db_id=ctrl.id,
            family=ctrl.family,
            control_title=ctrl.title,
            objective_key=obj.objective_key,
            objective_text=obj.text,
            status=cs.status,
            responsibility=cs.responsibility,
            sourced_from_product_id=cs.sourced_from_product_id,
            sourced_from_product_key=prod.key if prod is not None else None,
            statement_status=imp_stmt.status if imp_stmt is not None else None,
            evidence_count=ev_count or 0,
        )
        for cs, obj, ctrl, imp_stmt, prod, ev_count in session.execute(stmt).all()
    ]


@router.patch(
    "/assessments/{assessment_id}/control-states/{control_state_id}",
    response_model=PatchControlStateOut,
)
def patch_control_state(
    org_id: uuid.UUID,
    assessment_id: uuid.UUID,
    control_state_id: uuid.UUID,
    body: PatchControlStateIn,
    session: Session = Depends(get_session),
) -> PatchControlStateOut:
    if body.status not in _VALID_STATUSES:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid status {body.status!r}. Must be one of: {sorted(_VALID_STATUSES)}",
        )

    assessment = session.get(Assessment, assessment_id)
    if assessment is None or assessment.org_id != org_id:
        raise HTTPException(status_code=404, detail="Assessment not found")

    cs = session.get(ControlState, control_state_id)
    if cs is None or cs.assessment_id != assessment_id:
        raise HTTPException(status_code=404, detail="Control state not found")

    history = ControlStateHistory(
        control_state_id=cs.id,
        previous_status=cs.status,
        new_status=body.status,
        previous_responsibility=cs.responsibility,
        new_responsibility=cs.responsibility,
    )
    session.add(history)
    cs.status = body.status
    session.commit()
    return PatchControlStateOut(id=cs.id, status=cs.status)


@router.get(
    "/assessments/{assessment_id}/controls/{control_db_id}/statements",
    response_model=list[StatementOut],
)
def get_statements(
    org_id: uuid.UUID,
    assessment_id: uuid.UUID,
    control_db_id: uuid.UUID,
    session: Session = Depends(get_session),
) -> list[StatementOut]:
    assessment = session.get(Assessment, assessment_id)
    if assessment is None or assessment.org_id != org_id:
        raise HTTPException(status_code=404, detail="Assessment not found")
    ctrl = session.get(Control, control_db_id)
    if ctrl is None or ctrl.framework_id != assessment.framework_id:
        raise HTTPException(status_code=404, detail="Control not found")

    objectives = session.scalars(
        select(AssessmentObjective)
        .where(AssessmentObjective.control_id == control_db_id)
        .order_by(AssessmentObjective.objective_key)
    ).all()

    obj_ids = [o.id for o in objectives]
    existing: dict[uuid.UUID, ImplementationStatement] = {}
    if obj_ids:
        rows = session.scalars(
            select(ImplementationStatement).where(
                ImplementationStatement.assessment_id == assessment_id,
                ImplementationStatement.objective_id.in_(obj_ids),
            )
        ).all()
        existing = {r.objective_id: r for r in rows}

    states_by_obj: dict[uuid.UUID, ControlState] = {}
    if obj_ids:
        states_by_obj = {
            cs.objective_id: cs
            for cs in session.scalars(
                select(ControlState).where(
                    ControlState.assessment_id == assessment_id,
                    ControlState.objective_id.in_(obj_ids),
                )
            ).all()
        }

    return [
        StatementOut(
            id=existing[o.id].id if o.id in existing else None,
            objective_id=o.id,
            control_state_id=states_by_obj[o.id].id if o.id in states_by_obj else None,
            objective_key=o.objective_key,
            objective_text=o.text,
            objective_guidance=o.guidance,
            body=existing[o.id].body if o.id in existing else "",
            status=existing[o.id].status if o.id in existing else None,
            control_discussion=ctrl.discussion,
        )
        for o in objectives
    ]


@router.put(
    "/assessments/{assessment_id}/controls/{control_db_id}/statements",
    response_model=list[StatementOut],
)
def upsert_statements(
    org_id: uuid.UUID,
    assessment_id: uuid.UUID,
    control_db_id: uuid.UUID,
    items: list[UpsertStatementIn],
    session: Session = Depends(get_session),
) -> list[StatementOut]:
    invalid = [i for i in items if i.status not in _VALID_STMT_STATUSES]
    if invalid:
        valid = sorted(_VALID_STMT_STATUSES)
        raise HTTPException(
            status_code=422,
            detail=f"Invalid status {invalid[0].status!r}. Must be one of: {valid}",
        )
    assessment = session.get(Assessment, assessment_id)
    if assessment is None or assessment.org_id != org_id:
        raise HTTPException(status_code=404, detail="Assessment not found")
    ctrl = session.get(Control, control_db_id)
    if ctrl is None or ctrl.framework_id != assessment.framework_id:
        raise HTTPException(status_code=404, detail="Control not found")

    obj_ids = [i.objective_id for i in items]
    objectives = {
        o.id: o
        for o in session.scalars(
            select(AssessmentObjective).where(
                AssessmentObjective.control_id == control_db_id,
                AssessmentObjective.id.in_(obj_ids),
            )
        ).all()
    }
    for item in items:
        if item.objective_id not in objectives:
            raise HTTPException(status_code=404, detail=f"Objective {item.objective_id} not found")

    existing: dict[uuid.UUID, ImplementationStatement] = {}
    if obj_ids:
        rows = session.scalars(
            select(ImplementationStatement).where(
                ImplementationStatement.assessment_id == assessment_id,
                ImplementationStatement.objective_id.in_(obj_ids),
            )
        ).all()
        existing = {r.objective_id: r for r in rows}

    saved: list[ImplementationStatement] = []
    for item in items:
        if item.objective_id in existing:
            stmt = existing[item.objective_id]
            stmt.body = item.body
            stmt.status = item.status
        else:
            stmt = ImplementationStatement(
                org_id=org_id,
                objective_id=item.objective_id,
                assessment_id=assessment_id,
                body=item.body,
                status=item.status,
            )
            session.add(stmt)
        saved.append(stmt)

    session.flush()  # populate DB-generated UUIDs before building the response
    session.commit()

    upsert_obj_ids = [stmt.objective_id for stmt in saved]
    upsert_states: dict[uuid.UUID, ControlState] = {
        cs.objective_id: cs
        for cs in session.scalars(
            select(ControlState).where(
                ControlState.assessment_id == assessment_id,
                ControlState.objective_id.in_(upsert_obj_ids),
            )
        ).all()
    }

    return [
        StatementOut(
            id=stmt.id,
            objective_id=stmt.objective_id,
            control_state_id=upsert_states[stmt.objective_id].id
            if stmt.objective_id in upsert_states
            else None,
            objective_key=objectives[stmt.objective_id].objective_key,
            objective_text=objectives[stmt.objective_id].text,
            objective_guidance=objectives[stmt.objective_id].guidance,
            body=stmt.body,
            status=stmt.status,
        )
        for stmt in saved
    ]
