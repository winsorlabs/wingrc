"""List library L.1: the template importer and the versioned list model.

docs/PLAN-list-library.md is the spec. The importer reads the template
convention (§3) and reports every collision (§4) instead of resolving it;
`list_version` is append-only from its first migration (§4a).
"""

from __future__ import annotations

import json
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.auth import get_current_user
from app.db import get_session
from app.list_library import (
    ImportRefused,
    apply_import,
    content_of,
    diff_import,
    history_of,
    import_cause,
    record_import,
    version_causes,
)
from app.list_templates import DEVICE_MERGE, MergeRule, control_keys, plan_import
from app.main import app
from app.models import ListDefinition, ListVersion, Organization
from tests.conftest import _app_session, _authed, _grant, _make_fake_user
from tests.list_template_fixtures import DEVICE_COLUMNS, build_library


def _plan(root):
    return plan_import(root / "New Lists", root / "Changelog" / "Lists", root / "Archive" / "Lists")


def _sheet(plan, sheet):
    return next(s for s in plan.sheets if s.sheet == sheet)


# ---------------------------------------------------------------------------
# The parser (no database)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("CM.L2-3.4.3 / 3.4.4", ("CM.L2-3.4.3", "CM.L2-3.4.4")),
        ("AC.L2-3.1.12 / 3.1.14 / 3.1.15", ("AC.L2-3.1.12", "AC.L2-3.1.14", "AC.L2-3.1.15")),
        ("SC.L2-3.13.3  (Separate user functionality)", ("SC.L2-3.13.3",)),
        ("3.4.1 / 3.4.1", ("CM.L2-3.4.1",)),
        ("no practice here", ()),
    ],
)
def test_control_keys_are_parsed_from_the_practice_row(text, expected):
    assert control_keys(text) == expected


def test_a_conforming_sheet_yields_a_full_definition(tmp_path):
    plan = _plan(build_library(tmp_path))
    risk = _sheet(plan, "3.11.1a Risk Register")
    assert risk.status == "importable"
    assert risk.workbook_title == "Risk Register"  # read from merged C1, not A1
    assert risk.title == "Organizational Risk Register"
    assert risk.control_keys == ("RA.L2-3.11.1",)
    assert risk.description == "Identified risks to CUI."
    assert (risk.responsible, risk.review_cadence) == ("Customer Risk Owner", "Annually")
    assert risk.columns == ("Risk ID", "Risk Description", "Status")
    assert risk.rows[0] == ("[PLACEHOLDER]", "[PLACEHOLDER - no risks assessed yet]", "Open")
    assert len(risk.rows) == 2, "two identical example rows are two rows -- no row identity"


def test_collisions_are_reported_not_resolved(tmp_path):
    plan = _plan(build_library(tmp_path))
    users = _sheet(plan, "3.1.1a Authorized Users")
    assert users.status == "needs_normalization" and "multi-section" in users.reason
    assert users.sections == ("Authorized Users - Datto RMM",)
    assert users.rows == (), "a multi-section tab is not flattened"

    wide = _sheet(plan, "3.1.5c")
    assert wide.status == "needs_normalization" and "repeat 2 times" in wide.reason

    bad = {s.file: s for s in plan.of("non_conforming")}
    assert set(bad) == {
        "MA/CMMC_372_Controls.xlsx",
        "AC/3.1.20/CMMC_3.1.20_External_Connections.xlsx",
    }
    assert (
        "superseded by AC/3.1.20/External Systems and Connections.xlsx"
        in bad["AC/3.1.20/CMMC_3.1.20_External_Connections.xlsx"].reason
    )
    assert plan.not_ingested == ["Lists/AC/3.1.1/Authorized-Entities.xlsx"]


def test_the_device_lists_merge_by_the_recorded_mapping(tmp_path):
    plan = _plan(build_library(tmp_path))
    devices = _sheet(plan, "3.1.1c Authorized Devices")
    hardware = _sheet(plan, "3.4.1a Hardware")
    assert hardware.status == "merged"
    at = DEVICE_COLUMNS.index("Model") + 1
    assert devices.columns == (
        *DEVICE_COLUMNS[:at], "Device Type", "Baseline Ref", *DEVICE_COLUMNS[at:]
    ), "the added columns sit beside the identity columns, in one fixed order"
    assert devices.title == "Authorized Devices & Hardware Inventory"
    assert all(r[at:at + 2] == ("", "") for r in devices.rows), (
        "the examples are 3.1.1c's; the importer writes no content into the added columns"
    )
    assert devices.control_keys == ("AC.L2-3.1.1", "CM.L2-3.4.1", "CM.L2-3.4.2")
    assert devices.absorbed == (
        "CM/3.4.1/Hardware and Software Asset Inventory.xlsx#3.4.1a Hardware",
    )
    assert all(len(r) == len(devices.columns) for r in devices.rows)
    assert "Type" not in devices.columns, "a device class is never named like the CMMC category"
    assert _sheet(plan, "3.4.1b Software").status == "importable"


def test_a_merge_is_never_guessed(tmp_path, monkeypatch):
    from app import list_templates

    incomplete = MergeRule(
        target=DEVICE_MERGE.target,
        absorbed=DEVICE_MERGE.absorbed,
        column_map={k: v for k, v in DEVICE_MERGE.column_map.items() if k != "Baseline Ref"},
        added_columns=DEVICE_MERGE.added_columns,
    )
    monkeypatch.setattr(list_templates, "MERGES", (incomplete,))
    with pytest.raises(ValueError, match="no recorded mapping"):
        _plan(build_library(tmp_path))


def test_a_merge_column_placement_is_never_guessed(tmp_path, monkeypatch):
    from dataclasses import replace

    from app import list_templates

    monkeypatch.setattr(
        list_templates, "MERGES", (replace(DEVICE_MERGE, added_after="No Such Column"),)
    )
    with pytest.raises(ValueError, match="to place the added columns after"):
        _plan(build_library(tmp_path))


def test_changelog_provenance_is_matched(tmp_path):
    plan = _plan(build_library(tmp_path))
    assert _sheet(plan, "3.11.1a Risk Register").changelogs == ("New_Lists_changelog.md",)
    assert _sheet(plan, "3.12.2a POAM").changelogs == ()


# ---------------------------------------------------------------------------
# The model, through the database and real HTTP
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


def _org(db_session) -> uuid.UUID:
    org = Organization(name=f"ListLibOrg-{uuid.uuid4().hex[:8]}")
    db_session.add(org)
    db_session.flush()
    return org.id


def _client(db_session, org_id, role="msp_engineer") -> TestClient:
    user = _make_fake_user(role=role, org_id=org_id, email=f"{uuid.uuid4().hex[:8]}@x.com")
    _grant(db_session, user)
    app.dependency_overrides[get_session] = _app_session(db_session)
    app.dependency_overrides[get_current_user] = _authed(db_session, user)
    return TestClient(app)


def _imported(db_session, tmp_path, **kw):
    org_id = _org(db_session)
    root = build_library(tmp_path, **kw)
    apply_import(db_session, org_id, _plan(root), root / "Changelog" / "Lists")
    db_session.flush()
    return org_id, root


@pytest.mark.integration
def test_import_creates_template_definitions_and_reimport_proposes_nothing(db_session, tmp_path):
    org_id, root = _imported(db_session, tmp_path)
    defs = db_session.scalars(
        select(ListDefinition)
        .where(ListDefinition.org_id == org_id)
        .order_by(ListDefinition.list_key)
    ).all()
    assert sorted(d.list_key for d in defs) == [
        "3-1-1c-authorized-devices",
        "3-11-1a-risk-register",
        "3-12-2a-poam",
        "3-4-1b-software",
    ]
    assert all(d.is_template for d in defs)
    risk = next(d for d in defs if d.list_key == "3-11-1a-risk-register")
    v1 = db_session.get(ListVersion, risk.current_version_id)
    assert v1.version_number == 1
    assert v1.provenance["changelogs"][0]["path"] == "Changelog/Lists/New_Lists_changelog.md"
    assert "replaced generic example rows" in v1.provenance["changelogs"][0]["text"]
    assert v1.provenance["source_sha256"]

    again = diff_import(db_session, org_id, _plan(root))
    assert {a.action for a in again.actions} == {"unchanged"}


@pytest.mark.integration
def test_a_changed_template_becomes_a_new_version_of_only_that_list(db_session, tmp_path):
    org_id, _ = _imported(db_session, tmp_path)
    changed = build_library(tmp_path / "v2", risk_status="Closed")
    diff = apply_import(db_session, org_id, _plan(changed))
    db_session.flush()
    assert [(a.sheet.list_key, a.action) for a in diff.of("new_version")] == [
        ("3-11-1a-risk-register", "new_version")
    ]
    assert len(diff.of("unchanged")) == 3


@pytest.mark.integration
def test_the_merged_list_rename_lands_as_one_new_version(db_session, tmp_path, monkeypatch):
    """L.2.1's rename and column move, as an org imported before it sees them:
    one new version of the merged list, the earlier version untouched, no
    other list moved."""
    from dataclasses import replace

    from app import list_templates

    before_rule = replace(DEVICE_MERGE, title="", added_after="")
    monkeypatch.setattr(list_templates, "MERGES", (before_rule,))
    org_id, root = _imported(db_session, tmp_path)
    devices = db_session.scalar(
        select(ListDefinition).where(
            ListDefinition.org_id == org_id,
            ListDefinition.list_key == "3-1-1c-authorized-devices",
        )
    )
    v1 = db_session.get(ListVersion, devices.current_version_id)
    v1_content = (v1.title, list(v1.columns), [list(r) for r in v1.rows])
    assert v1.columns[-2:] == ["Device Type", "Baseline Ref"]

    monkeypatch.setattr(list_templates, "MERGES", (DEVICE_MERGE,))
    diff = apply_import(db_session, org_id, _plan(root))
    db_session.flush()
    assert [(a.sheet.list_key, a.changed_fields) for a in diff.of("new_version")] == [
        ("3-1-1c-authorized-devices", ("title", "columns", "rows"))
    ]
    v2 = db_session.get(ListVersion, devices.current_version_id)
    assert v2.version_number == 2
    assert v2.title == "Authorized Devices & Hardware Inventory"
    at = v2.columns.index("Model") + 1
    assert v2.columns[at:at + 2] == ["Device Type", "Baseline Ref"]
    db_session.refresh(v1)
    assert (v1.title, list(v1.columns), [list(r) for r in v1.rows]) == v1_content


@pytest.mark.integration
def test_control_tags_round_trip_over_http(db_session, tmp_path):
    org_id, _ = _imported(db_session, tmp_path)
    client = _client(db_session, org_id)
    for key in ("CA.L2-3.12.1", "CA.L2-3.12.2", "CA.L2-3.12.3"):
        r = client.get(f"/orgs/{org_id}/list-definitions", params={"control_key": key})
        assert [d["list_key"] for d in r.json()] == ["3-12-2a-poam"], key
    devices = client.get(
        f"/orgs/{org_id}/list-definitions", params={"control_key": "CM.L2-3.4.1"}
    ).json()
    assert {d["list_key"] for d in devices} == {"3-1-1c-authorized-devices", "3-4-1b-software"}


@pytest.mark.integration
def test_editing_creates_a_version_and_the_prior_one_is_byte_identical(db_session, tmp_path):
    org_id, _ = _imported(db_session, tmp_path)
    client = _client(db_session, org_id)
    [risk] = client.get(
        f"/orgs/{org_id}/list-definitions", params={"control_key": "RA.L2-3.11.1"}
    ).json()
    detail = client.get(f"/orgs/{org_id}/list-definitions/{risk['id']}").json()
    before = client.get(f"/orgs/{org_id}/list-definitions/{risk['id']}/versions/1").content

    r = client.post(
        f"/orgs/{org_id}/list-definitions/{risk['id']}/versions",
        json={
            "base_version_id": detail["current"]["id"],
            "rows": [["R-1", "Phishing", "Open"]],
            "note": "first real risk",
        },
    )
    assert r.status_code == 201, r.text
    assert r.json()["version_number"] == 2
    assert client.get(f"/orgs/{org_id}/list-definitions/{risk['id']}/versions/1").content == before
    assert json.loads(before)["rows"][0][1] == "[PLACEHOLDER - no risks assessed yet]"

    stale = client.post(
        f"/orgs/{org_id}/list-definitions/{risk['id']}/versions",
        json={"base_version_id": detail["current"]["id"], "rows": []},
    )
    assert stale.status_code == 409
    ragged = client.post(
        f"/orgs/{org_id}/list-definitions/{risk['id']}/versions",
        json={"base_version_id": r.json()["id"], "rows": [["only one cell"]]},
    )
    assert ragged.status_code == 422


@pytest.mark.integration
def test_the_database_refuses_to_rewrite_a_version(db_session, tmp_path):
    org_id, _ = _imported(db_session, tmp_path)
    vid = db_session.scalar(select(ListVersion.id).where(ListVersion.org_id == org_id).limit(1))
    with pytest.raises(DBAPIError, match="append-only"):
        with db_session.begin_nested():
            db_session.execute(
                text("UPDATE list_version SET rows = '[]'::jsonb WHERE id = :id"), {"id": vid}
            )


@pytest.mark.integration
def test_lists_are_tenant_isolated_over_http(db_session, tmp_path):
    org_a, _ = _imported(db_session, tmp_path / "a")
    org_b = _org(db_session)
    list_a = db_session.scalar(select(ListDefinition.id).where(ListDefinition.org_id == org_a))
    client_b = _client(db_session, org_b)
    assert client_b.get(f"/orgs/{org_b}/list-definitions").json() == []
    assert client_b.get(f"/orgs/{org_b}/list-definitions/{list_a}").status_code == 404
    assert client_b.get(f"/orgs/{org_a}/list-definitions").status_code == 403
    assert client_b.get(f"/orgs/{org_a}/list-definitions/{list_a}").status_code == 403


@pytest.mark.integration
def test_c3pao_assessor_reads_and_cannot_write(db_session, tmp_path):
    org_id, _ = _imported(db_session, tmp_path)
    client = _client(db_session, org_id, role="c3pao_assessor")
    lists = client.get(f"/orgs/{org_id}/list-definitions").json()
    assert len(lists) == 4
    detail = client.get(f"/orgs/{org_id}/list-definitions/{lists[0]['id']}").json()
    r = client.post(
        f"/orgs/{org_id}/list-definitions/{lists[0]['id']}/versions",
        json={"base_version_id": detail["current"]["id"], "rows": []},
    )
    assert r.status_code == 403


@pytest.mark.integration
def test_the_scope_graph_lists_are_untouched(db_session, tmp_path):
    org_id, _ = _imported(db_session, tmp_path)
    client = _client(db_session, org_id)
    views = client.get(f"/orgs/{org_id}/lists").json()
    assert [v["id"] for v in views] == [
        "3.1.1a-authorized-users",
        "3.1.1b-auth-processes",
        "3.1.1c-authorized-devices",
        "external-services",
    ]


@pytest.mark.integration
def test_import_and_edit_never_touch_sprs_or_control_state(db_session, tmp_path):
    from app.engine import start_assessment
    from app.models import AssessmentObjective, Control, ControlState, Framework

    org_id = _org(db_session)
    fw = Framework(key=f"fw-ll-{uuid.uuid4().hex[:6]}", name="LL FW", version="r2")
    db_session.add(fw)
    db_session.flush()
    ctrl = Control(
        framework_id=fw.id,
        control_id="RA.L2-3.11.1",
        family="RA",
        title="Risk",
        requirement_text="Assess risk.",
        sprs_weight=3,
        sequence_order=1,
    )
    db_session.add(ctrl)
    db_session.flush()
    db_session.add(AssessmentObjective(control_id=ctrl.id, objective_key="a", text="Risk"))
    db_session.flush()
    a = start_assessment(db_session, org_id, fw.id, "LL SPRS")
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
    root = build_library(tmp_path)
    apply_import(db_session, org_id, _plan(root))
    db_session.flush()
    client = _client(db_session, org_id)
    [risk] = client.get(
        f"/orgs/{org_id}/list-definitions", params={"control_key": "RA.L2-3.11.1"}
    ).json()
    cur = client.get(f"/orgs/{org_id}/list-definitions/{risk['id']}").json()["current"]["id"]
    assert (
        client.post(
            f"/orgs/{org_id}/list-definitions/{risk['id']}/versions",
            json={"base_version_id": cur, "rows": []},
        ).status_code
        == 201
    )
    assert snapshot() == before


def test_lists_import_refuses_to_run_without_an_explicit_org(tmp_path, monkeypatch):
    """No default target: on live, the old default (deployment_settings.
    msp_org_id) named a leftover demo org. Asserted on the exit code and on
    the import never being reached -- not on the rendered error text, whose
    formatting (rich boxes, ANSI, terminal width) differs between runners."""
    from typer.testing import CliRunner

    from app import list_templates
    from app.cli import app as cli_app

    def _reached(*_a, **_k):
        raise AssertionError("the import ran without an explicit --org-id")

    monkeypatch.setattr(list_templates, "plan_import", _reached)
    result = CliRunner().invoke(cli_app, ["lists-import", str(build_library(tmp_path))])
    assert result.exit_code == 2, (result.exit_code, result.output)


# ---------------------------------------------------------------------------
# L.2: what the screen reads, the conflict it shows, and the edit audit
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_the_merged_device_list_says_what_it_merged(db_session, tmp_path):
    org_id, _ = _imported(db_session, tmp_path)
    client = _client(db_session, org_id)
    [devices] = [
        d for d in client.get(f"/orgs/{org_id}/list-definitions").json()
        if d["list_key"] == "3-1-1c-authorized-devices"
    ]
    # 3.1.1c's own practice plus every one the absorbed hardware sheet named.
    assert devices["control_keys"] == ["AC.L2-3.1.1", "CM.L2-3.4.1", "CM.L2-3.4.2"]
    assert devices["merged_from"] == [
        "CM/3.4.1/Hardware and Software Asset Inventory.xlsx#3.4.1a Hardware"
    ]
    merge = client.get(f"/orgs/{org_id}/list-definitions/{devices['id']}").json()["merge"]
    assert merge["absorbed_ref"].endswith("#3.4.1a Hardware")
    mapped = {c["source_column"]: (c["target_column"], c["added"]) for c in merge["columns"]}
    assert mapped["Type"] == ("Device Type", True)
    assert mapped["Asset Name"] == ("Name", False)
    assert set(mapped) == set(DEVICE_MERGE.column_map)

    others = [
        client.get(f"/orgs/{org_id}/list-definitions/{d['id']}").json()["merge"]
        for d in client.get(f"/orgs/{org_id}/list-definitions").json()
        if d["id"] != devices["id"]
    ]
    assert others == [None, None, None]


@pytest.mark.integration
def test_summaries_carry_when_a_list_changed_and_its_changelog(db_session, tmp_path):
    org_id, _ = _imported(db_session, tmp_path)
    client = _client(db_session, org_id)
    by_key = {d["list_key"]: d for d in client.get(f"/orgs/{org_id}/list-definitions").json()}
    assert by_key["3-11-1a-risk-register"]["has_changelog"] is True
    assert by_key["3-12-2a-poam"]["has_changelog"] is False
    assert all(d["updated_at"] and d["saved_by"] is None for d in by_key.values())


@pytest.mark.integration
def test_a_stale_save_says_who_saved_the_newer_version(db_session, tmp_path):
    org_id, _ = _imported(db_session, tmp_path)
    first = _client(db_session, org_id)
    [risk] = first.get(
        f"/orgs/{org_id}/list-definitions", params={"control_key": "RA.L2-3.11.1"}
    ).json()
    base = first.get(f"/orgs/{org_id}/list-definitions/{risk['id']}").json()["current"]
    saved = first.post(
        f"/orgs/{org_id}/list-definitions/{risk['id']}/versions",
        json={"base_version_id": base["id"], "rows": [["R-1", "Phishing", "Open"]]},
    )
    assert saved.status_code == 201
    assert saved.json()["saved_by"]

    second = _client(db_session, org_id)
    stale = second.post(
        f"/orgs/{org_id}/list-definitions/{risk['id']}/versions",
        json={"base_version_id": base["id"], "rows": [["R-2", "Lost laptop", "Open"]]},
    )
    assert stale.status_code == 409
    detail = stale.json()["detail"]
    assert detail["base_version_id"] == base["id"]
    assert detail["current_version_id"] == saved.json()["id"]
    assert detail["current_version_number"] == 2
    assert detail["saved_by"] == saved.json()["saved_by"]
    assert detail["saved_at"]
    # Nothing was written by the refused save.
    versions = second.get(f"/orgs/{org_id}/list-definitions/{risk['id']}/versions").json()
    assert [v["version_number"] for v in versions] == [1, 2]


@pytest.mark.integration
def test_every_edit_is_audited_with_what_changed(db_session, tmp_path):
    from app.models import AuditLog

    org_id, _ = _imported(db_session, tmp_path)
    client = _client(db_session, org_id)
    [risk] = client.get(
        f"/orgs/{org_id}/list-definitions", params={"control_key": "RA.L2-3.11.1"}
    ).json()
    base = client.get(f"/orgs/{org_id}/list-definitions/{risk['id']}").json()["current"]
    kept = base["rows"][0]
    r = client.post(
        f"/orgs/{org_id}/list-definitions/{risk['id']}/versions",
        json={
            "base_version_id": base["id"],
            "rows": [kept, ["R-1", "Phishing", "Open"]],
            "note": "first real risk",
        },
    )
    assert r.status_code == 201
    [event] = db_session.scalars(
        select(AuditLog).where(
            AuditLog.org_id == org_id, AuditLog.action == "list.version_create"
        )
    ).all()
    assert event.entity_id == uuid.UUID(risk["id"])
    assert event.before_value["version_number"] == 1
    # The two template rows are identical; one was kept, one replaced. Since
    # L.2.2 the row also names columns added and removed -- none here.
    assert event.after_value == {
        "version_number": 2, "row_count": 2, "rows_added": 1, "rows_removed": 1,
        "columns_added": [], "columns_removed": [],
    }
    assert event.context["note"] == "first real risk"


@pytest.mark.integration
def test_the_import_report_says_what_was_never_imported(db_session, tmp_path):
    from app.list_library import record_import

    org_id = _org(db_session)
    root = build_library(tmp_path)
    plan = _plan(root)
    record_import(db_session, org_id, plan, apply_import(db_session, org_id, plan))
    db_session.flush()

    client = _client(db_session, org_id)
    report = client.get(f"/orgs/{org_id}/list-library/import-report").json()
    assert report["recorded_at"]
    statuses = {n["source_ref"]: n["status"] for n in report["not_imported"]}
    assert statuses == {
        s.source_ref: s.status for s in plan.sheets if s.status != "importable"
    }
    assert "merged" in statuses.values()
    assert report["not_ingested"] == plan.not_ingested

    empty = _org(db_session)
    other = _client(db_session, empty)
    assert other.get(f"/orgs/{empty}/list-library/import-report").json() == {
        "recorded_at": None, "not_imported": [], "not_ingested": [],
    }
    assert other.get(f"/orgs/{org_id}/list-library/import-report").status_code == 403


@pytest.mark.integration
def test_c3pao_assessor_can_read_the_import_report(db_session, tmp_path):
    org_id, _ = _imported(db_session, tmp_path)
    client = _client(db_session, org_id, role="c3pao_assessor")
    assert client.get(f"/orgs/{org_id}/list-library/import-report").status_code == 200


@pytest.mark.integration
def test_a_document_can_no_longer_be_called_a_list(db_session):
    org_id = _org(db_session)
    client = _client(db_session, org_id)
    r = client.post(
        f"/orgs/{org_id}/documents",
        json={"doc_id": "LST-1", "doc_type": "list", "title": "Not a list", "body": ""},
    )
    assert r.status_code == 422
    assert "Library > Lists" in r.text
    ok = client.post(
        f"/orgs/{org_id}/documents",
        json={"doc_id": "POL-1", "doc_type": "policy", "title": "A policy", "body": ""},
    )
    assert ok.status_code == 201, ok.text


# Why a version exists (2026-10-14). Importer-caused versions recur: the
# next importer change writes a version of every list it touches, and the
# history has to say the product did that, not a person.


def _risk(db_session, org_id) -> ListDefinition:
    return db_session.scalar(
        select(ListDefinition).where(
            ListDefinition.org_id == org_id, ListDefinition.list_key == "3-11-1a-risk-register"
        )
    )


def _derived(history):
    """The same history with every stored cause removed: what
    `version_causes` concludes for a version written before causes were."""
    return version_causes([
        ListVersion(
            id=v.id, version_number=v.version_number,
            provenance={k: x for k, x in (v.provenance or {}).items() if k != "cause"},
            **content_of(v),
        )
        for v in history
    ])


def test_an_import_cause_compares_the_sheet_before_the_file():
    last = ListVersion(
        version_number=1, provenance={"source_sha256": "f1", "sheet_sha256": "s1"},
        title="T", description="", responsible="", review_cadence="", columns=["A"], rows=[["1"]],
    )
    same = content_of(last)
    other = same | {"title": "T2"}
    assert import_cause("f1", "s1", other, None) == "first_import"
    # A rebuilt workbook (new file hash) whose sheet is unchanged: the importer.
    assert import_cause("f2", "s1", other, last) == "importer_changed"
    assert import_cause("f1", "s2", other, last) == "source_changed"
    assert import_cause("f1", "s1", same, last) == "source_reapplied"
    # A version from before sheet hashes falls back to the file's.
    legacy = ListVersion(version_number=1, provenance={"source_sha256": "f1"}, **same)
    assert import_cause("f1", "s1", other, legacy) == "importer_changed"
    assert import_cause("f2", "s1", other, legacy) == "source_changed"


@pytest.mark.integration
def test_an_importer_change_is_recorded_as_one_not_as_an_edit(
    db_session, tmp_path, monkeypatch
):
    """L.2.1's rename on an org imported before it: v2 says the importer
    changed and the source did not -- on the version, over HTTP, and in the
    import's audit row -- and the same answer is derived without the stored
    cause. In between, the device workbook is re-saved with a changed
    document property, so its file hash moves while its cells do not: the
    sheet hash is what keeps that from reading as a changed source."""
    from dataclasses import replace

    import openpyxl

    from app import list_templates
    from app.models import AuditLog

    monkeypatch.setattr(
        list_templates, "MERGES", (replace(DEVICE_MERGE, title="", added_after=""),)
    )
    org_id, _ = _imported(db_session, tmp_path / "a")
    monkeypatch.setattr(list_templates, "MERGES", (DEVICE_MERGE,))
    root = build_library(tmp_path / "b")
    book = root / "New Lists/AC/3.1.1/Authorized-Entities.xlsx"
    wb = openpyxl.load_workbook(book)
    wb.properties.title = "Re-saved, cells untouched"
    wb.save(book)
    plan = _plan(root)
    diff = apply_import(db_session, org_id, plan)
    record_import(db_session, org_id, plan, diff)
    db_session.flush()

    devices = db_session.scalar(
        select(ListDefinition).where(
            ListDefinition.org_id == org_id,
            ListDefinition.list_key == "3-1-1c-authorized-devices",
        )
    )
    history = history_of(db_session, devices.id)
    assert [v.provenance["cause"] for v in history] == ["first_import", "importer_changed"]
    assert history[0].provenance["source_sha256"] != history[1].provenance["source_sha256"], (
        "fixture must produce distinct file bytes or this test proves nothing"
    )
    assert history[1].provenance["previous_import_version"] == 1
    assert history[1].provenance["changed_fields"] == ["title", "columns", "rows"]
    assert "saved_by" not in history[1].provenance
    assert _derived(history) == version_causes(history)

    client = _client(db_session, org_id)
    versions = client.get(f"/orgs/{org_id}/list-definitions/{devices.id}/versions").json()
    assert [v["cause"] for v in versions] == ["first_import", "importer_changed"]
    assert [v["saved_by"] for v in versions] == [None, None]
    summary = next(
        s for s in client.get(f"/orgs/{org_id}/list-definitions").json()
        if s["id"] == str(devices.id)
    )
    assert summary["cause"] == "importer_changed"

    row = db_session.scalars(
        select(AuditLog).where(AuditLog.org_id == org_id, AuditLog.action == "list.import")
    ).one()
    assert row.actor_type == "system"
    assert row.context["versions"] == [{
        "list_key": "3-1-1c-authorized-devices",
        "version_id": str(history[1].id),
        "version_number": 2,
        "cause": "importer_changed",
        "changed_fields": ["title", "columns", "rows"],
    }]


@pytest.mark.integration
def test_a_changed_source_is_recorded_as_one(db_session, tmp_path):
    org_id, _ = _imported(db_session, tmp_path / "a")
    changed = build_library(tmp_path / "b", risk_status="Closed")
    diff = apply_import(db_session, org_id, _plan(changed))
    db_session.flush()
    assert [a.sheet.list_key for a in diff.of("new_version")] == ["3-11-1a-risk-register"]
    history = history_of(db_session, _risk(db_session, org_id).id)
    assert [v.provenance["cause"] for v in history] == ["first_import", "source_changed"]
    assert _derived(history) == version_causes(history)


@pytest.mark.integration
def test_an_import_over_an_edit_says_it_put_the_source_back(db_session, tmp_path):
    """A person edits a template, then the same library is imported again
    with that edit named: the import restores the source's rows, and the
    version says so and names the edit it replaced, rather than passing for
    a source change."""
    org_id, root = _imported(db_session, tmp_path)
    risk = _risk(db_session, org_id)
    client = _client(db_session, org_id)
    _edit(client, org_id, risk.id, [["R-1", "Edited", "Open"]])

    apply_import(
        db_session, org_id, _plan(root),
        overwrite_edits=frozenset({"3-11-1a-risk-register@v2"}),
    )
    db_session.flush()
    history = history_of(db_session, risk.id)
    assert [v.provenance["cause"] for v in history] == ["first_import", "edit", "source_reapplied"]
    assert history[2].provenance["replaces_edit_in_version"] == 2
    assert history[2].provenance["previous_import_version"] == 1
    assert _derived(history) == version_causes(history)
    versions = client.get(f"/orgs/{org_id}/list-definitions/{risk.id}/versions").json()
    assert [v["cause"] for v in versions] == ["first_import", "edit", "source_reapplied"]


# An import never replaces a person's edit unless that edit is named
# (2026-10-14). Same rule as a sync never overwriting a field it has no
# source for; the override is per edit, never "all", like asset approval.


def _edit(client, org_id, list_id, rows) -> dict:
    base = client.get(f"/orgs/{org_id}/list-definitions/{list_id}").json()["current"]
    r = client.post(
        f"/orgs/{org_id}/list-definitions/{list_id}/versions",
        json={"base_version_id": base["id"], "rows": rows},
    )
    assert r.status_code == 201 and r.json()["cause"] == "edit", r.text
    return r.json()


def _poam(db_session, org_id) -> ListDefinition:
    return db_session.scalar(
        select(ListDefinition).where(
            ListDefinition.org_id == org_id, ListDefinition.list_key == "3-12-2a-poam"
        )
    )


@pytest.mark.integration
def test_an_edited_list_is_a_conflict_the_import_leaves_alone(db_session, tmp_path):
    """The dry run reports it before anything is written, naming the edit;
    the apply skips it -- content, version and tags untouched -- and the
    audit row says which edits it left in place."""
    from app.models import AuditLog

    org_id, _ = _imported(db_session, tmp_path / "a")
    client = _client(db_session, org_id)
    risk = _risk(db_session, org_id)
    _edit(client, org_id, risk.id, [["R-1", "Kept by a person", "Open"]])
    changed = _plan(build_library(tmp_path / "b", risk_status="Closed"))

    dry = diff_import(db_session, org_id, changed)
    [c] = dry.of("conflict")
    assert c.edit_ref == "3-11-1a-risk-register@v2"
    assert c.changed_fields == ("rows",)
    assert dry.of("new_version") == []

    diff = apply_import(db_session, org_id, changed)
    record_import(db_session, org_id, changed, diff)
    db_session.flush()
    history = history_of(db_session, risk.id)
    assert [v.provenance["cause"] for v in history] == ["first_import", "edit"]
    assert db_session.get(ListDefinition, risk.id).current_version_id == history[-1].id
    assert history[-1].rows == [["R-1", "Kept by a person", "Open"]]

    row = db_session.scalars(
        select(AuditLog).where(AuditLog.org_id == org_id, AuditLog.action == "list.import")
    ).all()[-1]
    assert row.context["actions"]["conflict"] == 1
    assert [(c["list_key"], c["edit"]) for c in row.context["conflicts"]] == [
        ("3-11-1a-risk-register", "3-11-1a-risk-register@v2")
    ]
    assert row.context["overwrote_edits"] == []
    assert row.context["versions"] == []


@pytest.mark.integration
def test_only_the_named_edit_is_replaced(db_session, tmp_path):
    """Two lists carry edits; naming one replaces that one and leaves the
    other as a conflict. Naming one cannot authorize the rest."""
    from app.models import AuditLog

    org_id, root = _imported(db_session, tmp_path)
    client = _client(db_session, org_id)
    risk, poam = _risk(db_session, org_id), _poam(db_session, org_id)
    _edit(client, org_id, risk.id, [["R-1", "Edited", "Open"]])
    _edit(client, org_id, poam.id, [["W9", "M9"]])

    plan = _plan(root)
    diff = apply_import(
        db_session, org_id, plan, overwrite_edits=frozenset({"3-12-2a-poam@v2"})
    )
    record_import(db_session, org_id, plan, diff)
    db_session.flush()
    assert [a.edit_ref for a in diff.of("conflict")] == ["3-11-1a-risk-register@v2"]
    assert [v.provenance["cause"] for v in history_of(db_session, risk.id)] == [
        "first_import", "edit"
    ]
    poam_history = history_of(db_session, poam.id)
    assert [v.provenance["cause"] for v in poam_history] == [
        "first_import", "edit", "source_reapplied"
    ]
    assert poam_history[-1].provenance["replaces_edit_in_version"] == 2
    row = db_session.scalars(
        select(AuditLog).where(AuditLog.org_id == org_id, AuditLog.action == "list.import")
    ).all()[-1]
    assert row.context["overwrote_edits"] == ["3-12-2a-poam@v2"]
    assert [c["edit"] for c in row.context["conflicts"]] == ["3-11-1a-risk-register@v2"]


@pytest.mark.integration
def test_naming_an_edit_that_is_not_a_current_conflict_writes_nothing(db_session, tmp_path):
    """A typo, a list with no edit, or an edit someone has since saved over
    (v2 named, v3 current) refuses the whole apply before any write."""
    org_id, root = _imported(db_session, tmp_path / "a")
    client = _client(db_session, org_id)
    risk = _risk(db_session, org_id)
    _edit(client, org_id, risk.id, [["R-1", "First edit", "Open"]])
    _edit(client, org_id, risk.id, [["R-1", "Second edit", "Open"]])
    changed = _plan(build_library(tmp_path / "b", risk_status="Closed"))
    before = {d.id: d.current_version_id for d in db_session.scalars(
        select(ListDefinition).where(ListDefinition.org_id == org_id)
    )}

    for named in ("3-11-1a-risk-register@v2", "3-12-2a-poam@v1", "no-such-list@v2"):
        with pytest.raises(ImportRefused, match="Nothing was written"):
            apply_import(db_session, org_id, changed, overwrite_edits=frozenset({named}))
        db_session.flush()
        after = {d.id: d.current_version_id for d in db_session.scalars(
            select(ListDefinition).where(ListDefinition.org_id == org_id)
        )}
        assert after == before, named
    # The current edit, named exactly, is accepted.
    diff = apply_import(
        db_session, org_id, changed, overwrite_edits=frozenset({"3-11-1a-risk-register@v3"})
    )
    assert [a.sheet.list_key for a in diff.of("new_version")] == ["3-11-1a-risk-register"]


def test_overwrite_edit_without_apply_is_refused(tmp_path):
    from typer.testing import CliRunner

    from app.cli import app as cli_app

    result = CliRunner().invoke(cli_app, [
        "lists-import", str(build_library(tmp_path)), "--org-id", str(uuid.uuid4()),
        "--overwrite-edit", "3-11-1a-risk-register@v2",
    ])
    assert result.exit_code == 2, (result.exit_code, result.output)
