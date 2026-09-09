"""RACI assignment endpoints (G.7 — docs/PLAN-gui-restructure.md).

GET    /orgs/{org_id}/assessments/{assessment_id}/raci        Every assignment, joined to Contact
POST   /orgs/{org_id}/assessments/{assessment_id}/raci        Assign one (control_state_id,
                                                                contact_id, raci_letter)
DELETE /orgs/{org_id}/assessments/{assessment_id}/raci/{id}   Remove one assignment
POST   /orgs/{org_id}/assessments/{assessment_id}/raci/bulk   Family-level assign, cascading

RaciAssignment carries no org_id of its own (see models.py's docstring) —
tenant isolation runs through the control_state join, the same precedent
control_state_history already uses, so it deliberately gets no RLS policy
of its own (see 0002_assessment_engine.py's `_enable_rls` call sites: only
tables with a direct org_id column get one).

No magic-loop pre-suggestion endpoint here: ControlState.responsibility is
already returned by GET .../control-states (assessments.py), so the
frontend derives the MSP-vs-customer suggestion from data it already has
rather than this router re-deriving it from BaselineControl and shipping a
second, parallel source for the same fact. Never auto-written either way —
see RolesPanel.tsx.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import log_event
from ..auth import require_org_access, require_write
from ..db import get_session
from ..models import Assessment, AssessmentObjective, Contact, Control, ControlState, RaciAssignment

router = APIRouter(
    prefix="/orgs/{org_id}",
    tags=["raci"],
    dependencies=[Depends(require_org_access()), Depends(require_write())],
)

_VALID_LETTERS = frozenset({"A", "R", "C", "I"})


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class RaciAssignmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    control_state_id: uuid.UUID
    contact_id: uuid.UUID
    contact_name: str
    # G.7 Part 3: RolesPanel.tsx displays this alongside the name so the
    # matrix shows MSP-vs-customer at a glance, not just who -- the whole
    # point of the CRM this doubles as (see bundle_service.py's CrmSnap).
    contact_affiliation: str
    raci_letter: str
    created_at: datetime


class RaciAssignIn(BaseModel):
    control_state_id: uuid.UUID
    contact_id: uuid.UUID
    raci_letter: str

    @field_validator("raci_letter")
    @classmethod
    def validate_letter(cls, v: str) -> str:
        if v not in _VALID_LETTERS:
            raise ValueError(f"raci_letter must be one of: {sorted(_VALID_LETTERS)}")
        return v


class RaciBulkIn(BaseModel):
    family: str
    contact_id: uuid.UUID
    raci_letter: str

    @field_validator("raci_letter")
    @classmethod
    def validate_letter(cls, v: str) -> str:
        if v not in _VALID_LETTERS:
            raise ValueError(f"raci_letter must be one of: {sorted(_VALID_LETTERS)}")
        return v


class RaciBulkOut(BaseModel):
    assigned: int
    skipped: int


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_assessment(session: Session, org_id: uuid.UUID, assessment_id: uuid.UUID) -> Assessment:
    assessment = session.get(Assessment, assessment_id)
    if assessment is None or assessment.org_id != org_id:
        raise HTTPException(status_code=404, detail="Assessment not found")
    return assessment


def _get_contact(session: Session, org_id: uuid.UUID, contact_id: uuid.UUID) -> Contact:
    contact = session.scalars(
        select(Contact).where(Contact.id == contact_id, Contact.org_id == org_id)
    ).first()
    if contact is None:
        raise HTTPException(status_code=404, detail="Contact not found")
    return contact


def _get_control_state(
    session: Session, org_id: uuid.UUID, assessment_id: uuid.UUID, control_state_id: uuid.UUID
) -> ControlState:
    cs = session.scalars(
        select(ControlState).where(
            ControlState.id == control_state_id,
            ControlState.org_id == org_id,
            ControlState.assessment_id == assessment_id,
        )
    ).first()
    if cs is None:
        raise HTTPException(status_code=404, detail="Control state not found")
    return cs


def _out(
    assignment: RaciAssignment, contact_name: str, contact_affiliation: str
) -> RaciAssignmentOut:
    return RaciAssignmentOut(
        id=assignment.id,
        control_state_id=assignment.control_state_id,
        contact_id=assignment.contact_id,
        contact_name=contact_name,
        contact_affiliation=contact_affiliation,
        raci_letter=assignment.raci_letter,
        created_at=assignment.created_at,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/assessments/{assessment_id}/raci", response_model=list[RaciAssignmentOut])
def list_raci_assignments(
    org_id: uuid.UUID,
    assessment_id: uuid.UUID,
    session: Session = Depends(get_session),
) -> list[RaciAssignmentOut]:
    _get_assessment(session, org_id, assessment_id)

    rows = session.execute(
        select(RaciAssignment, Contact.name, Contact.affiliation)
        .join(ControlState, RaciAssignment.control_state_id == ControlState.id)
        .join(Contact, RaciAssignment.contact_id == Contact.id)
        .where(ControlState.org_id == org_id, ControlState.assessment_id == assessment_id)
        .order_by(RaciAssignment.created_at)
    ).all()
    return [_out(a, name, affiliation) for a, name, affiliation in rows]


@router.post("/assessments/{assessment_id}/raci", response_model=RaciAssignmentOut, status_code=201)
def create_raci_assignment(
    org_id: uuid.UUID,
    assessment_id: uuid.UUID,
    body: RaciAssignIn,
    session: Session = Depends(get_session),
) -> RaciAssignmentOut:
    _get_assessment(session, org_id, assessment_id)
    cs = _get_control_state(session, org_id, assessment_id, body.control_state_id)
    contact = _get_contact(session, org_id, body.contact_id)

    existing = session.scalars(
        select(RaciAssignment).where(
            RaciAssignment.control_state_id == cs.id,
            RaciAssignment.contact_id == contact.id,
            RaciAssignment.raci_letter == body.raci_letter,
        )
    ).first()
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail=f"{contact.name} already holds {body.raci_letter!r} on this objective",
        )

    assignment = RaciAssignment(
        control_state_id=cs.id, contact_id=contact.id, raci_letter=body.raci_letter
    )
    session.add(assignment)
    session.flush()
    log_event(
        session,
        org_id=org_id,
        action="raci.assign",
        entity_type="raci_assignment",
        entity_id=assignment.id,
        after_value={
            "control_state_id": str(cs.id),
            "contact_id": str(contact.id),
            "raci_letter": body.raci_letter,
        },
        context={"via": "api"},
    )
    session.commit()
    session.refresh(assignment)
    return _out(assignment, contact.name, contact.affiliation)


@router.delete("/assessments/{assessment_id}/raci/{raci_id}", status_code=204)
def delete_raci_assignment(
    org_id: uuid.UUID,
    assessment_id: uuid.UUID,
    raci_id: uuid.UUID,
    session: Session = Depends(get_session),
) -> None:
    _get_assessment(session, org_id, assessment_id)

    row = session.execute(
        select(RaciAssignment, Contact.name)
        .join(ControlState, RaciAssignment.control_state_id == ControlState.id)
        .join(Contact, RaciAssignment.contact_id == Contact.id)
        .where(
            RaciAssignment.id == raci_id,
            ControlState.org_id == org_id,
            ControlState.assessment_id == assessment_id,
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="RACI assignment not found")
    assignment, contact_name = row

    log_event(
        session,
        org_id=org_id,
        action="raci.unassign",
        entity_type="raci_assignment",
        entity_id=assignment.id,
        before_value={
            "control_state_id": str(assignment.control_state_id),
            "contact_id": str(assignment.contact_id),
            "contact_name": contact_name,
            "raci_letter": assignment.raci_letter,
        },
        context={"via": "api"},
    )
    session.delete(assignment)
    session.commit()


@router.post("/assessments/{assessment_id}/raci/bulk", response_model=RaciBulkOut)
def bulk_assign_raci(
    org_id: uuid.UUID,
    assessment_id: uuid.UUID,
    body: RaciBulkIn,
    session: Session = Depends(get_session),
) -> RaciBulkOut:
    """Family-level assign, cascading to every child control_state in the
    family — without clobbering objective-level overrides already in place.

    "Already in place" is checked per (control_state_id, raci_letter), not
    per control_state alone: an override on a different letter (e.g. someone
    hand-picked the 'C' contact) must not block this letter's cascade, and a
    prior bulk-assign of the same letter must not either — both leave a row
    behind for that (control_state, letter) pair, which is exactly the
    signal this checks for. This is what makes bulk-assign idempotent and
    override-safe: re-running it with the same (family, letter) only fills
    in gaps, and running it with a different contact after a manual
    per-objective override leaves that override untouched.
    """
    _get_assessment(session, org_id, assessment_id)
    contact = _get_contact(session, org_id, body.contact_id)
    family = body.family.upper()

    control_state_ids = session.scalars(
        select(ControlState.id)
        .join(AssessmentObjective, ControlState.objective_id == AssessmentObjective.id)
        .join(Control, AssessmentObjective.control_id == Control.id)
        .where(
            ControlState.org_id == org_id,
            ControlState.assessment_id == assessment_id,
            Control.family == family,
        )
    ).all()
    if not control_state_ids:
        raise HTTPException(
            status_code=404, detail=f"No controls found for family {family!r} in this assessment"
        )

    already_covered = set(
        session.scalars(
            select(RaciAssignment.control_state_id).where(
                RaciAssignment.control_state_id.in_(control_state_ids),
                RaciAssignment.raci_letter == body.raci_letter,
            )
        ).all()
    )

    to_create = [cs_id for cs_id in control_state_ids if cs_id not in already_covered]
    for cs_id in to_create:
        session.add(
            RaciAssignment(
                control_state_id=cs_id, contact_id=contact.id, raci_letter=body.raci_letter
            )
        )
    session.flush()

    log_event(
        session,
        org_id=org_id,
        action="raci.bulk_assign",
        entity_type="raci_assignment",
        entity_id=assessment_id,
        after_value={
            "family": family,
            "contact_id": str(contact.id),
            "raci_letter": body.raci_letter,
            "assigned": len(to_create),
            "skipped": len(already_covered),
        },
        context={"via": "api"},
    )
    session.commit()
    return RaciBulkOut(assigned=len(to_create), skipped=len(already_covered))
