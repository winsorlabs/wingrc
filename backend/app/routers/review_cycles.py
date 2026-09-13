"""Periodic review & attestation API -- see models.py's review_cycle
docstring and review_cycles.py's module docstring for the full design
(immutable snapshot, non-response as evidence, attest-never-mutates-scope,
and the derived AC.L2-3.1.1[a]/[c] control mapping).

  POST /orgs/{org_id}/review-cycles                          Open a cycle now (MSP-side only)
  GET  /orgs/{org_id}/review-cycles                           List cycles, newest first
  GET  /orgs/{org_id}/review-cycles/{cycle_id}                Detail: items + reviewers + flags
  POST /orgs/{org_id}/review-cycles/{cycle_id}/attest         Record the caller's attestation
  POST .../items/{item_id}/flag                               Flag an item for MSP follow-up
  POST .../flags/{flag_id}/resolve                            Resolve a flag (MSP-side only)

Auth (§1 of the design task): customer_poc is NOT in auth.py's
_READ_ONLY_ROLES -- verified before writing a line of this file, not
assumed (only c3pao_assessor is). customer_poc already has ordinary
write access at the require_write() gate every other org-data router
uses. The actual carve-out this router needs runs the OTHER direction:
without an extra restriction, the standard router-wide
require_org_access() + require_write() gate would let a customer_poc
open cycles and resolve MSP-follow-up flags too, which is exactly the
MSP-only management surface this feature must not hand to a client
reviewer. So: attest/flag/read stay on the router-wide gate (open to any
org member, including customer_poc, matching this codebase's general
"any org member can act on org data" convention); open/resolve-flag get
an explicit narrower require_org_access("msp_admin", "msp_engineer",
"consultant_admin") on top, the same per-route role-narrowing pattern
users.py's grant/revoke-membership endpoints already use. c3pao_assessor
is excluded from every mutating route here by the ordinary require_write()
gate, unchanged.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import review_cycles
from ..audit import log_event
from ..auth import CurrentUser, require_org_access, require_write
from ..db import get_session
from ..models import ReviewCycle, ReviewCycleFlag, ReviewCycleItem, ReviewCycleReviewer
from ..storage import StorageClient, get_storage_client

router = APIRouter(
    prefix="/orgs/{org_id}/review-cycles",
    tags=["review-cycles"],
    dependencies=[Depends(require_org_access()), Depends(require_write())],
)

_MSP_ROLES = ("msp_admin", "msp_engineer", "consultant_admin")


class ReviewCycleItemOut(BaseModel):
    id: uuid.UUID
    subject_type: str
    natural_key: str
    scope_category: str | None
    attributes: dict


class ReviewCycleReviewerOut(BaseModel):
    id: uuid.UUID
    # None whenever the reviewer's user account was later anonymized/
    # hard-deleted (ON DELETE SET NULL) -- reviewer_name/reviewer_email
    # stay populated regardless (denormalized at request time), so the
    # record of who was asked survives that. The frontend uses this only
    # to render "is this me" (Attest button); it is never the identity of
    # record for the attestation itself -- that's the denormalized name/
    # email plus this row's own existence.
    user_id: uuid.UUID | None
    reviewer_name: str
    reviewer_email: str
    reviewer_side: str
    status: str
    requested_at: datetime
    viewed_at: datetime | None
    attested_at: datetime | None
    comment: str | None
    # None until the first successful notification, and never cleared by
    # a later failure -- "were they ever reached." notification_error is
    # the most recent attempt's reason when it failed (unconfigured vs.
    # provider rejection are different operator problems), cleared to
    # None on success. Added after the wl-util-1 false-no_response bug --
    # see models.py:ReviewCycleReviewer's own docstring.
    notified_at: datetime | None
    notification_error: str | None


class ReviewCycleFlagOut(BaseModel):
    id: uuid.UUID
    cycle_item_id: uuid.UUID
    flagged_by_name: str
    reason: str
    created_at: datetime
    resolved_at: datetime | None
    resolved_note: str | None


class ReviewCycleOut(BaseModel):
    id: uuid.UUID
    org_id: uuid.UUID
    opened_at: datetime
    due_at: datetime
    closed_at: datetime | None
    status: str
    cadence_months: int
    opened_by: str


class ReviewCycleDetailOut(ReviewCycleOut):
    items: list[ReviewCycleItemOut]
    reviewers: list[ReviewCycleReviewerOut]
    flags: list[ReviewCycleFlagOut]


class AttestIn(BaseModel):
    comment: str | None = None


class FlagIn(BaseModel):
    reason: str


class ResolveFlagIn(BaseModel):
    note: str


def _cycle_out(c: ReviewCycle) -> ReviewCycleOut:
    return ReviewCycleOut(
        id=c.id, org_id=c.org_id, opened_at=c.opened_at, due_at=c.due_at,
        closed_at=c.closed_at, status=c.status, cadence_months=c.cadence_months,
        opened_by=c.opened_by,
    )


@router.post("", response_model=ReviewCycleOut, status_code=201)
def open_cycle(
    org_id: uuid.UUID,
    db: Session = Depends(get_session),
    current_user: CurrentUser = Depends(require_org_access(*_MSP_ROLES)),
) -> ReviewCycleOut:
    try:
        cycle, reviewers = review_cycles.open_cycle(
            db, org_id=org_id, opened_by=str(current_user.id)
        )
    except review_cycles.ReviewCycleError as e:
        status = 404 if "not found" in str(e).lower() else 409
        raise HTTPException(status_code=status, detail=str(e)) from e

    log_event(
        db, org_id=org_id, action="review_cycle.open", entity_type="review_cycle",
        entity_id=cycle.id,
        after_value={"reviewer_count": len(reviewers), "due_at": cycle.due_at.isoformat()},
        context={"via": "api"}, actor=str(current_user.id),
    )
    db.commit()
    return _cycle_out(cycle)


@router.get("", response_model=list[ReviewCycleOut])
def list_cycles(org_id: uuid.UUID, db: Session = Depends(get_session)) -> list[ReviewCycleOut]:
    rows = db.scalars(
        select(ReviewCycle)
        .where(ReviewCycle.org_id == org_id)
        .order_by(ReviewCycle.opened_at.desc())
    ).all()
    return [_cycle_out(c) for c in rows]


@router.get("/{cycle_id}", response_model=ReviewCycleDetailOut)
def get_cycle(
    org_id: uuid.UUID,
    cycle_id: uuid.UUID,
    db: Session = Depends(get_session),
    current_user: CurrentUser = Depends(require_org_access()),
) -> ReviewCycleDetailOut:
    cycle = db.get(ReviewCycle, cycle_id)
    if cycle is None or cycle.org_id != org_id:
        raise HTTPException(status_code=404, detail="Review cycle not found")

    # Mark viewed if the caller is a reviewer on this cycle -- a no-op
    # otherwise (see review_cycles.record_view's own docstring). Deliberately
    # NOT committed here: app.current_org is set via set_config(..., true)
    # (transaction-LOCAL scope, per require_org_access()'s own docstring) --
    # a commit mid-request ends that transaction and Postgres discards the
    # GUC with it, so every RLS-scoped query after a mid-handler commit would
    # silently see org_id as NULL and match nothing. One commit at the end
    # of the request, after every read, matches every other router in this
    # codebase and is exactly what tests/conftest.py's _app_session wrapper
    # (RESET app.current_org after commit) exists to catch.
    review_cycles.record_view(db, org_id=org_id, cycle_id=cycle_id, user_id=current_user.id)

    items = db.scalars(
        select(ReviewCycleItem).where(ReviewCycleItem.cycle_id == cycle_id)
    ).all()
    reviewers = db.scalars(
        select(ReviewCycleReviewer).where(ReviewCycleReviewer.cycle_id == cycle_id)
    ).all()
    flags: list[ReviewCycleFlag] = []
    if items:
        flags = db.scalars(
            select(ReviewCycleFlag).where(
                ReviewCycleFlag.cycle_item_id.in_([i.id for i in items])
            )
        ).all()
    db.commit()

    return ReviewCycleDetailOut(
        **_cycle_out(cycle).model_dump(),
        items=[
            ReviewCycleItemOut(
                id=i.id, subject_type=i.subject_type, natural_key=i.natural_key,
                scope_category=i.scope_category, attributes=i.attributes,
            )
            for i in items
        ],
        reviewers=[
            ReviewCycleReviewerOut(
                id=r.id, user_id=r.user_id, reviewer_name=r.reviewer_name,
                reviewer_email=r.reviewer_email,
                reviewer_side=r.reviewer_side, status=r.status, requested_at=r.requested_at,
                viewed_at=r.viewed_at, attested_at=r.attested_at, comment=r.comment,
                notified_at=r.notified_at, notification_error=r.notification_error,
            )
            for r in reviewers
        ],
        flags=[
            ReviewCycleFlagOut(
                id=f.id, cycle_item_id=f.cycle_item_id, flagged_by_name=f.flagged_by_name,
                reason=f.reason, created_at=f.created_at, resolved_at=f.resolved_at,
                resolved_note=f.resolved_note,
            )
            for f in flags
        ],
    )


@router.post("/{cycle_id}/attest", response_model=ReviewCycleReviewerOut)
def attest_cycle(
    org_id: uuid.UUID,
    cycle_id: uuid.UUID,
    body: AttestIn,
    db: Session = Depends(get_session),
    storage: StorageClient = Depends(get_storage_client),
    current_user: CurrentUser = Depends(require_org_access()),
) -> ReviewCycleReviewerOut:
    try:
        reviewer = review_cycles.attest(
            db, storage, org_id=org_id, cycle_id=cycle_id, user_id=current_user.id,
            comment=body.comment,
        )
    except review_cycles.ReviewCycleError as e:
        status = 404 if "not found" in str(e).lower() else 409
        raise HTTPException(status_code=status, detail=str(e)) from e

    log_event(
        db, org_id=org_id, action="review_cycle.attest", entity_type="review_cycle",
        entity_id=cycle_id,
        after_value={"reviewer_id": str(reviewer.id), "reviewer_side": reviewer.reviewer_side},
        context={"via": "api"}, actor=str(current_user.id),
    )
    db.commit()
    return ReviewCycleReviewerOut(
        id=reviewer.id, user_id=reviewer.user_id, reviewer_name=reviewer.reviewer_name,
        reviewer_email=reviewer.reviewer_email,
        reviewer_side=reviewer.reviewer_side, status=reviewer.status,
        requested_at=reviewer.requested_at, viewed_at=reviewer.viewed_at,
        attested_at=reviewer.attested_at, comment=reviewer.comment,
        notified_at=reviewer.notified_at, notification_error=reviewer.notification_error,
    )


@router.post("/{cycle_id}/items/{item_id}/flag", response_model=ReviewCycleFlagOut, status_code=201)
def flag_item(
    org_id: uuid.UUID,
    cycle_id: uuid.UUID,
    item_id: uuid.UUID,
    body: FlagIn,
    db: Session = Depends(get_session),
    current_user: CurrentUser = Depends(require_org_access()),
) -> ReviewCycleFlagOut:
    reviewer = db.scalars(
        select(ReviewCycleReviewer).where(
            ReviewCycleReviewer.cycle_id == cycle_id, ReviewCycleReviewer.user_id == current_user.id
        )
    ).first()
    try:
        flag = review_cycles.flag_item(
            db, org_id=org_id, cycle_item_id=item_id,
            reviewer_id=reviewer.id if reviewer else None,
            flagged_by_name=current_user.display_name, reason=body.reason,
        )
    except review_cycles.ReviewCycleError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e

    log_event(
        db, org_id=org_id, action="review_cycle.flag", entity_type="review_cycle_item",
        entity_id=item_id, after_value={"reason": body.reason}, context={"via": "api"},
        actor=str(current_user.id),
    )
    db.commit()
    return ReviewCycleFlagOut(
        id=flag.id, cycle_item_id=flag.cycle_item_id, flagged_by_name=flag.flagged_by_name,
        reason=flag.reason, created_at=flag.created_at, resolved_at=flag.resolved_at,
        resolved_note=flag.resolved_note,
    )


@router.post("/{cycle_id}/flags/{flag_id}/resolve", response_model=ReviewCycleFlagOut)
def resolve_flag(
    org_id: uuid.UUID,
    cycle_id: uuid.UUID,
    flag_id: uuid.UUID,
    body: ResolveFlagIn,
    db: Session = Depends(get_session),
    current_user: CurrentUser = Depends(require_org_access(*_MSP_ROLES)),
) -> ReviewCycleFlagOut:
    try:
        flag = review_cycles.resolve_flag(db, org_id=org_id, flag_id=flag_id, note=body.note)
    except review_cycles.ReviewCycleError as e:
        status = 404 if "not found" in str(e).lower() else 409
        raise HTTPException(status_code=status, detail=str(e)) from e

    log_event(
        db, org_id=org_id, action="review_cycle.flag_resolved", entity_type="review_cycle_flag",
        entity_id=flag.id, after_value={"resolved_note": body.note}, context={"via": "api"},
        actor=str(current_user.id),
    )
    db.commit()
    return ReviewCycleFlagOut(
        id=flag.id, cycle_item_id=flag.cycle_item_id, flagged_by_name=flag.flagged_by_name,
        reason=flag.reason, created_at=flag.created_at, resolved_at=flag.resolved_at,
        resolved_note=flag.resolved_note,
    )
