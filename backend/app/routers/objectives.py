"""Practitioner-notes editing (D.1-style: deployment-wide, not org-scoped --
AssessmentObjective is shared catalog data read by every org's assessments,
the same tier as Product/Framework and D.1's IntegrationConnection).

PATCH /objectives/{objective_id}/practitioner-notes         Edit in place
POST  /objectives/{objective_id}/practitioner-notes/revert  Restore AI original

msp_admin only, router-wide -- Jarrod's ask was "admin or C3PAO," but
c3pao_assessor is deliberately, permanently read-only (require_write()
blocks it from every non-idempotent method; see auth.py's own docstring
on that role). Carving a write exception for it here would weaken that
security property as a side effect of this task, not a decision made on
its own terms -- out of scope, left to Jarrod explicitly (see this
feature's roadmap writeup for a sketched alternative: a C3PAO "suggest an
edit" surface an msp_admin reviews before it lands, never a direct write).
msp_engineer was also considered and rejected: this table has no org_id at
all, so there's no membership boundary to scope an msp_engineer's write to
-- editing here changes catalog content every org on this deployment sees.

consultant_admin (migration 0034) was considered and rejected for the
same deployment-wide reason, deliberately -- NOT extended here the way it
was for routers/integrations.py. The distinction: an external consultant
hired for one client's engagement editing AI-drafted guidance every other
client on this deployment reads is a bigger blast radius than a single
compliance-data mutation, and unlike integrations config there's no
"Can" list entry naming this explicitly, so the omission is read as
deliberate rather than inferred. (Integrations diverged from this router
on the identical deployment-wide-scope property -- see that router's own
docstring for the tension that leaves open, flagged rather than silently
accepted.)

Read access to practitioner_notes (and its provenance) is NOT gated here
-- it already flows through the existing
GET .../assessments/{id}/controls/{id}/statements endpoint
(routers/assessments.py), open to anyone with assessment access. This
router is write-only by design: viewing a note and editing one are
different questions, and only the second needs restricting.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..audit import log_event
from ..auth import CurrentUser, get_current_user, require_role
from ..db import get_session
from ..models import AssessmentObjective

router = APIRouter(
    prefix="/objectives",
    tags=["objectives"],
    dependencies=[Depends(require_role("msp_admin"))],
)


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class ResolvedEditorOut(BaseModel):
    """Mirrors audit_log.py's _identity_out shape (and the frontend's
    existing ResolvedIdentity type) so the UI can render the same
    active/anonymized/deleted fallback it already knows how to render for
    audit-log actors -- this is the same fact (a user identity that may
    have since changed or been scrubbed under ADR 0006), just surfaced
    from a different endpoint."""

    id: uuid.UUID
    status: Literal["active", "anonymized", "deleted"]
    display_name: str | None
    email: str | None


class PractitionerNotesOut(BaseModel):
    objective_id: uuid.UUID
    practitioner_notes: str | None
    practitioner_notes_generated_at: datetime | None
    practitioner_notes_model: str | None
    practitioner_notes_edited_at: datetime | None
    practitioner_notes_edited_by: ResolvedEditorOut | None


class PractitionerNotesIn(BaseModel):
    text: str

    @field_validator("text")
    @classmethod
    def not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("practitioner_notes text cannot be blank")
        return v.strip()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_editor(session: Session, user_id: uuid.UUID | None) -> ResolvedEditorOut | None:
    """No org scoping here (unlike audit_log.py's _identity_out) --
    practitioner_notes_edited_by isn't an org-scoped fact, so there's no
    org_id to filter by. Deliberately does NOT use session.get(User, ...):
    that runs under user's RLS policy (0015), home_org_id = app.current_org,
    which is the *viewer's* org, not the edited-by user's -- a real editor
    in a different org would be silently misreported as "deleted" (or, on
    a connection where app.current_org has been touched and then rolled
    back without being reset, ''::uuid raises outright -- migration 0025
    hit this exact class of bug once already for the same reason). Calls
    migration 0033's auth.resolve_user_identity() SECURITY DEFINER
    function instead, which intentionally bypasses RLS for this one
    cross-org lookup. Same ADR 0006 fallback chain otherwise: row gone
    entirely -> "deleted"; row present but scrubbed -> "anonymized"; else
    "active" with real display_name/email."""
    if user_id is None:
        return None
    row = session.execute(
        text("SELECT display_name, email, deleted_at FROM auth.resolve_user_identity(:id)"),
        {"id": user_id},
    ).first()
    if row is None:
        return ResolvedEditorOut(id=user_id, status="deleted", display_name=None, email=None)
    if row.deleted_at is not None:
        return ResolvedEditorOut(id=user_id, status="anonymized", display_name=None, email=None)
    return ResolvedEditorOut(
        id=user_id, status="active", display_name=row.display_name, email=row.email
    )


def _out(session: Session, obj: AssessmentObjective) -> PractitionerNotesOut:
    return PractitionerNotesOut(
        objective_id=obj.id,
        practitioner_notes=obj.practitioner_notes,
        practitioner_notes_generated_at=obj.practitioner_notes_generated_at,
        practitioner_notes_model=obj.practitioner_notes_model,
        practitioner_notes_edited_at=obj.practitioner_notes_edited_at,
        practitioner_notes_edited_by=_resolve_editor(session, obj.practitioner_notes_edited_by),
    )


def _get_objective(session: Session, objective_id: uuid.UUID) -> AssessmentObjective:
    obj = session.get(AssessmentObjective, objective_id)
    if obj is None:
        raise HTTPException(status_code=404, detail="Objective not found")
    return obj


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.patch("/{objective_id}/practitioner-notes", response_model=PractitionerNotesOut)
def edit_practitioner_notes(
    objective_id: uuid.UUID,
    body: PractitionerNotesIn,
    session: Session = Depends(get_session),
    current_user: CurrentUser = Depends(get_current_user),
) -> PractitionerNotesOut:
    obj = _get_objective(session, objective_id)
    before_text = obj.practitioner_notes

    obj.practitioner_notes = body.text
    obj.practitioner_notes_edited_by = current_user.id
    obj.practitioner_notes_edited_at = datetime.now(UTC)
    session.flush()

    log_event(
        session,
        org_id=None,
        action="practitioner_notes.edit",
        entity_type="assessment_objective",
        entity_id=obj.id,
        before_value={"practitioner_notes": before_text},
        after_value={"practitioner_notes": obj.practitioner_notes},
        context={"via": "api"},
    )
    session.commit()
    session.refresh(obj)
    return _out(session, obj)


@router.post("/{objective_id}/practitioner-notes/revert", response_model=PractitionerNotesOut)
def revert_practitioner_notes(
    objective_id: uuid.UUID,
    session: Session = Depends(get_session),
) -> PractitionerNotesOut:
    obj = _get_objective(session, objective_id)
    if obj.practitioner_notes_edited_at is None:
        raise HTTPException(
            status_code=400, detail="This note hasn't been edited -- nothing to revert."
        )
    if obj.practitioner_notes_original is None:
        # Shouldn't happen in practice (seed_catalog always sets _original
        # alongside practitioner_notes, and edits never clear it) but fail
        # closed with a clear message rather than silently blanking the note.
        raise HTTPException(
            status_code=409,
            detail="No AI-generated original is on record for this objective to revert to.",
        )

    before_text = obj.practitioner_notes
    obj.practitioner_notes = obj.practitioner_notes_original
    obj.practitioner_notes_edited_by = None
    obj.practitioner_notes_edited_at = None
    session.flush()

    log_event(
        session,
        org_id=None,
        action="practitioner_notes.revert",
        entity_type="assessment_objective",
        entity_id=obj.id,
        before_value={"practitioner_notes": before_text},
        after_value={"practitioner_notes": obj.practitioner_notes},
        context={"via": "api"},
    )
    session.commit()
    session.refresh(obj)
    return _out(session, obj)
