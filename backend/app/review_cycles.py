"""Periodic review & attestation -- the DB adapter for `review_cycle` and
its four child tables (models.py has the full design writeup: the
immutable-snapshot discipline, non-response as evidence, and the
attest-never-mutates-scope boundary).

Every function here is a thin DB adapter, same shape as engine.py's /
sprs_submissions.py's from the prior two slices -- routers and the
scheduler call these, never construct review_cycle* rows directly, so
"what counts as due for a reminder," "what closing a cycle does," and
"what evidence it produces" each have exactly one implementation.

Control mapping (AC.L2-3.1.1[a]/[c]) -- DERIVED, not recalled, from the
seeded catalog (app/seeds/cmmc_l2.yaml) and cross-checked against
catalog.py's existing AUTHORIZED_USERS/AUTHORIZED_DEVICES ListViews
(both already `control_ids=("AC.L2-3.1.1",)`, predating this slice):

    AC.L2-3.1.1 "Limit system access to authorized users, processes
    acting on behalf of authorized users, and devices (including other
    systems)."
      [a] "Authorized users are identified." (satisfaction_type=
          document_list -- "a scope-graph-generated list", per
          AssessmentObjective's own docstring)
      [c] "Devices (and other systems) authorized to connect to the
          system are identified." (document_list)

Objectives [b]/[d]/[e]/[f] are deliberately NOT mapped: [b] is about
*processes* acting on behalf of users, a third subject type this slice's
own scope explicitly excludes (design task §8); [d]/[e]/[f] are
type=product ("system access IS limited to...", i.e. real-time
enforcement by a configured tool) -- a periodic human review attests to
*identification*, not to enforcement, so it evidences [a]/[c] only.
"""

from __future__ import annotations

import html
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from .models import (
    AssessmentObjective,
    Control,
    ControlState,
    Evidence,
    EvidenceStateLink,
    Organization,
    ReviewCycle,
    ReviewCycleFlag,
    ReviewCycleItem,
    ReviewCycleReminderLog,
    ReviewCycleReviewer,
    ScopeEntity,
)
from .storage import StorageClient

MAPPED_CONTROL_ID = "AC.L2-3.1.1"
MAPPED_OBJECTIVE_KEYS = {"user": "a", "device": "c"}

# How long reviewers have to respond to one already-open cycle, and when
# reminders fire within that window. Fixed, not per-org configurable --
# only the OPENING cadence (Organization.review_cadence_months) is the
# contractual fact that varies client to client; the response window for
# one open cycle is an operational constant.
RESPONSE_WINDOW_DAYS = 21
REMINDER_DAYS = (7, 14)  # reminder_number 1 at day 7, 2 at day 14 of the window


class ReviewCycleError(Exception):
    """Raised for any caller-facing failure (not found, wrong org, wrong
    state) -- routers translate to the appropriate HTTP status."""


def _snapshot_items(session: Session, *, cycle_id: uuid.UUID, org_id: uuid.UUID) -> int:
    rows = session.scalars(
        select(ScopeEntity).where(
            ScopeEntity.org_id == org_id,
            ScopeEntity.entity_type.in_(("person", "device")),
            ScopeEntity.status == "active",
            ScopeEntity.in_boundary.is_(True),
        )
    ).all()
    for e in rows:
        session.add(
            ReviewCycleItem(
                id=uuid.uuid4(),
                cycle_id=cycle_id,
                org_id=org_id,
                subject_type="user" if e.entity_type == "person" else "device",
                scope_entity_id=e.id,
                natural_key=e.natural_key,
                scope_category=e.scope_category,
                attributes=dict(e.attributes or {}),
            )
        )
    return len(rows)


def _snapshot_reviewers(
    session: Session, *, cycle_id: uuid.UUID, org_id: uuid.UUID
) -> list[ReviewCycleReviewer]:
    candidates = session.execute(
        text(
            "SELECT user_id, email, display_name, reviewer_side "
            "FROM auth.org_reviewer_candidates(:org_id)"
        ),
        {"org_id": org_id},
    ).all()
    created: list[ReviewCycleReviewer] = []
    for c in candidates:
        reviewer = ReviewCycleReviewer(
            id=uuid.uuid4(),
            cycle_id=cycle_id,
            org_id=org_id,
            user_id=c.user_id,
            reviewer_name=c.display_name,
            reviewer_email=c.email,
            reviewer_side=c.reviewer_side,
        )
        session.add(reviewer)
        created.append(reviewer)
    return created


def open_cycle(
    session: Session, *, org_id: uuid.UUID, opened_by: str
) -> tuple[ReviewCycle, list[ReviewCycleReviewer]]:
    """Opens a new cycle: snapshots every in-scope user/device
    (status=active, in_boundary=true) and every eligible reviewer at this
    exact moment. Does not send anything -- the caller (router or
    scheduler job) owns emailing, matching this codebase's established
    split (engine.py doesn't call log_event; the router does).

    opened_by is 'scheduler' or a user id string (manual open).
    """
    org = session.get(Organization, org_id)
    if org is None:
        raise ReviewCycleError("Organization not found")

    now = datetime.now(UTC)
    cycle = ReviewCycle(
        id=uuid.uuid4(),
        org_id=org_id,
        opened_at=now,
        due_at=now + timedelta(days=RESPONSE_WINDOW_DAYS),
        cadence_months=org.review_cadence_months,
        opened_by=opened_by,
    )
    session.add(cycle)
    session.flush()

    _snapshot_items(session, cycle_id=cycle.id, org_id=org_id)
    reviewers = _snapshot_reviewers(session, cycle_id=cycle.id, org_id=org_id)
    session.flush()
    return cycle, reviewers


def record_view(
    session: Session, *, org_id: uuid.UUID, cycle_id: uuid.UUID, user_id: uuid.UUID
) -> ReviewCycleReviewer | None:
    """First GET of the cycle by this reviewer -- requested -> viewed.
    A no-op (not an error) if already viewed/attested, or if this user
    isn't a reviewer on this cycle (e.g. an msp_admin just browsing who
    wasn't snapshotted as a reviewer -- browsing isn't reviewing)."""
    reviewer = session.scalars(
        select(ReviewCycleReviewer).where(
            ReviewCycleReviewer.cycle_id == cycle_id,
            ReviewCycleReviewer.org_id == org_id,
            ReviewCycleReviewer.user_id == user_id,
        )
    ).first()
    if reviewer is None or reviewer.status != "requested":
        return reviewer
    reviewer.status = "viewed"
    reviewer.viewed_at = datetime.now(UTC)
    session.flush()
    return reviewer


def attest(
    session: Session,
    storage: StorageClient,
    *,
    org_id: uuid.UUID,
    cycle_id: uuid.UUID,
    user_id: uuid.UUID,
    comment: str | None,
) -> ReviewCycleReviewer:
    """Records ONE reviewer's attestation -- the authenticated caller's
    identity, never a submitted name field (the reviewer row was already
    snapshotted at open time from auth.org_reviewer_candidates(); this
    only flips status and stamps attested_at/comment on that existing
    row). If every reviewer on the cycle has now attested, closes it
    immediately as 'completed' rather than waiting for the next
    scheduler tick -- see close_cycle.
    """
    cycle = session.get(ReviewCycle, cycle_id)
    if cycle is None or cycle.org_id != org_id:
        raise ReviewCycleError("Review cycle not found")
    if cycle.status != "open":
        raise ReviewCycleError(f"Cannot attest: cycle status is {cycle.status!r}")

    reviewer = session.scalars(
        select(ReviewCycleReviewer).where(
            ReviewCycleReviewer.cycle_id == cycle_id,
            ReviewCycleReviewer.org_id == org_id,
            ReviewCycleReviewer.user_id == user_id,
        )
    ).first()
    if reviewer is None:
        raise ReviewCycleError("You are not a reviewer on this cycle")
    if reviewer.status == "attested":
        raise ReviewCycleError("Already attested")

    now = datetime.now(UTC)
    reviewer.status = "attested"
    reviewer.attested_at = now
    if reviewer.viewed_at is None:
        reviewer.viewed_at = now
    reviewer.comment = comment
    session.flush()

    all_reviewers = session.scalars(
        select(ReviewCycleReviewer).where(ReviewCycleReviewer.cycle_id == cycle_id)
    ).all()
    if all(r.status == "attested" for r in all_reviewers):
        close_cycle(session, storage, cycle=cycle, status="completed")

    return reviewer


def flag_item(
    session: Session, *, org_id: uuid.UUID, cycle_item_id: uuid.UUID, reviewer_id: uuid.UUID | None,
    flagged_by_name: str, reason: str,
) -> ReviewCycleFlag:
    """A reviewer's "this doesn't look right" -- MSP follow-up only, NEVER
    a scope_entity mutation. Actually changing scope still goes through
    the existing reconcile()/dry-run/apply path, untouched by this."""
    item = session.get(ReviewCycleItem, cycle_item_id)
    if item is None or item.org_id != org_id:
        raise ReviewCycleError("Review cycle item not found")
    flag = ReviewCycleFlag(
        id=uuid.uuid4(),
        cycle_item_id=cycle_item_id,
        org_id=org_id,
        flagged_by_reviewer_id=reviewer_id,
        flagged_by_name=flagged_by_name,
        reason=reason,
    )
    session.add(flag)
    session.flush()
    return flag


def resolve_flag(
    session: Session, *, org_id: uuid.UUID, flag_id: uuid.UUID, note: str
) -> ReviewCycleFlag:
    flag = session.get(ReviewCycleFlag, flag_id)
    if flag is None or flag.org_id != org_id:
        raise ReviewCycleError("Flag not found")
    if flag.resolved_at is not None:
        raise ReviewCycleError("Flag already resolved")
    flag.resolved_at = datetime.now(UTC)
    flag.resolved_note = note
    session.flush()
    return flag


# ---------------------------------------------------------------------------
# Closing a cycle -> the evidence output
# ---------------------------------------------------------------------------


def _esc(s: str | None) -> str:
    return html.escape(s or "")


def _render_attestation_html(
    cycle: ReviewCycle, items: list[ReviewCycleItem], reviewers: list[ReviewCycleReviewer]
) -> str:
    """The evidence document itself -- must show who, when, what was
    shown, and (implicitly, by being generated once at close time and
    never re-rendered) that it hasn't changed since. Plain, self-
    contained HTML, same spirit as bundle_service.py's rendering but not
    sharing code with it (review_cycles.py must not import
    bundle_service, which will import THIS module's output instead --
    see routers/review_cycles.py / bundle_service.py wiring)."""
    item_rows = "".join(
        f"<tr><td>{_esc(i.subject_type)}</td><td>{_esc(i.natural_key)}</td>"
        f"<td>{_esc(i.scope_category)}</td></tr>"
        for i in items
    )
    reviewer_rows = "".join(
        f"<tr><td>{_esc(r.reviewer_name)}</td><td>{_esc(r.reviewer_email)}</td>"
        f"<td>{_esc(r.reviewer_side)}</td><td>{_esc(r.status)}</td>"
        f"<td>{r.requested_at.isoformat()}</td>"
        f"<td>{r.attested_at.isoformat() if r.attested_at else ''}</td>"
        f"<td>{_esc(r.comment)}</td></tr>"
        for r in reviewers
    )
    outcome = {
        "completed": "All reviewers attested.",
        "closed_unattested": "Cycle closed at its due date without full attestation "
        "-- see reviewer status below for who did and did not respond.",
    }.get(cycle.status, cycle.status)
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<style>body{font-family:sans-serif;font-size:13px}"
        "table{border-collapse:collapse;width:100%;margin:1em 0}"
        "th,td{border:1px solid #ccc;padding:4px 8px;text-align:left}</style>"
        "</head><body>"
        "<h1>Periodic Review of Authorized Users &amp; Devices</h1>"
        f"<p>Cycle opened {cycle.opened_at.isoformat()}, due {cycle.due_at.isoformat()}, "
        f"closed {cycle.closed_at.isoformat() if cycle.closed_at else ''}. "
        f"Review cadence: every {cycle.cadence_months} month(s) (organization-defined, "
        f"AC.L2-3.1.1[a]/[c]). Outcome: {_esc(outcome)}</p>"
        "<h2>Items presented for review</h2>"
        f"<table><tr><th>Type</th><th>Identifier</th><th>Category</th></tr>{item_rows}</table>"
        "<h2>Reviewers</h2>"
        "<table><tr><th>Name</th><th>Email</th><th>Side</th><th>Status</th>"
        f"<th>Requested</th><th>Attested</th><th>Comment</th></tr>{reviewer_rows}</table>"
        "</body></html>"
    )


def _mapped_control_states(session: Session, *, org_id: uuid.UUID) -> list[ControlState]:
    """ControlState rows for AC.L2-3.1.1[a]/[c], across every currently
    in_progress assessment for this org -- evidence attaches to whatever
    assessment is actually active, same as any other evidence collected
    today. If no assessment is in_progress, returns []; the Evidence row
    is still created (org-scoped, no link required), just unlinked until
    a human links it via the existing evidence UI."""
    from .models import Assessment  # local import: avoids a cycle with engine.py's own imports

    objective_ids = session.scalars(
        select(AssessmentObjective.id)
        .join(Control, Control.id == AssessmentObjective.control_id)
        .where(
            Control.control_id == MAPPED_CONTROL_ID,
            AssessmentObjective.objective_key.in_(MAPPED_OBJECTIVE_KEYS.values()),
        )
    ).all()
    if not objective_ids:
        return []
    assessment_ids = session.scalars(
        select(Assessment.id).where(Assessment.org_id == org_id, Assessment.status == "in_progress")
    ).all()
    if not assessment_ids:
        return []
    return list(
        session.scalars(
            select(ControlState).where(
                ControlState.assessment_id.in_(assessment_ids),
                ControlState.objective_id.in_(objective_ids),
            )
        )
    )


def close_cycle(
    session: Session, storage: StorageClient, *, cycle: ReviewCycle, status: str
) -> Evidence:
    """Closes a cycle (status: 'completed' or 'closed_unattested'),
    stamps every still-`requested`/`viewed` reviewer to `no_response`
    (the non-response-as-evidence record §3 calls the most valuable part
    of this feature -- never left ambiguous, never silently dropped),
    and produces the Evidence row: an HTML document, kind='file',
    artifact_type='attestation' (see migration 0045 for why this needed
    a new type), linked to AC.L2-3.1.1[a]/[c]'s control_state rows on
    every currently in_progress assessment for this org.
    """
    now = datetime.now(UTC)
    reviewers = list(
        session.scalars(select(ReviewCycleReviewer).where(ReviewCycleReviewer.cycle_id == cycle.id))
    )
    for r in reviewers:
        if r.status in ("requested", "viewed"):
            r.status = "no_response"

    items = list(
        session.scalars(select(ReviewCycleItem).where(ReviewCycleItem.cycle_id == cycle.id))
    )

    cycle.status = status
    cycle.closed_at = now
    session.flush()

    body = _render_attestation_html(cycle, items, reviewers).encode()
    evidence = Evidence(
        id=uuid.uuid4(),
        org_id=cycle.org_id,
        title=f"Periodic user/device review -- {cycle.opened_at.date().isoformat()}",
        description=(
            f"Attestation record for the review cycle opened {cycle.opened_at.date().isoformat()} "
            f"({status})."
        ),
        kind="file",
        artifact_type="attestation",
        mime_type="text/html",
        file_size_bytes=len(body),
        collected_at=now,
    )
    storage_key = f"{cycle.org_id}/evidence/{evidence.id}/{evidence.id}.html"
    storage.upload_file(storage_key, body, "text/html")
    evidence.storage_key = storage_key
    session.add(evidence)
    session.flush()

    for cs in _mapped_control_states(session, org_id=cycle.org_id):
        session.add(EvidenceStateLink(evidence_id=evidence.id, control_state_id=cs.id))
    session.flush()

    return evidence


# ---------------------------------------------------------------------------
# Reminder idempotency
# ---------------------------------------------------------------------------


def due_reminder_number(
    reviewer: ReviewCycleReviewer, *, now: datetime, already_sent: set[int]
) -> int | None:
    """Which reminder (1 or 2) is due for this reviewer right now, or
    None. Only relevant for a reviewer still requested/viewed -- an
    attested reviewer needs no reminder."""
    if reviewer.status in ("attested",):
        return None
    elapsed_days = (now - reviewer.requested_at).days
    for number, threshold in enumerate(REMINDER_DAYS, start=1):
        if elapsed_days >= threshold and number not in already_sent:
            return number
    return None


def record_reminder_sent(
    session: Session, *, cycle_id: uuid.UUID, reviewer_id: uuid.UUID, reminder_number: int
) -> None:
    session.add(
        ReviewCycleReminderLog(
            id=uuid.uuid4(),
            cycle_id=cycle_id,
            reviewer_id=reviewer_id,
            reminder_number=reminder_number,
        )
    )
    session.flush()
