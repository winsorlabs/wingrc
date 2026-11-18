"""The SSP import review session (app/ssp_import_session.py; migration
0074; docs/roadmap.md, "Importing implementation statements from a
customer's SSP").

What these pin, each through real HTTP with RLS enforced per request
unless marked pure:
- a row lands as a draft on the objective it was accepted onto, with the
  provenance in `grounded_in`, read back from the database;
- across a whole import, control_state, its status and SPRS do not move,
  though the source says Met on every row;
- a statement written by hand mid-session makes that row a conflict, and
  neither side wins until a person chooses; a replace must name the
  statement as it was shown;
- decisions survive a reload, because each one is its own commit;
- two open sessions on one assessment are refused, by the route and by
  the database;
- an unmatched row cannot be accepted onto anything; reject touches
  nothing;
- the role gate, and another org's session, through HTTP;
- the uploaded file is not kept.
"""

from __future__ import annotations

import io
import os
import tempfile
import uuid

import pytest
from docx import Document
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from app import ssp_import_session as imp
from app.auth import get_current_user
from app.db import get_session
from app.engine import start_assessment
from app.main import app
from app.models import (
    AssessmentObjective,
    AuditLog,
    Control,
    ControlState,
    Evidence,
    Framework,
    ImplementationStatement,
    Organization,
    SprsSnapshot,
    SspImportRow,
    SspImportSession,
)
from tests.conftest import _app_session, _authed, _grant, _make_fake_user

BASE = "/orgs/{org}/assessments/{a}/ssp-imports"

_TEXTS = {
    ("AC.L2-3.1.1", "a"): "Authorized users are identified.",
    ("AC.L2-3.1.1", "b"): "Processes acting on behalf of authorized users are identified.",
    ("AC.L2-3.1.1", "c"): "Devices (and other systems) authorized to connect to the system are "
                          "identified.",
    ("AC.L2-3.1.2", "a"): "One sentence two objectives share.",
    ("AC.L2-3.1.3", "a"): "One sentence two objectives share.",
}

# (Control cell, Statement cell) per source row, in order.
_ROWS = [
    (_TEXTS[("AC.L2-3.1.1", "a")], "We identify users in the directory."),
    (_TEXTS[("AC.L2-3.1.1", "b")], "Service accounts are listed in the register."),
    ("Devices including other systems authorized to connect to the system are identified.",
     "Devices are enrolled before they connect."),
    ("One sentence two objectives share.", "A statement for one of the two."),
    ("Our firewall is very good.", "Nothing to place."),
    (_TEXTS[("AC.L2-3.1.1", "a")], "-"),
]


# ---------------------------------------------------------------------------
# Pure
# ---------------------------------------------------------------------------


def _row(**kw):
    base = dict(number=1, family="AC", heading="8.1 Access Control", statement="x",
                match_kind="exact", matched_objective_id=uuid.uuid4(), candidates=[],
                decision="pending")
    base.update(kw)
    return SspImportRow(**base)


def test_what_a_row_may_be_accepted_onto_written_out():
    oid = uuid.uuid4()
    c1, c2 = str(uuid.uuid4()), str(uuid.uuid4())
    same = "Shared text."
    cands = [{"objective_id": c1, "ref": "X[a]", "score": 1.0},
             {"objective_id": c2, "ref": "Y[a]", "score": 1.0}]
    texts = {c1: same, c2: same}
    assert imp.acceptable_onto(_row(matched_objective_id=oid), {}) == (str(oid),)
    for kind in ("ambiguous", "family_mismatch", "no_family"):
        r = _row(match_kind=kind, matched_objective_id=None, candidates=cands,
                 control_text=same)
        assert imp.acceptable_onto(r, texts) == (c1, c2), kind
        assert [o.exact_text for o in imp.offers(r, texts)] == [True, True]

    # Near matches: offered at or above the floor, and only those.
    near, mid, low = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    fuzzy = [{"objective_id": near, "ref": "N[c]", "score": 0.94},
             {"objective_id": mid, "ref": "M[b]", "score": 0.80},
             {"objective_id": low, "ref": "L[a]", "score": 0.79}]
    ftexts = {near: "Devices (and other systems) are identified.",
              mid: "Something else.", low: "Another thing."}
    r = _row(match_kind="unmatched", matched_objective_id=None, candidates=fuzzy,
             control_text="Devices including other systems are identified.")
    assert imp.acceptable_onto(r, ftexts) == (near, mid)
    assert [(o.ref, o.offered, o.exact_text) for o in imp.offers(r, ftexts)] == [
        ("N[c]", True, False), ("M[b]", True, False), ("L[a]", False, False)]
    assert imp.placement(r, near, ftexts) == ("person_confirmed_near_match", 0.94)
    below = _row(match_kind="unmatched", matched_objective_id=None, candidates=fuzzy[2:],
                 control_text="x")
    assert imp.acceptable_onto(below, ftexts) == ()
    assert "0.80 similarity floor" in imp.why_not_acceptable(below, ftexts)

    # A long near match can round to 1.0; exactness is read from the text.
    rounded = [{"objective_id": near, "ref": "N[c]", "score": 1.0}]
    r = _row(match_kind="no_family", matched_objective_id=None, candidates=rounded,
             control_text="Devices including other systems are identified.")
    assert imp.offers(r, ftexts)[0].exact_text is False
    assert imp.placement(r, near, ftexts) == ("person_confirmed_near_match", 1.0)
    exact_pick = _row(match_kind="ambiguous", matched_objective_id=None, candidates=cands,
                      control_text=same)
    assert imp.placement(exact_pick, c2, texts) == ("person_chose_exact_text", None)
    assert imp.placement(_row(matched_objective_id=oid), str(oid), {}) == ("exact_match", None)

    assert imp.acceptable_onto(_row(statement=None), {}) == ()
    assert "no statement text" in imp.why_not_acceptable(_row(statement=None), {})


def test_progress_counts_per_family_in_file_order():
    rows = [_row(number=1, family="AC", decision="accepted"),
            _row(number=2, family="AC"),
            _row(number=3, family="IA", heading="8.5 Identification", decision="rejected"),
            _row(number=4, family=None, heading=None)]
    got = [(f.key, f.total, f.decided) for f in imp.progress(rows)]
    assert got == [("AC", 2, 1), ("IA", 1, 1), (imp.NO_FAMILY, 1, 0)]


# ---------------------------------------------------------------------------
# Through the database and real HTTP
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


def _as(db_session, user) -> TestClient:
    app.dependency_overrides[get_session] = _app_session(db_session)
    app.dependency_overrides[get_current_user] = _authed(db_session, user)
    return TestClient(app)


def _person(db_session, org_id, role="msp_engineer"):
    user = _make_fake_user(role=role, org_id=org_id, email=f"{uuid.uuid4().hex[:8]}@x.com",
                           display_name=f"Person {role}")
    _grant(db_session, user)
    # Committed: a refused request rolls back, and an uncommitted grant
    # would go with it, so the next request would be a 403 for no reason.
    db_session.commit()
    return user


@pytest.fixture
def world(db_session):
    tag = uuid.uuid4().hex[:6]
    org = Organization(name=f"SspSess-{tag}")
    other = Organization(name=f"SspSessOther-{tag}")
    fw = Framework(key=f"fw-sspsess-{tag}", name="Test FW", version="r2")
    db_session.add_all([org, other, fw])
    db_session.flush()
    controls, objectives = {}, {}
    for n, (cid, key) in enumerate(_TEXTS):
        if cid not in controls:
            controls[cid] = Control(framework_id=fw.id, control_id=cid, family="AC", title="t",
                                    requirement_text="r", sprs_weight=5, sequence_order=n)
            db_session.add(controls[cid])
            db_session.flush()
        objectives[(cid, key)] = AssessmentObjective(
            control_id=controls[cid].id, objective_key=key, text=_TEXTS[(cid, key)])
        db_session.add(objectives[(cid, key)])
    db_session.flush()
    assessment = start_assessment(db_session, org.id, fw.id, f"SSP {tag}")
    elsewhere = start_assessment(db_session, other.id, fw.id, f"Other {tag}")
    db_session.commit()
    return dict(org=org, other=other, a=assessment, elsewhere=elsewhere,
                controls=controls, objectives=objectives)


def _docx(rows=_ROWS) -> bytes:
    doc = Document()
    doc.add_paragraph("8.1 Access Control")
    t = doc.add_table(rows=1, cols=5)
    for cell, h in zip(t.rows[0].cells, ["Control", "Status", "Notes",
                                         "Implementation Statement", "RACI"], strict=True):
        cell.text = h
    for control, statement in rows:
        for cell, v in zip(t.add_row().cells,
                           [control, "Met", "-", statement, "Pat Doe pat@example.com"],
                           strict=True):
            cell.text = v
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _upload(client, w, data=None, name="CustomerSSP.docx"):
    return client.post(BASE.format(org=w["org"].id, a=w["a"].id),
                       files={"file": (name, data if data is not None else _docx(),
                                       "application/octet-stream")})


def _decide(client, w, sid, row_id, **body):
    return client.post(f"{BASE.format(org=w['org'].id, a=w['a'].id)}/{sid}/rows/{row_id}/decision",
                       json=body)


def _rows(view) -> dict[int, dict]:
    return {r["number"]: r for r in view["rows"]}


def _posture(db_session, assessment):
    db_session.expire_all()
    statuses = sorted(
        (str(oid), s) for oid, s in db_session.execute(
            select(ControlState.objective_id, ControlState.status)
            .where(ControlState.assessment_id == assessment.id)))
    db_session.refresh(assessment)
    snaps = db_session.scalar(select(func.count()).select_from(SprsSnapshot)
                              .where(SprsSnapshot.assessment_id == assessment.id))
    return statuses, assessment.sprs_score, snaps


def _statements(db_session, assessment):
    db_session.expire_all()
    return {st.objective_id: st for st in db_session.scalars(
        select(ImplementationStatement)
        .where(ImplementationStatement.assessment_id == assessment.id))}


@pytest.mark.integration
def test_upload_opens_a_session_and_writes_no_statement(db_session, world):
    w = world
    user = _person(db_session, w["org"].id)
    before = _posture(db_session, w["a"])
    res = _upload(_as(db_session, user), w)
    assert res.status_code == 201, res.text
    view = res.json()
    rows = _rows(view)
    assert view["session"]["status"] == "open"
    assert view["session"]["rows"] == 6 and view["session"]["written"] == 0
    assert [rows[n]["match_kind"] for n in range(1, 7)] == [
        "exact", "exact", "unmatched", "ambiguous", "unmatched", "exact"]
    assert rows[6]["shares_objective_with"] == [1] and rows[1]["shares_objective_with"] == [6]
    assert rows[1]["source_status"] == "Met"
    assert rows[1]["raci_present"] is True
    assert "pat@example.com" not in res.text, "RACI content must never leave the importer"
    assert _statements(db_session, w["a"]) == {}
    assert _posture(db_session, w["a"]) == before
    stored = db_session.scalars(select(SspImportRow.statement)
                                .where(SspImportRow.session_id == view["session"]["id"]))
    assert "Pat Doe" not in " ".join(s or "" for s in stored)


@pytest.mark.integration
def test_opening_the_session_writes_nothing(db_session, world):
    w = world
    user = _person(db_session, w["org"].id)
    c = _as(db_session, user)
    sid = _upload(c, w).json()["session"]["id"]
    audits = db_session.scalar(select(func.count()).select_from(AuditLog))
    for _ in range(2):
        assert c.get(f"{BASE.format(org=w['org'].id, a=w['a'].id)}/{sid}").status_code == 200
        assert c.get(BASE.format(org=w["org"].id, a=w["a"].id)).status_code == 200
    assert db_session.scalar(select(func.count()).select_from(AuditLog)) == audits
    assert _statements(db_session, w["a"]) == {}


@pytest.mark.integration
def test_a_whole_import_writes_drafts_with_provenance_and_moves_no_status(db_session, world):
    w = world
    user = _person(db_session, w["org"].id)
    c = _as(db_session, user)
    before = _posture(db_session, w["a"])
    assert all(s != "met" for _, s in before[0]), (
        "fixture must start with nothing met, or this test proves nothing")
    view = _upload(c, w).json()
    sid, rows = view["session"]["id"], _rows(view)
    o = w["objectives"]

    assert _decide(c, w, sid, rows[1]["id"], action="accept").status_code == 200
    r = _decide(c, w, sid, rows[2]["id"], action="edit", body="Edited by a person.\n\nTwo paras.")
    assert r.status_code == 200, r.text
    pick = str(o[("AC.L2-3.1.3", "a")].id)
    assert set(rows[4]["acceptable_onto"]) == {str(o[("AC.L2-3.1.2", "a")].id), pick}
    r =_decide(c, w, sid, rows[4]["id"], action="accept", objective_id=pick)
    assert r.status_code == 200, r.text
    near = str(o[("AC.L2-3.1.1", "c")].id)
    assert rows[3]["acceptable_onto"] == [near], "only the near match above the floor"
    r = _decide(c, w, sid, rows[3]["id"], action="accept", objective_id=near)
    assert r.status_code == 200, r.text
    for n in (5, 6):
        assert _decide(c, w, sid, rows[n]["id"], action="reject").status_code == 200
    done = c.post(f"{BASE.format(org=w['org'].id, a=w['a'].id)}/{sid}/close",
                  json={"outcome": "completed"})
    assert done.status_code == 200, done.text
    assert done.json()["session"]["status"] == "completed"

    st = _statements(db_session, w["a"])
    assert set(st) == {o[("AC.L2-3.1.1", "a")].id, o[("AC.L2-3.1.1", "b")].id,
                       o[("AC.L2-3.1.1", "c")].id, o[("AC.L2-3.1.3", "a")].id}
    # Exact, a person's choice among exact texts, a person confirming a
    # near match, and an edit: each told apart in the record.
    c_ = st[o[("AC.L2-3.1.1", "c")].id]
    assert (c_.status, c_.body) == ("draft", "Devices are enrolled before they connect.")
    assert c_.grounded_in["placement"] == "person_confirmed_near_match"
    assert c_.grounded_in["near_match_score"] >= imp.NEAR_MATCH_FLOOR
    assert [x["ref"] for x in c_.grounded_in["candidates_offered"]] == ["AC.L2-3.1.1[c]"]
    assert c_.grounded_in["candidates_offered"][0]["exact_text"] is False
    a = st[o[("AC.L2-3.1.1", "a")].id]
    b = st[o[("AC.L2-3.1.1", "b")].id]
    assert a.status == b.status == "draft"
    assert a.body == "We identify users in the directory."
    assert b.body == "Edited by a person.\n\nTwo paras."
    assert a.generation_model is None
    # JSONB does not keep key order, so the keys are compared as a set.
    assert set(a.grounded_in) == set(imp.PROVENANCE_KEYS)
    assert a.grounded_in["source"] == "ssp_import"
    assert a.grounded_in["session_id"] == sid
    assert a.grounded_in["file_name"] == "CustomerSSP.docx"
    assert a.grounded_in["file_sha256"] == view["session"]["file_sha256"]
    assert a.grounded_in["source_row"] == 1
    assert a.grounded_in["source_status_claim"] == "Met"
    assert a.grounded_in["match_kind"] == "exact"
    assert a.grounded_in["decision"] == "accepted"
    assert b.grounded_in["decision"] == "edited"
    assert a.grounded_in["decided_by"] == "Person msp_engineer"
    assert st[o[("AC.L2-3.1.3", "a")].id].grounded_in["match_kind"] == "ambiguous"
    assert (a.grounded_in["placement"], a.grounded_in["near_match_score"]) == ("exact_match", None)
    assert (b.grounded_in["placement"], b.grounded_in["decision"]) == ("exact_match", "edited")
    picked = st[o[("AC.L2-3.1.3", "a")].id].grounded_in
    assert (picked["placement"], picked["near_match_score"]) == ("person_chose_exact_text", None)
    assert {x["ref"] for x in picked["candidates_offered"]} == {"AC.L2-3.1.2[a]",
                                                                "AC.L2-3.1.3[a]"}
    assert _posture(db_session, w["a"]) == before, "control_state, status and SPRS must not move"
    actions = set(db_session.scalars(select(AuditLog.action).where(
        AuditLog.entity_type == "implementation_statement",
        AuditLog.entity_id.in_([s.id for s in st.values()]))))
    assert actions == {"implementation_statement.import"}


@pytest.mark.integration
def test_a_statement_written_mid_session_is_a_conflict_neither_side_wins(db_session, world):
    w = world
    user = _person(db_session, w["org"].id)
    c = _as(db_session, user)
    view = _upload(c, w).json()
    sid, rows = view["session"]["id"], _rows(view)
    obj = w["objectives"][("AC.L2-3.1.1", "a")]
    put = c.put(f"/orgs/{w['org'].id}/assessments/{w['a'].id}/controls/"
                f"{w['controls']['AC.L2-3.1.1'].id}/statements",
                json=[{"objective_id": str(obj.id), "body": "Written by hand.",
                       "status": "reviewed"}])
    assert put.status_code == 200, put.text

    r = _decide(c, w, sid, rows[1]["id"], action="accept")
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail["reason"] == "conflict"
    assert detail["statement"]["body"] == "Written by hand."
    hand = _statements(db_session, w["a"])[obj.id]
    assert (hand.body, hand.status) == ("Written by hand.", "reviewed")
    shown = c.get(f"{BASE.format(org=w['org'].id, a=w['a'].id)}/{sid}").json()
    assert _rows(shown)[1]["decision"] == "pending"
    assert _rows(shown)[1]["existing"]["body"] == "Written by hand."

    stale = _decide(c, w, sid, rows[1]["id"], action="accept",
                    replace_statement_id=detail["statement"]["id"],
                    replace_updated_at="2000-01-01T00:00:00+00:00")
    assert stale.status_code == 409, "a replace naming another moment must not win"
    assert _statements(db_session, w["a"])[obj.id].body == "Written by hand."

    r = _decide(c, w, sid, rows[1]["id"], action="accept",
                replace_statement_id=detail["statement"]["id"],
                replace_updated_at=detail["statement"]["updated_at"])
    assert r.status_code == 200, r.text
    after = _statements(db_session, w["a"])[obj.id]
    assert (after.body, after.status) == ("We identify users in the directory.", "draft")
    assert _rows(r.json())[1]["resolution"] == "replaced"
    replaced = db_session.scalars(select(AuditLog).where(
        AuditLog.action == "implementation_statement.import_replace")).one()
    assert replaced.before_value["body"] == "Written by hand."

    # Row 6 names the same objective: the draft row 1 just wrote is the
    # existing statement now, and keeping it leaves it alone.
    r = _decide(c, w, sid, rows[6]["id"], action="reject")
    assert r.status_code == 200
    assert _decide(c, w, sid, rows[6]["id"], action="reopen").status_code == 200


@pytest.mark.integration
def test_keep_existing_rejects_the_row_and_leaves_the_statement(db_session, world):
    w = world
    user = _person(db_session, w["org"].id)
    c = _as(db_session, user)
    obj = w["objectives"][("AC.L2-3.1.1", "b")]
    db_session.add(ImplementationStatement(org_id=w["org"].id, objective_id=obj.id,
                                           assessment_id=w["a"].id, body="Already here.",
                                           status="approved"))
    db_session.commit()
    view = _upload(c, w).json()
    sid, rows = view["session"]["id"], _rows(view)
    assert rows[2]["existing"]["body"] == "Already here."
    r = _decide(c, w, sid, rows[2]["id"], action="keep_existing")
    assert r.status_code == 200, r.text
    row = _rows(r.json())[2]
    assert (row["decision"], row["resolution"]) == ("rejected", "kept_existing")
    st = _statements(db_session, w["a"])[obj.id]
    assert (st.body, st.status) == ("Already here.", "approved")


@pytest.mark.integration
def test_decisions_survive_a_reload(db_session, world):
    w = world
    user = _person(db_session, w["org"].id)
    view = _upload(_as(db_session, user), w).json()
    sid, rows = view["session"]["id"], _rows(view)
    _decide(_as(db_session, user), w, sid, rows[1]["id"], action="accept")
    _decide(_as(db_session, user), w, sid, rows[5]["id"], action="reject")
    # A new client, as after closing the tab: everything comes from the server.
    fresh = _as(db_session, user).get(f"{BASE.format(org=w['org'].id, a=w['a'].id)}/{sid}")
    got = _rows(fresh.json())
    assert (got[1]["decision"], got[5]["decision"], got[2]["decision"]) == (
        "accepted", "rejected", "pending")
    assert fresh.json()["families"] == [
        {"key": "AC", "heading": "8.1 Access Control", "total": 6, "decided": 2}]


@pytest.mark.integration
def test_two_open_sessions_on_one_assessment_are_refused(db_session, world):
    w = world
    user = _person(db_session, w["org"].id)
    c = _as(db_session, user)
    first = _upload(c, w).json()["session"]["id"]
    second = _upload(c, w, name="Revised.docx")
    assert second.status_code == 409
    assert second.json()["detail"]["session_id"] == first
    n = db_session.scalar(select(func.count()).select_from(SspImportSession)
                          .where(SspImportSession.assessment_id == w["a"].id))
    assert n == 1
    with pytest.raises(IntegrityError), db_session.begin_nested():
        db_session.add(SspImportSession(org_id=w["org"].id, assessment_id=w["a"].id,
                                        status="open", file_name="x", file_sha256="0" * 64,
                                        file_size=1, importer="ssp_docx"))
        db_session.flush()
    # Discarded, it no longer blocks; it is still listed.
    close = c.post(f"{BASE.format(org=w['org'].id, a=w['a'].id)}/{first}/close",
                   json={"outcome": "discarded"})
    assert close.status_code == 200
    assert _upload(c, w, name="Revised.docx").status_code == 201
    listed = c.get(BASE.format(org=w["org"].id, a=w["a"].id)).json()
    assert [s["status"] for s in listed] == ["open", "discarded"]


@pytest.mark.integration
def test_a_near_match_lands_only_where_a_person_puts_it_and_below_the_floor_nowhere(
        db_session, world):
    w = world
    user = _person(db_session, w["org"].id)
    c = _as(db_session, user)
    view = _upload(c, w).json()
    sid, rows = view["session"]["id"], _rows(view)
    near = rows[3]
    assert near["match_kind"] == "unmatched" and near["candidates"][0]["ref"] == "AC.L2-3.1.1[c]", (
        "fixture must give the near-match row a close candidate, or this test proves nothing")
    offered = [x for x in near["candidates"] if x["offered"]]
    hidden = [x for x in near["candidates"] if not x["offered"]]
    assert [x["ref"] for x in offered] == ["AC.L2-3.1.1[c]"]
    assert hidden and all(x["score"] < near["near_match_floor"] for x in hidden)
    assert offered[0]["text"].startswith("Devices (and other systems)"), "catalog text, to read"
    # Nothing preselected: the row has no objective until a person names one.
    assert near["objective_id"] is None and near["decided_objective_id"] is None
    unchosen = _decide(c, w, sid, near["id"], action="accept")
    assert unchosen.status_code == 422 and "None is chosen for you" in unchosen.text
    below = _decide(c, w, sid, near["id"], action="accept", objective_id=hidden[0]["objective_id"])
    assert below.status_code == 422, "a candidate under the floor is not offered"
    assert _statements(db_session, w["a"]) == {}

    # A row whose every candidate is under the floor offers none.
    far = rows[5]
    assert far["acceptable_onto"] == [] and not any(x["offered"] for x in far["candidates"])
    assert "similarity floor" in far["not_acceptable"]
    for body in ({"action": "accept"},
                 {"action": "accept", "objective_id": far["candidates"][0]["objective_id"]}):
        assert _decide(c, w, sid, far["id"], **body).status_code == 422

    nothing = _decide(c, w, sid, rows[6]["id"], action="accept")
    assert nothing.status_code == 422 and "no statement text" in nothing.text
    wrong = _decide(c, w, sid, rows[4]["id"], action="accept",
                    objective_id=str(w["objectives"][("AC.L2-3.1.1", "a")].id))
    assert wrong.status_code == 422, "an ambiguous row lands only on one of its candidates"
    unchosen = _decide(c, w, sid, rows[4]["id"], action="accept")
    assert unchosen.status_code == 422, "an ambiguous row's objective is never chosen for them"

    before = _posture(db_session, w["a"])
    landed = _decide(c, w, sid, near["id"], action="accept",
                     objective_id=offered[0]["objective_id"])
    assert landed.status_code == 200, landed.text
    st = _statements(db_session, w["a"])
    assert set(st) == {w["objectives"][("AC.L2-3.1.1", "c")].id}
    assert st[w["objectives"][("AC.L2-3.1.1", "c")].id].status == "draft"
    assert _posture(db_session, w["a"]) == before, "a confirmed near match moves no status"
    before = _posture(db_session, w["a"])
    assert _decide(c, w, sid, rows[1]["id"], action="reject").status_code == 200
    assert len(_statements(db_session, w["a"])) == 1, "reject wrote nothing"
    assert _posture(db_session, w["a"]) == before
    done = c.post(f"{BASE.format(org=w['org'].id, a=w['a'].id)}/{sid}/close",
                  json={"outcome": "completed"})
    assert done.status_code == 409, "undecided rows block completing"


@pytest.mark.integration
def test_a_discarded_session_takes_no_more_decisions_and_keeps_its_drafts(db_session, world):
    w = world
    user = _person(db_session, w["org"].id)
    c = _as(db_session, user)
    view = _upload(c, w).json()
    sid, rows = view["session"]["id"], _rows(view)
    _decide(c, w, sid, rows[1]["id"], action="accept")
    r = c.post(f"{BASE.format(org=w['org'].id, a=w['a'].id)}/{sid}/close",
               json={"outcome": "discarded"})
    assert r.json()["session"]["written"] == 1
    assert _decide(c, w, sid, rows[2]["id"], action="accept").status_code == 409
    assert len(_statements(db_session, w["a"])) == 1


@pytest.mark.integration
@pytest.mark.parametrize("role,allowed", [
    ("msp_admin", True), ("consultant_admin", True), ("msp_engineer", True),
    ("customer_poc", False), ("c3pao_assessor", False),
])
def test_the_role_gate_on_every_route(db_session, world, role, allowed):
    w = world
    owner = _person(db_session, w["org"].id)
    sid = _upload(_as(db_session, owner), w).json()["session"]["id"]
    row = db_session.scalars(select(SspImportRow).where(
        SspImportRow.session_id == sid, SspImportRow.number == 1)).one()
    c = _as(db_session, _person(db_session, w["org"].id, role))
    base = BASE.format(org=w["org"].id, a=w["a"].id)
    calls = [
        c.get(base), c.get(f"{base}/{sid}"),
        _decide(c, w, sid, row.id, action="reject"),
        c.post(f"{base}/{sid}/close", json={"outcome": "discarded"}),
    ]
    codes = [r.status_code for r in calls]
    if allowed:
        assert codes == [200, 200, 200, 200], codes
    else:
        assert codes == [403] * 4, codes
        assert _upload(c, w).status_code == 403


@pytest.mark.integration
def test_another_orgs_session_is_not_found(db_session, world):
    w = world
    sid = _upload(_as(db_session, _person(db_session, w["org"].id)), w).json()["session"]["id"]
    outsider = _person(db_session, w["other"].id, "msp_admin")
    c = _as(db_session, outsider)
    # Through its own org's path, naming the other org's session.
    via_own = f"/orgs/{w['other'].id}/assessments/{w['elsewhere'].id}/ssp-imports/{sid}"
    assert c.get(via_own).status_code == 404
    # Its own org with the other org's assessment.
    assert c.get(f"/orgs/{w['other'].id}/assessments/{w['a'].id}/ssp-imports").status_code == 404
    # The other org's path, with no membership there.
    assert c.get(f"{BASE.format(org=w['org'].id, a=w['a'].id)}/{sid}").status_code == 403


@pytest.mark.integration
def test_rls_hides_the_session_from_the_app_role_in_another_org(db_session, world):
    """Not the route's org filter: the policy itself. As wingrc_app with
    the other org set, the rows are invisible."""
    w = world
    sid = _upload(_as(db_session, _person(db_session, w["org"].id)), w).json()["session"]["id"]
    conn = db_session.connection()
    conn.execute(text("SET ROLE wingrc_app"))
    try:
        conn.execute(text(f"SET LOCAL app.current_org = '{w['other'].id}'"))
        assert conn.execute(text("SELECT count(*) FROM ssp_import_session WHERE id = :i"),
                            {"i": sid}).scalar() == 0
        assert conn.execute(text("SELECT count(*) FROM ssp_import_row WHERE session_id = :i"),
                            {"i": sid}).scalar() == 0
        conn.execute(text(f"SET LOCAL app.current_org = '{w['org'].id}'"))
        assert conn.execute(text("SELECT count(*) FROM ssp_import_session WHERE id = :i"),
                            {"i": sid}).scalar() == 1, (
            "the same query in its own org must see it, or this test proves nothing")
    finally:
        conn.execute(text("RESET ROLE"))


@pytest.mark.integration
def test_refusals_and_the_file_is_not_kept(db_session, world, monkeypatch):
    w = world
    c = _as(db_session, _person(db_session, w["org"].id))
    made: list[str] = []
    real = tempfile.mkstemp

    def _recording(*a, **kw):
        fd, path = real(*a, **kw)
        made.append(path)
        return fd, path

    monkeypatch.setattr(imp.tempfile, "mkstemp", _recording)
    assert _upload(c, w, data=b"not a zip", name="x.docx").status_code == 422
    assert _upload(c, w, data=_docx(), name="x.pdf").status_code == 422
    empty = _upload(c, w, data=_docx(rows=[]), name="Empty.docx")
    assert empty.status_code == 422 and "no objective rows" in empty.text
    other = Document()
    other.add_paragraph("No table here.")
    buf = io.BytesIO()
    other.save(buf)
    r = _upload(c, w, data=buf.getvalue(), name="Plain.docx")
    assert r.status_code == 422 and "Plain.docx" in r.text
    assert db_session.scalar(select(func.count()).select_from(SspImportSession)
                             .where(SspImportSession.assessment_id == w["a"].id)) == 0
    ok = _upload(c, w)
    assert ok.status_code == 201
    assert made, "the parse must go through the recorded temporary file"
    assert not any(os.path.exists(p) for p in made), "the uploaded file must not be kept"
    assert db_session.scalar(select(func.count()).select_from(Evidence)
                             .where(Evidence.org_id == w["org"].id)) == 0
