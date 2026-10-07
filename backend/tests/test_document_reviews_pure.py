"""Pure cadence arithmetic -- roadmap N.3, no database.

`document_reviews.review_state` decides whether a policy is overdue, which
is a compliance-visible verdict, so it is deliberately a pure function over
(last approval, cadence, now) and is tested without any infrastructure. No
`integration` marker anywhere in this file: every test here runs in CI's
no-database job, which is the point.

The boundary cases are the ones worth pinning. "Overdue" starting a day
late, or a document approved on the 31st drifting forward a few days every
year, are exactly the kind of bug that is invisible until an assessor asks
when a policy was last reviewed.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.document_reviews import (
    CURRENT,
    DUE_SOON,
    DUE_SOON_DAYS,
    NEVER_APPROVED,
    OVERDUE,
    add_months,
    review_state,
)


def _at(year: int, month: int, day: int, hour: int = 12) -> datetime:
    return datetime(year, month, day, hour, tzinfo=UTC)


# ---------------------------------------------------------------------------
# add_months
# ---------------------------------------------------------------------------


def test_add_months_simple():
    assert add_months(_at(2026, 1, 15), 12) == _at(2027, 1, 15)
    assert add_months(_at(2026, 1, 15), 6) == _at(2026, 7, 15)
    assert add_months(_at(2026, 1, 15), 1) == _at(2026, 2, 15)


def test_add_months_crosses_year_boundary():
    assert add_months(_at(2026, 11, 3), 3) == _at(2027, 2, 3)
    assert add_months(_at(2026, 12, 31), 1) == _at(2027, 1, 31)


def test_add_months_clamps_to_the_shorter_month():
    """31 January plus one month is 28 February, not 3 March.

    The clamp is what stops a policy approved on the 31st drifting forward
    a few days at every review, and stops the obvious
    `replace(month=month + 1)` implementation raising outright.
    """
    assert add_months(_at(2026, 1, 31), 1) == _at(2026, 2, 28)
    assert add_months(_at(2026, 3, 31), 1) == _at(2026, 4, 30)
    assert add_months(_at(2026, 8, 31), 6) == _at(2027, 2, 28)


def test_add_months_clamps_into_a_leap_february():
    # 2028 is a leap year; 2027 is not.
    assert add_months(_at(2028, 1, 31), 1) == _at(2028, 2, 29)
    assert add_months(_at(2027, 1, 31), 1) == _at(2027, 2, 28)


def test_add_months_zero_is_identity():
    moment = _at(2026, 5, 17)
    assert add_months(moment, 0) is moment or add_months(moment, 0) == moment


def test_add_months_preserves_time_of_day_and_tz():
    out = add_months(datetime(2026, 1, 15, 8, 42, 7, tzinfo=UTC), 2)
    assert (out.hour, out.minute, out.second) == (8, 42, 7)
    assert out.tzinfo is UTC


# ---------------------------------------------------------------------------
# review_state
# ---------------------------------------------------------------------------


def test_never_approved_when_there_is_no_approval():
    """A document with no approval is not "fine until its first due date".

    It has never been through the control the cadence exists to evidence,
    so it needs attention -- and it has no due date, because there is
    nothing to count from.
    """
    state = review_state(last_approved_at=None, cadence_months=12, now=_at(2026, 9, 30))
    assert state.status == NEVER_APPROVED
    assert state.next_due_at is None
    assert state.days_until_due is None
    assert state.needs_attention is True


def test_current_well_before_the_due_date():
    state = review_state(
        last_approved_at=_at(2026, 1, 1), cadence_months=12, now=_at(2026, 3, 1)
    )
    assert state.status == CURRENT
    assert state.next_due_at == _at(2027, 1, 1)
    assert state.needs_attention is False


def test_overdue_begins_exactly_on_the_due_date():
    """The due date itself is overdue, not the day after.

    Asserted on both sides of a one-second boundary so an off-by-one in
    either direction fails. `due_soon` is a warning window, not a grace
    period -- nothing extends the deadline.
    """
    approved = _at(2026, 1, 1)
    due = add_months(approved, 12)

    just_before = review_state(
        last_approved_at=approved, cadence_months=12, now=due - timedelta(seconds=1)
    )
    exactly_at = review_state(last_approved_at=approved, cadence_months=12, now=due)

    assert just_before.status == DUE_SOON
    assert exactly_at.status == OVERDUE


def test_due_soon_window_boundaries():
    """`due_soon` starts exactly DUE_SOON_DAYS before the due date."""
    approved = _at(2026, 1, 1)
    due = add_months(approved, 12)

    outside = review_state(
        last_approved_at=approved,
        cadence_months=12,
        now=due - timedelta(days=DUE_SOON_DAYS, seconds=1),
    )
    inside = review_state(
        last_approved_at=approved, cadence_months=12, now=due - timedelta(days=DUE_SOON_DAYS)
    )
    assert outside.status == CURRENT
    assert inside.status == DUE_SOON


def test_days_until_due_is_negative_once_overdue():
    state = review_state(
        last_approved_at=_at(2026, 1, 1), cadence_months=12, now=_at(2027, 2, 1)
    )
    assert state.status == OVERDUE
    assert state.days_until_due is not None
    assert state.days_until_due < 0


def test_shorter_cadence_makes_an_older_approval_overdue():
    """Changing the cadence changes the verdict, which is why it is derived.

    The same approval date is current at annual review and overdue at
    quarterly. A stored `next_due_at` computed under the old cadence would
    still report current -- the reason document_reviews.py has no such
    column.
    """
    approved = _at(2026, 1, 1)
    now = _at(2026, 8, 1)
    assert review_state(last_approved_at=approved, cadence_months=12, now=now).status == CURRENT
    assert review_state(last_approved_at=approved, cadence_months=3, now=now).status == OVERDUE


def test_non_positive_cadence_is_treated_as_no_cadence():
    """A cadence of 0 must not make every document permanently overdue.

    Flagging an operator's whole library because of one odd configuration
    value is worse than honouring it. The column defaults to 12 and the
    router validates it, so this is a floor rather than a supported mode.
    """
    for cadence in (0, -1):
        state = review_state(
            last_approved_at=_at(2026, 1, 1), cadence_months=cadence, now=_at(2030, 1, 1)
        )
        assert state.status == CURRENT
        assert state.next_due_at is None


def test_needs_attention_covers_exactly_the_three_actionable_statuses():
    approved = _at(2026, 1, 1)
    cases = {
        NEVER_APPROVED: review_state(
            last_approved_at=None, cadence_months=12, now=approved
        ),
        CURRENT: review_state(
            last_approved_at=approved, cadence_months=12, now=_at(2026, 2, 1)
        ),
        DUE_SOON: review_state(
            last_approved_at=approved, cadence_months=12, now=_at(2026, 12, 20)
        ),
        OVERDUE: review_state(
            last_approved_at=approved, cadence_months=12, now=_at(2027, 6, 1)
        ),
    }
    for expected, state in cases.items():
        assert state.status == expected, f"{expected}: got {state.status}"
    assert cases[CURRENT].needs_attention is False
    for status in (NEVER_APPROVED, DUE_SOON, OVERDUE):
        assert cases[status].needs_attention is True


def test_review_state_is_frozen():
    """Frozen because it is a computed verdict, not a record.

    A caller that could mutate it would be tempted to "fix up" a status
    locally, which is how two implementations of overdue start.
    """
    import dataclasses

    state = review_state(last_approved_at=None, cadence_months=12, now=_at(2026, 1, 1))
    try:
        state.status = OVERDUE  # type: ignore[misc]
    except dataclasses.FrozenInstanceError:
        return
    raise AssertionError("ReviewState should be immutable")
