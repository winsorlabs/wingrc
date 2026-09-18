"""D.3 second half: daily Liongard sync + asset/user onboarding approval --
the DB adapter for `liongard_sync_result` and its child tables (models.py
has the full design writeup: the persisted-sync-result discipline, the
supersede policy, and why the checklist is manually-confirmed rather than
Liongard-metrics-derived in this slice).

Same shape as review_cycles.py / sprs_submissions.py: routers and the
scheduler call these, never construct these rows directly, so "what counts
as a fresh pull," "what applying an approval does to scope," and "who gets
notified" each have exactly one implementation.

**The structural fix this module exists for:** before this, a Liongard
sync's diff only ever existed as an HTTP response
(routers/scope.py:liongard_sync_dry_run) -- nothing was persisted, so a
scheduled job (no browser to carry a diff between dry-run and apply) had
no way to produce something a human could review later. `pull_and_
reconcile` below is the exact same pull-plus-diff computation that
function already did, extracted so both the interactive HTTP endpoint and
the new scheduled job (scheduler.py:_liongard_daily_sync) share one
implementation -- never two dry-run computations that could drift.

**Pending-by-default applies to BOTH callers, not just the scheduled
path.** A brand-new Liongard-observed entity's `incoming.status` is
forced to `pending_approval` here, in the shared function -- not only when
persist_sync_result() is used. Giving the interactive "Sync now" flow a
different trust level than the scheduled job (manual apply goes straight
to `active`, only the scheduler's output gates) would mean one connector
producing two different levels of automatic trust depending on who
clicked what, which is exactly the inconsistency "candidates, never
auto-met" (CLAUDE.md) exists to prevent. Workbook imports are unaffected
by this -- a human already reviewed that file at upload time, a different
trust boundary entirely.

**The scheduled job never writes scope_entity.** persist_sync_result()
only ever writes liongard_sync_result/liongard_sync_result_change rows.
scope_entity is touched for the first time only by approve_change/
reject_change below, which require an authenticated human caller -- see
scheduler.py's own module docstring for why this is a hard constraint the
whole scheduler design rests on, not a style preference.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import repo
from .connectors import liongard as liongard_connector
from .domain import CanonicalEntity, ChangeType, EntityStatus, EntityType, ReconcileResult, Source
from .importers.liongard import build_source_ref, devices_to_canonical, identities_to_canonical
from .models import (
    AssetApproval,
    AssetApprovalChecklistItem,
    Contact,
    ContactDocumentationRole,
    LiongardSyncNotification,
    LiongardSyncResult,
    LiongardSyncResultChange,
    OrgLiongardEnvironment,
    OrgProduct,
    Product,
    ScopeEntity,
)
from .reconcile import reconcile

NOTIFY_ROLES = ("security_officer", "it_admin")


class LiongardSyncError(Exception):
    """Raised for any caller-facing failure (no mapping, not found, wrong
    org, wrong state) -- routers translate to the appropriate HTTP status."""


@dataclass
class PullStatusInfo:
    entity_label: str
    total_found: int
    inventory_count: int
    changed_count: int


@dataclass
class PullAndReconcileResult:
    reconcile_result: ReconcileResult
    pull_statuses: list[PullStatusInfo]
    pull_level_warnings: list[str] = field(default_factory=list)
    row_warnings: dict[tuple[str, str], list[str]] = field(default_factory=dict)
    pulled_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    liongard_environment_id: int = 0
    liongard_environment_name: str | None = None


def get_liongard_mapping(session: Session, org_id: uuid.UUID) -> OrgLiongardEnvironment | None:
    return session.scalars(
        select(OrgLiongardEnvironment).where(OrgLiongardEnvironment.org_id == org_id)
    ).first()


def pull_and_reconcile(session: Session, org_id: uuid.UUID) -> PullAndReconcileResult:
    """Pull devices + identities from this org's mapped Liongard
    Environment and reconcile against current scope. No writes. Shared by
    routers/scope.py:liongard_sync_dry_run (interactive) and
    scheduler.py:_liongard_daily_sync (scheduled) -- see this module's own
    docstring for why this must stay the one implementation.

    Raises LiongardSyncError if no Environment is mapped;
    liongard_connector.LiongardAPIError propagates unchanged (both callers
    already handle it -- the router as a 502, the scheduler job as a
    recorded failure, matching JobSpec's "must raise on failure" contract).
    """
    # Deferred import: routers/scope.py owns credential loading (it's the
    # Integrations screen's own concern) and already re-exports it for
    # routers/contacts.py's Liongard-identities import to reuse -- this is
    # the second non-router consumer of that same function, not a new
    # credential-loading implementation.
    from .routers.scope import get_liongard_credential

    mapping = get_liongard_mapping(session, org_id)
    if mapping is None:
        raise LiongardSyncError("No Liongard Environment is mapped to this org yet.")
    config, credential = get_liongard_credential(session)

    pulled_at = datetime.now(UTC)
    source_ref = build_source_ref(
        mapping.liongard_environment_id, mapping.liongard_environment_name, pulled_at.isoformat()
    )

    device_pull = liongard_connector.pull_device_profiles(
        config, credential, mapping.liongard_environment_id
    )
    identity_pull = liongard_connector.pull_identities(
        config, credential, mapping.liongard_environment_id
    )

    devices, device_warnings = devices_to_canonical(device_pull.records, source_ref)
    identities, identity_warnings = identities_to_canonical(identity_pull.records, source_ref)
    incoming = devices + identities

    row_warnings: dict[tuple[str, str], list[str]] = {}
    pull_level_warnings: list[str] = []
    for warnings_by_key in (device_warnings, identity_warnings):
        for key, messages in warnings_by_key.items():
            if key == ("_skipped", "_skipped"):
                pull_level_warnings.extend(messages)
            else:
                row_warnings.setdefault(key, []).extend(messages)

    current = repo.list_entities(session, org_id)
    result = reconcile(current, incoming)

    # Pending-by-default: force every brand-new Liongard-observed entity to
    # pending_approval -- see this module's own docstring for why this
    # applies to both this function's callers, not just the scheduled one.
    #
    # For a CHANGED row, reconcile.py's own diff is attributes-only (never
    # status -- see its module docstring) and a fresh pull's incoming
    # CanonicalEntity defaults to status=ACTIVE regardless of what's
    # actually stored. Left alone, applying a mere attribute update (a
    # hostname rename, say) through the *existing* workbook-apply path
    # would silently promote a still-pending_approval device to active --
    # a real gap, not a hypothetical one, since CHANGED/MISSING rows are
    # explicitly NOT gated by this slice's approve/reject workflow (see
    # LiongardSyncResultChange's own docstring). So: once pending, a
    # device's status only ever changes via an explicit approve/reject
    # decision, never as a side effect of an unrelated attribute sync.
    for change in result.changes:
        if change.incoming is None:
            continue
        if change.change_type == ChangeType.NEW:
            change.incoming.status = EntityStatus.PENDING_APPROVAL
        elif change.current is not None and change.current.status == EntityStatus.PENDING_APPROVAL:
            change.incoming.status = EntityStatus.PENDING_APPROVAL

    changed_by_type: dict[str, int] = {}
    for c in result.changes:
        if c.change_type in (ChangeType.NEW, ChangeType.CHANGED):
            changed_by_type[c.entity_type.value] = changed_by_type.get(c.entity_type.value, 0) + 1

    pull_statuses = [
        PullStatusInfo(
            entity_label="devices",
            total_found=device_pull.total_count,
            inventory_count=device_pull.inventory_count,
            changed_count=changed_by_type.get(EntityType.DEVICE.value, 0),
        ),
        PullStatusInfo(
            entity_label="identities",
            total_found=identity_pull.total_count,
            inventory_count=identity_pull.inventory_count,
            changed_count=changed_by_type.get(EntityType.PERSON.value, 0),
        ),
    ]

    return PullAndReconcileResult(
        reconcile_result=result,
        pull_statuses=pull_statuses,
        pull_level_warnings=pull_level_warnings,
        row_warnings=row_warnings,
        pulled_at=pulled_at,
        liongard_environment_id=mapping.liongard_environment_id,
        liongard_environment_name=mapping.liongard_environment_name,
    )


def _entity_to_jsonb(entity: CanonicalEntity) -> dict:
    return {
        "entity_type": entity.entity_type.value,
        "natural_key": entity.natural_key,
        "attributes": entity.attributes,
        "scope_category": entity.scope_category.value if entity.scope_category else None,
        "status": entity.status.value,
        "in_boundary": entity.in_boundary,
        "source": entity.source.value,
        "source_ref": entity.source_ref,
    }


def _entity_from_jsonb(data: dict) -> CanonicalEntity:
    from .domain import ScopeCategory

    return CanonicalEntity(
        entity_type=EntityType(data["entity_type"]),
        natural_key=data["natural_key"],
        attributes=dict(data.get("attributes") or {}),
        scope_category=ScopeCategory(data["scope_category"]) if data.get("scope_category") else None,
        status=EntityStatus(data["status"]),
        in_boundary=data.get("in_boundary", True),
        source=Source(data["source"]),
        source_ref=data.get("source_ref"),
    )


def persist_sync_result(
    session: Session, *, org_id: uuid.UUID, pull: PullAndReconcileResult,
    job_run_id: uuid.UUID | None = None,
) -> LiongardSyncResult:
    """Writes one liongard_sync_result + its liongard_sync_result_change
    rows from a pull_and_reconcile() output. Marks any still-pending_review
    result for this org 'superseded' first -- see LiongardSyncResult's own
    docstring for why superseding, not merging or queueing.

    job_run_id is None for a manual "Sync now" (still the existing
    interactive dry-run path) -- see LiongardSyncResult's own docstring.
    Never writes scope_entity.
    """
    prior_pending = session.scalars(
        select(LiongardSyncResult).where(
            LiongardSyncResult.org_id == org_id,
            LiongardSyncResult.status == "pending_review",
        )
    ).all()
    for prior in prior_pending:
        prior.status = "superseded"

    changes = [c for c in pull.reconcile_result.changes if c.change_type != ChangeType.UNCHANGED]
    has_new = any(c.change_type == ChangeType.NEW for c in changes)

    sync_result = LiongardSyncResult(
        org_id=org_id,
        job_run_id=job_run_id,
        liongard_environment_id=pull.liongard_environment_id,
        liongard_environment_name=pull.liongard_environment_name,
        pulled_at=pull.pulled_at,
        status="pending_review" if has_new else "no_changes",
        summary=pull.reconcile_result.summary(),
        warnings=pull.pull_level_warnings,
    )
    session.add(sync_result)
    session.flush()

    for c in changes:
        key = (c.entity_type.value, c.natural_key.strip().lower())
        session.add(
            LiongardSyncResultChange(
                sync_result_id=sync_result.id,
                org_id=org_id,
                change_type=c.change_type.value,
                entity_type=c.entity_type.value,
                natural_key=c.natural_key,
                field_diffs={k: list(v) for k, v in c.field_diffs.items()},
                incoming=_entity_to_jsonb(c.incoming) if c.incoming is not None else None,
                warnings=pull.row_warnings.get(key, []),
                resolution="pending" if c.change_type == ChangeType.NEW else None,
            )
        )
    session.flush()
    return sync_result


def list_sync_results(session: Session, org_id: uuid.UUID) -> list[LiongardSyncResult]:
    return list(
        session.scalars(
            select(LiongardSyncResult)
            .where(LiongardSyncResult.org_id == org_id)
            .order_by(LiongardSyncResult.pulled_at.desc())
        )
    )


def get_sync_result(
    session: Session, org_id: uuid.UUID, sync_result_id: uuid.UUID
) -> LiongardSyncResult:
    row = session.get(LiongardSyncResult, sync_result_id)
    if row is None or row.org_id != org_id:
        raise LiongardSyncError("Sync result not found")
    return row


def _maybe_mark_reviewed(session: Session, sync_result: LiongardSyncResult) -> None:
    if sync_result.status != "pending_review":
        return
    still_pending = session.scalars(
        select(LiongardSyncResultChange).where(
            LiongardSyncResultChange.sync_result_id == sync_result.id,
            LiongardSyncResultChange.change_type == "new",
            LiongardSyncResultChange.resolution == "pending",
        )
    ).first()
    if still_pending is None:
        sync_result.status = "reviewed"


# ---------------------------------------------------------------------------
# Checklist (v1: activated Tools, manually confirmed -- see module docstring)
# ---------------------------------------------------------------------------


def activated_products_for_org(session: Session, org_id: uuid.UUID) -> list[Product]:
    """v1 checklist source: the org's ACTIVATED Tools. Deterministic,
    needs no AI, and the OrgProduct/Product concept already exists --
    see models.py:AssetApprovalChecklistItem's own docstring for why
    this is not (yet) verified against real per-device Liongard metrics.
    """
    return list(
        session.scalars(
            select(Product)
            .join(OrgProduct, OrgProduct.product_id == Product.id)
            .where(OrgProduct.org_id == org_id, OrgProduct.status == "active")
            .order_by(Product.name)
        )
    )


# ---------------------------------------------------------------------------
# Approve / reject
# ---------------------------------------------------------------------------


def approve_change(
    session: Session, *, org_id: uuid.UUID, change: LiongardSyncResultChange,
    decided_by: str, decided_by_name: str, checklist_confirmations: dict[str, bool],
) -> AssetApproval:
    """Approves one change_type='new' change: writes scope_entity for the
    first time (repo.upsert, status flipped pending_approval -> active),
    records the acceptance (AssetApproval + AssetApprovalChecklistItem
    snapshot), and resolves the change row. Requires an authenticated
    caller -- routers/liongard_sync.py's require_write() gate, same as
    every other mutating org-data route; c3pao_assessor is excluded by
    that gate unchanged (§5's hard constraint).
    """
    if change.org_id != org_id:
        raise LiongardSyncError("Change not found")
    if change.change_type != "new":
        raise LiongardSyncError("Only a new entity goes through the approval workflow")
    if change.resolution != "pending":
        raise LiongardSyncError(f"Already resolved: {change.resolution}")
    if change.incoming is None:
        raise LiongardSyncError("Change has no incoming data to apply")

    entity = _entity_from_jsonb(change.incoming)
    entity.status = EntityStatus.ACTIVE
    row = repo.upsert(session, org_id, entity)
    session.flush()

    now = datetime.now(UTC)
    approval = AssetApproval(
        org_id=org_id,
        scope_entity_id=row.id,
        sync_result_change_id=change.id,
        decision="approved",
        decided_by=decided_by,
        decided_by_name=decided_by_name,
        decided_at=now,
    )
    session.add(approval)
    session.flush()

    products = activated_products_for_org(session, org_id)
    for p in products:
        session.add(
            AssetApprovalChecklistItem(
                approval_id=approval.id,
                org_id=org_id,
                product_key=p.key,
                product_name=p.name,
                confirmed=bool(checklist_confirmations.get(p.key, False)),
            )
        )

    change.resolution = "approved"
    change.resolved_at = now
    change.resolved_by = decided_by
    session.flush()

    sync_result = session.get(LiongardSyncResult, change.sync_result_id)
    if sync_result is not None:
        _maybe_mark_reviewed(session, sync_result)

    return approval


def reject_change(
    session: Session, *, org_id: uuid.UUID, change: LiongardSyncResultChange,
    decided_by: str, decided_by_name: str, reason: str,
) -> AssetApproval:
    """Rejects one change_type='new' change. Writes scope_entity so the
    device is remembered (status=active -- Liongard does observe it,
    WinGRC has no authority to assert it's gone) with in_boundary=False --
    it exists on the network either way; rejection asserts only "not part
    of the CUI boundary," never "decommissioned." Sticky: re-syncing never
    re-flips this on its own, because the row now exists and reconciles as
    unchanged/changed, never 'new' again, unless a HUMAN later edits it
    back through the ordinary scope UI.
    """
    if change.org_id != org_id:
        raise LiongardSyncError("Change not found")
    if change.change_type != "new":
        raise LiongardSyncError("Only a new entity goes through the approval workflow")
    if change.resolution != "pending":
        raise LiongardSyncError(f"Already resolved: {change.resolution}")
    if change.incoming is None:
        raise LiongardSyncError("Change has no incoming data to apply")
    if not reason or not reason.strip():
        raise LiongardSyncError("A rejection reason is required")

    entity = _entity_from_jsonb(change.incoming)
    entity.status = EntityStatus.ACTIVE
    entity.in_boundary = False
    row = repo.upsert(session, org_id, entity)
    session.flush()

    now = datetime.now(UTC)
    approval = AssetApproval(
        org_id=org_id,
        scope_entity_id=row.id,
        sync_result_change_id=change.id,
        decision="rejected",
        decided_by=decided_by,
        decided_by_name=decided_by_name,
        decided_at=now,
        rejection_reason=reason.strip(),
    )
    session.add(approval)
    session.flush()

    change.resolution = "rejected"
    change.resolved_at = now
    change.resolved_by = decided_by
    session.flush()

    sync_result = session.get(LiongardSyncResult, change.sync_result_id)
    if sync_result is not None:
        _maybe_mark_reviewed(session, sync_result)

    return approval


# ---------------------------------------------------------------------------
# Notification routing
# ---------------------------------------------------------------------------


@dataclass
class NotifyCandidate:
    contact_id: uuid.UUID
    name: str
    email: str


def notify_candidates_for_org(session: Session, org_id: uuid.UUID) -> list[NotifyCandidate]:
    """Contacts holding security_officer or it_admin
    (models.py:ContactDocumentationRole) for this org -- ROADMAP.md D.3's
    own "routing targets already exist" note. Deduplicated by contact (one
    contact holding both roles is notified once, not twice)."""
    rows = session.execute(
        select(Contact)
        .join(ContactDocumentationRole, ContactDocumentationRole.contact_id == Contact.id)
        .where(Contact.org_id == org_id, ContactDocumentationRole.role.in_(NOTIFY_ROLES))
        .distinct()
    ).scalars().all()
    return [NotifyCandidate(contact_id=c.id, name=c.name, email=c.email) for c in rows]


def record_notification_result(
    session: Session, *, notification: LiongardSyncNotification, sent: bool, error: str | None
) -> None:
    """Same notified_at/notification_error discipline as
    review_cycles.record_notification_result -- see that function's own
    docstring for why notified_at is set only once and never cleared by a
    later failure."""
    if sent:
        if notification.notified_at is None:
            notification.notified_at = datetime.now(UTC)
        notification.notification_error = None
    else:
        notification.notification_error = error
    session.flush()
