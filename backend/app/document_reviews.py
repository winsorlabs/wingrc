"""Document approval and review cadence -- roadmap N.3.

Two halves, deliberately separated:

* **Pure cadence logic** (`add_months`, `review_state`) -- no session, no
  ORM, unit-testable with no database, so it runs in CI's no-database job.
  Whether a policy is overdue is arithmetic, and arithmetic this
  consequential should be testable without infrastructure.
* **DB adapter** (everything below `DocumentReviewError`) -- the same thin
  shape `engine.py`, `review_cycles.py` and `liongard_sync.py` use. Routers
  and the scheduler call these; nothing constructs `DocumentApproval` rows
  directly, so "what re-approval does" has exactly one implementation.

**Overdue is derived, never stored.** There is no `next_due_at` column and
no `overdue` flag. `review_state` computes it from the newest approval plus
`Document.cadence_months` on every read. Storing it would create a second
place that looks authoritative and would go stale the moment an operator
changed the cadence -- a document moved from annual to semi-annual review
should become due six months after its last approval, not on a date
computed under the old policy.

**Overdue flags; it never invalidates.** Passing the cadence changes
nothing: the `Evidence` row stays, its `EvidenceStateLink`s stay
unarchived, `control_state.status` is untouched, and the SPRS score does not
move. This is the same refusal that governs `sprs_snapshot` (never
rewritten), baseline versioning (a reimport never touches an activated
tenant) and bundles (frozen at export). Auto-detaching evidence on a date
would silently change what an assessment rests on with no human decision
behind it. An overdue policy review is a *finding* -- something a person
looks at -- not something a clock enacts.

**Why this does not reuse `review_cycle` and its child tables**, per the
plan's cross-cutting rule 6 ("reuse it or explain why it doesn't fit"). The
*machinery* is reused: `email_service.send`, the digest shape, and the
`notified_at`-set-once / `notification_error`-always-current discipline are
lifted directly, and `record_notification_result` below is deliberately the
same function three times over rather than a cleverer abstraction. The
*tables* are not, for two concrete reasons:

1. `ReviewCycleReviewer` is `cycle_id`-scoped to a `review_cycle` row, and
   a `review_cycle` is the org's users-and-devices attestation cycle with
   its own snapshot items, response window and attestation document.
   Reusing it for documents would mean inventing synthetic cycles that
   attest to nothing -- distorting it, which the plan says not to do.
2. Its reviewers come from `auth.org_reviewer_candidates()` and are
   **Users** (`user_id`, login accounts). Document approvers are
   **Contacts**, often with no login at all (see `DocumentApproval`'s
   docstring). The routing target is a different kind of entity.

D.3 hit exactly this and resolved it the same way: `LiongardSyncNotification`
is a separate table following the identical shape rather than a contortion
of `ReviewCycleReviewer`. This follows that precedent.
"""

from __future__ import annotations

import calendar
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import (
    Contact,
    ContactDocumentationRole,
    Document,
    DocumentApproval,
    DocumentReviewNotification,
    DocumentVersion,
)

# Contacts notified about documents coming due, beyond each document's own
# named approver. Same two roles D.3 routes asset approvals to, and the same
# reason: they are the roles an MSP actually staffs, and they exist already.
NOTIFY_ROLES = ("security_officer", "it_admin")

# How long before the due date a document starts reporting `due_soon`. A
# warning window, not a grace period -- `overdue` still begins exactly on
# the due date. Thirty days so a monthly-cadence document does not spend its
# entire life in warning state.
DUE_SOON_DAYS = 30

# The four values `review_state` can return.
NEVER_APPROVED = "never_approved"
CURRENT = "current"
DUE_SOON = "due_soon"
OVERDUE = "overdue"


def add_months(moment: datetime, months: int) -> datetime:
    """`moment` plus `months` calendar months, clamping the day.

    Hand-rolled rather than adding `python-dateutil` for ten lines of
    arithmetic: this product ships air-gapped, and every dependency is
    something an operator has to vendor.

    Clamping matters and is tested: 31 January plus one month is 28
    February (29 in a leap year), not 3 March. A policy approved on the
    31st must not drift forward a few days every year, and it must not
    raise.
    """
    if months == 0:
        return moment
    zero_based = moment.month - 1 + months
    year = moment.year + zero_based // 12
    month = zero_based % 12 + 1
    day = min(moment.day, calendar.monthrange(year, month)[1])
    return moment.replace(year=year, month=month, day=day)


@dataclass(frozen=True)
class ReviewState:
    """Where one document stands against its cadence, at one moment.

    Frozen and computed, never persisted -- see the module docstring on why
    there is no stored `next_due_at`.
    """

    status: str
    last_approved_at: datetime | None
    next_due_at: datetime | None
    days_until_due: int | None

    @property
    def needs_attention(self) -> bool:
        """True for anything an operator should act on.

        `never_approved` counts: a document with no approval at all is not
        "fine until its first due date" -- it has never been through the
        control the cadence exists to evidence.
        """
        return self.status in (NEVER_APPROVED, DUE_SOON, OVERDUE)


def review_state(
    *,
    last_approved_at: datetime | None,
    cadence_months: int,
    now: datetime,
) -> ReviewState:
    """Pure: the cadence verdict for one document.

    `last_approved_at` is the newest approval of ANY kind for the document
    -- initial or reaffirmation, across versions. Publishing a new version
    resets the clock because that is a fresh approval; so does reaffirming
    an unchanged one. Both are "a named person decided this is current on
    this date", which is the fact the cadence tracks.

    A `cadence_months` of 0 or less would make every document permanently
    overdue; it is treated as "no cadence" and reports `current`, because
    silently flagging every document in an org is worse than honouring an
    odd configuration. The column defaults to 12 and the router validates
    it, so this is a floor, not a supported mode.
    """
    if last_approved_at is None:
        return ReviewState(
            status=NEVER_APPROVED,
            last_approved_at=None,
            next_due_at=None,
            days_until_due=None,
        )
    if cadence_months <= 0:
        return ReviewState(
            status=CURRENT,
            last_approved_at=last_approved_at,
            next_due_at=None,
            days_until_due=None,
        )

    next_due = add_months(last_approved_at, cadence_months)
    remaining = (next_due - now).days
    if now >= next_due:
        status = OVERDUE
    elif next_due - now <= timedelta(days=DUE_SOON_DAYS):
        status = DUE_SOON
    else:
        status = CURRENT
    return ReviewState(
        status=status,
        last_approved_at=last_approved_at,
        next_due_at=next_due,
        days_until_due=remaining,
    )


class DocumentReviewError(Exception):
    """Caller-facing failure (not found, wrong org, wrong state). Routers
    translate to the appropriate HTTP status -- same contract as
    `ReviewCycleError`."""


def record_approval(
    session: Session,
    *,
    document: Document,
    version: DocumentVersion,
    contact: Contact,
    approval_type: str,
    now: datetime,
    note: str | None = None,
) -> DocumentApproval:
    """Append one approval decision. The only writer of `document_approval`.

    Does NOT touch `version.status`, `version.body`, evidence, or control
    state. `publish_document` sets the version's own status and the
    denormalized `approved_at`/`approved_by_contact_id` mirror separately,
    before calling this with `approval_type='initial'`; a reaffirmation
    calls it with `'reaffirmation'` and changes nothing else anywhere.
    """
    approval = DocumentApproval(
        id=uuid.uuid4(),
        document_id=document.id,
        document_version_id=version.id,
        org_id=document.org_id,
        approval_type=approval_type,
        approved_at=now,
        approved_by_contact_id=contact.id,
        # Denormalized so the record survives the contact being deleted.
        approver_name=contact.name,
        note=note,
    )
    session.add(approval)
    session.flush()
    return approval


def latest_approval(
    session: Session, *, document_id: uuid.UUID
) -> DocumentApproval | None:
    """Newest approval of any kind for this document.

    Ordered by `approved_at DESC, id DESC` -- the id tiebreak is not
    decoration. A reaffirmation recorded in the same transaction as another
    approval can share a timestamp to microsecond precision, and this value
    drives the cadence verdict, so an unordered tie would make "is this
    overdue" nondeterministic. Same totality rule the bundle queries now
    carry.
    """
    return session.scalars(
        select(DocumentApproval)
        .where(DocumentApproval.document_id == document_id)
        .order_by(DocumentApproval.approved_at.desc(), DocumentApproval.id.desc())
        .limit(1)
    ).first()


def approvals_for_document(
    session: Session, *, document_id: uuid.UUID
) -> list[DocumentApproval]:
    """Full approval history, newest first. Totally ordered (see
    `latest_approval`)."""
    return list(
        session.scalars(
            select(DocumentApproval)
            .where(DocumentApproval.document_id == document_id)
            .order_by(DocumentApproval.approved_at.desc(), DocumentApproval.id.desc())
        ).all()
    )


def reaffirm(
    session: Session,
    *,
    org_id: uuid.UUID,
    document_id: uuid.UUID,
    contact_id: uuid.UUID,
    now: datetime,
    note: str | None = None,
) -> DocumentApproval:
    """Record "reviewed and still current" against the document's currently
    approved version.

    **Creates no version and mutates nothing.** That is the whole point of
    the slice: the approved version stays byte-identical, its status stays
    `approved`, and N.2's diff history gains no entry, because nothing
    changed. Only a `document_approval` row is added.

    Requires an approved version to reaffirm. A draft has nothing to
    re-affirm -- it has never been approved, so the correct action is
    publishing it, not reaffirming it, and conflating the two would let
    approval happen outside `publish_document` (which is the only path that
    attaches evidence).
    """
    document = session.get(Document, document_id)
    if document is None or document.org_id != org_id:
        raise DocumentReviewError("Document not found")

    version = session.scalars(
        select(DocumentVersion)
        .where(
            DocumentVersion.document_id == document_id,
            DocumentVersion.status == "approved",
        )
        .order_by(
            DocumentVersion.version_number.desc(),
            DocumentVersion.id.desc(),
        )
        .limit(1)
    ).first()
    if version is None:
        raise DocumentReviewError(
            "Document has no approved version to reaffirm -- publish it first"
        )

    contact = session.get(Contact, contact_id)
    if contact is None or contact.org_id != org_id:
        raise DocumentReviewError(
            "approved_by_contact_id must be a contact in this org"
        )

    return record_approval(
        session,
        document=document,
        version=version,
        contact=contact,
        approval_type="reaffirmation",
        now=now,
        note=note,
    )


@dataclass(frozen=True)
class DueDocument:
    document: Document
    state: ReviewState


def documents_needing_attention(
    session: Session, *, org_id: uuid.UUID, now: datetime
) -> list[DueDocument]:
    """Every document in this org whose review needs a human.

    Includes `never_approved`, `due_soon` and `overdue` -- see
    `ReviewState.needs_attention`. Totally ordered so the digest count and
    any rendered list are reproducible: worst first, then by `doc_id`,
    which is unique per org (`uq_document_doc_id`).
    """
    docs = session.scalars(
        select(Document)
        .where(Document.org_id == org_id)
        .order_by(Document.doc_id)
    ).all()

    severity = {OVERDUE: 0, NEVER_APPROVED: 1, DUE_SOON: 2}
    out: list[DueDocument] = []
    for doc in docs:
        approval = latest_approval(session, document_id=doc.id)
        state = review_state(
            last_approved_at=approval.approved_at if approval else None,
            cadence_months=doc.cadence_months,
            now=now,
        )
        if state.needs_attention:
            out.append(DueDocument(document=doc, state=state))
    out.sort(key=lambda d: (severity[d.state.status], d.document.doc_id))
    return out


@dataclass(frozen=True)
class NotifyCandidate:
    contact_id: uuid.UUID
    name: str
    email: str
    # Why this person is being told, for the UI and for the audit trail.
    # 'approver' means they personally approved one of the due documents;
    # 'role' means they hold security_officer or it_admin.
    reason: str


def notify_candidates_for_org(
    session: Session, *, org_id: uuid.UUID, due: list[DueDocument]
) -> list[NotifyCandidate]:
    """Who to tell that documents are coming due, and why.

    Two sources, unioned and deduplicated by contact:

    1. **Each due document's own named approver** -- the person whose
       decision satisfies the cadence (see `DocumentApproval`'s docstring
       on approver-versus-actor). They are the obvious candidate: the
       control is "the named approver reviewed this", so telling anyone
       else first is indirection.
    2. **Contacts holding `security_officer` or `it_admin`** -- D.3's
       routing precedent, and the reason a deleted or never-set approver
       does not mean nobody hears about it.

    Deduplicated so one person holding both a role and an approval is told
    once. `reason` keeps why they were included, and 'approver' wins over
    'role' because it is the more specific fact.

    Returns an empty list when the org has neither -- the caller must
    handle that explicitly rather than treating it as "nothing to do"; see
    `_document_review_digest`. That exact bug (a notification silently
    dropped because no contact held the role) has been fixed twice in this
    codebase, so it is the caller's contract, not an edge case.
    """
    candidates: dict[uuid.UUID, NotifyCandidate] = {}

    approver_ids = {
        a.approved_by_contact_id
        for a in (
            latest_approval(session, document_id=d.document.id) for d in due
        )
        if a is not None and a.approved_by_contact_id is not None
    }
    if approver_ids:
        approvers = session.scalars(
            select(Contact)
            .where(Contact.org_id == org_id, Contact.id.in_(approver_ids))
            .order_by(Contact.name, Contact.id)
        ).all()
        for c in approvers:
            candidates[c.id] = NotifyCandidate(
                contact_id=c.id, name=c.name, email=c.email, reason="approver"
            )

    role_holders = session.scalars(
        select(Contact)
        .join(ContactDocumentationRole, ContactDocumentationRole.contact_id == Contact.id)
        .where(
            Contact.org_id == org_id,
            ContactDocumentationRole.role.in_(NOTIFY_ROLES),
        )
        .order_by(Contact.name, Contact.id)
        .distinct()
    ).all()
    for c in role_holders:
        candidates.setdefault(
            c.id,
            NotifyCandidate(
                contact_id=c.id, name=c.name, email=c.email, reason="role"
            ),
        )

    return sorted(candidates.values(), key=lambda c: (c.name, str(c.contact_id)))


def record_notification_result(
    session: Session,
    *,
    notification: DocumentReviewNotification,
    sent: bool,
    error: str | None,
) -> None:
    """Same `notified_at`/`notification_error` discipline as
    `review_cycles.record_notification_result` and
    `liongard_sync.record_notification_result` -- see the former's docstring
    for why `notified_at` is set only once and never cleared by a later
    failure. Kept as a third copy rather than abstracted: the three tables
    have no common base, and the shared thing worth keeping identical is the
    *rule*, which a comment pins better than an inherited mixin would.
    """
    if sent:
        if notification.notified_at is None:
            notification.notified_at = datetime.now(UTC)
        notification.notification_error = None
    else:
        notification.notification_error = error
    session.flush()
