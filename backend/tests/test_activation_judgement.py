"""Activation asks before it overwrites a judgement (Jarrod, 2026-11-03).

docs/roadmap.md's "Activating a tool overwrites a status a person set" is
the spec. Where a product would be the first contributor to an objective a
person judged (met, partial, not_applicable, inherited), the activation
screen asks; every path where nobody is asked sets needs_review.

The paths that reach assessment.contributor_added_status, each tested here:
  - activation with the person's answers (the screen's POST);
  - activation with none (an API call that sends no decisions);
  - a baseline version move that brings in a newly covered control;
  - start_assessment, where nothing can be judged yet.

Every starting status is covered both ways in test_assessment_engine.py;
here each path is driven through the database and its record read back.

The fixture's framework, by SPRS weight:
  AC.L2-3.1.1 (5): objectives a, b -- P covers both
  AC.L2-3.1.2 (3): objective a     -- P covers it (the judged one)
  AU.L2-3.3.1 (1): objective a     -- P and Q cover it
All three not_met: 110 - 9 = 101.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit
from app.assessment import JUDGED_STATUSES, ControlStatus
from app.auth import get_current_user
from app.db import get_session
from app.engine import (
    ActivationJudgementsChanged,
    activate_org_product,
    activation_preview,
    move_org_product_version,
    recompute_sprs,
    start_assessment,
)
from app.main import app
from app.models import (
    Assessment,
    AssessmentObjective,
    AuditLog,
    BaselineControl,
    Control,
    ControlState,
    ControlStateContributor,
    ControlStateHistory,
    Framework,
    Organization,
    OrgProduct,
    Product,
    ProductBaselineVersion,
)
from tests.conftest import _app_session, _authed, _grant

pytestmark = pytest.mark.integration


def _product(db_session, fw, key, claims):
    """A published product whose v1 claims {control: (objectives, classification)}."""
    p = Product(
        framework_id=fw.id, key=key, name=key.title(), provider="Test", category="ESP",
        asset_type="SPA", role="test", is_published=True,
    )
    db_session.add(p)
    db_session.flush()
    v = ProductBaselineVersion(product_id=p.id, version_number=1)
    db_session.add(v)
    db_session.flush()
    p.current_version_id = v.id
    for ctrl, (objs, cls) in claims.items():
        db_session.add(BaselineControl(
            product_id=p.id, baseline_version_id=v.id, control_id=ctrl.id,
            objectives=objs, classification=cls,
        ))
    db_session.flush()
    return p, v


@pytest.fixture
def w(db_session: Session) -> dict:
    org = Organization(name=f"Judgement-{uuid.uuid4().hex[:6]}")
    fw = Framework(key=f"fw-judge-{uuid.uuid4().hex[:6]}", name="FW", version="r2")
    db_session.add_all([org, fw])
    db_session.flush()
    c1 = Control(framework_id=fw.id, control_id="AC.L2-3.1.1", family="AC", title="c1",
                 requirement_text="c1", sprs_weight=5, sequence_order=1)
    c2 = Control(framework_id=fw.id, control_id="AC.L2-3.1.2", family="AC", title="c2",
                 requirement_text="c2", sprs_weight=3, sequence_order=2)
    c3 = Control(framework_id=fw.id, control_id="AU.L2-3.3.1", family="AU", title="c3",
                 requirement_text="c3", sprs_weight=1, sequence_order=3)
    db_session.add_all([c1, c2, c3])
    db_session.flush()
    objs = {
        "c1a": AssessmentObjective(control_id=c1.id, objective_key="a", text="c1[a]"),
        "c1b": AssessmentObjective(control_id=c1.id, objective_key="b", text="c1[b]"),
        "c2a": AssessmentObjective(control_id=c2.id, objective_key="a", text="c2[a] text"),
        "c3a": AssessmentObjective(control_id=c3.id, objective_key="a", text="c3[a]"),
    }
    db_session.add_all(objs.values())
    db_session.flush()
    p, pv1 = _product(db_session, fw, "tool-p", {
        c1: (["a", "b"], "provider_satisfies"),
        c2: (["a"], "provider_satisfies"),
        c3: (["a"], "shared"),
    })
    q, _ = _product(db_session, fw, "tool-q", {c3: (["a"], "shared")})
    a = start_assessment(db_session, org.id, fw.id, "A")
    states = {
        k: db_session.scalars(select(ControlState).where(
            ControlState.assessment_id == a.id, ControlState.objective_id == o.id)).one()
        for k, o in objs.items()
    }
    assert a.sprs_score == 101, "fixture must start at 110 - 9 or the numbers prove nothing"
    # Committed (a savepoint released inside the test's own transaction) so
    # a request that rolls back, as the preview always does, keeps it.
    db_session.commit()
    return {"org": org, "fw": fw, "c1": c1, "c2": c2, "c3": c3, "p": p, "pv1": pv1, "q": q,
            "a": a, "s": states}


def _set_by_hand(db_session, w, key, status):
    w["s"][key].status = status
    db_session.flush()
    score = recompute_sprs(db_session, w["a"].id)
    db_session.commit()
    return score


def _status(db_session, cs):
    db_session.expire(cs)
    return db_session.get(ControlState, cs.id).status


# -- activation, nobody asked ----------------------------------------------


@pytest.mark.parametrize("status", list(ControlStatus))
def test_nobody_asked_every_starting_status(db_session, w, status):
    _set_by_hand(db_session, w, "c2a", status)
    activate_org_product(db_session, w["org"].id, w["p"].id, w["a"].id)
    expected = {
        "not_met": "pending_evidence", "pending_evidence": "pending_evidence",
        "needs_review": "needs_review",
    }.get(status, "needs_review")
    assert _status(db_session, w["s"]["c2a"]) == expected


def test_nobody_asked_is_recorded_as_the_default(db_session, w):
    _set_by_hand(db_session, w, "c2a", "met")
    activate_org_product(db_session, w["org"].id, w["p"].id, w["a"].id)
    row = db_session.scalars(select(AuditLog).where(
        AuditLog.entity_id == w["s"]["c2a"].id, AuditLog.action == "control_state.update",
    )).one()
    assert row.context["decided_by"] == "default"
    assert row.context["decided_at"] == "activation"
    assert row.before_value == {"status": "met"} and row.after_value == {"status": "needs_review"}
    hist = db_session.scalars(select(ControlStateHistory).where(
        ControlStateHistory.control_state_id == w["s"]["c2a"].id,
        ControlStateHistory.new_status == "needs_review",
    )).one()
    assert "the default when nobody is asked" in hist.change_reason
    assert "Magic loop" not in hist.change_reason


# -- activation, asked -----------------------------------------------------


# Named here, not taken from JUDGED_STATUSES: a case list drawn from the
# set under test shrinks with it, so dropping a status would drop its cases
# instead of failing them (seen by mutation on the bench, 2026-11-03).
@pytest.mark.parametrize("status", ["inherited", "met", "not_applicable", "partial"])
@pytest.mark.parametrize("choice", ["keep", "needs_review"])
def test_asked_every_judged_status_both_answers(db_session, w, status, choice):
    _set_by_hand(db_session, w, "c2a", status)
    cs_id = w["s"]["c2a"].id
    result = activate_org_product(
        db_session, w["org"].id, w["p"].id, w["a"].id, decisions={cs_id: (status, choice)},
    )
    want = status if choice == "keep" else "needs_review"
    assert _status(db_session, w["s"]["c2a"]) == want
    assert [j["decided_by"] for j in result["judged"]] == ["person"]
    hist = db_session.scalars(select(ControlStateHistory).where(
        ControlStateHistory.control_state_id == cs_id,
        ControlStateHistory.change_reason.like("Asked at%"),
    )).one()
    assert "chosen by a person" in hist.change_reason


@pytest.fixture
def as_person(fake_msp_admin):
    """The request's actor, as auth sets it, reset afterwards: a ContextVar
    left set leaks into every later test in the thread."""
    a = audit._current_actor.set(str(fake_msp_admin.id))
    t = audit._current_actor_type.set("user")
    yield fake_msp_admin
    audit._current_actor.reset(a)
    audit._current_actor_type.reset(t)


def test_asked_records_the_person_and_the_score(db_session, w, as_person):
    fake_msp_admin = as_person
    _set_by_hand(db_session, w, "c2a", "met")
    assert w["a"].sprs_score == 104, "fixture: c2 met by hand must lift 101 to 104"
    cs_id = w["s"]["c2a"].id
    activate_org_product(
        db_session, w["org"].id, w["p"].id, w["a"].id, decisions={cs_id: ("met", "keep")},
    )
    row = db_session.scalars(select(AuditLog).where(
        AuditLog.entity_id == cs_id, AuditLog.action == "control_state.update")).one()
    assert row.actor == str(fake_msp_admin.id)
    assert row.context["decided_by"] == "person" and row.context["choice"] == "keep"
    act = db_session.scalars(
        select(AuditLog).where(AuditLog.action == "org_product.activate")
    ).one()
    assert act.before_value["sprs_score"] == 104
    assert act.after_value["sprs_score"] == 104
    assert act.context["asked"] is True
    assert act.after_value["judged"] == [{
        "control_state_id": str(cs_id), "from": "met", "to": "met", "decided_by": "person",
    }]


def test_the_score_before_and_after(db_session, w):
    """The numbers in the PR. c2 (weight 3) met by hand: 104. The old code
    took it to pending_evidence (101); asked and kept, 104; asked and sent
    to review, or nobody asked, 101."""
    _set_by_hand(db_session, w, "c2a", "met")
    assert db_session.get(Assessment, w["a"].id).sprs_score == 104
    cs_id = w["s"]["c2a"].id
    p = activation_preview(db_session, w["org"].id, w["p"].id, w["a"].id)
    assert (p["sprs_now"], p["sprs_after"]) == (104, 101)  # default: needs_review
    p = activation_preview(db_session, w["org"].id, w["p"].id, w["a"].id, {cs_id: "keep"})
    assert (p["sprs_now"], p["sprs_after"]) == (104, 104)
    activate_org_product(db_session, w["org"].id, w["p"].id, w["a"].id,
                         decisions={cs_id: ("met", "keep")})
    assert db_session.get(Assessment, w["a"].id).sprs_score == 104


def test_preview_states_the_score_the_activation_then_gives(db_session, w):
    _set_by_hand(db_session, w, "c2a", "not_applicable")
    _set_by_hand(db_session, w, "c1a", "met")
    _set_by_hand(db_session, w, "c1b", "met")
    p = activation_preview(db_session, w["org"].id, w["p"].id, w["a"].id)
    choices = {uuid.UUID(o["control_state_id"]): o["choice"] for o in p["objectives"]}
    assert {o["status"]: o["default"] for o in p["objectives"]} == {
        "met": "needs_review", "not_applicable": "keep",
    }
    activate_org_product(db_session, w["org"].id, w["p"].id, w["a"].id, decisions={
        uuid.UUID(o["control_state_id"]): (o["status"], choices[uuid.UUID(o["control_state_id"])])
        for o in p["objectives"]
    })
    assert db_session.get(Assessment, w["a"].id).sprs_score == p["sprs_after"]


def test_preview_writes_nothing(db_session, w):
    _set_by_hand(db_session, w, "c2a", "met")
    before = (
        db_session.scalar(select(OrgProduct.id).where(OrgProduct.product_id == w["p"].id)),
        db_session.scalars(select(AuditLog.id)).all(),
        db_session.scalars(select(ControlStateContributor.id)).all(),
        {k: _status(db_session, cs) for k, cs in w["s"].items()},
        db_session.get(Assessment, w["a"].id).sprs_score,
    )
    activation_preview(db_session, w["org"].id, w["p"].id, w["a"].id)
    after = (
        db_session.scalar(select(OrgProduct.id).where(OrgProduct.product_id == w["p"].id)),
        db_session.scalars(select(AuditLog.id)).all(),
        db_session.scalars(select(ControlStateContributor.id)).all(),
        {k: _status(db_session, cs) for k, cs in w["s"].items()},
        db_session.get(Assessment, w["a"].id).sprs_score,
    )
    assert after == before


# -- what is not asked -----------------------------------------------------


def test_an_existing_contributor_is_not_asked_and_not_changed(db_session, w):
    activate_org_product(db_session, w["org"].id, w["q"].id, w["a"].id)
    _set_by_hand(db_session, w, "c3a", "met")
    p = activation_preview(db_session, w["org"].id, w["p"].id, w["a"].id)
    assert all(o["control_state_id"] != str(w["s"]["c3a"].id) for o in p["objectives"])
    activate_org_product(db_session, w["org"].id, w["p"].id, w["a"].id, decisions={})
    assert _status(db_session, w["s"]["c3a"]) == "met"


def test_unjudged_objectives_are_not_shown(db_session, w):
    p = activation_preview(db_session, w["org"].id, w["p"].id, w["a"].id)
    assert p["objectives"] == []
    _set_by_hand(db_session, w, "c2a", "needs_review")
    assert activation_preview(db_session, w["org"].id, w["p"].id, w["a"].id)["objectives"] == []


# -- answers that no longer match ------------------------------------------


def test_a_status_changed_since_asked_refuses(db_session, w):
    _set_by_hand(db_session, w, "c2a", "met")
    cs_id = w["s"]["c2a"].id
    _set_by_hand(db_session, w, "c2a", "not_applicable")
    with pytest.raises(ActivationJudgementsChanged):
        activate_org_product(db_session, w["org"].id, w["p"].id, w["a"].id,
                             decisions={cs_id: ("met", "keep")})


def test_a_judgement_not_answered_refuses(db_session, w):
    _set_by_hand(db_session, w, "c2a", "met")
    with pytest.raises(ActivationJudgementsChanged):
        activate_org_product(db_session, w["org"].id, w["p"].id, w["a"].id, decisions={})


def test_an_answer_for_nothing_judged_refuses(db_session, w):
    with pytest.raises(ActivationJudgementsChanged):
        activate_org_product(db_session, w["org"].id, w["p"].id, w["a"].id,
                             decisions={w["s"]["c2a"].id: ("met", "keep")})


# -- the other paths -------------------------------------------------------


def test_start_assessment_judges_nothing(db_session, w):
    activate_org_product(db_session, w["org"].id, w["p"].id, w["a"].id)
    a2 = start_assessment(db_session, w["org"].id, w["fw"].id, "A2")
    reasons = db_session.scalars(select(ControlStateHistory.change_reason).join(
        ControlState, ControlStateHistory.control_state_id == ControlState.id,
    ).where(ControlState.assessment_id == a2.id)).all()
    assert reasons, "fixture: the active product must reach the new assessment"
    assert not any("nobody is asked" in r for r in reasons)


@pytest.mark.parametrize("status", list(ControlStatus))
def test_version_move_every_starting_status(db_session, w, status):
    """v1 of R claims c1 only; v2 adds c2. Moving brings c2 in with nobody asked."""
    r, v1 = _product(db_session, w["fw"], "tool-r", {w["c1"]: (["a", "b"], "shared")})
    activate_org_product(db_session, w["org"].id, r.id, w["a"].id)
    v2 = ProductBaselineVersion(product_id=r.id, version_number=2)
    db_session.add(v2)
    db_session.flush()
    for ctrl in (w["c1"], w["c2"]):
        db_session.add(BaselineControl(
            product_id=r.id, baseline_version_id=v2.id, control_id=ctrl.id,
            objectives=["a", "b"] if ctrl is w["c1"] else ["a"], classification="shared",
        ))
    db_session.flush()
    _set_by_hand(db_session, w, "c2a", status)
    move_org_product_version(db_session, w["org"].id, r.id, w["a"].id, v2.id)
    expected = {
        "not_met": "pending_evidence", "pending_evidence": "pending_evidence",
    }.get(status, "needs_review")
    assert _status(db_session, w["s"]["c2a"]) == expected
    if status in JUDGED_STATUSES:
        row = db_session.scalars(select(AuditLog).where(
            AuditLog.entity_id == w["s"]["c2a"].id,
            AuditLog.action == "control_state.update",
            AuditLog.context["decided_at"].astext == "baseline_version_move",
        )).one()
        assert row.context["decided_by"] == "default"


# -- through the API: the screen's two calls, and a cancel -----------------


@pytest.fixture
def client(db_session, fake_msp_admin, w):
    _grant(db_session, fake_msp_admin, org_id=w["org"].id)
    app.dependency_overrides[get_session] = _app_session(db_session)
    app.dependency_overrides[get_current_user] = _authed(db_session, fake_msp_admin)
    yield TestClient(app)
    app.dependency_overrides.clear()


def _url(w, tail):
    return f"/orgs/{w['org'].id}/assessments/{w['a'].id}/products/{w['p'].id}/{tail}"


def test_api_cancel_leaves_everything_as_it_was(client, db_session, w):
    """A cancel, a closed tab and a dropped connection are the same thing
    to the server: the preview was asked and the activate never came."""
    _set_by_hand(db_session, w, "c2a", "met")
    r = client.post(_url(w, "activation-preview"), json={})
    assert r.status_code == 200, r.text
    body = r.json()
    assert [o["status"] for o in body["objectives"]] == ["met"]
    assert (body["sprs_now"], body["sprs_after"]) == (104, 101)
    assert db_session.scalar(select(OrgProduct.status).where(
        OrgProduct.product_id == w["p"].id)) is None
    assert _status(db_session, w["s"]["c2a"]) == "met"
    assert db_session.get(Assessment, w["a"].id).sprs_score == 104
    assert db_session.scalars(select(AuditLog).where(
        AuditLog.action == "org_product.activate")).all() == []


def test_api_confirm_with_answers(client, db_session, w):
    _set_by_hand(db_session, w, "c2a", "met")
    cs_id = str(w["s"]["c2a"].id)
    r = client.post(_url(w, "activation-preview"),
                    json={"choices": [{"control_state_id": cs_id, "choice": "keep"}]})
    assert r.json()["sprs_after"] == 104
    r = client.post(_url(w, "activate"), json={"decisions": [
        {"control_state_id": cs_id, "shown_status": "met", "choice": "keep"},
    ]})
    assert r.status_code == 200, r.text
    assert r.json()["judged"] == [{
        "control_state_id": cs_id, "status_before": "met", "status_after": "met",
        "decided_by": "person",
    }]
    assert _status(db_session, w["s"]["c2a"]) == "met"
    assert db_session.get(Assessment, w["a"].id).sprs_score == 104


def test_api_stale_answers_are_refused_and_nothing_moves(client, db_session, w):
    _set_by_hand(db_session, w, "c2a", "met")
    cs_id = str(w["s"]["c2a"].id)
    _set_by_hand(db_session, w, "c2a", "inherited")
    r = client.post(_url(w, "activate"), json={"decisions": [
        {"control_state_id": cs_id, "shown_status": "met", "choice": "keep"},
    ]})
    assert r.status_code == 409, r.text
    assert "Nothing was activated" in r.json()["detail"]
    assert db_session.scalar(select(OrgProduct.status).where(
        OrgProduct.product_id == w["p"].id)) is None
    assert _status(db_session, w["s"]["c2a"]) == "inherited"
