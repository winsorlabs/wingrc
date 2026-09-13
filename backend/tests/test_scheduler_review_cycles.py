"""Integration tests for scheduler.py's review_cycle_open/
review_cycle_sweep jobs -- opening cadence, reminder idempotency,
force-close-into-a-durable-record, and the content rule.

email_service.send() is monkeypatched (not a real SMTP server), matching
test_scheduler_sprs_reminder.py's own reasoning: what's under test here is
scheduler.py's own logic, not the SMTP transport.

Run in-container:
    docker compose exec backend pytest tests/test_scheduler_review_cycles.py -m integration -v
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app import scheduler
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


@pytest.fixture
def sent_emails(monkeypatch):
    calls: list[tuple[str, str, str]] = []

    def _fake_send(session, *, to, subject, body, template):
        calls.append((to, subject, body))
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
    db_session, *, org_id, requested_days_ago: int, due_days_from_now: int = 10
):
    cycle = ReviewCycle(
        id=uuid.uuid4(), org_id=org_id,
        opened_at=datetime.now(UTC) - timedelta(days=requested_days_ago),
        due_at=datetime.now(UTC) + timedelta(days=due_days_from_now),
        status="open", cadence_months=6, opened_by="scheduler",
    )
    db_session.add(cycle)
    db_session.flush()
    reviewer = ReviewCycleReviewer(
        id=uuid.uuid4(), cycle_id=cycle.id, org_id=org_id,
        reviewer_name="Reviewer", reviewer_email=f"rev-{uuid.uuid4().hex[:6]}@example.com",
        reviewer_side="client", status="requested",
        requested_at=datetime.now(UTC) - timedelta(days=requested_days_ago),
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
    unresponsive = ReviewCycleReviewer(
        id=uuid.uuid4(), cycle_id=cycle.id, org_id=org.id,
        reviewer_name="Never Responded", reviewer_email="ghost@example.com",
        reviewer_side="client", status="requested",
        requested_at=datetime.now(UTC) - timedelta(days=25),
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
