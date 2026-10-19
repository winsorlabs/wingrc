"""Lists as evidence -- roadmap L.4 (list_evidence.py holds the rules).

  GET    /orgs/{org}/assessments/{a}/control-states/{cs}/lists
             The org's lists tagged with this objective's practice: offered,
             or why not; and the version linked here, with its drift.
  POST   /orgs/{org}/assessments/{a}/control-states/{cs}/lists/{list_id}
             Attach the list's current version.
  POST   …/lists/{list_id}/move-forward
             Move this objective's link onto the current version (a new
             link; the old one is archived, never rewritten).
  DELETE …/lists/{list_id}
             Remove the list from this objective's evidence.
  GET    /orgs/{org}/assessments/{a}/list-links
             Every live list link in an assessment -- the bundle picker's
             "linked as evidence" group.
  GET    /orgs/{org}/list-definitions/{list_id}/evidence-links
             The list's page: offered or why not, and where it is linked.
  GET    /orgs/{org}/list-definitions/{list_id}/link-events
             Every link, move forward and unlink, for the list's History
             (L.10).

None of these reads or writes control_state.status or the SPRS score.
Read-only roles (c3pao_assessor) see every GET and make, move or break
nothing -- require_write, router-wide.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import list_evidence
from ..auth import CurrentUser, require_org_access, require_write
from ..db import get_session
from ..models import Assessment, ControlState, ListDefinition, ListVersion

router = APIRouter(
    prefix="/orgs/{org_id}",
    tags=["list-evidence"],
    dependencies=[Depends(require_org_access()), Depends(require_write())],
)


class ListCandidateOut(BaseModel):
    list_id: uuid.UUID
    list_key: str
    title: str
    current_version_id: uuid.UUID | None
    current_version_number: int | None
    offered: bool
    not_offered: str | None
    linked_version_id: uuid.UUID | None
    linked_version_number: int | None
    evidence_id: uuid.UUID | None
    drift: str | None
    is_template: bool = False


class ListLinkOut(BaseModel):
    list_id: uuid.UUID
    list_key: str
    title: str
    current_version_number: int | None
    linked_version_id: uuid.UUID
    linked_version_number: int
    assessment_id: uuid.UUID
    assessment_name: str
    control_state_id: uuid.UUID
    control_key: str
    objective_key: str
    drift: str | None


class LinkEventOut(BaseModel):
    id: uuid.UUID
    change: str  # linked | moved_forward | unlinked
    version_number: int
    from_version_numbers: list[int] | None
    assessment_id: uuid.UUID
    assessment_name: str
    control_key: str
    objective_key: str
    saved_by: str | None
    created_at: datetime


class ListEvidenceOut(BaseModel):
    offered: bool
    not_offered: str | None
    links: list[ListLinkOut]


def _state(
    session: Session, org_id: uuid.UUID, assessment_id: uuid.UUID, cs_id: uuid.UUID
) -> ControlState:
    a = session.get(Assessment, assessment_id)
    if a is None or a.org_id != org_id:
        raise HTTPException(status_code=404, detail="Assessment not found")
    cs = session.get(ControlState, cs_id)
    if cs is None or cs.assessment_id != assessment_id:
        raise HTTPException(status_code=404, detail="Control state not found")
    return cs


def _candidate_out(c: list_evidence.Candidate) -> ListCandidateOut:
    return ListCandidateOut(
        list_id=c.list_id, list_key=c.list_key, title=c.title,
        current_version_id=c.current_version_id, current_version_number=c.current_version_number,
        offered=c.not_offered is None, not_offered=c.not_offered,
        linked_version_id=c.linked_version_id, linked_version_number=c.linked_version_number,
        evidence_id=c.evidence_id, drift=c.drift, is_template=c.is_template,
    )


def _link_out(v: list_evidence.LinkView) -> ListLinkOut:
    return ListLinkOut(
        list_id=v.list_id, list_key=v.list_key, title=v.title,
        current_version_number=v.current_version_number, linked_version_id=v.linked_version_id,
        linked_version_number=v.linked_version_number, assessment_id=v.assessment_id,
        assessment_name=v.assessment_name, control_state_id=v.control_state_id,
        control_key=v.control_key, objective_key=v.objective_key, drift=v.drift,
    )


@router.get(
    "/assessments/{assessment_id}/control-states/{cs_id}/lists",
    response_model=list[ListCandidateOut],
)
def state_lists(
    org_id: uuid.UUID, assessment_id: uuid.UUID, cs_id: uuid.UUID,
    session: Session = Depends(get_session),
) -> list[ListCandidateOut]:
    cs = _state(session, org_id, assessment_id, cs_id)
    return [_candidate_out(c) for c in list_evidence.candidates(session, org_id, cs)]


def _act(
    fn, org_id, assessment_id, cs_id, list_id, session, user: CurrentUser
) -> list[ListCandidateOut]:
    cs = _state(session, org_id, assessment_id, cs_id)
    try:
        fn(session, org_id, cs, list_id, saved_by=user.display_name or user.email)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except list_evidence.ListLinkRefused as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    session.commit()
    return [_candidate_out(c) for c in list_evidence.candidates(session, org_id, cs)]


@router.post(
    "/assessments/{assessment_id}/control-states/{cs_id}/lists/{list_id}",
    response_model=list[ListCandidateOut], status_code=201,
)
def link_list(
    org_id: uuid.UUID, assessment_id: uuid.UUID, cs_id: uuid.UUID, list_id: uuid.UUID,
    session: Session = Depends(get_session),
    current_user: CurrentUser = Depends(require_org_access()),
) -> list[ListCandidateOut]:
    return _act(
        list_evidence.link, org_id, assessment_id, cs_id, list_id, session, current_user
    )


@router.post(
    "/assessments/{assessment_id}/control-states/{cs_id}/lists/{list_id}/move-forward",
    response_model=list[ListCandidateOut],
)
def move_list_forward(
    org_id: uuid.UUID, assessment_id: uuid.UUID, cs_id: uuid.UUID, list_id: uuid.UUID,
    session: Session = Depends(get_session),
    current_user: CurrentUser = Depends(require_org_access()),
) -> list[ListCandidateOut]:
    return _act(
        list_evidence.move_forward, org_id, assessment_id, cs_id, list_id, session, current_user
    )


@router.delete(
    "/assessments/{assessment_id}/control-states/{cs_id}/lists/{list_id}",
    response_model=list[ListCandidateOut],
)
def unlink_list(
    org_id: uuid.UUID, assessment_id: uuid.UUID, cs_id: uuid.UUID, list_id: uuid.UUID,
    session: Session = Depends(get_session),
    current_user: CurrentUser = Depends(require_org_access()),
) -> list[ListCandidateOut]:
    return _act(
        list_evidence.unlink, org_id, assessment_id, cs_id, list_id, session, current_user
    )


@router.get("/assessments/{assessment_id}/list-links", response_model=list[ListLinkOut])
def assessment_list_links(
    org_id: uuid.UUID, assessment_id: uuid.UUID, session: Session = Depends(get_session),
) -> list[ListLinkOut]:
    a = session.get(Assessment, assessment_id)
    if a is None or a.org_id != org_id:
        raise HTTPException(status_code=404, detail="Assessment not found")
    return [
        _link_out(v) for v in list_evidence.links(session, org_id, assessment_id=assessment_id)
    ]


@router.get("/list-definitions/{list_id}/evidence-links", response_model=ListEvidenceOut)
def list_evidence_links(
    org_id: uuid.UUID, list_id: uuid.UUID, session: Session = Depends(get_session),
) -> ListEvidenceOut:
    d = session.get(ListDefinition, list_id)
    if d is None or d.org_id != org_id:
        raise HTTPException(status_code=404, detail="List not found")
    v = session.get(ListVersion, d.current_version_id) if d.current_version_id else None
    reason = list_evidence.not_offered_reason(d, v)
    return ListEvidenceOut(
        offered=reason is None, not_offered=reason,
        links=[_link_out(x) for x in list_evidence.links(session, org_id, list_id=list_id)],
    )


@router.get("/list-definitions/{list_id}/link-events", response_model=list[LinkEventOut])
def list_link_events(
    org_id: uuid.UUID, list_id: uuid.UUID, session: Session = Depends(get_session),
) -> list[LinkEventOut]:
    d = session.get(ListDefinition, list_id)
    if d is None or d.org_id != org_id:
        raise HTTPException(status_code=404, detail="List not found")
    return [
        LinkEventOut(
            id=e.id, change=e.change, version_number=e.version_number,
            from_version_numbers=e.from_version_numbers, assessment_id=e.assessment_id,
            assessment_name=e.assessment_name, control_key=e.control_key,
            objective_key=e.objective_key, saved_by=e.saved_by, created_at=e.created_at,
        )
        for e in list_evidence.link_events(session, d.id)
    ]
