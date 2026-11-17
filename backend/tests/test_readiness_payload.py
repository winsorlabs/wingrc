"""What the readiness bars read from the control-states payload, and what
they must not touch (docs/roadmap.md, "The readiness bars").

The bars' rules live in frontend/src/lib/readiness.ts. This file holds the
server half: `raci_letters` is right, it costs one query for the whole
assessment rather than one per row, and reading it moves no status and no
SPRS score. `evidence_count`'s live-links-only definition is pinned by
test_evidence_links.py:test_counts_and_widgets_read_live_links_only.
"""

from __future__ import annotations

import pytest
from sqlalchemy import event, select

from app.models import Assessment, ControlState
from tests.conftest import _grant
from tests.test_raci import _url, client, ref  # noqa: F401 -- fixtures

pytestmark = pytest.mark.integration


def _states_url(ref: dict) -> str:  # noqa: F811
    return f"/orgs/{ref['org'].id}/assessments/{ref['assessment'].id}/control-states"


def _assign(client, ref, cs, contact, letter):  # noqa: F811
    r = client.post(
        _url(ref),
        json={
            "control_state_id": str(cs.id),
            "contact_id": str(contact.id),
            "raci_letter": letter,
        },
    )
    assert r.status_code == 201, r.text


def _status_and_sprs(db_session, ref):  # noqa: F811
    db_session.expire_all()
    statuses = sorted(
        (str(i), s)
        for i, s in db_session.execute(
            select(ControlState.id, ControlState.status).where(
                ControlState.assessment_id == ref["assessment"].id
            )
        ).all()
    )
    sprs = db_session.scalar(
        select(Assessment.sprs_score).where(Assessment.id == ref["assessment"].id)
    )
    return statuses, sprs


def test_raci_letters_are_the_distinct_letters_held_sorted(
    client, db_session, fake_msp_admin, ref  # noqa: F811
):
    _grant(db_session, fake_msp_admin, org_id=ref["org"].id)
    # [a]: R and A, each by a different person, given out of order.
    _assign(client, ref, ref["ac1a"], ref["jane"], "R")
    _assign(client, ref, ref["ac1a"], ref["bob"], "A")
    # [b]: R held twice. One letter, not two.
    _assign(client, ref, ref["ac1b"], ref["jane"], "R")
    _assign(client, ref, ref["ac1b"], ref["bob"], "R")
    # ac2[a]: C and I only, so R and A are both missing.
    _assign(client, ref, ref["ac2a"], ref["bob"], "I")
    _assign(client, ref, ref["ac2a"], ref["bob"], "C")

    by_id = {s["id"]: s for s in client.get(_states_url(ref)).json()}
    assert by_id[str(ref["ac1a"].id)]["raci_letters"] == ["A", "R"]
    assert by_id[str(ref["ac1b"].id)]["raci_letters"] == ["R"]
    assert by_id[str(ref["ac2a"].id)]["raci_letters"] == ["C", "I"]
    assert by_id[str(ref["ia1a"].id)]["raci_letters"] == [], "no assignment, no letters"


def test_raci_letters_cost_one_query_not_one_per_row(
    client, db_session, fake_msp_admin, ref  # noqa: F811
):
    _grant(db_session, fake_msp_admin, org_id=ref["org"].id)
    for key in ("ac1a", "ac1b", "ac2a", "ia1a"):
        _assign(client, ref, ref[key], ref["jane"], "R")

    seen: list[str] = []

    def _record(conn, cursor, statement, *args):
        if "raci_assignment" in statement:
            seen.append(statement)

    bind = db_session.get_bind()
    event.listen(bind, "before_cursor_execute", _record)
    try:
        states = client.get(_states_url(ref)).json()
    finally:
        event.remove(bind, "before_cursor_execute", _record)

    assert all(s["raci_letters"] == ["R"] for s in states), (
        "fixture: every row holds a letter, so a per-row lookup would show here"
    )
    assert len(states) == 4, "fixture: four objectives"
    assert len(seen) == 1, seen


def test_the_bars_read_and_decide_nothing(
    client, db_session, fake_msp_admin, ref  # noqa: F811
):
    """Reading the payload, and assigning the letters a segment reads,
    leave every objective's status and the SPRS score where they were."""
    _grant(db_session, fake_msp_admin, org_id=ref["org"].id)
    before = _status_and_sprs(db_session, ref)
    assert before[0], "fixture: the assessment has objectives"

    client.get(_states_url(ref))
    _assign(client, ref, ref["ac1a"], ref["jane"], "R")
    _assign(client, ref, ref["ac1a"], ref["bob"], "A")
    states = client.get(_states_url(ref)).json()
    assert next(s for s in states if s["id"] == str(ref["ac1a"].id))["raci_letters"] == [
        "A", "R",
    ], "fixture: the letters landed"

    after = _status_and_sprs(db_session, ref)
    assert after[0] == before[0], "no status moved"
    assert after[1] == before[1], "nor SPRS"
