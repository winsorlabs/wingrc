"""SSP import review sessions (app/ssp_import_session.py holds the rules).

  GET  /orgs/{org}/assessments/{a}/ssp-imports
           Every session on the assessment, open or closed, with counts.
  POST /orgs/{org}/assessments/{a}/ssp-imports
           Upload a customer's SSP (.docx): parse, match, open a session.
           The file is not kept.
  GET  …/ssp-imports/{session_id}
           The session, its rows, and progress per family. Writes nothing.
  POST …/ssp-imports/{session_id}/rows/{row_id}/decision
           accept | edit | reject | keep_existing | reopen. An accept or an
           edit writes one draft statement, or answers 409 with the
           statement already there.
  POST …/ssp-imports/{session_id}/close
           completed (every row decided) or discarded (any time).

Who: msp_admin, consultant_admin and msp_engineer (Jarrod, 2026-11-18),
on every route including the reads: the session is working material for
the people doing the import. customer_poc and c3pao_assessor get 403.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import ssp_import_session as imp
from ..auth import CurrentUser, require_org_access
from ..db import get_session

IMPORT_ROLES = ("msp_admin", "consultant_admin", "msp_engineer")

router = APIRouter(
    prefix="/orgs/{org_id}/assessments/{assessment_id}/ssp-imports",
    tags=["ssp-imports"],
)
_gate = require_org_access(*IMPORT_ROLES)


def _actor(user: CurrentUser) -> str:
    return user.display_name or user.email


def _refused(e: imp.Refused) -> HTTPException:
    return HTTPException(status_code=e.status, detail=e.detail)


class SessionSummaryOut(BaseModel):
    id: uuid.UUID
    status: str
    file_name: str
    file_sha256: str
    file_size: int
    started_by: str | None
    started_at: datetime
    closed_by: str | None
    closed_at: datetime | None
    rows: int
    decided: int
    written: int


class CandidateOut(BaseModel):
    objective_id: uuid.UUID
    ref: str
    score: float
    text: str  # the catalog text, to read against the source's
    exact_text: bool
    offered: bool  # at or above the near-match floor, or exact text


class ExistingOut(BaseModel):
    id: uuid.UUID
    body: str
    status: str
    updated_at: datetime
    from_this_session: bool


class RowOut(BaseModel):
    id: uuid.UUID
    number: int
    family: str
    heading: str | None
    control_text: str
    statement: str | None
    source_status: str | None
    notes_present: bool
    raci_present: bool
    match_kind: str
    objective_id: uuid.UUID | None
    objective_ref: str | None
    objective_text: str | None
    candidates: list[CandidateOut]
    near_match_floor: float
    acceptable_onto: list[uuid.UUID]
    not_acceptable: str | None
    decision: str
    resolution: str | None
    decided_objective_id: uuid.UUID | None
    decided_ref: str | None
    statement_id: uuid.UUID | None
    decided_by: str | None
    decided_at: datetime | None
    existing: ExistingOut | None
    shares_objective_with: list[int]


class FamilyOut(BaseModel):
    key: str
    heading: str
    total: int
    decided: int


class SessionOut(BaseModel):
    session: SessionSummaryOut
    families: list[FamilyOut]
    families_complete: int
    rows: list[RowOut]


class DecisionIn(BaseModel):
    action: str
    objective_id: uuid.UUID | None = None
    body: str | None = None
    replace_statement_id: uuid.UUID | None = None
    replace_updated_at: datetime | None = None


class CloseIn(BaseModel):
    outcome: str


def _summary(s, rows: int, decided: int, written: int) -> SessionSummaryOut:
    return SessionSummaryOut(
        id=s.id, status=s.status, file_name=s.file_name, file_sha256=s.file_sha256,
        file_size=s.file_size, started_by=s.started_by, started_at=s.started_at,
        closed_by=s.closed_by, closed_at=s.closed_at, rows=rows or 0, decided=decided or 0,
        written=written or 0,
    )


def _session_out(v: imp.SessionView) -> SessionOut:
    rows = [
        RowOut(
            id=rv.row.id, number=rv.row.number, family=imp.family_key(rv.row),
            heading=rv.row.heading, control_text=rv.row.control_text,
            statement=rv.row.statement, source_status=rv.row.source_status,
            notes_present=rv.row.notes_present, raci_present=rv.row.raci_present,
            match_kind=rv.row.match_kind, objective_id=rv.row.matched_objective_id,
            objective_ref=rv.objective_ref, objective_text=rv.objective_text,
            candidates=[CandidateOut(objective_id=uuid.UUID(o.objective_id), ref=o.ref,
                                     score=o.score, text=o.text, exact_text=o.exact_text,
                                     offered=o.offered) for o in rv.offers],
            near_match_floor=imp.NEAR_MATCH_FLOOR,
            acceptable_onto=[uuid.UUID(x) for x in rv.acceptable_onto],
            not_acceptable=rv.not_acceptable, decision=rv.row.decision,
            resolution=rv.row.resolution, decided_objective_id=rv.row.decided_objective_id,
            decided_ref=rv.decided_ref, statement_id=rv.row.statement_id,
            decided_by=rv.row.decided_by, decided_at=rv.row.decided_at,
            existing=ExistingOut(**vars(rv.existing)) if rv.existing else None,
            shares_objective_with=list(rv.shares_objective_with),
        )
        for rv in v.rows
    ]
    decided = sum(1 for r in rows if r.decision != "pending")
    return SessionOut(
        session=_summary(v.session, len(rows), decided, v.written),
        families=[FamilyOut(**vars(f)) for f in v.families],
        families_complete=sum(1 for f in v.families if f.decided == f.total),
        rows=rows,
    )


@router.get("", response_model=list[SessionSummaryOut])
def list_sessions(
    org_id: uuid.UUID, assessment_id: uuid.UUID,
    session: Session = Depends(get_session), user: CurrentUser = Depends(_gate),
) -> list[SessionSummaryOut]:
    try:
        found = imp.sessions(session, org_id, assessment_id)
    except imp.Refused as e:
        raise _refused(e) from e
    return [_summary(s, n, d, w) for s, n, d, w in found]


@router.post("", response_model=SessionOut, status_code=201)
def start_session(
    org_id: uuid.UUID, assessment_id: uuid.UUID, file: UploadFile = File(...),
    session: Session = Depends(get_session), user: CurrentUser = Depends(_gate),
) -> SessionOut:
    # Sync on purpose: an async route doing blocking database work starves
    # the event loop (docs/roadmap.md, get_current_user's lesson).
    data = file.file.read(imp.MAX_BYTES + 1)
    try:
        s = imp.start(session, org_id=org_id, assessment_id=assessment_id,
                      file_name=file.filename or "upload.docx", data=data,
                      actor=_actor(user))
        session.commit()
        return _session_out(imp.view(session, org_id, assessment_id, s.id))
    except imp.Refused as e:
        session.rollback()
        raise _refused(e) from e


@router.get("/{session_id}", response_model=SessionOut)
def get_session_view(
    org_id: uuid.UUID, assessment_id: uuid.UUID, session_id: uuid.UUID,
    session: Session = Depends(get_session), user: CurrentUser = Depends(_gate),
) -> SessionOut:
    try:
        return _session_out(imp.view(session, org_id, assessment_id, session_id))
    except imp.Refused as e:
        raise _refused(e) from e


@router.post("/{session_id}/rows/{row_id}/decision", response_model=SessionOut)
def decide_row(
    org_id: uuid.UUID, assessment_id: uuid.UUID, session_id: uuid.UUID, row_id: uuid.UUID,
    body: DecisionIn, session: Session = Depends(get_session),
    user: CurrentUser = Depends(_gate),
) -> SessionOut:
    try:
        imp.decide(session, org_id=org_id, assessment_id=assessment_id,
                   session_id=session_id, row_id=row_id, action=body.action,
                   actor=_actor(user), objective_id=body.objective_id, body=body.body,
                   replace_statement_id=body.replace_statement_id,
                   replace_updated_at=body.replace_updated_at)
        session.commit()
        return _session_out(imp.view(session, org_id, assessment_id, session_id))
    except imp.Refused as e:
        session.rollback()
        raise _refused(e) from e


@router.post("/{session_id}/close", response_model=SessionOut)
def close_session(
    org_id: uuid.UUID, assessment_id: uuid.UUID, session_id: uuid.UUID, body: CloseIn,
    session: Session = Depends(get_session), user: CurrentUser = Depends(_gate),
) -> SessionOut:
    try:
        imp.close(session, org_id=org_id, assessment_id=assessment_id, session_id=session_id,
                  outcome=body.outcome, actor=_actor(user))
        session.commit()
        return _session_out(imp.view(session, org_id, assessment_id, session_id))
    except imp.Refused as e:
        session.rollback()
        raise _refused(e) from e
