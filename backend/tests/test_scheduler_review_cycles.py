"""Integration tests for scheduler.py's review_cycle_open/
review_cycle_sweep jobs -- opening cadence, reminder idempotency,
force-close-into-a-durable-record, the content rule, and (since
2026-09-14) per-reviewer notification-delivery tracking: a live bug on
wl-util-1 closed a cycle's reviewers as 'no_response' when neither an
SMTP credential nor WINGRC_PUBLIC_URL was configured -- asserting two
named people failed to respond to a request that was never delivered.
See review_cycles.py's module docstring and close_cycle's own docstring
for the full fix.

email_service.send() is monkeypatched (not a real SMTP server), matching
test_scheduler_sprs_reminder.py's own reasoning: what's under test here is
scheduler.py's own logic, not the SMTP transport. WINGRC_PUBLIC_URL is set
by an autouse fixture below so the existing happy-path tests keep
exercising real (mocked) delivery attempts by default -- tests of the
undeliverable paths override it per-test.

Run in-container:
    docker compose exec backend pytest tests/test_scheduler_review_cycles.py -m integration -v
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app import scheduler
from app.config import get_settings
from app.email_service import EmailSendResult
from app.models import (
    Evidence,
    Organization,
    ReviewCycle,
    ReviewCycleReviewer,
    ScopeEntity,
    User,
)
from tests.conftest import _grant, _make_fake_user

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _public_url(monkeypatch):
    monkeypatch.setenv("WINGRC_PUBLIC_URL", "https://wingrc.example.com")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class _SentEmails(list):
    """A plain list has no __dict__, so it can't carry the extra
    `.results` queue below -- subclassing is the simplest way to keep
    every existing `len(sent_emails)`/`sent_emails[0]`/`.clear()` call
    site working unchanged while adding it."""

    def __init__(self) -> None:
        super().__init__()
        # Queue of results to return, in order, before falling back to
        # sent=True -- lets a test simulate a specific failure (e.g. a
        # provider rejection) on a chosen attempt without affecting
        # every other call in the same test.
        self.results: list[EmailSendResult] = []


@pytest.fixture
def sent_emails(monkeypatch):
    calls = _SentEmails()

    def _fake_send(session, *, to, subject, body, template):
        calls.append((to, subject, body))
        if calls.results:
            return calls.results.pop(0)
        return EmailSendResult(sent=True)

    monkeypatch.setattr(scheduler.email_service, "send", _fake_send)
    return calls


def _seed_org(db_session, *, cadence_months: int = 6, name: str | None = None) -> Organization:
    org = Organization(
        name=name or f"RevCycleOrg-{uuid.uuid4().hex[:8]}",
        review_cadence_months=cadence_months,
    )
    db_session.add(org)
    db_session.flush()
    return org


def _seed_msp_admin(db_session, *, org_id, email: str | None = None) -> User:
    user = User(
        home_org_id=org_id, email=email or f"admin-{uuid.uuid4().hex[:8]}@example.com",
        display_name="MSP Admin", login_method="local", role="msp_admin", is_active=True,
    )
    db_session.add(user)
    db_session.flush()
    _grant(db_session, _make_fake_user(id=user.id, org_id=org_id, role="msp_admin"), org_id=org_id)
    return user


def _seed_scope_entity(
    db_session, *, org_id, entity_type="person", natural_key=None
) -> ScopeEntity:
    e = ScopeEntity(
        org_id=org_id, entity_type=entity_type,
        natural_key=natural_key or f"{entity_type}-{uuid.uuid4().hex[:6]}",
        status="active", in_boundary=True,
    )
    db_session.add(e)
    db_session.flush()
    return e


# ---------------------------------------------------------------------------
# Opening
# ---------------------------------------------------------------------------


def test_opens_cycle_for_org_with_no_prior_cycle(db_session, sent_emails):
    org = _seed_org(db_session)
    _seed_msp_admin(db_session, org_id=org.id)
    _seed_scope_entity(db_session, org_id=org.id)

    result = scheduler._review_cycle_open(db_session)

    assert result == {"cycles_opened": 1}
    cycle = db_session.scalars(select(ReviewCycle).where(ReviewCycle.org_id == org.id)).one()
    assert cycle.status == "open"
    assert cycle.opened_by == "scheduler"
    assert len(sent_emails) == 1  # one reviewer (the msp_admin) seeded


def test_does_not_open_a_second_cycle_while_one_is_open(db_session, sent_emails):
    org = _seed_org(db_session)
    _seed_msp_admin(db_session, org_id=org.id)
    _seed_scope_entity(db_session, org_id=org.id)

    scheduler._review_cycle_open(db_session)
    assert len(sent_emails) == 1

    result = scheduler._review_cycle_open(db_session)
    assert result == {"cycles_opened": 0}
    assert len(sent_emails) == 1  # unchanged

    count = db_session.scalars(select(ReviewCycle).where(ReviewCycle.org_id == org.id)).all()
    assert len(count) == 1


def test_org_not_yet_due_by_cadence_is_skipped(db_session, sent_emails):
    org = _seed_org(db_session, cadence_months=6)
    _seed_msp_admin(db_session, org_id=org.id)
    # A prior cycle closed just 1 month ago -- not due again for 5 more.
    db_session.add(
        ReviewCycle(
            id=uuid.uuid4(), org_id=org.id,
            opened_at=datetime.now(UTC) - timedelta(days=30),
            due_at=datetime.now(UTC) - timedelta(days=9),
            closed_at=datetime.now(UTC) - timedelta(days=9),
            status="completed", cadence_months=6, opened_by="scheduler",
        )
    )
    db_session.commit()

    result = scheduler._review_cycle_open(db_session)
    assert result == {"cycles_opened": 0}
    assert sent_emails == []


# ---------------------------------------------------------------------------
# Reminders: idempotent per reviewer per reminder number
# ---------------------------------------------------------------------------


def _seed_open_cycle_with_reviewer(
    db_session, *, org_id, requested_days_ago: int, due_days_from_now: int = 10,
    notified: bool = True,
):
    """notified=True (the default) represents the common case: the
    reviewer WAS successfully notified at request time, so the reminder
    clock (which runs from notified_at, not requested_at -- see
    review_cycles.due_reminder_number's own docstring) starts there too.
    Pass notified=False to represent a reviewer never yet reached."""
    cycle = ReviewCycle(
        id=uuid.uuid4(), org_id=org_id,
        opened_at=datetime.now(UTC) - timedelta(days=requested_days_ago),
        due_at=datetime.now(UTC) + timedelta(days=due_days_from_now),
        status="open", cadence_months=6, opened_by="scheduler",
    )
    db_session.add(cycle)
    db_session.flush()
    requested_at = datetime.now(UTC) - timedelta(days=requested_days_ago)
    reviewer = ReviewCycleReviewer(
        id=uuid.uuid4(), cycle_id=cycle.id, org_id=org_id,
        reviewer_name="Reviewer", reviewer_email=f"rev-{uuid.uuid4().hex[:6]}@example.com",
        reviewer_side="client", status="requested",
        requested_at=requested_at,
        notified_at=requested_at if notified else None,
    )
    db_session.add(reviewer)
    db_session.commit()
    return cycle, reviewer


def test_reminder_sent_once_reviewer_is_overdue_and_not_resent(db_session, sent_emails):
    org = _seed_org(db_session)
    cycle, reviewer = _seed_open_cycle_with_reviewer(
        db_session, org_id=org.id, requested_days_ago=8
    )

    scheduler._review_cycle_sweep(db_session)
    assert len(sent_emails) == 1

    # Same day, second tick -- must not resend reminder #1.
    scheduler._review_cycle_sweep(db_session)
    assert len(sent_emails) == 1


def test_second_reminder_sent_at_day_14(db_session, sent_emails):
    org = _seed_org(db_session)
    cycle, reviewer = _seed_open_cycle_with_reviewer(
        db_session, org_id=org.id, requested_days_ago=15
    )

    scheduler._review_cycle_sweep(db_session)
    # Both reminder #1 (day 7) and #2 (day 14) are now overdue in one tick.
    assert len(sent_emails) == 2


def test_attested_reviewer_gets_no_reminder(db_session, sent_emails):
    org = _seed_org(db_session)
    cycle, reviewer = _seed_open_cycle_with_reviewer(
        db_session, org_id=org.id, requested_days_ago=8
    )
    reviewer.status = "attested"
    reviewer.attested_at = datetime.now(UTC)
    db_session.commit()

    scheduler._review_cycle_sweep(db_session)
    assert sent_emails == []


# ---------------------------------------------------------------------------
# Force-close into a durable non-response record
# ---------------------------------------------------------------------------


def test_overdue_cycle_force_closes_with_no_response_and_evidence(db_session, sent_emails):
    org = _seed_org(db_session)
    cycle = ReviewCycle(
        id=uuid.uuid4(), org_id=org.id,
        opened_at=datetime.now(UTC) - timedelta(days=25),
        due_at=datetime.now(UTC) - timedelta(days=4),  # already overdue
        status="open", cadence_months=6, opened_by="scheduler",
    )
    db_session.add(cycle)
    db_session.flush()
    # notified_at set: this reviewer WAS reached, just never answered --
    # the no_response path specifically. See the sibling
    # closed_undeliverable tests below for the never-reached path.
    requested_at = datetime.now(UTC) - timedelta(days=25)
    unresponsive = ReviewCycleReviewer(
        id=uuid.uuid4(), cycle_id=cycle.id, org_id=org.id,
        reviewer_name="Never Responded", reviewer_email="ghost@example.com",
        reviewer_side="client", status="requested",
        requested_at=requested_at, notified_at=requested_at,
    )
    db_session.add(unresponsive)
    db_session.commit()

    result = scheduler._review_cycle_sweep(db_session)
    assert result["cycles_closed"] == 1

    db_session.refresh(cycle)
    assert cycle.status == "closed_unattested"
    assert cycle.closed_at is not None

    db_session.refresh(unresponsive)
    assert unresponsive.status == "no_response"

    evidence_rows = db_session.scalars(
        select(Evidence).where(Evidence.org_id == org.id, Evidence.artifact_type == "attestation")
    ).all()
    assert len(evidence_rows) == 1


# ---------------------------------------------------------------------------
# Notification delivery tracking (the wl-util-1 bug fix)
# ---------------------------------------------------------------------------


def test_never_notified_cycle_closes_as_undeliverable_not_no_response(db_session):
    # A reviewer nobody ever successfully reached, on a cycle already
    # past its due date -- exactly the shape of the live wl-util-1 bug
    # (there, WINGRC_PUBLIC_URL and SMTP were both unconfigured; this
    # constructs the end state directly to test close_cycle's own
    # decision in isolation from how notification failed).
    org = _seed_org(db_session)
    cycle = ReviewCycle(
        id=uuid.uuid4(), org_id=org.id,
        opened_at=datetime.now(UTC) - timedelta(days=25),
        due_at=datetime.now(UTC) - timedelta(days=4),
        status="open", cadence_months=6, opened_by="scheduler",
    )
    db_session.add(cycle)
    db_session.flush()
    unreached = ReviewCycleReviewer(
        id=uuid.uuid4(), cycle_id=cycle.id, org_id=org.id,
        reviewer_name="Never Reached", reviewer_email="ghost@example.com",
        reviewer_side="client", status="requested",
        requested_at=datetime.now(UTC) - timedelta(days=25),
    )
    db_session.add(unreached)
    db_session.commit()

    result = scheduler._review_cycle_sweep(db_session)
    assert result["cycles_closed"] == 1

    db_session.refresh(cycle)
    assert cycle.status == "closed_undeliverable"

    db_session.refresh(unreached)
    assert unreached.status == "not_notified"
    assert unreached.notified_at is None


def test_mixed_notification_outcomes_close_each_reviewer_correctly(db_session):
    # The case most likely to be wrong: one reviewer was reached, one
    # never was. The cycle itself stays closed_unattested (a review WAS
    # attempted -- just not delivered to everyone), while each reviewer
    # gets its own, different, honest terminal status.
    org = _seed_org(db_session)
    cycle = ReviewCycle(
        id=uuid.uuid4(), org_id=org.id,
        opened_at=datetime.now(UTC) - timedelta(days=25),
        due_at=datetime.now(UTC) - timedelta(days=4),
        status="open", cadence_months=6, opened_by="scheduler",
    )
    db_session.add(cycle)
    db_session.flush()
    requested_at = datetime.now(UTC) - timedelta(days=25)
    reached = ReviewCycleReviewer(
        id=uuid.uuid4(), cycle_id=cycle.id, org_id=org.id,
        reviewer_name="Reached", reviewer_email="reached@example.com",
        reviewer_side="msp", status="requested",
        requested_at=requested_at, notified_at=requested_at,
    )
    unreached = ReviewCycleReviewer(
        id=uuid.uuid4(), cycle_id=cycle.id, org_id=org.id,
        reviewer_name="Unreached", reviewer_email="unreached@example.com",
        reviewer_side="client", status="requested",
        requested_at=requested_at,
    )
    db_session.add_all([reached, unreached])
    db_session.commit()

    result = scheduler._review_cycle_sweep(db_session)
    assert result["cycles_closed"] == 1

    db_session.refresh(cycle)
    assert cycle.status == "closed_unattested"

    db_session.refresh(reached)
    assert reached.status == "no_response"

    db_session.refresh(unreached)
    assert unreached.status == "not_notified"


def test_notification_error_distinguishes_unconfigured_from_provider_rejection(
    db_session, sent_emails, monkeypatch
):
    org = _seed_org(db_session)
    _seed_msp_admin(db_session, org_id=org.id)
    _seed_scope_entity(db_session, org_id=org.id)

    # First attempt: WINGRC_PUBLIC_URL is configured (autouse fixture),
    # but the "SMTP provider" rejects the send -- a different operator
    # problem than "not configured at all."
    sent_emails.results.append(
        EmailSendResult(sent=False, error="mail.example.com rejected the message: 550 mailbox full")
    )
    scheduler._review_cycle_open(db_session)
    reviewer = db_session.scalars(
        select(ReviewCycleReviewer).where(ReviewCycleReviewer.org_id == org.id)
    ).one()
    assert reviewer.notified_at is None
    assert "mailbox full" in reviewer.notification_error

    # Then: WINGRC_PUBLIC_URL becomes unconfigured -- a distinguishable,
    # different reason, recorded on the same row (most-recent-attempt
    # semantics -- see record_notification_result's own docstring).
    monkeypatch.delenv("WINGRC_PUBLIC_URL", raising=False)
    get_settings.cache_clear()
    scheduler._review_cycle_sweep(db_session)  # cycle not yet due -> retries initial notify
    db_session.refresh(reviewer)
    assert reviewer.notified_at is None
    assert "WINGRC_PUBLIC_URL" in reviewer.notification_error


def test_reviewer_notified_on_a_later_sweep_once_reachable(db_session, monkeypatch, sent_emails):
    # The live wl-util-1 scenario end to end: a cycle opens while
    # undeliverable, and later -- once the deployment becomes reachable
    # (e.g. an operator configures WINGRC_PUBLIC_URL/SMTP) -- the very
    # next sweep tick notifies the still-open cycle's reviewer with no
    # separate "catch up an old cycle" step required.
    monkeypatch.delenv("WINGRC_PUBLIC_URL", raising=False)
    get_settings.cache_clear()

    org = _seed_org(db_session)
    _seed_msp_admin(db_session, org_id=org.id)
    _seed_scope_entity(db_session, org_id=org.id)
    scheduler._review_cycle_open(db_session)

    reviewer = db_session.scalars(
        select(ReviewCycleReviewer).where(ReviewCycleReviewer.org_id == org.id)
    ).one()
    assert reviewer.notified_at is None
    assert len(sent_emails) == 0  # never even reached email_service.send

    monkeypatch.setenv("WINGRC_PUBLIC_URL", "https://wingrc.example.com")
    get_settings.cache_clear()

    result = scheduler._review_cycle_sweep(db_session)
    assert result["notifications_sent"] == 1

    db_session.refresh(reviewer)
    assert reviewer.notified_at is not None
    assert reviewer.notification_error is None
    assert len(sent_emails) == 1


# ---------------------------------------------------------------------------
# Content rule
# ---------------------------------------------------------------------------


def test_open_and_reminder_emails_carry_no_org_or_item_detail(db_session, sent_emails):
    org = _seed_org(db_session, name="Extremely Distinctive Client Name LLC")
    _seed_msp_admin(db_session, org_id=org.id)
    _seed_scope_entity(db_session, org_id=org.id, natural_key="super.secret.user@corp.local")

    scheduler._review_cycle_open(db_session)
    assert len(sent_emails) == 1
    _to, subject, body = sent_emails[0]
    for forbidden in (org.name, "super.secret.user", "AC.L2-3.1.1"):
        assert forbidden not in subject
        assert forbidden not in body
    assert "WinGRC" in body

    sent_emails.clear()
    cycle, reviewer = _seed_open_cycle_with_reviewer(
        db_session, org_id=org.id, requested_days_ago=8
    )
    scheduler._review_cycle_sweep(db_session)
    assert len(sent_emails) == 1
    _to, subject, body = sent_emails[0]
    assert org.name not in subject and org.name not in body
