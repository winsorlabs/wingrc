"""SPRS submission record-keeping API -- what was actually filed with the
DoD's SPRS system. See sprs_submissions.py (the DB-adapter module) and
models.py:SprsSubmission for the full design: org-scoped (not assessment-
scoped), append-only, the score stored as a value never a live reference,
and the submitter's identity denormalized so the record survives the
contact being hard-deleted.

  POST   /orgs/{org_id}/sprs-submissions            Record one submission
  GET    /orgs/{org_id}/sprs-submissions            Full history, newest first
  GET    /orgs/{org_id}/sprs-submissions/current     The current (non-voided) one, or null
  POST   /orgs/{org_id}/sprs-submissions/{id}/void   One-way annotation; the row survives

Entirely optional at every call site that offers it (onboarding,
dashboard, the post-completion prompt) -- an org with no submission on
file is a normal, common state (a first-time assessment), never an error
and never something this router invents a fallback for.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from ..audit import log_event
from ..auth import CurrentUser, require_org_access, require_write
from ..db import get_session
from ..models import SprsSubmission
from ..sprs_submissions import (
    get_current_submission,
    list_submissions,
    record_submission,
    resolve_submitter,
    void_submission,
)

router = APIRouter(
    prefix="/orgs/{org_id}/sprs-submissions",
    tags=["sprs-submissions"],
    dependencies=[Depends(require_org_access()), Depends(require_write())],
)

# CLAUDE.md's verified weight distribution: 44*5 + 14*3 + 52*1 = 314 max
# deduction from 110 -- score range is -204 to 110. Applied here too: the
# onboarding/prior-submission case may carry a score from a different
# scoring methodology or scope boundary than this app would ever compute,
# but it must still be a value SPRS itself can represent.
_MIN_SCORE = -204
_MAX_SCORE = 110


class RecordSubmissionIn(BaseModel):
    score: int = Field(ge=_MIN_SCORE, le=_MAX_SCORE)
    submitted_date: date
    submitted_by_contact_id: uuid.UUID
    note: str | None = None
    assessment_id: uuid.UUID | None = None


class SprsSubmissionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    org_id: uuid.UUID
    assessment_id: uuid.UUID | None
    score: int
    submitted_date: date
    submitted_by_contact_id: uuid.UUID | None
    submitted_by_name: str
    submitted_by_email: str | None
    note: str | None
    created_by: str | None
    created_at: datetime
    voided_at: datetime | None
    voided_reason: str | None


class VoidSubmissionIn(BaseModel):
    reason: str = Field(min_length=1)


def _out(row: SprsSubmission) -> SprsSubmissionOut:
    return SprsSubmissionOut.model_validate(row)


@router.post("", response_model=SprsSubmissionOut, status_code=201)
def create_submission(
    org_id: uuid.UUID,
    body: RecordSubmissionIn,
    session: Session = Depends(get_session),
    current_user: CurrentUser = Depends(require_org_access()),
) -> SprsSubmissionOut:
    try:
        contact_id, name, email = resolve_submitter(
            session, org_id=org_id, contact_id=body.submitted_by_contact_id
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    row = record_submission(
        session,
        org_id=org_id,
        score=body.score,
        submitted_date=body.submitted_date,
        submitted_by_contact_id=contact_id,
        submitted_by_name=name,
        submitted_by_email=email,
        note=body.note,
        assessment_id=body.assessment_id,
        created_by=str(current_user.id),
    )
    log_event(
        session,
        org_id=org_id,
        action="sprs_submission.record",
        entity_type="sprs_submission",
        entity_id=row.id,
        after_value={
            "score": row.score,
            "submitted_date": row.submitted_date.isoformat(),
            "submitted_by_name": row.submitted_by_name,
            "assessment_id": str(row.assessment_id) if row.assessment_id else None,
        },
        context={"via": "api"},
        actor=str(current_user.id),
    )
    session.commit()
    return _out(row)


@router.get("", response_model=list[SprsSubmissionOut])
def get_submission_history(
    org_id: uuid.UUID, session: Session = Depends(get_session)
) -> list[SprsSubmissionOut]:
    return [_out(r) for r in list_submissions(session, org_id=org_id)]


@router.get("/current", response_model=SprsSubmissionOut | None)
def get_current(
    org_id: uuid.UUID, session: Session = Depends(get_session)
) -> SprsSubmissionOut | None:
    row = get_current_submission(session, org_id=org_id)
    return _out(row) if row else None


@router.post("/{submission_id}/void", response_model=SprsSubmissionOut)
def void(
    org_id: uuid.UUID,
    submission_id: uuid.UUID,
    body: VoidSubmissionIn,
    session: Session = Depends(get_session),
    current_user: CurrentUser = Depends(require_org_access()),
) -> SprsSubmissionOut:
    """Marks a submission as superseded/incorrect -- see models.py's
    docstring: this never changes what the row claims was filed, only
    whether it's currently authoritative. The row is never deleted."""
    try:
        row = void_submission(
            session, org_id=org_id, submission_id=submission_id, reason=body.reason
        )
    except ValueError as e:
        status = 404 if "not found" in str(e).lower() else 409
        raise HTTPException(status_code=status, detail=str(e)) from e

    log_event(
        session,
        org_id=org_id,
        action="sprs_submission.void",
        entity_type="sprs_submission",
        entity_id=row.id,
        after_value={"voided_reason": body.reason},
        context={"via": "api"},
        actor=str(current_user.id),
    )
    session.commit()
    return _out(row)
