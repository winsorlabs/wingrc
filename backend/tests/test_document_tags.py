"""Item X.1: a document is tagged from a screen, and a tag a link rests on
cannot be removed (document_evidence.py's tag section; docs/roadmap.md).

What these pin:
- A typed key is either spelling, one objective or a whole practice; a
  practice expands to its objectives, each stored as its own tag and
  removable alone. An unknown key is refused by name.
- Removing a tag is refused while a live link from any version of the
  document rests on it, in any assessment -- by the check and by the
  DELETE on its own -- and named as fields, the list refusal's shape.
- Who tags is who defines a list; c3pao_assessor and customer_poc are
  refused; RLS keeps orgs apart. No control_state or SPRS change.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.engine import start_assessment
from app.main import app
from app.models import AuditLog, ControlState
from tests.test_document_evidence import (
    DOCS,
    STATE_DOCS,
    _as,
    _attach,
    _edit,
    _objective,
    _person,
    _publish,
    _setup,
    _states,
)


def test_the_tag_removal_check_is_a_get_and_the_changes_are_not():
    paths = app.openapi()["paths"]
    base = "/orgs/{org_id}/documents/{document_id}/objective-tags"
    assert set(paths[base]) == {"post"}
    assert set(paths[base + "/{objective_id}"]) == {"delete"}
    assert set(paths[base + "/{objective_id}/removal"]) == {"get"}


def _tags_url(w):
    return f"{DOCS.format(org=w['org'])}/{w['doc']['id']}/objective-tags"


def _tagged(client, w):
    r = client.get(f"{DOCS.format(org=w['org'])}/{w['doc']['id']}")
    assert r.status_code == 200, r.text
    return [t["objective_key"] for t in r.json()["tagged_objectives"]]


def _tag(client, w, typed):
    return client.post(_tags_url(w), json={"objective_key": typed})


@pytest.mark.integration
def test_a_practice_key_tags_each_objective_and_each_is_removable_alone(db_session):
    w = _setup(db_session, "expand", tags=())
    blake = _as(db_session, w["blake"])
    assert _tagged(blake, w) == [], "fixture must start untagged or this test proves nothing"

    r = _tag(blake, w, "3.10.3")
    assert r.status_code == 201, r.text
    assert [t["objective_key"] for t in r.json()["added"]] == ["PE.L2-3.10.3[a]", "PE.L2-3.10.3[b]"]
    assert _tagged(blake, w) == ["PE.L2-3.10.3[a]", "PE.L2-3.10.3[b]"]

    again = _tag(blake, w, "PE.L2-3.10.3[a]").json()
    assert (again["added"], [t["objective_key"] for t in again["already_tagged"]]) == (
        [], ["PE.L2-3.10.3[a]"]
    )

    b = _objective(db_session, w, "PE.L2-3.10.3[b]")
    assert blake.delete(f"{_tags_url(w)}/{b}").status_code == 204
    assert _tagged(blake, w) == ["PE.L2-3.10.3[a]"], "removing one leaves its sibling"

    db_session.expire_all()
    audit = db_session.execute(
        select(AuditLog.action, AuditLog.after_value, AuditLog.before_value)
        .where(
            AuditLog.entity_id == uuid.UUID(w["doc"]["id"]),
            AuditLog.action.like("document.objective_tag%"),
        )
    ).all()
    # Sorted, not ordered by created_at: every row here has the one now().
    assert sorted((a, (av or bv)["objective_key"]) for a, av, bv in audit) == [
        ("document.objective_tag.add", "PE.L2-3.10.3[a]"),
        ("document.objective_tag.add", "PE.L2-3.10.3[b]"),
        ("document.objective_tag.remove", "PE.L2-3.10.3[b]"),
    ], "one record per objective, each saying which"


@pytest.mark.integration
def test_both_spellings_are_accepted_and_an_unknown_key_is_named(db_session):
    w = _setup(db_session, "spell", tags=())
    blake = _as(db_session, w["blake"])
    assert _tag(blake, w, "pe.l2-3.10.3[B]").status_code == 201
    assert _tag(blake, w, " 3.1.1[a] ").status_code == 201
    assert _tagged(blake, w) == ["PE.L2-3.10.3[b]", "AC.L2-3.1.1[a]"]

    for typed, named in [
        ("3.10.3[z]", "PE.L2-3.10.3[z] is not in the catalog"),
        ("3.4.9", "CM.L2-3.4.9 is not in the catalog"),
        ("3.99.1", "'3.99.1' is not an objective or practice"),
        ("access control", "'access control' is not an objective or practice"),
    ]:
        r = _tag(blake, w, typed)
        assert r.status_code == 422 and named in r.json()["detail"], (typed, r.text)
    assert len(_tagged(blake, w)) == 2, "a refused key writes nothing"


@pytest.mark.integration
def test_a_tag_a_live_link_rests_on_cannot_be_removed(db_session):
    w = _setup(db_session, "refuse")
    blake = _as(db_session, w["blake"])
    _publish(blake, w)
    before = _states(db_session, w)
    a = _objective(db_session, w, "AC.L2-3.1.1[a]")
    check = f"{_tags_url(w)}/{a}/removal"
    assert blake.get(check).json() == {"removable": True, "reason": None, "links": []}, (
        "fixture: nothing rests on the tag before the attach, or the refusal proves nothing"
    )
    assert _attach(_as(db_session, w["avery"]), w, "AC.L2-3.1.1[a]").status_code == 201

    blake = _as(db_session, w["blake"])
    links = [{"version_number": 1, "objective_key": "AC.L2-3.1.1[a]",
              "assessment_name": "FY26 L2 refuse"}]
    assert blake.get(check).json() == {"removable": False, "reason": "evidence", "links": links}
    r = blake.delete(f"{_tags_url(w)}/{a}")
    assert r.status_code == 409, r.text
    assert (r.json()["detail"]["reason"], r.json()["detail"]["links"]) == ("evidence", links), (
        "the DELETE refuses on its own, in the check's fields"
    )
    assert _tagged(blake, w) == ["AC.L2-3.1.1[a]"]

    assert _attach(_as(db_session, w["avery"]), w, "AC.L2-3.1.1[a]", "delete").status_code == 200
    blake = _as(db_session, w["blake"])
    assert blake.get(check).json()["removable"] is True
    assert blake.delete(f"{_tags_url(w)}/{a}").status_code == 204, "detached, the tag may go"
    assert _states(db_session, w) == before, "tags and links move no status and no SPRS"


@pytest.mark.integration
def test_a_superseded_version_and_another_assessment_both_block(db_session):
    """Any version of the document, in any assessment: a republish leaves
    the link on v1 (item X), and it still rests on the tag."""
    w = _setup(db_session, "anyver")
    blake = _as(db_session, w["blake"])
    _publish(blake, w)
    assert _attach(_as(db_session, w["avery"]), w, "AC.L2-3.1.1[a]").status_code == 201
    second = start_assessment(db_session, w["org"], w["fw"], "Second")
    db_session.flush()
    a = _objective(db_session, w, "AC.L2-3.1.1[a]")
    cs2 = db_session.scalar(select(ControlState).where(
        ControlState.assessment_id == second.id, ControlState.objective_id == a,
    ))
    blake = _as(db_session, w["blake"])
    _edit(blake, w, "Version two.")
    _publish(blake, w)
    avery = _as(db_session, w["avery"])
    url2 = f"{STATE_DOCS.format(org=w['org'], a=second.id, cs=cs2.id)}/{w['doc']['id']}"
    assert avery.post(url2).status_code == 201, "fixture: v2 attached in the second assessment"

    check = f"{_tags_url(w)}/{a}/removal"
    got = avery.get(check).json()
    assert got["links"] == [
        {"version_number": 1, "objective_key": "AC.L2-3.1.1[a]",
         "assessment_name": "FY26 L2 anyver"},
        {"version_number": 2, "objective_key": "AC.L2-3.1.1[a]", "assessment_name": "Second"},
    ]
    assert _attach(avery, w, "AC.L2-3.1.1[a]", "delete").status_code == 200
    assert [k["assessment_name"] for k in avery.get(check).json()["links"]] == ["Second"], (
        "detached in one assessment, the other still blocks"
    )


@pytest.mark.integration
def test_who_may_tag_is_who_defines_a_list(db_session):
    w = _setup(db_session, "roles", tags=())
    for role, code in [
        ("msp_engineer", 201), ("consultant_admin", 201),
        ("customer_poc", 403), ("c3pao_assessor", 403),
    ]:
        who = _as(db_session, _person(db_session, w["org"], role, role))
        typed = "3.10.3[a]" if code == 201 else "3.10.3[b]"
        assert _tag(who, w, typed).status_code == code, role
    a = _objective(db_session, w, "PE.L2-3.10.3[a]")
    for role in ("customer_poc", "c3pao_assessor"):
        who = _as(db_session, _person(db_session, w["org"], role, role))
        assert who.delete(f"{_tags_url(w)}/{a}").status_code == 403, role
        assert who.get(f"{_tags_url(w)}/{a}/removal").status_code == 200, (
            f"{role} reads what a removal would do"
        )
    assert _tagged(_as(db_session, w["blake"]), w) == ["PE.L2-3.10.3[a]"]


@pytest.mark.integration
def test_another_org_cannot_tag_or_check_this_documents_tags(db_session):
    w = _setup(db_session, "rlsa")
    b = _setup(db_session, "rlsb", tags=())
    a = _objective(db_session, w, "AC.L2-3.1.1[a]")
    intruder = _as(db_session, _person(db_session, b["org"], "Ira Intruder", "msp_admin"))
    # Org B's own path, org A's document id: RLS hides it, so not found.
    own_path = f"{DOCS.format(org=b['org'])}/{w['doc']['id']}/objective-tags"
    assert intruder.post(own_path, json={"objective_key": "3.1.1[a]"}).status_code == 404
    assert intruder.get(f"{own_path}/{a}/removal").status_code == 404
    assert intruder.delete(f"{own_path}/{a}").status_code == 404
    assert intruder.post(_tags_url(w), json={"objective_key": "3.1.1[a]"}).status_code in (403, 404)
    assert _tagged(_as(db_session, w["blake"]), w) == ["AC.L2-3.1.1[a]"]
