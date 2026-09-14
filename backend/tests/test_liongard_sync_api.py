"""Integration tests for D.2's Liongard sync endpoints in routers/scope.py:
environment mapping CRUD, the sync dry-run (pull + reconcile, no writes),
and apply (reusing the existing POST /imports/workbook/apply endpoint
unmodified -- see that router's module docstring for why).

connectors/liongard.py's list_environments/pull_device_profiles/
pull_identities are monkeypatched at the module level so these tests never
make a real network call -- the connector's own HTTP-parsing/pagination
logic has its own coverage in test_liongard_connector.py. The credential
itself is seeded through the real PUT /integrations/liongard/credential
endpoint (not hand-rolled encryption) so these tests exercise the genuine
decrypt path too.

Run in-container:
    docker compose exec backend pytest tests/test_liongard_sync_api.py -m integration -v
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.auth import get_current_user
from app.connectors import liongard as liongard_module
from app.db import get_session
from app.main import app
from app.models import AuditLog, Organization, OrgLiongardEnvironment, ScopeEntity
from tests.conftest import _app_session, _authed, _grant

pytestmark = pytest.mark.integration

_CRED_BODY = {
    "config": {"instance_url": "https://myinstance.app.liongard.com"},
    "credential": {"access_key_id": "AKIDEXAMPLE", "access_key_secret": "s3cr3t-value"},
}

_ENVIRONMENTS = [
    liongard_module.LiongardEnvironment(id=8815, name="Acme Corp"),
    liongard_module.LiongardEnvironment(id=42, name="Other Client"),
]


def _device_row(hostname: str = "SBX-Mini-01") -> dict:
    return {
        "ID": f"id-{hostname}",
        "EnvironmentID": 8815,
        "InventoryState": "Inventory",
        "Hostname": hostname,
        "SerialNumber": f"SN-{hostname}",
        "MACAddress": ["14:9d:99:8b:72:36"],
        "Manufacturer": "Apple",
        "Model": "Macmini8,1",
        "OperatingSystem": "macOS Ventura 13.6.5",
        "Type": "desktop",
        "AssetTagNumber": None,
    }


def _identity_row(email: str = "ahmed@coopsys.com") -> dict:
    return {
        "ID": f"id-{email}",
        "EnvironmentID": 8815,
        "InventoryState": "Inventory",
        "Email": email,
        "Username": email,
        "Type": "user",
        "Enabled": True,
        "DisplayName": "Ahmed Abdelrehim",
    }


@pytest.fixture
def client(db_session, fake_msp_admin):
    app.dependency_overrides[get_session] = _app_session(db_session)
    app.dependency_overrides[get_current_user] = _authed(db_session, fake_msp_admin)
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def _stub_liongard(monkeypatch):
    """Every test gets a working, controllable stub by default -- individual
    tests override state['devices']/['identities']/['environments'] or
    monkeypatch a raising fake for the error-path tests.

    state['devices_total_count']/['identities_total_count'] default to
    None, meaning "same as len(records)" (the ordinary case: everything
    Liongard returned already survived the Inventory-state filter). A test
    exercising the found-but-all-Discovery message sets one of these
    higher than len(records) to simulate rows the connector's own filter
    would have dropped -- state['devices']/['identities'] only ever holds
    post-filter records, matching what the real connector returns in
    InventoryPull.records.
    """
    state = {
        "environments": list(_ENVIRONMENTS),
        "devices": [_device_row()],
        "identities": [_identity_row()],
        "devices_total_count": None,
        "identities_total_count": None,
    }
    monkeypatch.setattr(
        liongard_module, "list_environments", lambda config, credential: state["environments"]
    )
    monkeypatch.setattr(
        liongard_module,
        "pull_device_profiles",
        lambda config, credential, environment_id: liongard_module.InventoryPull(
            records=state["devices"],
            total_count=(
                state["devices_total_count"]
                if state["devices_total_count"] is not None
                else len(state["devices"])
            ),
        ),
    )
    monkeypatch.setattr(
        liongard_module,
        "pull_identities",
        lambda config, credential, environment_id: liongard_module.InventoryPull(
            records=state["identities"],
            total_count=(
                state["identities_total_count"]
                if state["identities_total_count"] is not None
                else len(state["identities"])
            ),
        ),
    )
    return state


def _org(db_session, fake_msp_admin) -> Organization:
    org = Organization(name=f"LiongardTestOrg-{uuid.uuid4().hex[:8]}")
    db_session.add(org)
    db_session.flush()
    _grant(db_session, fake_msp_admin, org_id=org.id)
    return org


def _set_credential(client):
    r = client.put("/integrations/liongard/credential", json=_CRED_BODY)
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# Environment mapping
# ---------------------------------------------------------------------------


def test_get_mapping_when_unset_returns_null(client, db_session, fake_msp_admin):
    org = _org(db_session, fake_msp_admin)
    r = client.get(f"/orgs/{org.id}/integrations/liongard/environment")
    assert r.status_code == 200
    assert r.json() is None


def test_list_environments_returns_available_options(client, db_session, fake_msp_admin):
    org = _org(db_session, fake_msp_admin)
    _set_credential(client)
    r = client.get(f"/orgs/{org.id}/integrations/liongard/environments")
    assert r.status_code == 200
    assert r.json() == [{"id": 8815, "name": "Acme Corp"}, {"id": 42, "name": "Other Client"}]


def test_list_environments_without_credential_400s(client, db_session, fake_msp_admin):
    org = _org(db_session, fake_msp_admin)
    r = client.get(f"/orgs/{org.id}/integrations/liongard/environments")
    assert r.status_code == 400


def test_set_mapping_validates_against_live_environment_list(client, db_session, fake_msp_admin):
    org = _org(db_session, fake_msp_admin)
    _set_credential(client)
    r = client.put(
        f"/orgs/{org.id}/integrations/liongard/environment",
        json={"liongard_environment_id": 99999},
    )
    assert r.status_code == 400
    assert "99999" in r.text


def test_set_mapping_success_persists_and_is_returned_by_get(client, db_session, fake_msp_admin):
    org = _org(db_session, fake_msp_admin)
    _set_credential(client)
    r = client.put(
        f"/orgs/{org.id}/integrations/liongard/environment",
        json={"liongard_environment_id": 8815},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["liongard_environment_id"] == 8815
    assert body["liongard_environment_name"] == "Acme Corp"

    r2 = client.get(f"/orgs/{org.id}/integrations/liongard/environment")
    assert r2.json()["liongard_environment_id"] == 8815


def test_set_mapping_writes_audit_event(client, db_session, fake_msp_admin):
    org = _org(db_session, fake_msp_admin)
    _set_credential(client)
    client.put(
        f"/orgs/{org.id}/integrations/liongard/environment",
        json={"liongard_environment_id": 8815},
    )
    entry = db_session.scalars(
        select(AuditLog).where(AuditLog.action == "liongard_environment.set")
    ).first()
    assert entry is not None
    assert entry.after_value["liongard_environment_id"] == 8815


def test_get_mapping_includes_liongard_sourced_scope_count(client, db_session, fake_msp_admin):
    org = _org(db_session, fake_msp_admin)
    _set_credential(client)
    client.put(
        f"/orgs/{org.id}/integrations/liongard/environment",
        json={"liongard_environment_id": 8815},
    )
    r = client.get(f"/orgs/{org.id}/integrations/liongard/environment")
    assert r.json()["liongard_sourced_scope_count"] == 0

    db_session.add(
        ScopeEntity(
            org_id=org.id,
            entity_type="device",
            natural_key=f"SN-{uuid.uuid4().hex[:6]}",
            source="liongard",
            source_ref="liongard:environment=8815 (Acme Corp):pulled_at=now",
        )
    )
    db_session.commit()

    r2 = client.get(f"/orgs/{org.id}/integrations/liongard/environment")
    assert r2.json()["liongard_sourced_scope_count"] == 1


def test_set_mapping_to_an_environment_another_org_already_holds_409s(
    client, db_session, fake_msp_admin
):
    """One environment -> at most one org (2026-09-16 decision, migration
    0050): claiming an environment another org already holds must fail
    with a real error naming that org, not silently double-map.
    """
    org_a = _org(db_session, fake_msp_admin)
    org_b = _org(db_session, fake_msp_admin)
    _set_credential(client)
    r1 = client.put(
        f"/orgs/{org_a.id}/integrations/liongard/environment",
        json={"liongard_environment_id": 8815},
    )
    assert r1.status_code == 200

    r2 = client.put(
        f"/orgs/{org_b.id}/integrations/liongard/environment",
        json={"liongard_environment_id": 8815},
    )
    assert r2.status_code == 409
    assert org_a.name in r2.text

    # org_b must not have been mapped despite the conflict.
    rows = db_session.scalars(
        select(OrgLiongardEnvironment).where(OrgLiongardEnvironment.org_id == org_b.id)
    ).all()
    assert rows == []


def test_set_mapping_to_the_same_environment_it_already_holds_is_not_a_conflict(
    client, db_session, fake_msp_admin
):
    """Re-PUTting the same (org, environment) pair -- a no-op re-save --
    must not trip the "another org already holds it" check against itself.
    """
    org = _org(db_session, fake_msp_admin)
    _set_credential(client)
    client.put(
        f"/orgs/{org.id}/integrations/liongard/environment",
        json={"liongard_environment_id": 8815},
    )
    r = client.put(
        f"/orgs/{org.id}/integrations/liongard/environment",
        json={"liongard_environment_id": 8815},
    )
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# Environment unmap
# ---------------------------------------------------------------------------


def test_unmap_without_a_mapping_404s(client, db_session, fake_msp_admin):
    org = _org(db_session, fake_msp_admin)
    r = client.delete(f"/orgs/{org.id}/integrations/liongard/environment")
    assert r.status_code == 404


def test_unmap_removes_the_mapping_and_reports_zero_orphaned_rows(
    client, db_session, fake_msp_admin
):
    org = _org(db_session, fake_msp_admin)
    _set_credential(client)
    client.put(
        f"/orgs/{org.id}/integrations/liongard/environment",
        json={"liongard_environment_id": 8815},
    )
    r = client.delete(f"/orgs/{org.id}/integrations/liongard/environment")
    assert r.status_code == 200
    body = r.json()
    assert body["liongard_environment_id"] == 8815
    assert body["orphaned_scope_entity_count"] == 0

    r2 = client.get(f"/orgs/{org.id}/integrations/liongard/environment")
    assert r2.json() is None

    rows = db_session.scalars(
        select(OrgLiongardEnvironment).where(OrgLiongardEnvironment.org_id == org.id)
    ).all()
    assert rows == []


def test_unmap_reports_orphaned_scope_entities_without_deleting_them(
    client, db_session, fake_msp_admin
):
    """The §1b orphan question: unmapping must never delete Liongard-
    sourced scope_entity rows as a side effect -- they become
    manually-owned entities, the count is surfaced, not the data.
    """
    org = _org(db_session, fake_msp_admin)
    _set_credential(client)
    client.put(
        f"/orgs/{org.id}/integrations/liongard/environment",
        json={"liongard_environment_id": 8815},
    )
    entity = ScopeEntity(
        org_id=org.id,
        entity_type="device",
        natural_key="SN-orphan-test",
        source="liongard",
        source_ref="liongard:environment=8815 (Acme Corp):pulled_at=now",
    )
    db_session.add(entity)
    db_session.commit()

    r = client.delete(f"/orgs/{org.id}/integrations/liongard/environment")
    assert r.status_code == 200
    assert r.json()["orphaned_scope_entity_count"] == 1

    row = db_session.get(ScopeEntity, entity.id)
    assert row is not None
    assert row.source == "liongard"
    assert row.natural_key == "SN-orphan-test"


def test_unmap_writes_audit_event_with_orphan_count(client, db_session, fake_msp_admin):
    org = _org(db_session, fake_msp_admin)
    _set_credential(client)
    client.put(
        f"/orgs/{org.id}/integrations/liongard/environment",
        json={"liongard_environment_id": 8815},
    )
    client.delete(f"/orgs/{org.id}/integrations/liongard/environment")

    entry = db_session.scalars(
        select(AuditLog).where(AuditLog.action == "liongard_environment.unmap")
    ).first()
    assert entry is not None
    assert entry.before_value["liongard_environment_id"] == 8815
    assert entry.context["orphaned_scope_entity_count"] == 0


def test_unmap_then_remap_to_the_now_free_environment_succeeds(client, db_session, fake_msp_admin):
    """The exact scenario this whole slice exists for: one org unmaps an
    environment, then a different org can claim it.
    """
    org_a = _org(db_session, fake_msp_admin)
    org_b = _org(db_session, fake_msp_admin)
    _set_credential(client)
    client.put(
        f"/orgs/{org_a.id}/integrations/liongard/environment",
        json={"liongard_environment_id": 8815},
    )
    conflict = client.put(
        f"/orgs/{org_b.id}/integrations/liongard/environment",
        json={"liongard_environment_id": 8815},
    )
    assert conflict.status_code == 409

    unmap = client.delete(f"/orgs/{org_a.id}/integrations/liongard/environment")
    assert unmap.status_code == 200

    remap = client.put(
        f"/orgs/{org_b.id}/integrations/liongard/environment",
        json={"liongard_environment_id": 8815},
    )
    assert remap.status_code == 200
    assert remap.json()["liongard_environment_id"] == 8815


def test_remapping_updates_existing_row_not_duplicating(client, db_session, fake_msp_admin):
    org = _org(db_session, fake_msp_admin)
    _set_credential(client)
    client.put(
        f"/orgs/{org.id}/integrations/liongard/environment",
        json={"liongard_environment_id": 8815},
    )
    client.put(
        f"/orgs/{org.id}/integrations/liongard/environment",
        json={"liongard_environment_id": 42},
    )
    rows = db_session.scalars(
        select(OrgLiongardEnvironment).where(OrgLiongardEnvironment.org_id == org.id)
    ).all()
    assert len(rows) == 1
    assert rows[0].liongard_environment_id == 42


# ---------------------------------------------------------------------------
# Sync dry-run
# ---------------------------------------------------------------------------


def test_dry_run_without_mapping_400s(client, db_session, fake_msp_admin):
    org = _org(db_session, fake_msp_admin)
    _set_credential(client)
    r = client.post(f"/orgs/{org.id}/integrations/liongard/sync/dry-run")
    assert r.status_code == 400
    assert "mapped" in r.text


def test_dry_run_without_credential_400s(client, db_session, fake_msp_admin):
    org = _org(db_session, fake_msp_admin)
    # No credential set at all -- mapping can't be set without one either,
    # so this exercises the credential check being hit first.
    r = client.post(f"/orgs/{org.id}/integrations/liongard/sync/dry-run")
    assert r.status_code == 400


def test_dry_run_classifies_new_device_and_person(client, db_session, fake_msp_admin):
    org = _org(db_session, fake_msp_admin)
    _set_credential(client)
    client.put(
        f"/orgs/{org.id}/integrations/liongard/environment",
        json={"liongard_environment_id": 8815},
    )
    r = client.post(f"/orgs/{org.id}/integrations/liongard/sync/dry-run")
    assert r.status_code == 200
    body = r.json()
    assert body["summary"]["new"] == 2
    types = {c["entity_type"] for c in body["changes"]}
    assert types == {"device", "person"}
    for c in body["changes"]:
        assert c["change_type"] == "new"
        assert c["incoming"]["source"] == "liongard"
        assert "liongard:environment=8815" in c["incoming"]["source_ref"]


# ---------------------------------------------------------------------------
# Sync dry-run — Inventory-state pull visibility
# ---------------------------------------------------------------------------


def test_dry_run_pull_status_reports_new_or_changed_counts(client, db_session, fake_msp_admin):
    org = _org(db_session, fake_msp_admin)
    _set_credential(client)
    client.put(
        f"/orgs/{org.id}/integrations/liongard/environment",
        json={"liongard_environment_id": 8815},
    )
    body = client.post(f"/orgs/{org.id}/integrations/liongard/sync/dry-run").json()

    by_label = {p["entity_label"]: p for p in body["pull_status"]}
    assert by_label["devices"]["total_found"] == 1
    assert by_label["devices"]["inventory_count"] == 1
    assert "1 new or changed" in by_label["devices"]["message"]
    assert by_label["identities"]["total_found"] == 1
    assert by_label["identities"]["inventory_count"] == 1
    assert "1 new or changed" in by_label["identities"]["message"]


def test_dry_run_pull_status_distinguishes_no_changes_from_nothing_found(
    client, db_session, fake_msp_admin
):
    """A genuinely-nothing-to-do sync (Inventory rows exist, already match
    scope) must read differently from an empty-because-filtered pull --
    the whole point of this feature.
    """
    org = _org(db_session, fake_msp_admin)
    _set_credential(client)
    client.put(
        f"/orgs/{org.id}/integrations/liongard/environment",
        json={"liongard_environment_id": 8815},
    )
    first = client.post(f"/orgs/{org.id}/integrations/liongard/sync/dry-run").json()
    client.post(f"/orgs/{org.id}/imports/workbook/apply", json={"changes": first["changes"]})

    body = client.post(f"/orgs/{org.id}/integrations/liongard/sync/dry-run").json()
    by_label = {p["entity_label"]: p for p in body["pull_status"]}
    assert by_label["devices"]["inventory_count"] == 1
    assert "compared against scope -- no changes" in by_label["devices"]["message"]
    assert "compared against scope -- no changes" in by_label["identities"]["message"]


def test_dry_run_pull_status_names_the_remedy_when_everything_is_still_discovery(
    client, db_session, fake_msp_admin, _stub_liongard
):
    """The exact situation found live on Jarrod's real tenant: records
    exist in Liongard but none have been promoted to Inventory state yet.
    Must name the remedy, not just report a zero.
    """
    org = _org(db_session, fake_msp_admin)
    _set_credential(client)
    client.put(
        f"/orgs/{org.id}/integrations/liongard/environment",
        json={"liongard_environment_id": 8815},
    )
    _stub_liongard["devices"] = []
    _stub_liongard["devices_total_count"] = 197
    _stub_liongard["identities"] = []
    _stub_liongard["identities_total_count"] = 40

    body = client.post(f"/orgs/{org.id}/integrations/liongard/sync/dry-run").json()
    by_label = {p["entity_label"]: p for p in body["pull_status"]}
    assert by_label["devices"]["total_found"] == 197
    assert by_label["devices"]["inventory_count"] == 0
    assert "197 devices" in by_label["devices"]["message"]
    assert "promote them from Discovery to Inventory" in by_label["devices"]["message"]
    assert "40 identities" in by_label["identities"]["message"]
    assert "promote them from Discovery to Inventory" in by_label["identities"]["message"]
    assert body["changes"] == []


def test_dry_run_pull_status_reports_liongard_returned_nothing_at_all(
    client, db_session, fake_msp_admin, _stub_liongard
):
    org = _org(db_session, fake_msp_admin)
    _set_credential(client)
    client.put(
        f"/orgs/{org.id}/integrations/liongard/environment",
        json={"liongard_environment_id": 8815},
    )
    _stub_liongard["devices"] = []
    _stub_liongard["identities"] = []

    body = client.post(f"/orgs/{org.id}/integrations/liongard/sync/dry-run").json()
    by_label = {p["entity_label"]: p for p in body["pull_status"]}
    assert by_label["devices"]["total_found"] == 0
    assert by_label["devices"]["message"] == "Liongard returned no devices for this Environment."
    assert (
        by_label["identities"]["message"]
        == "Liongard returned no identities for this Environment."
    )


def test_dry_run_performs_no_writes(client, db_session, fake_msp_admin):
    org = _org(db_session, fake_msp_admin)
    _set_credential(client)
    client.put(
        f"/orgs/{org.id}/integrations/liongard/environment",
        json={"liongard_environment_id": 8815},
    )
    client.post(f"/orgs/{org.id}/integrations/liongard/sync/dry-run")
    rows = db_session.scalars(select(ScopeEntity).where(ScopeEntity.org_id == org.id)).all()
    assert rows == []


def test_dry_run_surfaces_skipped_record_as_pull_level_warning(
    client, db_session, fake_msp_admin, _stub_liongard
):
    org = _org(db_session, fake_msp_admin)
    _set_credential(client)
    client.put(
        f"/orgs/{org.id}/integrations/liongard/environment",
        json={"liongard_environment_id": 8815},
    )
    unkeyable = {**_device_row(), "SerialNumber": None, "Hostname": None}
    _stub_liongard["devices"] = [unkeyable]
    r = client.post(f"/orgs/{org.id}/integrations/liongard/sync/dry-run")
    assert r.status_code == 200
    body = r.json()
    assert len(body["warnings"]) == 1
    assert "neither SerialNumber nor Hostname" in body["warnings"][0]


def test_dry_run_surfaces_liongard_api_error_as_502(
    client, db_session, fake_msp_admin, monkeypatch
):
    org = _org(db_session, fake_msp_admin)
    _set_credential(client)
    client.put(
        f"/orgs/{org.id}/integrations/liongard/environment",
        json={"liongard_environment_id": 8815},
    )

    def _boom(config, credential, environment_id):
        raise liongard_module.LiongardAPIError(
            "Liongard rejected the key (HTTP 401) — check that it's active."
        )

    monkeypatch.setattr(liongard_module, "pull_device_profiles", _boom)
    r = client.post(f"/orgs/{org.id}/integrations/liongard/sync/dry-run")
    assert r.status_code == 502
    assert "401" in r.text


# ---------------------------------------------------------------------------
# Apply (reuses POST /imports/workbook/apply unmodified)
# ---------------------------------------------------------------------------


def test_apply_writes_scope_entities_with_liongard_provenance(client, db_session, fake_msp_admin):
    org = _org(db_session, fake_msp_admin)
    _set_credential(client)
    client.put(
        f"/orgs/{org.id}/integrations/liongard/environment",
        json={"liongard_environment_id": 8815},
    )
    dry_run = client.post(f"/orgs/{org.id}/integrations/liongard/sync/dry-run").json()

    r = client.post(f"/orgs/{org.id}/imports/workbook/apply", json={"changes": dry_run["changes"]})
    assert r.status_code == 200
    assert r.json()["applied"] == 2

    rows = db_session.scalars(select(ScopeEntity).where(ScopeEntity.org_id == org.id)).all()
    assert len(rows) == 2
    for row in rows:
        assert row.source == "liongard"
        assert row.source_ref is not None
        assert "liongard:environment=8815" in row.source_ref

    audit_entries = db_session.scalars(
        select(AuditLog).where(AuditLog.action == "scope_entity.import_apply")
    ).all()
    assert len(audit_entries) == 2
    assert all(e.context["source"] == "liongard" for e in audit_entries)


def test_second_dry_run_after_apply_reports_no_new_or_changed(
    client, db_session, fake_msp_admin
):
    """The re-run-after-apply check the task's own Verify section calls
    for: applying a pull, then re-running the identical pull, must not
    report spurious NEW/CHANGED rows -- that would mean either natural-key
    matching or the mac_addresses order-insensitivity in reconcile.py
    isn't actually wired through this path.
    """
    org = _org(db_session, fake_msp_admin)
    _set_credential(client)
    client.put(
        f"/orgs/{org.id}/integrations/liongard/environment",
        json={"liongard_environment_id": 8815},
    )
    first = client.post(f"/orgs/{org.id}/integrations/liongard/sync/dry-run").json()
    client.post(f"/orgs/{org.id}/imports/workbook/apply", json={"changes": first["changes"]})

    second = client.post(f"/orgs/{org.id}/integrations/liongard/sync/dry-run").json()
    assert second["summary"]["new"] == 0
    assert second["summary"]["changed"] == 0
    assert second["changes"] == []
