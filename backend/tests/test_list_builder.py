"""List library L.2.2: the List Builder and editable control tags.

A list built by hand fills the same definition the template header block
does, and its v1 says a person made it. Tags record who put them there and
when, beside the versions; an import manages only its own; and removing a
tag that evidence rests on is refused, naming the link.
"""

from __future__ import annotations

import io
import uuid
from pathlib import Path

import openpyxl
import pytest
import yaml
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import list_library
from app.auth import get_current_user
from app.db import get_session
from app.list_library import (
    ImportRefused,
    add_tag,
    apply_import,
    diff_import,
    history_of,
    tag_events,
    tag_origins,
)
from app.list_templates import _FAMILY, _REQUIREMENTS, plan_import, practice_key
from app.main import app
from app.models import AuditLog, Base, ListDefinition, Organization
from tests.conftest import _app_session, _authed, _grant, _make_fake_user
from tests.list_template_fixtures import build_library

LISTS = "/orgs/{org}/list-definitions"
ONE = "/orgs/{org}/list-definitions/{list}"

BUILT = {
    "title": "Visitor Log",
    "control_keys": ["PE.L2-3.10.3", "3.10.4"],
    "description": "Everyone escorted through the CUI area.",
    "responsible": "Office Manager",
    "review_cadence": "Monthly",
    "columns": ["Date", "Visitor", "Escort"],
    "origin_note": "Lifted from our old SharePoint sheet",
}


# ---------------------------------------------------------------------------
# Pure: practice ids and the reference table behind them
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("typed", "key"),
    [
        ("CM.L2-3.4.1", "CM.L2-3.4.1"),
        (" cm.l2-3.4.1 ", "CM.L2-3.4.1"),
        ("3.13.16", "SC.L2-3.13.16"),
        ("3.1.22", "AC.L2-3.1.22"),
    ],
)
def test_a_typed_practice_is_keyed_as_row_2_keys_it(typed, key):
    assert practice_key(typed) == key


@pytest.mark.parametrize("typed", ["3.1.23", "3.15.1", "3.4.0", "AC.L2-3.4.1", "CM.3.4.1", ""])
def test_a_practice_that_is_not_a_requirement_is_refused(typed):
    with pytest.raises(ValueError):
        practice_key(typed)


def test_the_requirement_table_matches_the_seeded_catalog():
    """_REQUIREMENTS is reference data; it is checked against the catalog
    the platform seeds (cmmc_l2.yaml), not taken on trust."""
    seed = Path(__file__).resolve().parents[1] / "app" / "seeds" / "cmmc_l2.yaml"
    ids = [c["id"] for c in yaml.safe_load(seed.read_text(encoding="utf-8"))["controls"]]
    counts: dict[int, int] = {}
    for cid in ids:
        fam, num = cid.split(".L2-")
        mid = int(num.split(".")[1])
        assert _FAMILY[mid] == fam, cid
        counts[mid] = counts.get(mid, 0) + 1
    assert len(ids) == 110, "fixture must be the full 110-requirement catalog"
    assert counts == _REQUIREMENTS


# Tables that reference a list today, each settled for the tag-removal rule.
# `evidence` links a list version as evidence (L.4, migration 0067):
# tag_removal_blockers reads its links, which
# test_list_evidence.py's tag tests pin against real rows.
# `list_link_event` (L.10, migration 0068) is the history of those links, not
# a link: an unlinked list's events must not block a tag, and the blockers
# read the live EvidenceStateLinks only (tested in test_list_evidence.py).
_SETTLED = {
    "list_definition", "list_version", "list_control_tag", "list_tag_event", "evidence",
    "list_link_event",
}


def test_a_new_table_referencing_lists_must_settle_the_tag_removal_rule():
    """L.4 will link lists as evidence. When it lands, a table (or a column
    on `evidence`) will reference a list, and the tag-removal rule must then
    consult it: list_library.tag_removal_blockers has to return that link
    for the control it serves, or a tag removal will silently narrow what an
    assessment rests on. This fails until that is done and the table is
    added to _SETTLED -- loudly, on purpose."""
    referencing = {
        fk.parent.table.name
        for table in Base.metadata.tables.values()
        for fk in table.foreign_keys
        if fk.column.table.name in ("list_definition", "list_version")
    }
    assert referencing <= _SETTLED, (
        f"{sorted(referencing - _SETTLED)} now reference lists: make "
        "list_library.tag_removal_blockers return the evidence links they hold, "
        "with a test, then add them to _SETTLED"
    )


# ---------------------------------------------------------------------------
# Through the database and real HTTP (RLS enforced per request)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


def _org(db_session) -> uuid.UUID:
    org = Organization(name=f"ListBuilderOrg-{uuid.uuid4().hex[:8]}")
    db_session.add(org)
    db_session.flush()
    return org.id


def _client(db_session, org_id, role="msp_engineer", name="Pat Builder") -> TestClient:
    user = _make_fake_user(
        role=role, org_id=org_id, email=f"{uuid.uuid4().hex[:8]}@x.com", display_name=name
    )
    _grant(db_session, user)
    app.dependency_overrides[get_session] = _app_session(db_session)
    app.dependency_overrides[get_current_user] = _authed(db_session, user)
    return TestClient(app)


def _plan(root):
    return plan_import(root / "New Lists", root / "Archive" / "Lists", root / "Changelog" / "Lists")


def _build(client, org_id, **over) -> dict:
    r = client.post(LISTS.format(org=org_id), json=BUILT | over)
    assert r.status_code == 201, r.text
    return r.json()


def _header_block(xlsx: bytes, first_column: str) -> dict:
    """The export's header block, by position, with the version's own values
    taken out: the label each of rows 2-7 opens with, and the row the column
    headers sit on. Before roadmap T this compared rows 2-5 and passed while
    neither export carried What goes here, Responsible or Review cadence --
    the same header block, checking less than its name said."""
    ws = openpyxl.load_workbook(io.BytesIO(xlsx)).active
    head = [ws.cell(row=r, column=1).value or "" for r in range(1, 8)]
    return {
        "title_present": bool(head[0]),
        "row2": head[1].split(":")[0],
        "row3": head[2].split(":")[0],
        "row4": [part.split(":")[0].strip() for part in head[3].split("|")],
        "row5": head[4].split(" ")[:2],
        "row6": head[5].split(".")[0],
        "row7": head[6],
        "header_row": next(
            r for r in range(1, 12) if ws.cell(row=r, column=1).value == first_column
        ),
    }


@pytest.mark.integration
def test_a_list_built_by_hand_round_trips_like_an_imported_one(db_session, tmp_path):
    org_id = _org(db_session)
    root = build_library(tmp_path)
    apply_import(db_session, org_id, _plan(root))
    db_session.flush()
    client = _client(db_session, org_id)

    built = _build(client, org_id)
    assert built["is_template"] is False
    assert built["control_keys"] == ["PE.L2-3.10.3", "PE.L2-3.10.4"]
    v1 = built["current"]
    assert v1["cause"] == "created_by_hand"
    assert v1["saved_by"] == "Pat Builder"
    assert v1["provenance"]["origin_note"] == "Lifted from our old SharePoint sheet"
    assert "source_sha256" not in v1["provenance"] and built["source_ref"] is None
    assert (v1["description"], v1["responsible"], v1["review_cadence"]) == (
        "Everyone escorted through the CUI area.", "Office Manager", "Monthly"
    )

    r = client.post(
        f"{ONE.format(org=org_id, list=built['id'])}/versions",
        json={"base_version_id": v1["id"], "rows": [["2026-10-15", "A. Visitor", "J. Escort"]]},
    )
    assert r.status_code == 201 and r.json()["cause"] == "edit", r.text
    versions = client.get(f"{ONE.format(org=org_id, list=built['id'])}/versions").json()
    assert [v["cause"] for v in versions] == ["created_by_hand", "edit"]

    hand = client.get(f"{ONE.format(org=org_id, list=built['id'])}/export").content
    risk = next(
        s for s in client.get(LISTS.format(org=org_id)).json()
        if s["list_key"] == "3-11-1a-risk-register"
    )
    imported = client.get(f"{ONE.format(org=org_id, list=risk['id'])}/export").content
    assert _header_block(hand, "Date") == _header_block(imported, "Risk ID")
    ws = openpyxl.load_workbook(io.BytesIO(hand)).active
    assert ws["A1"].value == "Visitor Log"
    assert ws["A2"].value.endswith("PE.L2-3.10.3 / PE.L2-3.10.4")
    assert ws["A3"].value == "What goes here: Everyone escorted through the CUI area."
    assert ws["A4"].value == "Responsible: Office Manager   |   Review cadence: Monthly"
    assert "by Pat Builder" in ws["A5"].value
    assert [ws.cell(row=9, column=c).value for c in (1, 2, 3)] == [
        "2026-10-15", "A. Visitor", "J. Escort"
    ]

    row = db_session.scalars(
        select(AuditLog).where(AuditLog.org_id == org_id, AuditLog.action == "list.create")
    ).one()
    assert row.context["cause"] == "created_by_hand"


@pytest.mark.integration
def test_a_hand_built_list_an_import_also_produces_is_a_conflict(db_session, tmp_path):
    """Confirmed from the code rather than assumed: v1 is `via: api`, which is
    what diff_import's conflict rule keys on (not `saved_by`)."""
    org_id = _org(db_session)
    client = _client(db_session, org_id)
    built = _build(
        client, org_id, title="3.11.1a Risk Register", control_keys=["RA.L2-3.11.1"],
        columns=["Risk ID", "Risk Description", "Status"],
    )
    assert built["list_key"] == "3-11-1a-risk-register", (
        "fixture must collide with the importer's key or this test proves nothing"
    )
    plan = _plan(build_library(tmp_path))
    [c] = diff_import(db_session, org_id, plan).of("conflict")
    assert c.edit_ref == "3-11-1a-risk-register@v1"

    apply_import(db_session, org_id, plan)
    db_session.flush()
    d = db_session.get(ListDefinition, uuid.UUID(built["id"]))
    assert [v.provenance["cause"] for v in history_of(db_session, d.id)] == ["created_by_hand"]
    assert d.is_template is False


@pytest.mark.integration
def test_a_duplicate_title_is_refused_not_merged(db_session):
    org_id = _org(db_session)
    client = _client(db_session, org_id)
    first = _build(client, org_id)
    r = client.post(LISTS.format(org=org_id), json=BUILT | {"title": "visitor  log"})
    assert r.status_code == 409
    assert r.json()["detail"]["list_id"] == first["id"]


@pytest.mark.integration
@pytest.mark.parametrize(
    "bad",
    [
        {"control_keys": []},
        {"control_keys": ["3.1.23"]},
        {"columns": []},
        {"columns": ["Date", " "]},
        {"columns": ["Date", "date"]},
        {"rows": [["only one cell"]]},
        {"title": "  "},
    ],
)
def test_the_builder_refuses_what_the_template_convention_could_not_hold(db_session, bad):
    org_id = _org(db_session)
    r = _client(db_session, org_id).post(LISTS.format(org=org_id), json=BUILT | bad)
    assert r.status_code == 422, r.text


@pytest.mark.integration
def test_a_tag_added_is_visible_at_once_and_recorded_with_who_and_when(db_session):
    org_id = _org(db_session)
    client = _client(db_session, org_id, name="Tess Tagger")
    built = _build(client, org_id)
    base = ONE.format(org=org_id, list=built["id"])

    r = client.post(f"{base}/tags", json={"control_key": "3.4.2"})
    assert r.status_code == 201, r.text
    assert r.json()["control_key"] == "CM.L2-3.4.2"

    in_cm = client.get(LISTS.format(org=org_id), params={"control_key": "CM.L2-3.4.2"}).json()
    assert [s["id"] for s in in_cm] == [built["id"]]
    events = client.get(f"{base}/tag-events").json()
    assert [(e["control_key"], e["change"], e["via"], e["saved_by"]) for e in events] == [
        ("PE.L2-3.10.3", "added", "api", "Tess Tagger"),
        ("PE.L2-3.10.4", "added", "api", "Tess Tagger"),
        ("CM.L2-3.4.2", "added", "api", "Tess Tagger"),
    ]
    assert events[-1]["at_version"] == 1
    assert client.post(f"{base}/tags", json={"control_key": "CM.L2-3.4.2"}).status_code == 409

    row = db_session.scalars(
        select(AuditLog).where(AuditLog.org_id == org_id, AuditLog.action == "list.tag_add")
    ).one()
    assert row.after_value == {"control_key": "CM.L2-3.4.2"}


@pytest.mark.integration
def test_a_tag_removal_is_recorded_and_the_last_tag_stays(db_session):
    org_id = _org(db_session)
    client = _client(db_session, org_id)
    built = _build(client, org_id)
    base = ONE.format(org=org_id, list=built["id"])
    r = client.delete(f"{base}/tags/PE.L2-3.10.4")
    assert r.status_code == 200 and r.json()["change"] == "removed", r.text
    assert client.delete(f"{base}/tags/PE.L2-3.10.4").status_code == 404
    assert client.delete(f"{base}/tags/PE.L2-3.10.3").status_code == 409
    assert client.get(base).json()["control_keys"] == ["PE.L2-3.10.3"]


@pytest.mark.integration
def test_a_tag_removal_that_evidence_rests_on_is_refused_naming_the_link(
    db_session, tmp_path, monkeypatch
):
    """Written before L.4: the link is stood in for here. What it pins is
    that both writers -- a person, and an import whose source dropped the
    practice -- ask tag_removal_blockers and stop on its answer, writing
    nothing. When L.4 lands, test_a_new_table_referencing_lists_must_settle
    _the_tag_removal_rule makes it wire the real links in.

    Was a stand-in for tag_removal_blockers returning a sentence; since
    X.1 both writers read tag_removal_links (fields, so a screen writes
    its own sentence), and the import's sentences are built from it."""
    link = list_library.RestingLink(1, "PE.L2-3.10.4[a]", "Visitor log export FY26 L2")
    monkeypatch.setattr(
        list_library, "tag_removal_links",
        lambda session, list_id, key: [link] if key in ("PE.L2-3.10.4", "RA.L2-3.11.2") else [],
    )
    org_id = _org(db_session)
    client = _client(db_session, org_id)
    built = _build(client, org_id)
    base = ONE.format(org=org_id, list=built["id"])

    r = client.delete(f"{base}/tags/PE.L2-3.10.4")
    assert r.status_code == 409
    assert r.json()["detail"]["links"] == [{
        "version_number": 1, "objective_key": "PE.L2-3.10.4[a]",
        "assessment_name": "Visitor log export FY26 L2",
    }]
    assert "PE.L2-3.10.4" in client.get(base).json()["control_keys"]
    assert [e["change"] for e in client.get(f"{base}/tag-events").json()] == ["added", "added"]

    root = build_library(tmp_path)
    apply_import(db_session, org_id, _plan(root))
    risk = db_session.scalar(
        select(ListDefinition).where(
            ListDefinition.org_id == org_id, ListDefinition.list_key == "3-11-1a-risk-register"
        )
    )
    # As if an earlier source had named 3.11.2 and this one no longer does.
    add_tag(db_session, risk, "RA.L2-3.11.2", "template_import", None)
    db_session.flush()
    with pytest.raises(ImportRefused, match="Visitor log export"):
        apply_import(db_session, org_id, _plan(root))
    assert "RA.L2-3.11.2" in tag_origins(db_session, risk.id)


@pytest.mark.integration
def test_an_import_keeps_a_persons_tag_and_manages_its_own(db_session, tmp_path):
    """Before L.2.2 every import replaced a list's whole tag set with its
    source's row 2, so a tag a person added was gone at the next import."""
    org_id = _org(db_session)
    root = build_library(tmp_path)
    plan = _plan(root)
    apply_import(db_session, org_id, plan)
    db_session.flush()
    client = _client(db_session, org_id, name="Tess Tagger")
    risk = db_session.scalar(
        select(ListDefinition).where(
            ListDefinition.org_id == org_id, ListDefinition.list_key == "3-11-1a-risk-register"
        )
    )
    base = ONE.format(org=org_id, list=risk.id)
    assert client.post(f"{base}/tags", json={"control_key": "RA.L2-3.11.3"}).status_code == 201

    dry = diff_import(db_session, org_id, plan)
    assert dry.of("new_version") == [] and len(dry.of("unchanged")) == len(plan.definitions)
    apply_import(db_session, org_id, plan)
    db_session.flush()
    assert tag_origins(db_session, risk.id) == {
        "RA.L2-3.11.1": "template_import", "RA.L2-3.11.3": "api"
    }

    add_tag(db_session, risk, "RA.L2-3.11.2", "template_import", None)
    db_session.flush()
    [a] = diff_import(db_session, org_id, plan).of("new_version")
    assert a.changed_fields == ("control_tags",)
    apply_import(db_session, org_id, plan)
    db_session.flush()
    assert set(tag_origins(db_session, risk.id)) == {"RA.L2-3.11.1", "RA.L2-3.11.3"}
    last = tag_events(db_session, risk.id)[-1]
    assert (last.control_key, last.change, last.via, last.saved_by) == (
        "RA.L2-3.11.2", "removed", "template_import", None
    )
    assert [v.version_number for v in history_of(db_session, risk.id)] == [1], (
        "a tag change writes no version"
    )


@pytest.mark.integration
def test_removing_a_column_keeps_it_and_its_data_in_the_prior_version(db_session):
    org_id = _org(db_session)
    client = _client(db_session, org_id)
    built = _build(client, org_id, rows=[["2026-10-01", "A. Visitor", "J. Escort"]])
    base = ONE.format(org=org_id, list=built["id"])
    r = client.post(
        f"{base}/versions",
        json={
            "base_version_id": built["current"]["id"],
            "columns": ["Date", "Visitor"],
            "rows": [["2026-10-01", "A. Visitor"]],
        },
    )
    assert r.status_code == 201, r.text
    v1 = client.get(f"{base}/versions/1").json()
    assert v1["columns"] == ["Date", "Visitor", "Escort"]
    assert v1["rows"] == [["2026-10-01", "A. Visitor", "J. Escort"]]
    row = db_session.scalars(
        select(AuditLog).where(
            AuditLog.org_id == org_id, AuditLog.action == "list.version_create"
        )
    ).one()
    assert row.after_value["columns_removed"] == ["Escort"]
    assert row.after_value["columns_added"] == []


@pytest.mark.integration
@pytest.mark.parametrize(
    ("role", "define", "fill"),
    [
        ("msp_admin", True, True),
        ("consultant_admin", True, True),
        ("msp_engineer", True, True),
        ("customer_poc", False, True),
        ("c3pao_assessor", False, False),
    ],
)
def test_who_may_define_a_list_and_who_may_fill_it_in(db_session, role, define, fill):
    """Defining -- building, columns, tags -- is the practitioner's; a
    customer contact fills rows in. A refusal leaves the list as it was."""
    org_id = _org(db_session)
    built = _build(_client(db_session, org_id, role="msp_admin"), org_id)
    app.dependency_overrides.clear()
    client = _client(db_session, org_id, role=role)
    r = client.post(LISTS.format(org=org_id), json=BUILT | {"title": f"Built by {role}"})
    assert r.status_code == (201 if define else 403), r.text
    base = ONE.format(org=org_id, list=built["id"])
    added = client.post(f"{base}/tags", json={"control_key": "3.10.5"})
    assert added.status_code == (201 if define else 403), added.text
    assert client.delete(f"{base}/tags/PE.L2-3.10.4").status_code == (200 if define else 403)
    current = client.get(base).json()["current"]
    filled = client.post(
        f"{base}/versions",
        json={
            "base_version_id": current["id"],
            "rows": [["2026-10-01", "A. Visitor", "J. Escort"]],
        },
    )
    assert filled.status_code == (201 if fill else 403), filled.text
    current = client.get(base).json()["current"]
    widened = client.post(
        f"{base}/versions",
        json={
            "base_version_id": current["id"],
            "columns": ["Date", "Visitor", "Escort", "Badge"],
            "rows": [["2026-10-01", "A. Visitor", "J. Escort", "7"]] if fill else [],
        },
    )
    assert widened.status_code == (201 if define else 403), widened.text
    final = client.get(base).json()
    assert final["current"]["columns"] == (
        ["Date", "Visitor", "Escort", "Badge"] if define else BUILT["columns"]
    )
    assert final["current"]["version_number"] == 1 + fill + define
    assert final["control_keys"] == (
        ["PE.L2-3.10.3", "PE.L2-3.10.5"] if define else ["PE.L2-3.10.3", "PE.L2-3.10.4"]
    )
    assert client.get(f"{base}/tag-events").status_code == 200


@pytest.mark.integration
def test_tags_and_the_builder_are_tenant_isolated_over_http(db_session):
    org_a, org_b = _org(db_session), _org(db_session)
    built = _build(_client(db_session, org_a), org_a)
    app.dependency_overrides.clear()
    client_b = _client(db_session, org_b)
    own = ONE.format(org=org_b, list=built["id"])
    theirs = ONE.format(org=org_a, list=built["id"])
    assert client_b.post(f"{own}/tags", json={"control_key": "3.10.5"}).status_code == 404
    assert client_b.get(f"{own}/tag-events").status_code == 404
    assert client_b.delete(f"{own}/tags/PE.L2-3.10.4").status_code == 404
    assert client_b.post(f"{theirs}/tags", json={"control_key": "3.10.5"}).status_code == 403
    assert client_b.post(LISTS.format(org=org_a), json=BUILT).status_code == 403
    assert [e.control_key for e in tag_events(db_session, uuid.UUID(built["id"]))] == [
        "PE.L2-3.10.3", "PE.L2-3.10.4"
    ]


@pytest.mark.integration
def test_building_and_tagging_never_touch_sprs_or_control_state(db_session):
    from app.engine import start_assessment
    from app.models import AssessmentObjective, Control, ControlState, Framework

    org_id = _org(db_session)
    fw = Framework(key=f"fw-lb-{uuid.uuid4().hex[:6]}", name="LB FW", version="r2")
    db_session.add(fw)
    db_session.flush()
    ctrl = Control(
        framework_id=fw.id, control_id="PE.L2-3.10.3", family="PE", title="Escort",
        requirement_text="Escort visitors.", sprs_weight=1, sequence_order=1,
    )
    db_session.add(ctrl)
    db_session.flush()
    db_session.add(AssessmentObjective(control_id=ctrl.id, objective_key="a", text="Escort"))
    db_session.flush()
    a = start_assessment(db_session, org_id, fw.id, "LB SPRS")
    db_session.commit()

    def snapshot():
        db_session.expire_all()
        return (
            db_session.get(type(a), a.id).sprs_score,
            sorted(
                (s.id, s.status)
                for s in db_session.scalars(
                    select(ControlState).where(ControlState.assessment_id == a.id)
                )
            ),
        )

    before = snapshot()
    client = _client(db_session, org_id)
    built = _build(client, org_id)
    base = ONE.format(org=org_id, list=built["id"])
    assert client.post(f"{base}/tags", json={"control_key": "3.10.5"}).status_code == 201
    assert client.delete(f"{base}/tags/PE.L2-3.10.3").status_code == 200
    assert snapshot() == before
