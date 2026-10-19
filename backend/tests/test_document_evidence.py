"""Item X: a published document is offered as evidence and a person
attaches it (document_evidence.py; docs/roadmap.md item X).

What these pin:

- Publishing writes the Evidence row and no link; the document is offered
  on its tagged objectives, and only those, and only once approved.
- A person's link survives another person's republish, stays on its
  version, and says that version is superseded -- in the words a
  superseded policy needs, not a list's "a newer version is available".
- Moving forward is per objective: a new link, the old one archived.
- Removing the link keeps the published record.
- Every attach, move and removal is an audit row on the version, in the
  document's History.
- No control_state and no SPRS change, ever.
- A product's deactivation leaves an attached document alone.
- The bundle says superseded in the manifest and on the implementation
  page, and an unattached published version is not stray evidence.
- `review_cycles.close_cycle` passes the two-part rule.
- c3pao_assessor sees and changes nothing; RLS keeps orgs apart.
"""

from __future__ import annotations

import io
import re
import uuid
import zipfile

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select

from app import document_evidence, list_evidence, review_cycles
from app.auth import get_current_user
from app.db import get_session
from app.engine import activate_org_product, deactivate_org_product, start_assessment
from app.main import app
from app.models import (
    Assessment,
    AuditLog,
    Contact,
    ControlState,
    ControlStateHistory,
    DocumentObjectiveTag,
    Evidence,
    EvidenceStateLink,
)
from app.storage import NullStorageClient, get_storage_client
from tests.conftest import _app_session, _authed, _grant, _make_fake_user
from tests.test_list_evidence import _product_covering, _world

STATE_DOCS = "/orgs/{org}/assessments/{a}/control-states/{cs}/documents"
DOCS = "/orgs/{org}/documents"
BUNDLE = "/orgs/{org}/assessments/{a}/bundle"


# ---------------------------------------------------------------------------
# Pure
# ---------------------------------------------------------------------------


def test_superseded_is_said_as_superseded_not_as_a_newer_version():
    """The two sentences a person sees, side by side. A list's older
    version is still a true record; a superseded policy may not be in
    force. They must not read as the same fact."""
    doc = document_evidence.superseded_text(2, 3)
    lst = list_evidence.drift_text(2, 3)
    assert doc == (
        "Version 2, attached here, is superseded: version 3 is the approved one, and "
        "version 2 may no longer be in force. This evidence stays on version 2 until "
        "someone moves it forward."
    )
    assert lst == (
        "List version 3 is available. This evidence stays on version 2 until someone "
        "moves it forward."
    )
    assert "no longer be in force" in doc and "no longer be in force" not in lst
    assert "available" in lst and "available" not in doc


def test_the_document_routes_that_change_a_link_are_not_gets():
    """require_write lets every GET through for c3pao_assessor; these must
    not be GETs, or the assessor could change evidence."""
    paths = app.openapi()["paths"]
    base = "/orgs/{org_id}/assessments/{assessment_id}/control-states/{cs_id}/documents"
    assert set(paths[base]) == {"get"}
    assert set(paths[base + "/{document_id}"]) == {"post", "delete"}
    assert set(paths[base + "/{document_id}/move-forward"]) == {"post"}


# ---------------------------------------------------------------------------
# Through the database and real HTTP (RLS enforced per request)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


def _person(db_session, org_id, name, role="msp_engineer"):
    user = _make_fake_user(
        role=role, org_id=org_id, email=f"{uuid.uuid4().hex[:8]}@x.com", display_name=name
    )
    _grant(db_session, user)
    return user


def _as(db_session, user) -> TestClient:
    """Act as this person. The overrides are app-wide, so whoever acted
    last is who the next request is from."""
    app.dependency_overrides[get_session] = _app_session(db_session)
    app.dependency_overrides[get_current_user] = _authed(db_session, user)
    app.dependency_overrides[get_storage_client] = NullStorageClient
    return TestClient(app)


def _objective(db_session, w, cs_key):
    return db_session.get(ControlState, w["cs"][cs_key]).objective_id


def _setup(db_session, label, tags=("AC.L2-3.1.1[a]",)):
    w = _world(db_session, label)
    contact = Contact(org_id=w["org"], name="Pat Approver", email="pat@x.com",
                      affiliation="customer")
    db_session.add(contact)
    db_session.flush()
    w["contact"] = contact.id
    w["avery"] = _person(db_session, w["org"], "Avery Attacher")
    w["blake"] = _person(db_session, w["org"], "Blake Approver", role="msp_admin")
    client = _as(db_session, w["blake"])
    r = client.post(DOCS.format(org=w["org"]), json={
        "doc_id": "AC-POL-001", "doc_type": "policy", "title": "Access Control Policy",
        "body": "Version one.",
    })
    assert r.status_code == 201, r.text
    w["doc"] = r.json()
    for k in tags:
        r = client.post(
            f"{DOCS.format(org=w['org'])}/{w['doc']['id']}/objective-tags",
            json={"objective_id": str(_objective(db_session, w, k))},
        )
        assert r.status_code == 201, r.text
    return w


def _publish(client, w):
    r = client.post(
        f"{DOCS.format(org=w['org'])}/{w['doc']['id']}/publish",
        json={"approved_by_contact_id": str(w["contact"])},
    )
    assert r.status_code == 200, r.text
    return r.json()["current_version"]


def _edit(client, w, body):
    doc = client.get(f"{DOCS.format(org=w['org'])}/{w['doc']['id']}").json()
    r = client.post(
        f"{DOCS.format(org=w['org'])}/{w['doc']['id']}/versions",
        json={"body": body, "base_version_id": doc["current_version"]["id"]},
    )
    assert r.status_code == 201, r.text


def _docs(client, w, cs_key):
    r = client.get(STATE_DOCS.format(org=w["org"], a=w["a"], cs=w["cs"][cs_key]))
    assert r.status_code == 200, r.text
    return {c["doc_id"]: c for c in r.json()}


def _attach(client, w, cs_key, action=""):
    url = f"{STATE_DOCS.format(org=w['org'], a=w['a'], cs=w['cs'][cs_key])}/{w['doc']['id']}"
    return client.delete(url) if action == "delete" else client.post(url + action)


def _live_links(db_session, w):
    db_session.expire_all()
    return db_session.execute(
        select(EvidenceStateLink.control_state_id, Evidence.source_document_version_id,
               EvidenceStateLink.is_archived)
        .join(Evidence, Evidence.id == EvidenceStateLink.evidence_id)
        .where(Evidence.org_id == w["org"])
        .order_by(EvidenceStateLink.id)
    ).all()


def _states(db_session, w):
    db_session.expire_all()
    a = db_session.get(Assessment, w["a"])
    return a.sprs_score, sorted(
        (str(cs.id), cs.status) for cs in db_session.scalars(
            select(ControlState).where(ControlState.assessment_id == w["a"])
        )
    )


@pytest.mark.integration
def test_publishing_creates_the_evidence_row_and_no_link(db_session):
    w = _setup(db_session, "pub")
    blake = _as(db_session, w["blake"])
    before = _states(db_session, w)
    v1 = _publish(blake, w)

    assert _live_links(db_session, w) == [], "publish attaches nothing"
    ev = db_session.scalars(
        select(Evidence).where(Evidence.source_document_version_id == uuid.UUID(v1["id"]))
    ).one()
    assert ev.artifact_type == "document" and ev.kind == "reference"
    offer = _docs(blake, w, "AC.L2-3.1.1[a]")["AC-POL-001"]
    assert offer["offered"] is True and offer["linked_version_id"] is None
    assert offer["approved_version_number"] == 1
    assert _docs(blake, w, "PE.L2-3.10.3[a]") == {}, "offered only where it is tagged"
    assert _states(db_session, w) == before, "no control_state, no SPRS change"


@pytest.mark.integration
def test_only_an_approved_version_on_a_tagged_objective_is_offered(db_session):
    w = _setup(db_session, "offer")
    avery = _as(db_session, w["avery"])
    draft = _docs(avery, w, "AC.L2-3.1.1[a]")["AC-POL-001"]
    assert draft["offered"] is False and draft["not_offered"] == document_evidence.NOT_APPROVED
    r = _attach(avery, w, "AC.L2-3.1.1[a]")
    assert r.status_code == 409 and "approved" in r.json()["detail"]

    _publish(_as(db_session, w["blake"]), w)
    avery = _as(db_session, w["avery"])
    r = _attach(avery, w, "PE.L2-3.10.3[a]")
    assert r.status_code == 409 and "not tagged" in r.json()["detail"]
    assert _attach(avery, w, "AC.L2-3.1.1[a]").status_code == 201
    r = _attach(avery, w, "AC.L2-3.1.1[a]")
    assert r.status_code == 409 and "already attached" in r.json()["detail"]

    # Was: untagged through the API after attaching, the link still shown.
    # Since X.1 that removal is refused while the link rests on the tag
    # (test_document_tags.py). A tag removed before X.1 can still leave a
    # link like this, so the row is deleted directly to stand in for one,
    # and NOT_TAGGED is still what it says.
    blake = _as(db_session, w["blake"])
    objective = _objective(db_session, w, "AC.L2-3.1.1[a]")
    assert blake.delete(
        f"{DOCS.format(org=w['org'])}/{w['doc']['id']}/objective-tags/{objective}"
    ).status_code == 409
    db_session.execute(delete(DocumentObjectiveTag).where(
        DocumentObjectiveTag.document_id == uuid.UUID(w["doc"]["id"]),
        DocumentObjectiveTag.objective_id == objective,
    ))
    db_session.flush()
    shown = _docs(blake, w, "AC.L2-3.1.1[a]")["AC-POL-001"]
    assert shown["linked_version_number"] == 1
    assert shown["not_offered"] == document_evidence.NOT_TAGGED


@pytest.mark.integration
def test_attaching_is_a_persons_act_and_is_in_the_documents_history(db_session):
    w = _setup(db_session, "attach")
    v1 = _publish(_as(db_session, w["blake"]), w)
    before = _states(db_session, w)
    avery = _as(db_session, w["avery"])
    r = _attach(avery, w, "AC.L2-3.1.1[a]")
    assert r.status_code == 201, r.text
    [row] = r.json()
    assert row["linked_version_number"] == 1 and row["superseded"] is None

    assert _live_links(db_session, w) == [
        (w["cs"]["AC.L2-3.1.1[a]"], uuid.UUID(v1["id"]), False)
    ]
    [audit] = db_session.scalars(
        select(AuditLog).where(AuditLog.action == "evidence.document_link",
                               AuditLog.org_id == w["org"])
    ).all()
    assert audit.actor == str(w["avery"].id)
    assert audit.entity_type == "document_version" and str(audit.entity_id) == v1["id"]
    assert audit.after_value["control_key"] == "AC.L2-3.1.1"
    assert audit.after_value["objective_key"] == "a"

    history = avery.get(f"{DOCS.format(org=w['org'])}/{w['doc']['id']}/history").json()
    [event] = [e for e in history["events"] if e["action"] == "evidence.document_link"]
    assert event["actor_user"]["display_name"] == "Avery Attacher"
    assert _states(db_session, w) == before, "attached is not met: no state, no SPRS change"


@pytest.mark.integration
def test_a_persons_link_survives_another_persons_republish(db_session):
    """Item X's section 1. Before X a republish archived every link on the
    superseded version; once links are chosen, that would be Blake's
    approval silently undoing Avery's choice."""
    w = _setup(db_session, "two")
    v1 = _publish(_as(db_session, w["blake"]), w)
    assert _attach(_as(db_session, w["avery"]), w, "AC.L2-3.1.1[a]").status_code == 201

    blake = _as(db_session, w["blake"])
    _edit(blake, w, "Version two.")
    v2 = _publish(blake, w)
    assert v2["version_number"] == 2, "fixture must publish a second version"

    assert _live_links(db_session, w) == [
        (w["cs"]["AC.L2-3.1.1[a]"], uuid.UUID(v1["id"]), False)
    ], "Avery's link is where Avery put it: on version 1, live"
    shown = _docs(blake, w, "AC.L2-3.1.1[a]")["AC-POL-001"]
    assert shown["linked_version_number"] == 1 and shown["linked_superseded"] is True
    assert shown["approved_version_number"] == 2
    assert shown["superseded"] == document_evidence.superseded_text(1, 2)
    assert db_session.scalar(
        select(func.count()).select_from(AuditLog).where(
            AuditLog.org_id == w["org"], AuditLog.actor == str(w["blake"].id),
            AuditLog.action.like("evidence.document_%"),
        )
    ) == 0, "Blake's publish made, moved and removed no link"


@pytest.mark.integration
def test_moving_forward_is_per_objective_and_keeps_the_old_link(db_session):
    w = _setup(db_session, "move", tags=("AC.L2-3.1.1[a]", "PE.L2-3.10.3[a]"))
    v1 = _publish(_as(db_session, w["blake"]), w)
    avery = _as(db_session, w["avery"])
    for k in ("AC.L2-3.1.1[a]", "PE.L2-3.10.3[a]"):
        assert _attach(avery, w, k).status_code == 201
    blake = _as(db_session, w["blake"])
    _edit(blake, w, "Version two.")
    v2 = _publish(blake, w)

    avery = _as(db_session, w["avery"])
    r = _attach(avery, w, "AC.L2-3.1.1[a]", "/move-forward")
    assert r.status_code == 200, r.text
    links = set(_live_links(db_session, w))
    a, pe = w["cs"]["AC.L2-3.1.1[a]"], w["cs"]["PE.L2-3.10.3[a]"]
    assert links == {
        (a, uuid.UUID(v1["id"]), True), (a, uuid.UUID(v2["id"]), False),
        (pe, uuid.UUID(v1["id"]), False),
    }, "the old link archived, not rewritten; the other objective untouched"
    [audit] = db_session.scalars(
        select(AuditLog).where(AuditLog.action == "evidence.document_move_forward",
                               AuditLog.org_id == w["org"])
    ).all()
    assert audit.after_value["from_version_numbers"] == [1]
    assert audit.after_value["version_number"] == 2
    r = _attach(avery, w, "AC.L2-3.1.1[a]", "/move-forward")
    assert r.status_code == 409 and "already at version 2" in r.json()["detail"]
    assert _docs(avery, w, "AC.L2-3.1.1[a]")["AC-POL-001"]["superseded"] is None


@pytest.mark.integration
def test_removing_the_link_keeps_the_published_record(db_session):
    w = _setup(db_session, "unlink")
    v1 = _publish(_as(db_session, w["blake"]), w)
    avery = _as(db_session, w["avery"])
    assert _attach(avery, w, "AC.L2-3.1.1[a]").status_code == 201
    r = _attach(avery, w, "AC.L2-3.1.1[a]", "delete")
    assert r.status_code == 200, r.text
    # _live_links returns archived rows too (the move-forward tests read
    # them). This asserted [] while unlink deleted the link; since
    # 2026-10-19 the link stays, archived as detached, and says by whom
    # (evidence_links.py).
    assert [archived for *_, archived in _live_links(db_session, w)] == [True]
    detached = db_session.scalars(
        select(EvidenceStateLink).join(Evidence, Evidence.id == EvidenceStateLink.evidence_id)
        .where(Evidence.source_document_version_id == uuid.UUID(v1["id"]))
    ).all()
    assert [(lk.is_archived, lk.archive_cause) for lk in detached] == [(True, "detached")]
    assert detached[0].detached_by
    assert db_session.scalar(
        select(func.count()).select_from(Evidence)
        .where(Evidence.source_document_version_id == uuid.UUID(v1["id"]))
    ) == 1, "the Evidence row is the record that version 1 was published"
    assert db_session.scalar(
        select(func.count()).select_from(AuditLog)
        .where(AuditLog.action == "evidence.document_unlink", AuditLog.org_id == w["org"])
    ) == 1
    assert _as(db_session, w["blake"]).delete(
        f"{DOCS.format(org=w['org'])}/{w['doc']['id']}"
    ).status_code == 409, "a published document still cannot be deleted"
    assert _docs(_as(db_session, w["avery"]), w, "AC.L2-3.1.1[a]")["AC-POL-001"]["offered"]


@pytest.mark.integration
def test_deactivating_a_product_leaves_an_attached_document_alone(db_session):
    w = _setup(db_session, "deact", tags=("PE.L2-3.10.3[a]",))
    product = _product_covering(db_session, w, "PE.L2-3.10.3")
    activate_org_product(db_session, w["org"], product.id, w["a"])
    db_session.flush()
    _publish(_as(db_session, w["blake"]), w)
    assert _attach(_as(db_session, w["avery"]), w, "PE.L2-3.10.3[a]").status_code == 201

    deactivate_org_product(db_session, w["org"], product.id, w["a"])
    db_session.flush()
    [(_, _, archived)] = _live_links(db_session, w)
    assert archived is False, "a person attached it; a tool leaving does not retire it"


def _unstamped(page: str) -> str:
    return re.sub(r"generated [^<]*", "", page)


def _member(content: bytes, rel: str) -> str:
    zf = zipfile.ZipFile(io.BytesIO(content))
    [name] = [n for n in zf.namelist() if n.split("/", 1)[1] == rel]
    return zf.read(name).decode()


def _sections(docs_html: str) -> dict[str, str]:
    """Each document section on ssp/06_documents.html, by its id."""
    pattern = r'<section class="doc-entry" id="([^"]+)">(.*?)</section>'
    return dict(re.findall(pattern, docs_html, re.S))


@pytest.mark.integration
def test_a_superseded_document_reads_as_superseded_in_the_bundle(db_session):
    w = _setup(db_session, "bundle")
    _publish(_as(db_session, w["blake"]), w)
    assert _attach(_as(db_session, w["avery"]), w, "AC.L2-3.1.1[a]").status_code == 201
    blake = _as(db_session, w["blake"])
    _edit(blake, w, "Version two.")
    _publish(blake, w)

    r = blake.get(BUNDLE.format(org=w["org"], a=w["a"]))
    assert r.status_code == 200, r.text
    said = (
        "<strong>superseded</strong>: version 2 is the approved one, and version 1 may no "
        "longer be in force"
    )
    manifest = _member(r.content, "evidence/manifest.html")
    assert (
        "<span class='ev-path'><a href=\"../ssp/06_documents.html#doc-ac_pol_001-v1\">"
        "version 1</a></span> " + said
    ) in manifest
    assert manifest.count("Access Control Policy") == 1, (
        "version 2's unattached Evidence row is not stray evidence"
    )
    impl = _member(r.content, "ssp/02_implementation.html")
    assert '<a href="06_documents.html#doc-ac_pol_001-v1">version 1</a>' in impl
    assert said in impl

    # L.3/L.4's rule: the bundle carries the exact version the link names.
    sections = _sections(_member(r.content, "ssp/06_documents.html"))
    assert set(sections) == {"doc-ac_pol_001", "doc-ac_pol_001-v1"}
    v1, v2 = sections["doc-ac_pol_001-v1"], sections["doc-ac_pol_001"]
    assert "Version one." in v1 and "Version two." not in v1, "v1's own text, not v2's"
    assert "version 1 (superseded)" in v1 and said in v1
    assert "AC.L2-3.1.1[a]" in v1
    assert "Version two." in v2 and "superseded" not in v2
    assert "Not attached as evidence in this assessment" in v2, (
        "version 2 is the approved one and nobody has attached it"
    )
    pages = ("ssp/06_documents.html", "evidence/manifest.html", "ssp/02_implementation.html")
    again = blake.get(BUNDLE.format(org=w["org"], a=w["a"])).content
    for rel in pages:
        assert _unstamped(_member(r.content, rel)) == _unstamped(_member(again, rel))


@pytest.mark.integration
def test_a_superseded_version_nothing_names_is_not_carried(db_session):
    """Only a version a live link in this assessment names is carried: one
    nobody attached, or attached only in another assessment, is not."""
    w = _setup(db_session, "unnamed")
    _publish(_as(db_session, w["blake"]), w)
    second = start_assessment(db_session, w["org"], w["fw"], "Second")
    db_session.flush()
    cs2 = db_session.scalar(select(ControlState).where(
        ControlState.assessment_id == second.id,
        ControlState.objective_id == _objective(db_session, w, "AC.L2-3.1.1[a]"),
    ))
    avery = _as(db_session, w["avery"])
    r = avery.post(
        f"{STATE_DOCS.format(org=w['org'], a=second.id, cs=cs2.id)}/{w['doc']['id']}"
    )
    assert r.status_code == 201, "fixture: v1 attached in the other assessment"
    blake = _as(db_session, w["blake"])
    _edit(blake, w, "Version two.")
    _publish(blake, w)

    sections = _sections(_member(
        blake.get(BUNDLE.format(org=w["org"], a=w["a"])).content, "ssp/06_documents.html"
    ))
    assert set(sections) == {"doc-ac_pol_001"}
    assert "Version one." not in sections["doc-ac_pol_001"]


@pytest.mark.integration
def test_closing_a_review_cycle_is_a_persons_act_and_moves_nothing(db_session):
    """Item X's two-part rule, applied to the other path that links
    evidence: is the link created by a person's own act, and does it move
    an existing link? Only "yes, no" is right.

    close_cycle links the attestation to AC.L2-3.1.1[a]/[c] on every
    in_progress assessment. Moves nothing: asserted below -- a link a
    person made on the same objective is live and unchanged after. A
    person's act: when the last reviewer attests (`attest` closes the
    cycle), the attestation is that act, for exactly these objectives.
    This test covers that path only. A deadline or undeliverable close
    attaches nothing since 2026-10-19 ("Review cycles attach only what
    attests" in docs/roadmap.md; tests/test_review_cycle_record.py).
    """
    w = _setup(db_session, "cycle")
    _publish(_as(db_session, w["blake"]), w)
    assert _attach(_as(db_session, w["avery"]), w, "AC.L2-3.1.1[a]").status_code == 201
    before = _live_links(db_session, w)
    assert before, "fixture must hold a person's link on the cycle's objective"

    cycle, _ = review_cycles.open_cycle(db_session, org_id=w["org"], opened_by=str(w["blake"].id))
    db_session.flush()
    ev = review_cycles.close_cycle(db_session, NullStorageClient(), cycle=cycle, status="completed")
    db_session.flush()

    after = _live_links(db_session, w)
    assert [x for x in after if x[1] is not None] == list(before), "no earlier link moved"
    assert db_session.scalars(
        select(EvidenceStateLink.control_state_id).where(EvidenceStateLink.evidence_id == ev.id)
    ).all() == [w["cs"]["AC.L2-3.1.1[a]"]]


@pytest.mark.integration
def test_an_assessor_sees_documents_and_can_make_move_or_break_none(db_session):
    w = _setup(db_session, "c3pao")
    _publish(_as(db_session, w["blake"]), w)
    assert _attach(_as(db_session, w["avery"]), w, "AC.L2-3.1.1[a]").status_code == 201
    assessor = _as(db_session, _person(db_session, w["org"], "Ash Assessor", "c3pao_assessor"))
    assert _docs(assessor, w, "AC.L2-3.1.1[a]")["AC-POL-001"]["linked_version_number"] == 1
    assert _attach(assessor, w, "PE.L2-3.10.3[a]").status_code == 403
    assert _attach(assessor, w, "AC.L2-3.1.1[a]", "/move-forward").status_code == 403
    assert _attach(assessor, w, "AC.L2-3.1.1[a]", "delete").status_code == 403
    assert len(_live_links(db_session, w)) == 1, "the assessor's attempts wrote nothing"
    assert db_session.scalar(
        select(func.count()).select_from(ControlStateHistory)
        .join(ControlState, ControlState.id == ControlStateHistory.control_state_id)
        .where(ControlState.assessment_id == w["a"])
    ) == 0, "nothing on this path writes control_state history"


@pytest.mark.integration
def test_no_org_reaches_another_orgs_documents(db_session):
    a = _setup(db_session, "rls-a")
    _publish(_as(db_session, a["blake"]), a)
    b = _world(db_session, "rls-b")
    intruder = _as(db_session, _person(db_session, b["org"], "Ira Intruder", "msp_admin"))
    assert intruder.get(
        STATE_DOCS.format(org=a["org"], a=a["a"], cs=a["cs"]["AC.L2-3.1.1[a]"])
    ).status_code == 403, "no membership in org A"
    # Org B's own path, org A's ids: RLS hides them, so not found.
    assert intruder.get(
        STATE_DOCS.format(org=b["org"], a=a["a"], cs=a["cs"]["AC.L2-3.1.1[a]"])
    ).status_code == 404
    r = intruder.post(
        f"{STATE_DOCS.format(org=b['org'], a=b['a'], cs=b['cs']['AC.L2-3.1.1[a]'])}"
        f"/{a['doc']['id']}"
    )
    assert r.status_code == 404
    assert _live_links(db_session, a) == []
