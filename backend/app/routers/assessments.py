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
from ..engine import activate_org_product, deactivate_org_product, recompute_sprs, start_assessment
from ..models import (
    Assessment,
    AssessmentObjective,
    BaselineControl,
    BaselineEvidenceSpec,
    Control,
    ControlState,
    ControlStateHistory,
    EvidenceStateLink,
    EvidenceTask,
    EvidenceTaskStateLink,
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
    {"met", "not_met", "partial", "pending_evidence", "not_applicable", "inherited", "needs_review"}
)
_VALID_TASK_STATUSES = frozenset({"open", "collected", "na"})
_VALID_STMT_STATUSES = frozenset({"draft", "reviewed", "approved"})


class PatchControlStateIn(BaseModel):
    status: str


class PatchControlStateOut(BaseModel):
    id: uuid.UUID
    status: str
    sprs_score: int | None = None


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


class PatchEvidenceTaskIn(BaseModel):
    status: str


class PatchEvidenceTaskOut(BaseModel):
    id: uuid.UUID
    status: str
    is_archived: bool


class DeactivateOut(BaseModel):
    controls_flagged: int
    tasks_archived: int
    evidence_links_archived: int


class EvidenceTaskStateRef(BaseModel):
    control_state_id: uuid.UUID
    objective_id: uuid.UUID
    control_id: str
    objective_key: str


class EvidenceTaskOut(BaseModel):
    id: uuid.UUID
    title: str
    artifact_type: str
    status: str
    collection_session: str | None = None
    baseline_spec_id: uuid.UUID | None = None
    source_product_key: str | None = None
    source_product_name: str | None = None
    linked_states: list[EvidenceTaskStateRef] = []


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
    sprs_weight: int = 1
    is_level_1: bool = False


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
            sprs_weight=ctrl.sprs_weight,
            is_level_1=ctrl.is_level_1,
        )
        for cs, obj, ctrl, imp_stmt, prod, ev_count in session.execute(stmt).all()
    ]


@router.get(
    "/assessments/{assessment_id}/evidence-tasks",
    response_model=list[EvidenceTaskOut],
)
def list_evidence_tasks(
    org_id: uuid.UUID,
    assessment_id: uuid.UUID,
    session: Session = Depends(get_session),
) -> list[EvidenceTaskOut]:
    assessment = session.get(Assessment, assessment_id)
    if assessment is None or assessment.org_id != org_id:
        raise HTTPException(status_code=404, detail="Assessment not found")

    tasks = session.scalars(
        select(EvidenceTask)
        .where(
            EvidenceTask.assessment_id == assessment_id,
            EvidenceTask.org_id == org_id,
        )
        .order_by(EvidenceTask.collection_session, EvidenceTask.created_at)
    ).all()

    if not tasks:
        return []

    task_ids = [t.id for t in tasks]

    # Load all links with control/objective info in one query
    link_rows = session.execute(
        select(
            EvidenceTaskStateLink.task_id,
            EvidenceTaskStateLink.control_state_id,
            ControlState.objective_id,
            AssessmentObjective.objective_key,
            Control.control_id,
        )
        .join(ControlState, EvidenceTaskStateLink.control_state_id == ControlState.id)
        .join(AssessmentObjective, ControlState.objective_id == AssessmentObjective.id)
        .join(Control, AssessmentObjective.control_id == Control.id)
        .where(EvidenceTaskStateLink.task_id.in_(task_ids))
        .order_by(Control.sequence_order, AssessmentObjective.objective_key)
    ).all()

    links_by_task: dict[uuid.UUID, list[EvidenceTaskStateRef]] = {}
    for row in link_rows:
        links_by_task.setdefault(row.task_id, []).append(
            EvidenceTaskStateRef(
                control_state_id=row.control_state_id,
                objective_id=row.objective_id,
                control_id=row.control_id,
                objective_key=row.objective_key,
            )
        )

    # Resolve source product via baseline_spec → baseline_control → product
    spec_ids = [t.baseline_spec_id for t in tasks if t.baseline_spec_id is not None]
    product_by_spec: dict[uuid.UUID, tuple[str, str]] = {}
    if spec_ids:
        for row in session.execute(
            select(
                BaselineEvidenceSpec.id.label("spec_id"),
                Product.key.label("product_key"),
                Product.name.label("product_name"),
            )
            .join(BaselineControl, BaselineEvidenceSpec.baseline_control_id == BaselineControl.id)
            .join(Product, BaselineControl.product_id == Product.id)
            .where(BaselineEvidenceSpec.id.in_(spec_ids))
        ).all():
            product_by_spec[row.spec_id] = (row.product_key, row.product_name)

    return [
        EvidenceTaskOut(
            id=t.id,
            title=t.title,
            artifact_type=t.artifact_type,
            status=t.status,
            collection_session=t.collection_session,
            baseline_spec_id=t.baseline_spec_id,
            source_product_key=(
                product_by_spec[t.baseline_spec_id][0]
                if t.baseline_spec_id in product_by_spec
                else None
            ),
            source_product_name=(
                product_by_spec[t.baseline_spec_id][1]
                if t.baseline_spec_id in product_by_spec
                else None
            ),
            linked_states=links_by_task.get(t.id, []),
        )
        for t in tasks
    ]


@router.patch(
    "/assessments/{assessment_id}/evidence-tasks/{task_id}",
    response_model=PatchEvidenceTaskOut,
)
def patch_evidence_task(
    org_id: uuid.UUID,
    assessment_id: uuid.UUID,
    task_id: uuid.UUID,
    body: PatchEvidenceTaskIn,
    session: Session = Depends(get_session),
) -> PatchEvidenceTaskOut:
    if body.status not in _VALID_TASK_STATUSES:
        valid = sorted(_VALID_TASK_STATUSES)
        raise HTTPException(
            status_code=422,
            detail=f"Invalid status {body.status!r}. Must be one of: {valid}",
        )

    assessment = session.get(Assessment, assessment_id)
    if assessment is None or assessment.org_id != org_id:
        raise HTTPException(status_code=404, detail="Assessment not found")

    task = session.get(EvidenceTask, task_id)
    if task is None or task.assessment_id != assessment_id or task.org_id != org_id:
        raise HTTPException(status_code=404, detail="Evidence task not found")

    if task.is_archived:
        raise HTTPException(
            status_code=422,
            detail="Cannot update an archived evidence task",
        )

    task.status = body.status
    session.commit()
    return PatchEvidenceTaskOut(id=task.id, status=task.status, is_archived=task.is_archived)


@router.post(
    "/assessments/{assessment_id}/products/{product_id}/deactivate",
    response_model=DeactivateOut,
)
def deactivate_product(
    org_id: uuid.UUID,
    assessment_id: uuid.UUID,
    product_id: uuid.UUID,
    session: Session = Depends(get_session),
) -> DeactivateOut:
    assessment = session.get(Assessment, assessment_id)
    if assessment is None or assessment.org_id != org_id:
        raise HTTPException(status_code=404, detail="Assessment not found")

    op = session.scalars(
        select(OrgProduct).where(
            OrgProduct.org_id == org_id,
            OrgProduct.product_id == product_id,
        )
    ).first()
    if op is None or op.status != "active":
        raise HTTPException(status_code=404, detail="Active product not found")

    result = deactivate_org_product(
        session,
        org_id=org_id,
        product_id=product_id,
        assessment_id=assessment_id,
    )
    session.commit()
    return DeactivateOut(**result)


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
    score = recompute_sprs(session, assessment_id)
    session.commit()
    return PatchControlStateOut(id=cs.id, status=cs.status, sprs_score=score)


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
