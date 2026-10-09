"""One natural-key derivation for every importer (2026-10-09).

A live dry-run of Jarrod's populated workbook would have duplicated WL-DT26:
Liongard keyed it by hostname (its serial is ASUS's "System Serial Number"
placeholder) while the workbook keyed it by the Serial cell verbatim. Both
importers now call natural_key.py; the first tests here assert they agree on
the same device, which is the property that actually matters.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import openpyxl
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.auth import get_current_user
from app.catalog import AUTHORIZED_DEVICES, AUTHORIZED_USERS
from app.db import get_session
from app.domain import CanonicalEntity, ChangeType, EntityType, Source
from app.importers.liongard import device_profile_to_canonical, identity_to_canonical
from app.importers.workbook import parse_workbook
from app.main import app
from app.models import Organization, ScopeEntity
from app.natural_key import device_natural_key, is_placeholder_serial, person_natural_key
from app.reconcile import possible_person_matches, reconcile, source_precedence_conflicts
from tests.conftest import _app_session, _authed, _grant, _make_fake_user

# Verbatim from Jarrod's populated workbook (md5 be72ba10...).
WL_DT26_WORKBOOK_SERIAL = "System Serial Number (not set by OEM - recommend correcting)"


def _workbook(path: Path, devices=(), users=()) -> Path:
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for view, rows in ((AUTHORIZED_DEVICES, devices), (AUTHORIZED_USERS, users)):
        ws = wb.create_sheet(view.sheet_title)
        cols = [d for _, d in view.columns] + (["Email"] if view is AUTHORIZED_USERS else [])
        ws.append(cols)
        for r in rows:
            ws.append([r.get(c) for c in cols])
    wb.save(path)
    return path


def _liongard_device(serial, hostname) -> CanonicalEntity:
    e, _ = device_profile_to_canonical(
        {"ID": f"id-{hostname}", "EnvironmentID": 1, "SerialNumber": serial, "Hostname": hostname},
        source_ref="liongard:1",
    )
    return e


def _workbook_device_key(tmp_path, serial, name) -> str:
    path = _workbook(tmp_path / "d.xlsx", devices=[{"Name": name, "Serial # or Asset Tag": serial}])
    [e] = [e for e in parse_workbook(path) if e.entity_type == EntityType.DEVICE]
    return e.natural_key


# ---------------------------------------------------------------------------
# The two importers agree
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("liongard_serial", "workbook_serial", "hostname", "expected"),
    [
        # WL-DT26, exactly as each source spells it.
        (
            "System Serial Number",
            "System Serial Number (not set by OEM - recommend correcting)",
            "WL-DT26",
            "WL-DT26",
        ),
        ("PF3Y6K26", "PF3Y6K26", "WL-LT26", "PF3Y6K26"),
        ("To be filled by O.E.M.", "to be filled by o.e.m.", "WS-02", "WS-02"),
        (None, "[PLACEHOLDER - serial not in Datto RMM export]", "WS-03", "WS-03"),
    ],
)
def test_liongard_and_workbook_key_the_same_device_identically(
    tmp_path, liongard_serial, workbook_serial, hostname, expected
):
    assert _liongard_device(liongard_serial, hostname).natural_key == expected
    assert _workbook_device_key(tmp_path, workbook_serial, hostname) == expected


def test_liongard_and_workbook_key_the_same_person_identically(tmp_path):
    person, _ = identity_to_canonical(
        {"ID": "i1", "Email": "jarrod.winsor@winsorlabs.com", "DisplayName": "Jarrod Winsor"},
        source_ref="liongard:1",
    )
    path = _workbook(
        tmp_path / "u.xlsx",
        users=[
            {"First Name": "Jarrod", "Last Name": "Winsor", "Email": "jarrod.winsor@winsorlabs.com"}
        ],
    )
    [row] = [e for e in parse_workbook(path) if e.entity_type == EntityType.PERSON]
    assert person.natural_key == row.natural_key == "jarrod.winsor@winsorlabs.com"


def test_a_stated_gap_is_never_an_identity():
    assert device_natural_key("[PLACEHOLDER - Intune reports 0]", "PHONE-01") == "PHONE-01"
    assert device_natural_key("[PLACEHOLDER - a]", "[PLACEHOLDER - b]") == ""
    assert device_natural_key("[PLACEHOLDER]", None) == ""
    assert person_natural_key(email="[PLACEHOLDER - none]", display_name="Ada Lovelace") == (
        "Ada Lovelace"
    )
    assert is_placeholder_serial("System Serial Number (not set by OEM)")
    assert not is_placeholder_serial("PF3Y6K26")


def test_a_row_with_no_identifier_is_skipped_not_keyed_by_placeholder_text(tmp_path):
    path = _workbook(
        tmp_path / "d.xlsx",
        devices=[{"Name": "[PLACEHOLDER - unnamed]", "Serial # or Asset Tag": "[PLACEHOLDER - x]"}],
    )
    assert [e for e in parse_workbook(path) if e.entity_type == EntityType.DEVICE] == []


# ---------------------------------------------------------------------------
# Source precedence and possible matches (pure)
# ---------------------------------------------------------------------------


def _entity(etype, key, source, **attrs) -> CanonicalEntity:
    return CanonicalEntity(entity_type=etype, natural_key=key, attributes=attrs, source=source)


def test_a_workbook_value_never_replaces_a_connector_value_and_is_reported():
    current = _entity(EntityType.DEVICE, "PF3Y6K26", Source.LIONGARD, model="20XW", version="Win11")
    incoming = _entity(
        EntityType.DEVICE,
        "PF3Y6K26",
        Source.WORKBOOK,
        model="ThinkPad",
        version="Win11",
        location="HQ",
    )
    result = reconcile([current], [incoming])
    conflicts = source_precedence_conflicts(result)
    [change] = result.changes
    assert "model" not in change.field_diffs
    assert conflicts[current.key()] == [
        "Conflict, not applied: liongard supplies model='20XW'; the import says 'ThinkPad'."
    ]


def test_a_name_keyed_person_is_a_possible_match_never_a_merge():
    current = [
        _entity(
            EntityType.PERSON,
            "jarrod.winsor@winsorlabs.com",
            Source.LIONGARD,
            display_name="Jarrod Winsor",
        )
    ]
    incoming = [_entity(EntityType.PERSON, "Jarrod Winsor", Source.WORKBOOK)]
    result = reconcile(current, incoming)
    kinds = {c.natural_key: c.change_type for c in result.changes}
    assert kinds == {
        "Jarrod Winsor": ChangeType.NEW,
        "jarrod.winsor@winsorlabs.com": ChangeType.MISSING,
    }
    [msg] = possible_person_matches(result, current)[incoming[0].key()]
    assert "jarrod.winsor@winsorlabs.com" in msg and "Not merged" in msg


# ---------------------------------------------------------------------------
# Real HTTP, real RLS
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


def _client(db_session):
    org = Organization(name=f"NatKeyOrg-{uuid.uuid4().hex[:8]}")
    db_session.add(org)
    db_session.flush()
    user = _make_fake_user(
        role="msp_engineer", org_id=org.id, email=f"{uuid.uuid4().hex[:8]}@x.com"
    )
    _grant(db_session, user)
    app.dependency_overrides[get_session] = _app_session(db_session)
    app.dependency_overrides[get_current_user] = _authed(db_session, user)
    return TestClient(app), org.id


def _seed_liongard_wl_dt26(db_session, org_id):
    from app import repo

    repo.upsert(db_session, org_id, _liongard_device("System Serial Number", "WL-DT26"))
    db_session.flush()


def _dry_run(client, org_id, path):
    r = client.post(
        f"/orgs/{org_id}/imports/workbook/dry-run",
        files={"file": (path.name, path.read_bytes(), "application/octet-stream")},
    )
    assert r.status_code == 200, r.text
    return r.json()["changes"]


@pytest.mark.integration
def test_reimporting_wl_dt26_from_a_workbook_does_not_duplicate_it(db_session, tmp_path):
    client, org_id = _client(db_session)
    _seed_liongard_wl_dt26(db_session, org_id)
    path = _workbook(
        tmp_path / "w.xlsx",
        devices=[
            {
                "Name": "WL-DT26",
                "Serial # or Asset Tag": WL_DT26_WORKBOOK_SERIAL,
                "Location": "Office",
            }
        ],
    )
    changes = [c for c in _dry_run(client, org_id, path) if c["entity_type"] == "device"]
    assert [c["change_type"] for c in changes if c["change_type"] in ("new", "missing")] == []


@pytest.mark.integration
def test_applying_never_overwrites_connector_fields_or_takes_provenance(db_session, tmp_path):
    from app import repo

    _, org_id = _client(db_session)
    repo.upsert(
        db_session,
        org_id,
        _entity(EntityType.DEVICE, "PF3Y6K26", Source.LIONGARD, model="20XW", make_oem="LENOVO"),
    )
    repo.upsert(
        db_session,
        org_id,
        _entity(EntityType.DEVICE, "PF3Y6K26", Source.WORKBOOK, model="ThinkPad", location="HQ"),
    )
    db_session.flush()
    row = db_session.scalars(
        select(ScopeEntity).where(
            ScopeEntity.org_id == org_id, ScopeEntity.natural_key == "PF3Y6K26"
        )
    ).one()
    assert row.attributes["model"] == "20XW"
    assert row.attributes["make_oem"] == "LENOVO"
    assert row.attributes["location"] == "HQ", "a field only the workbook supplies still lands"
    assert row.source == "liongard"


@pytest.mark.integration
def test_missing_rows_are_never_acted_on(db_session, tmp_path):
    """Answering 'what does apply do with missing rows': nothing. Applying a
    dry-run's full change list -- missing rows included -- leaves an entity
    the workbook does not mention exactly as it was."""
    client, org_id = _client(db_session)
    _seed_liongard_wl_dt26(db_session, org_id)
    path = _workbook(
        tmp_path / "w.xlsx", devices=[{"Name": "OTHER-01", "Serial # or Asset Tag": "SN-1"}]
    )
    changes = _dry_run(client, org_id, path)
    assert any(c["change_type"] == "missing" and c["natural_key"] == "WL-DT26" for c in changes)

    r = client.post(f"/orgs/{org_id}/imports/workbook/apply", json={"changes": changes})
    assert r.status_code == 200, r.text
    db_session.expire_all()
    row = db_session.scalars(
        select(ScopeEntity).where(
            ScopeEntity.org_id == org_id, ScopeEntity.natural_key == "WL-DT26"
        )
    ).one()
    assert (row.status, row.in_boundary, row.source) == ("active", True, "liongard")


@pytest.mark.integration
def test_dry_run_shows_conflicts_and_possible_matches(db_session, tmp_path):
    from app import repo

    client, org_id = _client(db_session)
    repo.upsert(
        db_session,
        org_id,
        _entity(
            EntityType.DEVICE, "PF3Y6K26", Source.LIONGARD, model="20XW", display_name="WL-LT26"
        ),
    )
    repo.upsert(
        db_session,
        org_id,
        _entity(
            EntityType.PERSON,
            "jarrod.winsor@winsorlabs.com",
            Source.LIONGARD,
            display_name="Jarrod Winsor",
        ),
    )
    db_session.flush()
    path = _workbook(
        tmp_path / "w.xlsx",
        devices=[{"Name": "WL-LT26", "Serial # or Asset Tag": "PF3Y6K26", "Model": "ThinkPad"}],
        users=[{"First Name": "Jarrod", "Last Name": "Winsor"}],
    )
    changes = {c["natural_key"]: c for c in _dry_run(client, org_id, path)}
    pf = changes["PF3Y6K26"]
    assert "model" not in pf["field_diffs"]
    assert any("Conflict, not applied" in w and "model" in w for w in pf["warnings"])
    assert any("Possible match" in w for w in changes["Jarrod Winsor"]["warnings"])


# ---------------------------------------------------------------------------
# "Workbook wins" for the fields only a workbook supplies
# ---------------------------------------------------------------------------


def test_workbook_overlay_columns_land_under_the_protected_keys(tmp_path):
    path = _workbook(
        tmp_path / "w.xlsx",
        devices=[
            {
                "Name": "WS-1",
                "Serial # or Asset Tag": "SN-1",
                "Location": "Office",
                "In Service Date": "2025-01-10",
                "Decommissioned Date": "[PLACEHOLDER - still in service]",
            }
        ],
    )
    [d] = [e for e in parse_workbook(path) if e.entity_type == EntityType.DEVICE]
    assert d.attributes["location"] == "Office"
    assert d.attributes["in_service_date"] == "2025-01-10"
    assert d.attributes["decommissioned_date"] == "[PLACEHOLDER - still in service]"
    assert d.status.value == "active", "a stated gap must never decommission a device"


def test_reconcile_shows_an_overlay_field_the_import_carries():
    current = _entity(EntityType.DEVICE, "SN-1", Source.LIONGARD, model="X")
    incoming = _entity(EntityType.DEVICE, "SN-1", Source.WORKBOOK, model="X", location="Office")
    [c] = reconcile([current], [incoming]).changes
    assert c.change_type == ChangeType.CHANGED
    assert c.field_diffs == {"location": (None, "Office")}


@pytest.mark.integration
def test_workbook_location_survives_a_later_liongard_sync_and_raw_record_survives_workbook(
    db_session,
):
    from app import repo

    _, org_id = _client(db_session)
    synced = _liongard_device("SN-9", "WS-9")
    synced.attributes["LastSeen"] = "2026-10-09"
    repo.upsert(db_session, org_id, synced)
    repo.upsert(
        db_session,
        org_id,
        _entity(EntityType.DEVICE, "SN-9", Source.WORKBOOK, location="Office", Location="Office"),
    )
    db_session.flush()

    def row():
        db_session.expire_all()
        return db_session.scalars(
            select(ScopeEntity).where(
                ScopeEntity.org_id == org_id, ScopeEntity.natural_key == "SN-9"
            )
        ).one()

    assert row().attributes["LastSeen"] == "2026-10-09", "a workbook write keeps the raw record"
    repo.upsert(db_session, org_id, _liongard_device("SN-9", "WS-9"))  # same-source refresh
    db_session.flush()
    assert row().attributes["location"] == "Office"


def test_a_placeholder_never_becomes_a_canonical_value():
    from app.importers.workbook import _add_canonical_device_aliases

    attrs = {
        "Serial # or Asset Tag": WL_DT26_WORKBOOK_SERIAL,
        "Make": "[PLACEHOLDER - not in Datto RMM export]",
        "Model": "21CD000HUS",
    }
    _add_canonical_device_aliases(attrs)
    assert "asset_tag" not in attrs and "make_oem" not in attrs
    assert attrs["model"] == "21CD000HUS"
    assert attrs["Make"] == "[PLACEHOLDER - not in Datto RMM export]", "the raw record of why stays"
