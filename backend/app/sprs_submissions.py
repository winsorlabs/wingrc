"""SPRS submission record-keeping -- what was actually filed with the
DoD's SPRS system, as opposed to what WinGRC computed (sprs_snapshot) or
whether an assessment is complete in WinGRC (Assessment.status). See
models.py:SprsSubmission's own docstring for the full three-way
distinction and the append-only/contact-survival design.

Deliberately its own module, not folded into engine.py: engine.py is
assessment-lifecycle machinery (start/activate/deactivate/recompute);
this is a separate, org-level domain concept that predates and outlives
any one assessment (the first submission is typically captured at
onboarding, before an assessment exists at all).

Every function here is a thin DB adapter, same shape as engine.py's --
routers call these, never construct SprsSubmission rows directly, so
"what counts as the current submission" and "what a void does" each
have exactly one implementation.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Contact, SprsSubmission


def record_submission(
    session: Session,
    *,
    org_id: uuid.UUID,
    score: int,
    submitted_date: date,
    submitted_by_contact_id: uuid.UUID | None,
    submitted_by_name: str,
    submitted_by_email: str | None,
    note: str | None,
    assessment_id: uuid.UUID | None,
    created_by: str | None,
) -> SprsSubmission:
    """Insert one new submission row. Never updates an existing one --
    see models.py:SprsSubmission's docstring; a correction is a new row.

    submitted_by_name/submitted_by_email are the caller's responsibility
    to resolve up front (typically from the Contact the caller looked up
    by submitted_by_contact_id) and pass explicitly, not re-derived here
    -- this function does not require submitted_by_contact_id to point at
    a real, still-existing Contact at all (the onboarding case may name a
    submitter who was never entered as a Contact in this system), and
    denormalizing at the call site keeps that case and the normal
    look-up-a-contact case identical from this function's point of view.
    """
    row = SprsSubmission(
        id=uuid.uuid4(),
        org_id=org_id,
        assessment_id=assessment_id,
        score=score,
        submitted_date=submitted_date,
        submitted_by_contact_id=submitted_by_contact_id,
        submitted_by_name=submitted_by_name,
        submitted_by_email=submitted_by_email,
        note=note,
        created_by=created_by,
    )
    session.add(row)
    session.flush()
    return row


def _current_submission_query(org_id: uuid.UUID):
    return (
        select(SprsSubmission)
        .where(SprsSubmission.org_id == org_id, SprsSubmission.voided_at.is_(None))
        .order_by(SprsSubmission.submitted_date.desc(), SprsSubmission.created_at.desc())
        .limit(1)
    )


def get_current_submission(session: Session, *, org_id: uuid.UUID) -> SprsSubmission | None:
    """The submission that answers "have we filed, what was it, when" --
    most recent by submitted_date (created_at as tiebreak) among rows not
    voided. None means "this org has never recorded a submission," a
    legitimate, common state (a first-time assessment) -- callers must
    not treat it as an error or invent a fallback."""
    return session.scalars(_current_submission_query(org_id)).first()


def list_submissions(session: Session, *, org_id: uuid.UUID) -> list[SprsSubmission]:
    """Full history, newest first, voided rows included -- the void
    itself (and its reason) is part of the record, not something to hide
    from the history view."""
    return list(
        session.scalars(
            select(SprsSubmission)
            .where(SprsSubmission.org_id == org_id)
            .order_by(SprsSubmission.submitted_date.desc(), SprsSubmission.created_at.desc())
        )
    )


def void_submission(
    session: Session, *, org_id: uuid.UUID, submission_id: uuid.UUID, reason: str
) -> SprsSubmission:
    """Marks one row as no longer authoritative -- a one-way annotation,
    never an edit to what it claims was filed (score/submitted_date/
    submitted_by_*/note are untouched). Raises ValueError if the
    submission doesn't belong to this org, is already voided (voiding is
    one-way -- see models.py's docstring), or doesn't exist; callers
    translate to the appropriate HTTP status.
    """
    row = session.get(SprsSubmission, submission_id)
    if row is None or row.org_id != org_id:
        raise ValueError("Submission not found")
    if row.voided_at is not None:
        raise ValueError("Submission is already voided")
    row.voided_at = datetime.now(UTC)
    row.voided_reason = reason
    session.flush()
    return row


def resolve_submitter(
    session: Session, *, org_id: uuid.UUID, contact_id: uuid.UUID | None
) -> tuple[uuid.UUID | None, str, str | None]:
    """Looks up a Contact for denormalization at record-time. Returns
    (contact_id, name, email); raises ValueError if contact_id is given
    but doesn't resolve to a real Contact in this org -- an SPRS
    submission's submitter must be a real, named person at the moment
    it's recorded, even though that Contact row may later disappear."""
    if contact_id is None:
        raise ValueError("submitted_by_contact_id is required")
    contact = session.get(Contact, contact_id)
    if contact is None or contact.org_id != org_id:
        raise ValueError("Contact not found")
    return contact.id, contact.name, contact.email
