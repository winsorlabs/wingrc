"""D.3 second half: daily Liongard sync + asset/user onboarding approval
API -- see liongard_sync.py's module docstring (the persisted-sync-result
design, the pending-by-default semantic, the manual-approval checklist)
and models.py's LiongardSyncResult/AssetApproval docstrings for the full
design.

  POST /orgs/{org_id}/liongard-sync-results/sync-now
      Pull + reconcile + persist -- the manual trigger. Same
      pull_and_reconcile() the scheduled job uses; this is what "Sync now"
      actually calls now, not the older /integrations/liongard/sync/
      dry-run (routers/scope.py), which still exists unmodified for a
      live look without persisting.
  GET  /orgs/{org_id}/liongard-sync-results
      List, newest pull first.
  GET  /orgs/{org_id}/liongard-sync-results/{id}
      Detail: every change row plus the org's checklist (activated Tools).
  POST .../changes/{change_id}/approve
      Accept one new entity into the CUI boundary.
  POST .../changes/{change_id}/reject
      Reject one new entity (in_boundary=False, reason required).

Auth: c3pao_assessor must never approve an asset into the boundary (§5's
hard constraint) -- the ordinary require_write() gate already excludes it,
unchanged, same as every other mutating org-data router. No narrower gate
on top: unlike review_cycles.py's MSP-only open/resolve-flag carve-out,
D.3's own text names the org's Security Officer and IT/MSP contact as the
approvers, which maps to "any org member with write access," the standard
gate -- not an MSP-only restriction.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import liongard_sync
from ..audit import log_event
from ..auth import CurrentUser, actor_type_for, require_org_access, require_write
from ..connectors import liongard as liongard_connector
from ..db import get_session
from ..models import LiongardSyncResult, LiongardSyncResultChange

router = APIRouter(
    prefix="/orgs/{org_id}/liongard-sync-results",
    tags=["liongard-sync"],
    dependencies=[Depends(require_org_access()), Depends(require_write())],
)


class SyncResultOut(BaseModel):
    id: uuid.UUID
    org_id: uuid.UUID
    liongard_environment_id: int
    liongard_environment_name: str | None
    pulled_at: datetime
    status: str
    summary: dict
    warnings: list[str]
    created_at: datetime


class SyncResultChangeOut(BaseModel):
    id: uuid.UUID
    change_type: str
    entity_type: str
    natural_key: str
    field_diffs: dict
    incoming: dict | None
    warnings: list[str]
    resolution: str | None
    resolved_at: datetime | None
    resolved_by: str | None


class ChecklistProductOut(BaseModel):
    product_key: str
    product_name: str


class SyncResultDetailOut(SyncResultOut):
    changes: list[SyncResultChangeOut]
    checklist_products: list[ChecklistProductOut]


class ApproveIn(BaseModel):
    checklist_confirmations: dict[str, bool] = Field(default_factory=dict)


class RejectIn(BaseModel):
    reason: str


class ApprovalOut(BaseModel):
    id: uuid.UUID
    scope_entity_id: uuid.UUID
    decision: str
    decided_by_name: str
    decided_at: datetime
    rejection_reason: str | None


def _sync_result_out(r: LiongardSyncResult) -> SyncResultOut:
    return SyncResultOut(
        id=r.id, org_id=r.org_id, liongard_environment_id=r.liongard_environment_id,
        liongard_environment_name=r.liongard_environment_name, pulled_at=r.pulled_at,
        status=r.status, summary=r.summary or {}, warnings=r.warnings or [],
        created_at=r.created_at,
    )


def _change_out(c: LiongardSyncResultChange) -> SyncResultChangeOut:
    return SyncResultChangeOut(
        id=c.id, change_type=c.change_type, entity_type=c.entity_type,
        natural_key=c.natural_key, field_diffs=c.field_diffs or {}, incoming=c.incoming,
        warnings=c.warnings or [], resolution=c.resolution, resolved_at=c.resolved_at,
        resolved_by=c.resolved_by,
    )


@router.post("/sync-now", response_model=SyncResultOut, status_code=201)
def sync_now(
    org_id: uuid.UUID,
    session: Session = Depends(get_session),
    current_user: CurrentUser = Depends(require_org_access()),
) -> SyncResultOut:
    try:
        pull = liongard_sync.pull_and_reconcile(session, org_id)
    except liongard_sync.LiongardSyncError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except liongard_connector.LiongardAPIError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e

    result = liongard_sync.persist_sync_result(session, org_id=org_id, pull=pull)
    log_event(
        session, org_id=org_id, action="liongard_sync_result.create",
        entity_type="liongard_sync_result", entity_id=result.id,
        after_value={"status": result.status, "summary": result.summary},
        context={"via": "api"}, actor=str(current_user.id),
        actor_type=actor_type_for(current_user),
    )
    session.commit()
    return _sync_result_out(result)


@router.get("", response_model=list[SyncResultOut])
def list_sync_results(org_id: uuid.UUID, session: Session = Depends(get_session)) -> list[SyncResultOut]:
    rows = liongard_sync.list_sync_results(session, org_id)
    session.commit()
    return [_sync_result_out(r) for r in rows]


@router.get("/{sync_result_id}", response_model=SyncResultDetailOut)
def get_sync_result(
    org_id: uuid.UUID, sync_result_id: uuid.UUID, session: Session = Depends(get_session)
) -> SyncResultDetailOut:
    try:
        result = liongard_sync.get_sync_result(session, org_id, sync_result_id)
    except liongard_sync.LiongardSyncError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e

    changes = session.scalars(
        select(LiongardSyncResultChange)
        .where(LiongardSyncResultChange.sync_result_id == sync_result_id)
        .order_by(LiongardSyncResultChange.natural_key)
    ).all()
    products = liongard_sync.activated_products_for_org(session, org_id)
    session.commit()

    return SyncResultDetailOut(
        **_sync_result_out(result).model_dump(),
        changes=[_change_out(c) for c in changes],
        checklist_products=[
            ChecklistProductOut(product_key=p.key, product_name=p.name) for p in products
        ],
    )


@router.post("/{sync_result_id}/changes/{change_id}/approve", response_model=ApprovalOut)
def approve_change(
    org_id: uuid.UUID,
    sync_result_id: uuid.UUID,
    change_id: uuid.UUID,
    body: ApproveIn,
    session: Session = Depends(get_session),
    current_user: CurrentUser = Depends(require_org_access()),
) -> ApprovalOut:
    change = session.get(LiongardSyncResultChange, change_id)
    if change is None or change.sync_result_id != sync_result_id or change.org_id != org_id:
        raise HTTPException(status_code=404, detail="Change not found")

    try:
        approval = liongard_sync.approve_change(
            session, org_id=org_id, change=change,
            decided_by=str(current_user.id), decided_by_name=current_user.display_name,
            checklist_confirmations=body.checklist_confirmations,
        )
    except liongard_sync.LiongardSyncError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    log_event(
        session, org_id=org_id, action="asset_approval.approve",
        entity_type="asset_approval", entity_id=approval.id,
        after_value={
            "scope_entity_id": str(approval.scope_entity_id),
            "natural_key": change.natural_key, "entity_type": change.entity_type,
        },
        context={"via": "api", "sync_result_id": str(sync_result_id)},
        actor=str(current_user.id), actor_type=actor_type_for(current_user),
    )
    session.commit()
    return ApprovalOut(
        id=approval.id, scope_entity_id=approval.scope_entity_id, decision=approval.decision,
        decided_by_name=approval.decided_by_name, decided_at=approval.decided_at,
        rejection_reason=approval.rejection_reason,
    )


@router.post("/{sync_result_id}/changes/{change_id}/reject", response_model=ApprovalOut)
def reject_change(
    org_id: uuid.UUID,
    sync_result_id: uuid.UUID,
    change_id: uuid.UUID,
    body: RejectIn,
    session: Session = Depends(get_session),
    current_user: CurrentUser = Depends(require_org_access()),
) -> ApprovalOut:
    change = session.get(LiongardSyncResultChange, change_id)
    if change is None or change.sync_result_id != sync_result_id or change.org_id != org_id:
        raise HTTPException(status_code=404, detail="Change not found")

    try:
        approval = liongard_sync.reject_change(
            session, org_id=org_id, change=change,
            decided_by=str(current_user.id), decided_by_name=current_user.display_name,
            reason=body.reason,
        )
    except liongard_sync.LiongardSyncError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    log_event(
        session, org_id=org_id, action="asset_approval.reject",
        entity_type="asset_approval", entity_id=approval.id,
        after_value={
            "scope_entity_id": str(approval.scope_entity_id),
            "natural_key": change.natural_key, "entity_type": change.entity_type,
            "rejection_reason": approval.rejection_reason,
        },
        context={"via": "api", "sync_result_id": str(sync_result_id)},
        actor=str(current_user.id), actor_type=actor_type_for(current_user),
    )
    session.commit()
    return ApprovalOut(
        id=approval.id, scope_entity_id=approval.scope_entity_id, decision=approval.decision,
        decided_by_name=approval.decided_by_name, decided_at=approval.decided_at,
        rejection_reason=approval.rejection_reason,
    )
