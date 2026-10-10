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
from app.list_library import apply_import, diff_import
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
    assert devices.columns == tuple(DEVICE_COLUMNS) + ("Device Type", "Baseline Ref")
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


def test_lists_import_refuses_to_run_without_an_explicit_org(tmp_path):
    """No default target: on live, the old default (deployment_settings.
    msp_org_id) named a leftover demo org."""
    from typer.testing import CliRunner

    from app.cli import app as cli_app

    root = build_library(tmp_path)
    result = CliRunner().invoke(cli_app, ["lists-import", str(root)])
    assert result.exit_code != 0
    assert "org-id" in result.output.lower()
