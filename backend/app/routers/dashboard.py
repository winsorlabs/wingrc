"""Org dashboard — one aggregation endpoint over existing data (G.3).

GET /orgs/{org_id}/assessments/{assessment_id}/dashboard

Nine widgets, one round trip — mirrors OnboardingStatus's existing shape
(GET /orgs/{org_id}/onboarding-status: several independent completion
signals in one payload) rather than nine separate endpoints.

"Recent activity" (the tenth item in the plan's widget table) is
deliberately NOT included here. It reads audit_log, which is gated
msp_admin-only (routers/audit_log.py's require_org_access("msp_admin")).
Folding it into this endpoint would mean either leaking audit data to
every role that can view a dashboard (customer_poc, c3pao_assessor) or
conditionally omitting a field per-role inside an otherwise uniform
payload -- both worse than what the plan itself already says to do:
"reuse as-is." The frontend widget calls the existing
GET /orgs/{org_id}/audit-log?limit=N endpoint directly, unmodified,
keeping its current role gate exactly as it is today.

Every aggregation here fetches raw rows and rolls them up in Python
rather than pushing the rollup into SQL, mirroring engine.py's
recompute_sprs -- same reasoning: the rollup logic (e.g. "a control is
satisfied iff ALL its objectives are met/inherited") is exactly the kind
of thing that's easy to get subtly wrong re-deriving it in a GROUP BY,
and this way it's inspectable Python, not a query plan.
"""
from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session

from ..auth import require_org_access, require_write
from ..db import get_session
from ..models import (
    Assessment,
    AssessmentObjective,
    Contact,
    Control,
    ControlState,
    EvidenceStateLink,
    EvidenceTask,
    EvidenceTaskStateLink,
    Finding,
    ImplementationStatement,
    Organization,
    PoamItem,
    RaciAssignment,
    SprsSnapshot,
)

router = APIRouter(
    prefix="/orgs/{org_id}",
    tags=["dashboard"],
    dependencies=[Depends(require_org_access()), Depends(require_write())],
)

_EVIDENCE_EXPIRING_WINDOW_DAYS = 30
_LIST_WIDGET_CAP = 20
_TRAJECTORY_CAP = 200
_POAM_STATUSES = ("open", "on_track", "delayed", "completed", "cancelled")


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class FamilyHeatmapEntry(BaseModel):
    family: str
    controls_met: int
    controls_total: int


class SprsTrajectoryPoint(BaseModel):
    computed_at: datetime
    score: int


class SprsWidget(BaseModel):
    current_score: int | None
    trajectory: list[SprsTrajectoryPoint]


class StatementProgress(BaseModel):
    draft: int
    reviewed: int
    approved: int
    not_started: int


class EvidenceExpiringItem(BaseModel):
    task_id: uuid.UUID
    title: str
    expires_at: datetime


class ReviewQueueItem(BaseModel):
    control_state_id: uuid.UUID
    control_id: str
    family: str
    objective_key: str


class BlockedObjectiveItem(BaseModel):
    control_state_id: uuid.UUID
    control_id: str
    family: str
    objective_key: str


class RaciBucket(BaseModel):
    contact_id: uuid.UUID | None
    contact_name: str | None
    open_task_count: int


class PoamSummary(BaseModel):
    open: int
    on_track: int
    delayed: int
    completed: int
    cancelled: int


class DashboardOut(BaseModel):
    family_heatmap: list[FamilyHeatmapEntry]
    sprs: SprsWidget
    statement_progress: StatementProgress
    evidence_expiring: list[EvidenceExpiringItem]
    needs_review: list[ReviewQueueItem]
    needs_review_count: int
    blocked_objectives: list[BlockedObjectiveItem]
    blocked_objectives_count: int
    raci_open_tasks: list[RaciBucket]
    poam_summary: PoamSummary


# ---------------------------------------------------------------------------
# Per-widget aggregation
# ---------------------------------------------------------------------------


def _family_heatmap(session: Session, assessment_id: uuid.UUID) -> list[FamilyHeatmapEntry]:
    """A control counts as met iff ALL its objectives (within this
    assessment) have status in {met, inherited} -- same rule
    assessment.py:compute_sprs uses for the SPRS rollup, just grouped by
    family instead of weighted-summed."""
    rows = session.execute(
        select(
            Control.family,
            Control.control_id,
            ControlState.status,
        )
        .select_from(ControlState)
        .join(AssessmentObjective, ControlState.objective_id == AssessmentObjective.id)
        .join(Control, AssessmentObjective.control_id == Control.id)
        .where(ControlState.assessment_id == assessment_id)
    ).all()

    statuses_by_control: dict[tuple[str, str], list[str]] = defaultdict(list)
    for family, control_id, status in rows:
        statuses_by_control[(family, control_id)].append(status)

    totals: dict[str, int] = defaultdict(int)
    met: dict[str, int] = defaultdict(int)
    _satisfied = {"met", "inherited"}
    for (family, _control_id), statuses in statuses_by_control.items():
        totals[family] += 1
        if all(s in _satisfied for s in statuses):
            met[family] += 1

    return [
        FamilyHeatmapEntry(family=family, controls_met=met[family], controls_total=total)
        for family, total in sorted(totals.items())
    ]


def _sprs_widget(session: Session, assessment: Assessment) -> SprsWidget:
    trajectory_rows = session.execute(
        select(SprsSnapshot.computed_at, SprsSnapshot.score)
        .where(SprsSnapshot.assessment_id == assessment.id)
        .order_by(SprsSnapshot.seq.desc())
        .limit(_TRAJECTORY_CAP)
    ).all()
    trajectory = [
        SprsTrajectoryPoint(computed_at=computed_at, score=score)
        for computed_at, score in reversed(trajectory_rows)
    ]
    return SprsWidget(current_score=assessment.sprs_score, trajectory=trajectory)


def _statement_progress(session: Session, assessment_id: uuid.UUID) -> StatementProgress:
    total_objectives = session.scalar(
        select(func.count())
        .select_from(ControlState)
        .where(ControlState.assessment_id == assessment_id)
    ) or 0

    status_rows = session.execute(
        select(ImplementationStatement.status, func.count())
        .select_from(ImplementationStatement)
        .where(ImplementationStatement.assessment_id == assessment_id)
        .group_by(ImplementationStatement.status)
    ).all()
    counts = {"draft": 0, "reviewed": 0, "approved": 0}
    for status, count in status_rows:
        counts[status] = count

    started = counts["draft"] + counts["reviewed"] + counts["approved"]
    return StatementProgress(
        draft=counts["draft"],
        reviewed=counts["reviewed"],
        approved=counts["approved"],
        not_started=max(total_objectives - started, 0),
    )


def _evidence_expiring(session: Session, assessment_id: uuid.UUID) -> list[EvidenceExpiringItem]:
    """Tasks whose evidence expires within the window, regardless of
    collection status -- a `collected` task with expiring evidence still
    needs re-collection, so it belongs on this list same as an `open` one.
    `expires_at` is not populated by any write path yet (no recurrence
    engine -- CLAUDE.md roadmap item 9b), so this list is expected to be
    empty on every deployment today; the query is still correct once that
    lands."""
    cutoff = datetime.now(UTC) + timedelta(days=_EVIDENCE_EXPIRING_WINDOW_DAYS)
    rows = session.execute(
        select(EvidenceTask)
        .where(
            EvidenceTask.assessment_id == assessment_id,
            EvidenceTask.is_archived.is_(False),
            EvidenceTask.expires_at.is_not(None),
            EvidenceTask.expires_at <= cutoff,
        )
        .order_by(EvidenceTask.expires_at.asc())
        .limit(_LIST_WIDGET_CAP)
    ).scalars().all()
    return [
        EvidenceExpiringItem(task_id=t.id, title=t.title, expires_at=t.expires_at)
        for t in rows
        if t.expires_at is not None
    ]


def _review_queue_rows(session: Session, assessment_id: uuid.UUID, status: str):
    return session.execute(
        select(
            ControlState.id,
            Control.control_id,
            Control.family,
            AssessmentObjective.objective_key,
        )
        .select_from(ControlState)
        .join(AssessmentObjective, ControlState.objective_id == AssessmentObjective.id)
        .join(Control, AssessmentObjective.control_id == Control.id)
        .where(ControlState.assessment_id == assessment_id, ControlState.status == status)
        .order_by(Control.family, Control.control_id, AssessmentObjective.objective_key)
    ).all()


def _needs_review(session: Session, assessment_id: uuid.UUID) -> tuple[list[ReviewQueueItem], int]:
    rows = _review_queue_rows(session, assessment_id, "needs_review")
    items = [
        ReviewQueueItem(
            control_state_id=cs_id, control_id=control_id, family=family, objective_key=obj_key
        )
        for cs_id, control_id, family, obj_key in rows[:_LIST_WIDGET_CAP]
    ]
    return items, len(rows)


def _blocked_objectives(
    session: Session, assessment_id: uuid.UUID
) -> tuple[list[BlockedObjectiveItem], int]:
    """pending_evidence with zero EvidenceStateLink rows -- an objective
    the magic loop is waiting on evidence for, where nothing has actually
    been attached yet. Uses NOT EXISTS rather than a LEFT JOIN ... IS NULL:
    a control_state with multiple evidence links would otherwise produce
    duplicate rows that then need de-duplicating, and it's easy to forget
    that step -- exactly the "easiest of the nine to get subtly wrong"
    case the plan flags this widget as."""
    has_evidence = (
        select(EvidenceStateLink.id)
        .where(EvidenceStateLink.control_state_id == ControlState.id)
    )
    rows = session.execute(
        select(
            ControlState.id,
            Control.control_id,
            Control.family,
            AssessmentObjective.objective_key,
        )
        .select_from(ControlState)
        .join(AssessmentObjective, ControlState.objective_id == AssessmentObjective.id)
        .join(Control, AssessmentObjective.control_id == Control.id)
        .where(
            ControlState.assessment_id == assessment_id,
            ControlState.status == "pending_evidence",
            ~exists(has_evidence),
        )
        .order_by(Control.family, Control.control_id, AssessmentObjective.objective_key)
    ).all()
    items = [
        BlockedObjectiveItem(
            control_state_id=cs_id, control_id=control_id, family=family, objective_key=obj_key
        )
        for cs_id, control_id, family, obj_key in rows[:_LIST_WIDGET_CAP]
    ]
    return items, len(rows)


def _raci_open_tasks(session: Session, assessment_id: uuid.UUID) -> list[RaciBucket]:
    """Open tasks bucketed by their RACI-'R' contact, via
    EvidenceTask -> EvidenceTaskStateLink -> ControlState -> RaciAssignment
    (letter='R') -> Contact. A task can touch multiple control_states, so
    it can land in more than one contact's bucket (genuinely shared
    responsibility) -- but only falls into the "unassigned" bucket
    (contact_id=None) if NONE of its linked control_states have an 'R'
    assignment. Degrades to one big "unassigned" bucket entirely until
    G.7 (Roles UI) exists to populate raci_assignment -- expected, not
    an error state."""
    rows = session.execute(
        select(
            EvidenceTask.id.label("task_id"),
            RaciAssignment.contact_id,
            Contact.name.label("contact_name"),
        )
        .select_from(EvidenceTask)
        .join(EvidenceTaskStateLink, EvidenceTaskStateLink.task_id == EvidenceTask.id)
        .join(ControlState, ControlState.id == EvidenceTaskStateLink.control_state_id)
        .outerjoin(
            RaciAssignment,
            (RaciAssignment.control_state_id == ControlState.id)
            & (RaciAssignment.raci_letter == "R"),
        )
        .outerjoin(Contact, Contact.id == RaciAssignment.contact_id)
        .where(
            EvidenceTask.assessment_id == assessment_id,
            EvidenceTask.status == "open",
            EvidenceTask.is_archived.is_(False),
        )
    ).all()

    all_task_ids: set[uuid.UUID] = set()
    task_contact_ids: dict[uuid.UUID, set[uuid.UUID]] = defaultdict(set)
    contact_names: dict[uuid.UUID, str] = {}
    for task_id, contact_id, contact_name in rows:
        all_task_ids.add(task_id)
        if contact_id is not None:
            task_contact_ids[task_id].add(contact_id)
            contact_names[contact_id] = contact_name

    bucket_task_ids: dict[uuid.UUID | None, set[uuid.UUID]] = defaultdict(set)
    for task_id in all_task_ids:
        contacts = task_contact_ids.get(task_id, set())
        if contacts:
            for contact_id in contacts:
                bucket_task_ids[contact_id].add(task_id)
        else:
            bucket_task_ids[None].add(task_id)

    buckets = [
        RaciBucket(
            contact_id=contact_id,
            contact_name=contact_names.get(contact_id) if contact_id else None,
            open_task_count=len(task_ids),
        )
        for contact_id, task_ids in bucket_task_ids.items()
    ]
    buckets.sort(key=lambda b: (b.contact_id is None, (b.contact_name or "").lower()))
    return buckets


def _poam_summary(session: Session, assessment_id: uuid.UUID) -> PoamSummary:
    """Scoped via PoamItem.finding_id -> Finding.assessment_id. POA&M items
    with only a control_id (pre-assessment, not tied to any specific
    assessment run -- see PoamItem's own model comment) are out of scope
    for a per-assessment dashboard and are excluded here."""
    rows = session.execute(
        select(PoamItem.status, func.count())
        .select_from(PoamItem)
        .join(Finding, Finding.id == PoamItem.finding_id)
        .where(Finding.assessment_id == assessment_id)
        .group_by(PoamItem.status)
    ).all()
    counts = {status: 0 for status in _POAM_STATUSES}
    for status, count in rows:
        counts[status] = count
    return PoamSummary(**counts)


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@router.get("/assessments/{assessment_id}/dashboard", response_model=DashboardOut)
def get_dashboard(
    org_id: uuid.UUID,
    assessment_id: uuid.UUID,
    session: Session = Depends(get_session),
) -> DashboardOut:
    org = session.get(Organization, org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found")

    assessment = session.get(Assessment, assessment_id)
    if assessment is None or assessment.org_id != org_id:
        raise HTTPException(status_code=404, detail="Assessment not found")

    needs_review, needs_review_count = _needs_review(session, assessment_id)
    blocked, blocked_count = _blocked_objectives(session, assessment_id)

    return DashboardOut(
        family_heatmap=_family_heatmap(session, assessment_id),
        sprs=_sprs_widget(session, assessment),
        statement_progress=_statement_progress(session, assessment_id),
        evidence_expiring=_evidence_expiring(session, assessment_id),
        needs_review=needs_review,
        needs_review_count=needs_review_count,
        blocked_objectives=blocked,
        blocked_objectives_count=blocked_count,
        raci_open_tasks=_raci_open_tasks(session, assessment_id),
        poam_summary=_poam_summary(session, assessment_id),
    )
