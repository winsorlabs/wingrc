"""Documents as evidence -- roadmap item X (document_evidence.py holds the
rules).

  GET    /orgs/{org}/assessments/{a}/control-states/{cs}/documents
             The org's documents tagged with this objective, and any
             attached to it: offered, or why not; the version attached
             here, and whether it is superseded.
  POST   /orgs/{org}/assessments/{a}/control-states/{cs}/documents/{document_id}
             Attach the document's approved version.
  POST   …/documents/{document_id}/move-forward
             Move this objective's link onto the approved version (a new
             link; the old one is archived, never rewritten).
  DELETE …/documents/{document_id}
             Remove the document from this objective's evidence.

None of these reads or writes control_state.status or the SPRS score.
Read-only roles (c3pao_assessor) see the GET and make, move or break
nothing -- require_write, router-wide.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import document_evidence
from ..auth import require_org_access, require_write
from ..db import get_session
from ..models import Assessment, ControlState

router = APIRouter(
    prefix="/orgs/{org_id}",
    tags=["document-evidence"],
    dependencies=[Depends(require_org_access()), Depends(require_write())],
)


class DocumentCandidateOut(BaseModel):
    document_id: uuid.UUID
    doc_id: str
    title: str
    approved_version_id: uuid.UUID | None
    approved_version_number: int | None
    offered: bool
    not_offered: str | None
    linked_version_id: uuid.UUID | None
    linked_version_number: int | None
    linked_superseded: bool
    evidence_id: uuid.UUID | None
    superseded: str | None


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


def _out(session: Session, org_id: uuid.UUID, cs: ControlState) -> list[DocumentCandidateOut]:
    return [
        DocumentCandidateOut(
            document_id=c.document_id, doc_id=c.doc_id, title=c.title,
            approved_version_id=c.approved_version_id,
            approved_version_number=c.approved_version_number,
            offered=c.not_offered is None, not_offered=c.not_offered,
            linked_version_id=c.linked_version_id, linked_version_number=c.linked_version_number,
            linked_superseded=c.linked_superseded, evidence_id=c.evidence_id,
            superseded=c.superseded,
        )
        for c in document_evidence.candidates(session, org_id, cs)
    ]


@router.get(
    "/assessments/{assessment_id}/control-states/{cs_id}/documents",
    response_model=list[DocumentCandidateOut],
)
def state_documents(
    org_id: uuid.UUID, assessment_id: uuid.UUID, cs_id: uuid.UUID,
    session: Session = Depends(get_session),
) -> list[DocumentCandidateOut]:
    return _out(session, org_id, _state(session, org_id, assessment_id, cs_id))


def _act(fn, org_id, assessment_id, cs_id, document_id, session) -> list[DocumentCandidateOut]:
    cs = _state(session, org_id, assessment_id, cs_id)
    try:
        fn(session, org_id, cs, document_id)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except document_evidence.DocumentLinkRefused as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    session.commit()
    return _out(session, org_id, cs)


@router.post(
    "/assessments/{assessment_id}/control-states/{cs_id}/documents/{document_id}",
    response_model=list[DocumentCandidateOut], status_code=201,
)
def link_document(
    org_id: uuid.UUID, assessment_id: uuid.UUID, cs_id: uuid.UUID, document_id: uuid.UUID,
    session: Session = Depends(get_session),
) -> list[DocumentCandidateOut]:
    return _act(document_evidence.link, org_id, assessment_id, cs_id, document_id, session)


@router.post(
    "/assessments/{assessment_id}/control-states/{cs_id}/documents/{document_id}/move-forward",
    response_model=list[DocumentCandidateOut],
)
def move_document_forward(
    org_id: uuid.UUID, assessment_id: uuid.UUID, cs_id: uuid.UUID, document_id: uuid.UUID,
    session: Session = Depends(get_session),
) -> list[DocumentCandidateOut]:
    return _act(
        document_evidence.move_forward, org_id, assessment_id, cs_id, document_id, session
    )


@router.delete(
    "/assessments/{assessment_id}/control-states/{cs_id}/documents/{document_id}",
    response_model=list[DocumentCandidateOut],
)
def unlink_document(
    org_id: uuid.UUID, assessment_id: uuid.UUID, cs_id: uuid.UUID, document_id: uuid.UUID,
    session: Session = Depends(get_session),
) -> list[DocumentCandidateOut]:
    return _act(document_evidence.unlink, org_id, assessment_id, cs_id, document_id, session)
