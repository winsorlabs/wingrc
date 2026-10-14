"""List library L.3: exporting a list, and lists in the assessor bundle.

docs/PLAN-list-library.md §5 is the spec. What these pin:

- A list in a bundle is named by the `list_version` it embedded, and the
  bundle does not move when the list does.
- The workbook is a function of the version alone, so two exports of
  unchanged data are byte-identical -- standalone or inside a bundle -- and
  the bundle's artifact log and cover hash cover it.
- No list is in a bundle unless the operator chose it.
- The export is a GET rendered in memory: an assessor can export a list
  (and still cannot edit one), and two orgs never share a path.
"""

from __future__ import annotations

import hashlib
import io
import re
import uuid
import zipfile

import openpyxl
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.auth import get_current_user
from app.bundle_service import render_bundle, snapshot_bundle
from app.db import get_session
from app.list_library import create_version, set_tags
from app.main import app
from app.models import (
    Assessment,
    AssessmentObjective,
    AuditLog,
    Control,
    ControlState,
    Framework,
    ListDefinition,
    Organization,
)
from app.storage import NullStorageClient, get_storage_client
from tests.conftest import _app_session, _authed, _grant, _make_fake_user

EXPORT = "/orgs/{org}/list-definitions/{list}/export"
BUNDLE = "/orgs/{org}/assessments/{a}/bundle"


def test_list_export_route_is_get():
    ops = app.openapi()["paths"]["/orgs/{org_id}/list-definitions/{list_id}/export"]
    assert set(ops) == {"get"}


def test_the_workbook_carries_no_wall_clock_time():
    """Byte comparison alone cannot prove this: two exports inside one
    second match even if the save time leaks in. So check every time the
    file holds is the version's own: each zip entry's date and
    docProps/core.xml's created and modified."""
    from datetime import UTC, datetime

    from app.list_library import ListVersionSnap
    from app.render import render_list_version_bytes

    saved = datetime(2024, 3, 5, 6, 7, 8, tzinfo=UTC)
    snap = ListVersionSnap(
        "l", "k", False, ("AC.L2-3.1.1",), "vid", 1, "T", "", "", "", ("A",), (("x",),),
        saved, None,
    )
    zf = zipfile.ZipFile(io.BytesIO(render_list_version_bytes(snap)))
    assert {i.date_time for i in zf.infolist()} == {(2024, 3, 5, 6, 7, 8)}
    core = zf.read("docProps/core.xml").decode()
    stamps = re.findall(r"<dcterms:(?:created|modified)[^>]*>([^<]*)<", core)
    assert stamps == ["2024-03-05T06:07:08Z", "2024-03-05T06:07:08Z"], core


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


def _org_with_assessment(db_session, label: str) -> tuple[uuid.UUID, uuid.UUID]:
    org = Organization(name=f"ListBundle-{label}-{uuid.uuid4().hex[:8]}")
    fw = Framework(key=f"fw-lb-{uuid.uuid4().hex[:6]}", name="LB FW", version="r2")
    db_session.add_all([org, fw])
    db_session.flush()
    ctrl = Control(
        framework_id=fw.id, control_id="AC.L2-3.1.1", family="AC", title="Access Control",
        requirement_text="Limit access.", sprs_weight=5, sequence_order=1,
    )
    db_session.add(ctrl)
    db_session.flush()
    obj = AssessmentObjective(control_id=ctrl.id, objective_key="a", text="Objective a.")
    db_session.add(obj)
    db_session.flush()
    assessment = Assessment(org_id=org.id, framework_id=fw.id, name="LB", status="in_progress")
    db_session.add(assessment)
    db_session.flush()
    db_session.add(ControlState(
        assessment_id=assessment.id, org_id=org.id, objective_id=obj.id, status="not_met"
    ))
    db_session.flush()
    return org.id, assessment.id


def _list(db_session, org_id, key, rows, tags=("AC.L2-3.1.1", "CM.L2-3.4.1"), template=False):
    d = ListDefinition(org_id=org_id, list_key=key, is_template=template)
    db_session.add(d)
    db_session.flush()
    create_version(
        db_session, d,
        {"title": key.title(), "description": "", "responsible": "", "review_cadence": "",
         "columns": ["Name", "Serial"], "rows": rows},
        {"via": "test"},
    )
    set_tags(db_session, d, tuple(tags))
    return d


def _client(db_session, org_id, role="msp_engineer") -> TestClient:
    user = _make_fake_user(role=role, org_id=org_id, email=f"{uuid.uuid4().hex[:8]}@x.com")
    _grant(db_session, user)
    app.dependency_overrides[get_session] = _app_session(db_session)
    app.dependency_overrides[get_current_user] = _authed(db_session, user)
    app.dependency_overrides[get_storage_client] = NullStorageClient
    return TestClient(app)


def _zip(content: bytes) -> zipfile.ZipFile:
    return zipfile.ZipFile(io.BytesIO(content))


def _member(zf: zipfile.ZipFile, suffix: str) -> bytes:
    [name] = [n for n in zf.namelist() if n.endswith(suffix)]
    return zf.read(name)


def _cells(xlsx: bytes) -> list[list]:
    ws = openpyxl.load_workbook(io.BytesIO(xlsx)).active
    return [[c.value for c in row] for row in ws.iter_rows()]


def _unstamped(html: bytes) -> bytes:
    return re.sub(rb"generated [^<]*", b"", html)


@pytest.mark.integration
def test_no_list_is_in_a_bundle_unless_chosen(db_session):
    org_id, a_id = _org_with_assessment(db_session, "none")
    _list(db_session, org_id, "devices", [["ALPHA-01", "SN1"]])
    _list(db_session, org_id, "template-list", [["[PLACEHOLDER]", ""]], template=True)
    r = _client(db_session, org_id).get(BUNDLE.format(org=org_id, a=a_id))
    assert r.status_code == 200, r.text
    zf = _zip(r.content)
    assert not [n for n in zf.namelist() if n.endswith(".xlsx")]
    assert b"No lists were chosen for this bundle." in _member(zf, "lists/index.html")
    assert b"None chosen at export" in _member(zf, "cover.html")


@pytest.mark.integration
def test_the_bundle_names_the_version_it_embedded_and_hashes_it(db_session):
    org_id, a_id = _org_with_assessment(db_session, "embed")
    d = _list(db_session, org_id, "devices", [["ALPHA-01", "SN1"]])
    _list(db_session, org_id, "not-chosen", [["X", "Y"]])
    version_id = str(d.current_version_id)

    r = _client(db_session, org_id).get(
        BUNDLE.format(org=org_id, a=a_id), params={"list_id": str(d.id)}
    )
    assert r.status_code == 200, r.text
    zf = _zip(r.content)
    xlsx = _member(zf, "lists/devices_v1.xlsx")
    assert [n for n in zf.namelist() if n.endswith(".xlsx")] == [
        next(n for n in zf.namelist() if n.endswith("lists/devices_v1.xlsx"))
    ], "only the chosen list"
    cells = _cells(xlsx)
    assert version_id in cells[2][0]
    assert ["ALPHA-01", "SN1"] in cells
    index = _member(zf, "lists/index.html").decode()
    assert version_id in index and hashlib.sha256(xlsx).hexdigest() in index

    log = _member(zf, "artifact_log.txt")
    [line] = [ln for ln in log.decode().splitlines() if ln.endswith("lists/devices_v1.xlsx")]
    assert line.split(" | ")[1] == hashlib.sha256(xlsx).hexdigest()
    assert any(ln.endswith("lists/index.html") for ln in log.decode().splitlines())
    assert hashlib.sha256(log).hexdigest() in _member(zf, "cover.html").decode(), (
        "the cover's second-order hash covers the log that lists the embedded list"
    )

    audit = db_session.scalars(
        select(AuditLog)
        .where(AuditLog.org_id == org_id, AuditLog.action == "bundle.export")
        .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
    ).first()
    assert audit.after_value["list_versions"] == [
        {"list_id": str(d.id), "version_id": version_id, "version_number": 1}
    ]


@pytest.mark.integration
def test_editing_the_list_afterwards_does_not_change_the_bundle(db_session):
    org_id, a_id = _org_with_assessment(db_session, "edit")
    d = _list(db_session, org_id, "devices", [["ALPHA-01", "SN1"]])
    client = _client(db_session, org_id)
    v1_id = str(d.current_version_id)

    snap = snapshot_bundle(db_session, NullStorageClient(), org_id, a_id, [d.id])
    r = client.post(
        f"/orgs/{org_id}/list-definitions/{d.id}/versions",
        json={"base_version_id": v1_id, "rows": [["CHANGED-01", "SN9"]]},
    )
    assert r.status_code == 201, r.text

    zf = _zip(render_bundle(snap)[0])
    embedded = _member(zf, "lists/devices_v1.xlsx")
    assert ["ALPHA-01", "SN1"] in _cells(embedded)
    assert "CHANGED-01" not in str(_cells(embedded))
    assert v1_id in _member(zf, "lists/index.html").decode()
    old = client.get(EXPORT.format(org=org_id, list=d.id), params={"version": 1})
    assert old.content == embedded, "the bundle's list is version 1, byte for byte"

    fresh = _zip(client.get(
        BUNDLE.format(org=org_id, a=a_id), params={"list_id": str(d.id)}
    ).content)
    assert ["CHANGED-01", "SN9"] in _cells(_member(fresh, "lists/devices_v2.xlsx"))
    assert v1_id not in _member(fresh, "lists/index.html").decode()


@pytest.mark.integration
def test_two_exports_of_unchanged_data_are_byte_identical(db_session):
    org_id, a_id = _org_with_assessment(db_session, "same")
    tags = ("CM.L2-3.4.2", "AC.L2-3.1.1", "CM.L2-3.4.1")
    d = _list(db_session, org_id, "devices", [["B", "2"], ["A", "1"], ["B", "2"]], tags=tags)
    other = _list(db_session, org_id, "other", [["X", "Y"]])
    client = _client(db_session, org_id)

    def bundle():
        zf = _zip(client.get(
            BUNDLE.format(org=org_id, a=a_id), params={"list_id": [str(other.id), str(d.id)]}
        ).content)
        log = _member(zf, "artifact_log.txt").decode().splitlines()
        log = [ln for ln in log if "/lists/" in ln]
        return (
            _member(zf, "lists/devices_v1.xlsx"),
            _member(zf, "lists/other_v1.xlsx"),
            _unstamped(_member(zf, "lists/index.html")),
            log,
        )

    export_1 = client.get(EXPORT.format(org=org_id, list=d.id)).content
    bundle_1 = bundle()
    # Perturb physical row order between exports: rewrite the tags in a
    # different insertion order, and write an unrelated list version.
    set_tags(db_session, d, tuple(reversed(tags)))
    set_tags(db_session, other, ("CM.L2-3.4.1", "AC.L2-3.1.1"))
    set_tags(db_session, other, ("AC.L2-3.1.1", "CM.L2-3.4.1"))
    _list(db_session, org_id, "unrelated", [["Z", "Z"]])
    db_session.flush()
    export_2 = client.get(EXPORT.format(org=org_id, list=d.id)).content
    bundle_2 = bundle()

    assert export_1 == export_2
    assert bundle_1 == bundle_2
    assert bundle_1[0] == export_1, "the bundle and the standalone export are one renderer"
    assert "AC.L2-3.1.1 / CM.L2-3.4.1 / CM.L2-3.4.2" in _cells(export_1)[1][0]
    assert [ln.rsplit("/", 1)[1] for ln in bundle_1[3]] == [
        "index.html", "devices_v1.xlsx", "other_v1.xlsx"
    ], "embedded in list_key order, whatever order they were asked for"


@pytest.mark.integration
def test_c3pao_can_export_a_list_and_still_cannot_edit_one(db_session):
    org_id, _ = _org_with_assessment(db_session, "c3pao")
    d = _list(db_session, org_id, "devices", [["ALPHA-01", "SN1"]])
    assessor = _client(db_session, org_id, role="c3pao_assessor")

    r = assessor.get(EXPORT.format(org=org_id, list=d.id))
    assert r.status_code == 200, r.text
    assert r.headers["x-wingrc-list-version-id"] == str(d.current_version_id)
    assert 'filename="devices_v1.xlsx"' in r.headers["content-disposition"]
    assert ["ALPHA-01", "SN1"] in _cells(r.content)
    assert assessor.post(
        f"/orgs/{org_id}/list-definitions/{d.id}/versions",
        json={"base_version_id": str(d.current_version_id), "rows": []},
    ).status_code == 403
    audit = db_session.scalars(
        select(AuditLog).where(AuditLog.org_id == org_id, AuditLog.action == "list.export")
    ).one()
    assert audit.after_value == {"version_id": str(d.current_version_id), "version_number": 1}


@pytest.mark.integration
def test_two_orgs_exporting_the_same_list_each_get_only_their_own(
    db_session, tmp_path, monkeypatch
):
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
    org_a, a_a = _org_with_assessment(db_session, "a")
    org_b, a_b = _org_with_assessment(db_session, "b")
    da = _list(db_session, org_a, "3-1-1c-authorized-devices", [["ALPHA-WS-01", "SA"]])
    db = _list(db_session, org_b, "3-1-1c-authorized-devices", [["BRAVO-WS-01", "SB"]])
    client_a = _client(db_session, org_a)
    r_a = client_a.get(EXPORT.format(org=org_a, list=da.id))
    bundle_a = client_a.get(BUNDLE.format(org=org_a, a=a_a), params={"list_id": str(da.id)})
    client_b = _client(db_session, org_b)
    r_b = client_b.get(EXPORT.format(org=org_b, list=db.id))
    bundle_b = client_b.get(BUNDLE.format(org=org_b, a=a_b), params={"list_id": str(db.id)})

    for resp, mine, theirs in ((r_a, "ALPHA", "BRAVO"), (r_b, "BRAVO", "ALPHA")):
        assert resp.status_code == 200
        assert mine in str(_cells(resp.content)) and theirs not in str(_cells(resp.content))
    for resp, mine, theirs in ((bundle_a, "ALPHA", "BRAVO"), (bundle_b, "BRAVO", "ALPHA")):
        name = "lists/3-1-1c-authorized-devices_v1.xlsx"
        embedded = str(_cells(_member(_zip(resp.content), name)))
        assert mine in embedded and theirs not in embedded
    assert list(tmp_path.iterdir()) == [], "rendered in memory, never to a shared path"

    # Across the boundary: not a member, and RLS hides the other org's list.
    assert client_b.get(EXPORT.format(org=org_a, list=da.id)).status_code == 403
    assert client_b.get(EXPORT.format(org=org_b, list=da.id)).status_code == 404
    assert client_b.get(
        BUNDLE.format(org=org_b, a=a_b), params={"list_id": str(da.id)}
    ).status_code == 404


@pytest.mark.integration
def test_lists_in_a_bundle_do_not_move_the_score(db_session):
    org_id, a_id = _org_with_assessment(db_session, "sprs")
    d = _list(db_session, org_id, "devices", [["ALPHA-01", "SN1"]])
    client = _client(db_session, org_id)
    without = _member(_zip(client.get(BUNDLE.format(org=org_id, a=a_id)).content), "cover.html")
    with_list = _member(_zip(client.get(
        BUNDLE.format(org=org_id, a=a_id), params={"list_id": str(d.id)}
    ).content), "cover.html")
    score = re.compile(rb"<span class='score-big'>(-?\d+)</span>")
    assert score.search(without).group(1) == score.search(with_list).group(1)
    assert db_session.get(Assessment, a_id).sprs_score == int(score.search(without).group(1))
    assert {cs.status for cs in db_session.scalars(
        select(ControlState).where(ControlState.assessment_id == a_id)
    )} == {"not_met"}


@pytest.mark.integration
def test_a_cell_is_text_never_a_formula(db_session):
    """Over HTTP, every workbook an assessor can be handed: the list
    export, the bundle's copy, and a scope-drawn list whose device name
    and Notes are payloads. The full OWASP prefix set, not "=" alone
    (tests/test_workbook_formulas.py covers each renderer directly)."""
    from app.models import ScopeEntity
    from tests.test_workbook_formulas import PAYLOADS, _check

    org_id, a_id = _org_with_assessment(db_session, "formula")
    d = _list(db_session, org_id, "devices", [[p, "SN1"] for p in PAYLOADS])
    for i, p in enumerate(PAYLOADS):
        db_session.add(ScopeEntity(
            org_id=org_id, entity_type="device", natural_key=f"F-{i}",
            scope_category="CUI Asset", status="active", in_boundary=True,
            attributes={"Name": p, "notes": p},
        ))
    db_session.flush()
    client = _client(db_session, org_id)

    export = client.get(EXPORT.format(org=org_id, list=d.id)).content
    assert _check(export) == set(PAYLOADS)
    bundle = client.get(BUNDLE.format(org=org_id, a=a_id), params={"list_id": str(d.id)})
    assert _member(_zip(bundle.content), "devices_v1.xlsx") == export
    scope = client.get(f"/orgs/{org_id}/exports/3.1.1c-authorized-devices")
    assert scope.status_code == 200
    assert _check(scope.content) == set(PAYLOADS)
