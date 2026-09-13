"""Integration tests for scheduler.py's sprs_annual_reminder job -- the
first scheduler job with actual product meaning (expire_stale_invites is
a hygiene proof). Covers the clock (attested submission date only, never
invented from onboarding/assessment dates), idempotency (keyed to the
submission, not the tick), the content rule (no org name, no score, in
neither subject nor body), and the MSP-staff-only recipient decision.

email_service.send() is monkeypatched here (not a real aiosmtpd server,
unlike test_smtp_connector.py/test_email_service.py) -- what's under test
is scheduler.py's own logic (who it decides to email, what it decides to
say, whether it marks reminders sent), not the SMTP transport, which
those other files already cover.

Run in-container:
    docker compose exec backend pytest tests/test_scheduler_sprs_reminder.py -m integration -v
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import text

from app import scheduler
from app.email_service import EmailSendResult
from app.models import Organization, SprsSubmission, User

pytestmark = pytest.mark.integration


@pytest.fixture
def sent_emails(monkeypatch):
    """Captures every (to, subject, body) email_service.send() call the
    job makes, without a real SMTP server -- see module docstring."""
    calls: list[tuple[str, str, str]] = []

    def _fake_send(session, *, to, subject, body, template):
        calls.append((to, subject, body))
        return EmailSendResult(sent=True)

    monkeypatch.setattr(scheduler.email_service, "send", _fake_send)
    return calls


def _seed_org(db_session, *, name: str | None = None) -> Organization:
    org = Organization(name=name or f"SprsReminderOrg-{uuid.uuid4().hex[:8]}")
    db_session.add(org)
    db_session.flush()
    return org


def _seed_msp_admin(db_session, *, email: str | None = None, **overrides) -> User:
    org = _seed_org(db_session)
    defaults = dict(
        home_org_id=org.id,
        email=email or f"admin-{uuid.uuid4().hex[:8]}@example.com",
        display_name="MSP Admin",
        login_method="local",
        role="msp_admin",
        is_active=True,
    )
    defaults.update(overrides)
    user = User(**defaults)
    db_session.add(user)
    db_session.flush()
    return user


def _seed_submission(db_session, *, org_id: uuid.UUID, submitted_date: date, **overrides):
    defaults = dict(
        id=uuid.uuid4(),
        org_id=org_id,
        score=95,
        submitted_date=submitted_date,
        submitted_by_name="Jane Filer",
        submitted_by_email="jane@example.com",
    )
    defaults.update(overrides)
    row = SprsSubmission(**defaults)
    db_session.add(row)
    db_session.flush()
    return row


def _months_ago(n: int) -> date:
    return (datetime.now(UTC) - timedelta(days=30 * n + 1)).date()


# ---------------------------------------------------------------------------
# The clock: attested submission date only
# ---------------------------------------------------------------------------


def test_org_with_no_submission_generates_nothing(db_session, sent_emails):
    _seed_org(db_session)  # no SprsSubmission row at all
    _seed_msp_admin(db_session)

    result = scheduler._sprs_annual_reminder(db_session)

    assert result == {"orgs_due": 0, "recipients_emailed": 0, "email_sent": False}
    assert sent_emails == []


def test_submission_under_12_months_old_is_not_due(db_session, sent_emails):
    org = _seed_org(db_session)
    _seed_submission(db_session, org_id=org.id, submitted_date=_months_ago(6))
    _seed_msp_admin(db_session)

    result = scheduler._sprs_annual_reminder(db_session)

    assert result["orgs_due"] == 0
    assert sent_emails == []


def test_submission_past_12_months_is_due(db_session, sent_emails):
    org = _seed_org(db_session)
    _seed_submission(db_session, org_id=org.id, submitted_date=_months_ago(13))
    _seed_msp_admin(db_session)

    result = scheduler._sprs_annual_reminder(db_session)

    assert result["orgs_due"] == 1
    assert result["email_sent"] is True
    assert len(sent_emails) == 1


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


def test_reminder_is_not_resent_on_the_next_tick(db_session, sent_emails):
    org = _seed_org(db_session)
    _seed_submission(db_session, org_id=org.id, submitted_date=_months_ago(13))
    _seed_msp_admin(db_session)

    first = scheduler._sprs_annual_reminder(db_session)
    assert first["orgs_due"] == 1
    assert len(sent_emails) == 1

    second = scheduler._sprs_annual_reminder(db_session)
    assert second == {"orgs_due": 0, "recipients_emailed": 0, "email_sent": False}
    assert len(sent_emails) == 1  # still just the one from the first tick


def test_a_new_submission_resets_the_clock(db_session, sent_emails):
    """The org was already reminded for its old submission; a fresh
    submission recorded today must not be immediately due again just
    because the old (now-superseded-by-recency) one already fired."""
    org = _seed_org(db_session)
    _seed_submission(db_session, org_id=org.id, submitted_date=_months_ago(13))
    _seed_msp_admin(db_session)
    scheduler._sprs_annual_reminder(db_session)
    assert len(sent_emails) == 1

    # A new, current submission -- far from its own 12-month anniversary.
    _seed_submission(db_session, org_id=org.id, submitted_date=date.today())

    result = scheduler._sprs_annual_reminder(db_session)
    assert result["orgs_due"] == 0
    assert len(sent_emails) == 1  # unchanged


def test_voided_submission_is_not_the_current_one_for_due_checking(db_session, sent_emails):
    org = _seed_org(db_session)
    stale = _seed_submission(db_session, org_id=org.id, submitted_date=_months_ago(13))
    stale.voided_at = datetime.now(UTC)
    stale.voided_reason = "typo"
    db_session.flush()
    _seed_msp_admin(db_session)

    result = scheduler._sprs_annual_reminder(db_session)

    # No non-voided submission exists, so this org has no clock at all --
    # same as having recorded nothing.
    assert result["orgs_due"] == 0
    assert sent_emails == []


# ---------------------------------------------------------------------------
# Content rule
# ---------------------------------------------------------------------------


def test_email_carries_no_org_name_or_score_in_subject_or_body(db_session, sent_emails):
    org = _seed_org(db_session, name="Definitely Distinctive Org Name Inc")
    _seed_submission(
        db_session, org_id=org.id, submitted_date=_months_ago(13), score=-137
    )
    _seed_msp_admin(db_session)

    scheduler._sprs_annual_reminder(db_session)

    assert len(sent_emails) == 1
    _to, subject, body = sent_emails[0]
    for forbidden in (org.name, "-137", "137", "score", "Score"):
        assert forbidden not in subject
        assert forbidden not in body
    assert "sign in to WinGRC" in body or "WinGRC" in body
    assert "SPRS" in subject  # the generic, org-agnostic copy itself


def test_email_body_is_identical_regardless_of_how_many_orgs_are_due(db_session, sent_emails):
    """Two due orgs in the same tick must produce exactly one email per
    recipient, not one per org -- see scheduler.py's own docstring."""
    org1 = _seed_org(db_session)
    org2 = _seed_org(db_session)
    _seed_submission(db_session, org_id=org1.id, submitted_date=_months_ago(13))
    _seed_submission(db_session, org_id=org2.id, submitted_date=_months_ago(14))
    _seed_msp_admin(db_session)

    result = scheduler._sprs_annual_reminder(db_session)

    assert result["orgs_due"] == 2
    assert len(sent_emails) == 1  # one recipient, one email, regardless of org count


# ---------------------------------------------------------------------------
# Recipients: MSP staff only
# ---------------------------------------------------------------------------


def test_only_active_msp_admin_and_msp_engineer_are_recipients(db_session, sent_emails):
    org = _seed_org(db_session)
    _seed_submission(db_session, org_id=org.id, submitted_date=_months_ago(13))

    admin = _seed_msp_admin(db_session, email="admin@example.com")
    engineer = _seed_msp_admin(
        db_session, email="engineer@example.com", role="msp_engineer", display_name="Engineer"
    )
    _seed_msp_admin(
        db_session,
        email="inactive-admin@example.com",
        display_name="Inactive Admin",
        is_active=False,
    )
    _seed_msp_admin(
        db_session,
        email="consultant@example.com",
        display_name="Consultant",
        role="consultant_admin",
    )
    _seed_msp_admin(
        db_session, email="customer@example.com", display_name="Customer", role="customer_poc"
    )

    scheduler._sprs_annual_reminder(db_session)

    recipients = {to for to, _subject, _body in sent_emails}
    assert recipients == {admin.email, engineer.email}


def test_no_recipients_means_nothing_is_marked_sent(db_session, sent_emails):
    """No active MSP staff at all -- e.g. a fresh deployment mid-setup.
    Must not mark the reminder sent (so it's retried once staff exist),
    and must not error."""
    org = _seed_org(db_session)
    _seed_submission(db_session, org_id=org.id, submitted_date=_months_ago(13))
    # No User rows at all.

    result = scheduler._sprs_annual_reminder(db_session)

    assert result == {"orgs_due": 1, "recipients_emailed": 0, "email_sent": False}
    assert sent_emails == []

    # Confirm it's genuinely retried, not silently marked done.
    result2 = scheduler._sprs_annual_reminder(db_session)
    assert result2["orgs_due"] == 1


def test_all_send_failures_leave_the_reminder_unmarked_for_retry(db_session, monkeypatch):
    org = _seed_org(db_session)
    _seed_submission(db_session, org_id=org.id, submitted_date=_months_ago(13))
    _seed_msp_admin(db_session)

    monkeypatch.setattr(
        scheduler.email_service,
        "send",
        lambda session, *, to, subject, body, template: EmailSendResult(
            sent=False, error="SMTP down"
        ),
    )

    result = scheduler._sprs_annual_reminder(db_session)
    assert result == {"orgs_due": 1, "recipients_emailed": 0, "email_sent": False}

    count = db_session.execute(text("SELECT count(*) FROM sprs_reminder_log")).scalar_one()
    assert count == 0
