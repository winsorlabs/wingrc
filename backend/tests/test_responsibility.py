"""control_state.responsibility gets a human write path, and a tool never
overwrites a value a person set without asking (migration 0075;
engine.set_responsibility and engine._tool_responsibility; docs/roadmap.md,
"Responsibility had no human write path").

What these pin:
- the rule, every case written out (pure);
- the endpoint writes the value, marks it a person's, writes history and an
  audit row, and moves no status and no score; c3pao_assessor is refused;
- a RACI change alone never writes it;
- activation asks about a hand-set value it would change, keeps it when
  nobody is asked, keeps it on "keep", takes the tool's on "take_tool", and
  refuses an answer for a value that changed since it was shown;
- a value nobody set still moves with the tools, as before;
- deactivation and a version move keep a hand-set value, and the move
  says so afterwards.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.assessment import (
    ResponsibilityChoice,
    is_responsibility_in_question,
    tool_changed_responsibility,
)
from app.auth import get_current_user
from app.db import get_session
from app.engine import (
    ActivationJudgementsChanged,
    activate_org_product,
    activation_preview,
    deactivate_org_product,
    move_org_product_version,
    set_responsibility,
)
from app.main import app
from app.models import (
    Assessment,
    AuditLog,
    BaselineControl,
    Contact,
    ControlState,
    ControlStateHistory,
    ProductBaselineVersion,
)
from tests.conftest import _app_session, _authed, _grant, _make_fake_user
from tests.test_activation_judgement import _product, _world

P, S, C, E = "provider_satisfies", "shared", "customer_owns", "external_system"


@pytest.fixture
def w(db_session):
    return _world(db_session)


# ---------------------------------------------------------------------------
# Pure
# ---------------------------------------------------------------------------


def test_the_rule_every_case_written_out():
    # Nobody set it: the tool's value, whatever the choice.
    assert tool_changed_responsibility(C, P, False) == P
    assert tool_changed_responsibility(C, S, False, ResponsibilityChoice.KEEP) == S
    # A person set it, the tool agrees: nothing to ask, the same value.
    assert not is_responsibility_in_question(P, P, True)
    assert tool_changed_responsibility(P, P, True) == P
    # A person set it, the tool differs: kept unless they take the tool's.
    assert is_responsibility_in_question(E, P, True)
    assert tool_changed_responsibility(E, P, True) == E
    assert tool_changed_responsibility(E, P, True, ResponsibilityChoice.KEEP) == E
    assert tool_changed_responsibility(E, P, True, ResponsibilityChoice.TAKE_TOOL) == P
    assert not is_responsibility_in_question(C, P, False)


# ---------------------------------------------------------------------------
# Through the engine
# ---------------------------------------------------------------------------


def _cs(db_session, cs):
    db_session.expire(cs)
    return db_session.get(ControlState, cs.id)


def _by_hand(db_session, w, key, value):
    set_responsibility(db_session, w["org"].id, w["a"].id, w["s"][key].id, value, "Pat Person")
    db_session.commit()


@pytest.mark.integration
def test_set_by_hand_marks_it_writes_history_and_audit_and_moves_no_score(db_session, w):
    cs = w["s"]["c2a"]
    before_status, before_score = cs.status, w["a"].sprs_score
    _by_hand(db_session, w, "c2a", P)
    cs = _cs(db_session, cs)
    assert (cs.responsibility, cs.responsibility_set_by) == (P, "Pat Person")
    assert cs.responsibility_set_at is not None
    assert cs.status == before_status
    assert db_session.get(Assessment, w["a"].id).sprs_score == before_score
    h = db_session.scalars(select(ControlStateHistory).where(
        ControlStateHistory.control_state_id == cs.id)
        .order_by(ControlStateHistory.changed_at.desc())).first()
    assert (h.previous_responsibility, h.new_responsibility) == (C, P)
    assert h.previous_status == h.new_status == before_status
    assert "by hand by Pat Person" in h.change_reason
    row = db_session.scalars(select(AuditLog).where(
        AuditLog.entity_id == cs.id, AuditLog.action == "control_state.responsibility_set")).one()
    assert (row.before_value["responsibility"], row.after_value["responsibility"]) == (C, P)
    assert row.context["via"] == "manual"


@pytest.mark.integration
def test_a_value_nobody_set_still_moves_with_the_tool(db_session, w):
    activate_org_product(db_session, w["org"].id, w["p"].id, w["a"].id)
    assert _cs(db_session, w["s"]["c2a"]).responsibility == P
    assert _cs(db_session, w["s"]["c3a"]).responsibility == S


@pytest.mark.integration
def test_activation_with_nobody_asked_keeps_the_persons_value(db_session, w):
    _by_hand(db_session, w, "c2a", E)
    result = activate_org_product(db_session, w["org"].id, w["p"].id, w["a"].id)
    assert _cs(db_session, w["s"]["c2a"]).responsibility == E, "a tool never overwrites unasked"
    assert result["responsibility"] == [{
        "control_state_id": str(w["s"]["c2a"].id), "from": E, "tool": P, "to": E,
        "decided_by": "default"}]
    h = db_session.scalars(select(ControlStateHistory).where(
        ControlStateHistory.control_state_id == w["s"]["c2a"].id)
        .order_by(ControlStateHistory.changed_at.desc(), ControlStateHistory.id)).all()
    assert any("responsibility kept at external_system, set by a person" in (x.change_reason or "")
               for x in h)
    # The objective nobody touched by hand moved as before.
    assert _cs(db_session, w["s"]["c1a"]).responsibility == P


@pytest.mark.integration
@pytest.mark.parametrize("choice,expected", [("keep", E), ("take_tool", P)])
def test_activation_asked_both_answers(db_session, w, choice, expected):
    _by_hand(db_session, w, "c2a", E)
    cs_id = w["s"]["c2a"].id
    activate_org_product(db_session, w["org"].id, w["p"].id, w["a"].id, decisions={},
                         responsibility_decisions={cs_id: (E, P, choice)})
    assert _cs(db_session, w["s"]["c2a"]).responsibility == expected


@pytest.mark.integration
def test_the_preview_asks_and_writes_nothing(db_session, w):
    _by_hand(db_session, w, "c2a", E)
    preview = activation_preview(db_session, w["org"].id, w["p"].id, w["a"].id)
    db_session.rollback()
    assert [(r["control_id"], r["objective_key"], r["responsibility"], r["tool_would_set"],
             r["default"], r["becomes"], r["set_by"]) for r in preview["responsibility"]] == [
        ("AC.L2-3.1.2", "a", E, P, "keep", E, "Pat Person")]
    taken = activation_preview(db_session, w["org"].id, w["p"].id, w["a"].id,
                               responsibility_choices={w["s"]["c2a"].id: "take_tool"})
    db_session.rollback()
    assert taken["responsibility"][0]["becomes"] == P
    assert _cs(db_session, w["s"]["c2a"]).responsibility == E


@pytest.mark.integration
def test_an_answer_for_a_value_changed_since_shown_refuses(db_session, w):
    _by_hand(db_session, w, "c2a", E)
    cs_id = w["s"]["c2a"].id
    _by_hand(db_session, w, "c2a", C)
    with pytest.raises(ActivationJudgementsChanged):
        activate_org_product(db_session, w["org"].id, w["p"].id, w["a"].id, decisions={},
                             responsibility_decisions={cs_id: (E, P, "take_tool")})


@pytest.mark.integration
def test_an_unanswered_responsibility_refuses_when_answers_are_given(db_session, w):
    _by_hand(db_session, w, "c2a", E)
    with pytest.raises(ActivationJudgementsChanged):
        activate_org_product(db_session, w["org"].id, w["p"].id, w["a"].id, decisions={},
                             responsibility_decisions={})


@pytest.mark.integration
def test_deactivation_keeps_a_persons_value(db_session, w):
    """c3 is covered by P and Q (both shared). The person sets provider;
    removing P leaves Q, whose value (shared) would otherwise be set."""
    activate_org_product(db_session, w["org"].id, w["p"].id, w["a"].id)
    activate_org_product(db_session, w["org"].id, w["q"].id, w["a"].id)
    _by_hand(db_session, w, "c3a", P)
    deactivate_org_product(db_session, w["org"].id, w["p"].id, w["a"].id)
    assert _cs(db_session, w["s"]["c3a"]).responsibility == P


@pytest.mark.integration
def test_a_version_move_keeps_a_persons_value_and_says_so(db_session, w):
    r, _ = _product(db_session, w["fw"], "tool-r", {w["c1"]: (["a", "b"], "shared")})
    activate_org_product(db_session, w["org"].id, r.id, w["a"].id)
    _by_hand(db_session, w, "c1a", E)
    v2 = ProductBaselineVersion(product_id=r.id, version_number=2)
    db_session.add(v2)
    db_session.flush()
    db_session.add(BaselineControl(product_id=r.id, baseline_version_id=v2.id,
                                   control_id=w["c1"].id, objectives=["a", "b"],
                                   classification="provider_satisfies"))
    db_session.flush()
    result = move_org_product_version(db_session, w["org"].id, r.id, w["a"].id, v2.id)
    assert _cs(db_session, w["s"]["c1a"]).responsibility == E
    assert _cs(db_session, w["s"]["c1b"]).responsibility == P, "nobody set c1[b]; it moves"
    assert [(k["control_id"], k["objective_key"], k["kept"], k["tool_would_set"])
            for k in result["responsibility_kept"]] == [("AC.L2-3.1.1", "a", E, P)]


# ---------------------------------------------------------------------------
# Through HTTP
# ---------------------------------------------------------------------------


@pytest.fixture
def as_role(db_session, w):
    def _as(role):
        user = _make_fake_user(role=role, org_id=w["org"].id,
                               email=f"{uuid.uuid4().hex[:8]}@x.com", display_name=f"P {role}")
        _grant(db_session, user)
        db_session.commit()
        app.dependency_overrides[get_session] = _app_session(db_session)
        app.dependency_overrides[get_current_user] = _authed(db_session, user)
        return TestClient(app)
    yield _as
    app.dependency_overrides.clear()


def _resp_url(w, key):
    return (f"/orgs/{w['org'].id}/assessments/{w['a'].id}/control-states/"
            f"{w['s'][key].id}/responsibility")


@pytest.mark.integration
@pytest.mark.parametrize("role,allowed", [
    ("msp_admin", True), ("consultant_admin", True), ("msp_engineer", True),
    ("customer_poc", False), ("c3pao_assessor", False),
])
def test_the_endpoint_and_its_role_gate(db_session, w, as_role, role, allowed):
    c = as_role(role)
    r = c.put(_resp_url(w, "c2a"), json={"responsibility": P})
    if not allowed:
        assert r.status_code == 403, r.text
        assert _cs(db_session, w["s"]["c2a"]).responsibility == C
        return
    assert r.status_code == 200, r.text
    assert r.json()["responsibility_set_by"] == f"P {role}"
    assert _cs(db_session, w["s"]["c2a"]).responsibility == P
    bad = c.put(_resp_url(w, "c2a"), json={"responsibility": "nobody"})
    assert bad.status_code == 422


@pytest.mark.integration
def test_the_suggestion_is_recorded_as_one(db_session, w, as_role):
    c = as_role("msp_engineer")
    r = c.put(_resp_url(w, "c2a"), json={"responsibility": S, "via": "raci_suggestion"})
    assert r.status_code == 200, r.text
    row = db_session.scalars(select(AuditLog).where(
        AuditLog.action == "control_state.responsibility_set",
        AuditLog.entity_id == w["s"]["c2a"].id)).one()
    assert row.context["via"] == "raci_suggestion"


@pytest.mark.integration
def test_a_raci_change_alone_never_writes_responsibility(db_session, w, as_role):
    """Jarrod's mapping is the suggestion's content, shown in the drawer;
    the server never derives responsibility from RACI."""
    msp = Contact(org_id=w["org"].id, name="M", email="m@x.test", affiliation="msp")
    cust = Contact(org_id=w["org"].id, name="K", email="k@x.test", affiliation="customer")
    db_session.add_all([msp, cust])
    db_session.commit()
    c = as_role("msp_admin")
    base = f"/orgs/{w['org'].id}/assessments/{w['a'].id}/raci"
    for contact in (msp, cust):
        r = c.post(base, json={"control_state_id": str(w["s"]["c2a"].id),
                               "contact_id": str(contact.id), "raci_letter": "R"})
        assert r.status_code == 201, r.text
    r = c.post(f"{base}/bulk", json={"family": "AC", "contact_id": str(msp.id),
                                      "raci_letter": "R"})
    assert r.status_code == 200, r.text
    cs = _cs(db_session, w["s"]["c2a"])
    assert (cs.responsibility, cs.responsibility_set_by) == (C, None)
    assert db_session.scalars(select(AuditLog).where(
        AuditLog.action == "control_state.responsibility_set")).all() == []


@pytest.mark.integration
def test_activation_through_http_asks_then_keeps_or_takes(db_session, w, as_role):
    c = as_role("msp_admin")
    put = c.put(_resp_url(w, "c2a"), json={"responsibility": E})
    assert put.status_code == 200
    url = f"/orgs/{w['org'].id}/assessments/{w['a'].id}/products/{w['p'].id}"
    pv = c.post(f"{url}/activation-preview", json={})
    assert pv.status_code == 200, pv.text
    asks = pv.json()["responsibility"]
    assert [(a["responsibility"], a["tool_would_set"], a["default"]) for a in asks] == [
        (E, P, "keep")]
    r = c.post(f"{url}/activate", json={"decisions": [], "responsibility_decisions": [{
        "control_state_id": asks[0]["control_state_id"], "shown_responsibility": E,
        "tool_responsibility": P, "choice": "keep"}]})
    assert r.status_code == 200, r.text
    assert r.json()["responsibility"][0]["responsibility_after"] == E
    assert r.json()["responsibility"][0]["decided_by"] == "person"
    assert _cs(db_session, w["s"]["c2a"]).responsibility == E


@pytest.mark.integration
def test_a_customer_may_set_a_status_but_not_responsibility(db_session, w, as_role):
    """The asymmetry the gate rests on: a status is a claim about the
    customer's own system; responsibility asserts the MSP's obligations,
    so it is set on the MSP's side."""
    c = as_role("customer_poc")
    status = c.patch(f"/orgs/{w['org'].id}/assessments/{w['a'].id}/control-states/"
                     f"{w['s']['c2a'].id}", json={"status": "partial"})
    assert status.status_code == 200, (
        "customer_poc must be able to edit a status, or this test proves nothing")
    resp = c.put(_resp_url(w, "c2a"), json={"responsibility": P})
    assert resp.status_code == 403
    assert _cs(db_session, w["s"]["c2a"]).responsibility == C
