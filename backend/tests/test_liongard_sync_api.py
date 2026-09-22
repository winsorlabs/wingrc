"""Integration tests for D.2's Liongard sync endpoints in routers/scope.py:
environment mapping CRUD, and the sync dry-run (pull + reconcile; never
writes scope_entity).

Apply (POST /imports/workbook/apply) is still reused unmodified for
CHANGED/MISSING rows here -- see that router's module docstring. NEW rows
are different since the 2026-09-22 fix: applying one directly now 409s
(repo.PendingApprovalWriteError), and the dry-run call itself persists any
NEW row into the same asset-approval queue /liongard-sync-results/sync-now
uses (liongard_sync_dry_run's own docstring has the full incident writeup).
Tests that need a NEW liongard entity to actually land in scope_entity go
through that queue's approve endpoint, exactly like
test_liongard_sync_approval_api.py's own tests do, not through apply.

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


def _approve_all_new(client, org_id, sync_result_id) -> int:
    """Approve every still-pending NEW change on a persisted sync result --
    the only way a NEW liongard entity reaches scope_entity post-fix.
    Returns how many were approved.
    """
    detail = client.get(f"/orgs/{org_id}/liongard-sync-results/{sync_result_id}").json()
    approved = 0
    for c in detail["changes"]:
        if c["change_type"] != "new" or c["resolution"] != "pending":
            continue
        r = client.post(
            f"/orgs/{org_id}/liongard-sync-results/{sync_result_id}/changes/{c['id']}/approve",
            json={"checklist_confirmations": {}},
        )
        assert r.status_code == 200, r.text
        approved += 1
    return approved


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


def test_dry_run_with_new_entities_queues_them_for_approval(client, db_session, fake_msp_admin):
    """The 2026-09-22 fix's core behavior: a NEW entity found by this
    entry point lands in the exact same asset-approval queue
    /liongard-sync-results/sync-now produces, not just in the dry-run
    response -- see liongard_sync_dry_run's own docstring for the incident
    this closes (a stranded pending_approval scope_entity row with nothing
    to resolve it).
    """
    org = _org(db_session, fake_msp_admin)
    _set_credential(client)
    client.put(
        f"/orgs/{org.id}/integrations/liongard/environment",
        json={"liongard_environment_id": 8815},
    )
    body = client.post(f"/orgs/{org.id}/integrations/liongard/sync/dry-run").json()
    assert body["sync_result_id"] is not None

    detail = client.get(f"/orgs/{org.id}/liongard-sync-results/{body['sync_result_id']}").json()
    assert detail["status"] == "pending_review"
    assert {c["entity_type"] for c in detail["changes"]} == {"device", "person"}
    for c in detail["changes"]:
        assert c["change_type"] == "new"
        assert c["resolution"] == "pending"

    # And -- the actual bug -- no scope_entity row exists yet at all.
    assert db_session.scalars(select(ScopeEntity).where(ScopeEntity.org_id == org.id)).all() == []


def test_dry_run_without_new_entities_does_not_persist_a_sync_result(
    client, db_session, fake_msp_admin
):
    """No NEW rows -> nothing to queue -> no LiongardSyncResult at all, so
    a routine no-op preview doesn't spam Asset Approvals' history. Uses
    the CHANGED-only scenario test_first_sync_after_upgrade_... also uses:
    a pre-existing device plus no identities.
    """
    org = _org(db_session, fake_msp_admin)
    _set_credential(client)
    client.put(
        f"/orgs/{org.id}/integrations/liongard/environment",
        json={"liongard_environment_id": 8815},
    )
    db_session.add(
        ScopeEntity(
            org_id=org.id, entity_type="device", natural_key="SN-SBX-Mini-01", source="liongard",
        )
    )
    db_session.add(
        ScopeEntity(
            org_id=org.id, entity_type="person", natural_key="ahmed@coopsys.com", source="liongard",
        )
    )
    db_session.commit()

    body = client.post(f"/orgs/{org.id}/integrations/liongard/sync/dry-run").json()
    assert body["sync_result_id"] is None
    assert client.get(f"/orgs/{org.id}/liongard-sync-results").json() == []


def test_apply_of_a_new_liongard_entity_is_refused(client, db_session, fake_msp_admin):
    """The reported bug, reproduced directly: a NEW liongard-sourced
    change can no longer be written to scope_entity via
    /imports/workbook/apply -- it must go through approval instead. Before
    the fix this call succeeded and left an unrecoverable pending_approval
    row (see liongard_sync_dry_run's docstring).
    """
    org = _org(db_session, fake_msp_admin)
    _set_credential(client)
    client.put(
        f"/orgs/{org.id}/integrations/liongard/environment",
        json={"liongard_environment_id": 8815},
    )
    dry_run = client.post(f"/orgs/{org.id}/integrations/liongard/sync/dry-run").json()
    assert all(c["change_type"] == "new" for c in dry_run["changes"])

    r = client.post(f"/orgs/{org.id}/imports/workbook/apply", json={"changes": dry_run["changes"]})
    assert r.status_code == 409
    assert "pending" in r.text.lower()

    assert db_session.scalars(select(ScopeEntity).where(ScopeEntity.org_id == org.id)).all() == []
    # And it's still there, resolvable, in the queue this same dry-run created.
    assert _approve_all_new(client, org.id, dry_run["sync_result_id"]) == 2


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
    # Both rows are NEW -- must be approved to reach scope_entity, apply
    # would 409 (see test_apply_of_a_new_liongard_entity_is_refused).
    assert _approve_all_new(client, org.id, first["sync_result_id"]) == 2

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


def test_dry_run_never_writes_scope_entity(client, db_session, fake_msp_admin):
    """Still true after the 2026-09-22 fix even though this call now
    persists a LiongardSyncResult for the NEW rows in the default fixture
    (test_dry_run_with_new_entities_queues_them_for_approval covers that
    write) -- scope_entity itself is untouched either way. That's the
    actual invariant this endpoint's docstring calls "never writes
    scope_entity", not "no writes at all".
    """
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
# NEW entities: refused by apply, written only via approval (2026-09-22)
# ---------------------------------------------------------------------------


def test_new_entities_reach_scope_entity_only_via_approval_with_liongard_provenance(
    client, db_session, fake_msp_admin
):
    """Renamed from test_apply_writes_scope_entities_with_liongard_provenance:
    that test's premise (apply writes a brand-new liongard entity) is
    exactly the bug this fix closes -- see liongard_sync_dry_run's
    docstring. Same coverage, correct mechanism: apply is refused, approve
    is what actually writes scope_entity, and it must still carry the
    right provenance.
    """
    org = _org(db_session, fake_msp_admin)
    _set_credential(client)
    client.put(
        f"/orgs/{org.id}/integrations/liongard/environment",
        json={"liongard_environment_id": 8815},
    )
    dry_run = client.post(f"/orgs/{org.id}/integrations/liongard/sync/dry-run").json()

    blocked = client.post(
        f"/orgs/{org.id}/imports/workbook/apply", json={"changes": dry_run["changes"]}
    )
    assert blocked.status_code == 409

    assert _approve_all_new(client, org.id, dry_run["sync_result_id"]) == 2

    rows = db_session.scalars(select(ScopeEntity).where(ScopeEntity.org_id == org.id)).all()
    assert len(rows) == 2
    for row in rows:
        assert row.status == "active"
        assert row.source == "liongard"
        assert row.source_ref is not None
        assert "liongard:environment=8815" in row.source_ref

    audit_entries = db_session.scalars(
        select(AuditLog).where(AuditLog.action == "asset_approval.approve")
    ).all()
    assert len(audit_entries) == 2


def test_approved_device_shows_display_name_and_last_login_user(
    client, db_session, fake_msp_admin, _stub_liongard
):
    """Renamed from test_applied_device_shows_display_name_and_last_login_user
    for the same reason as the test above. WL-LT26 is the exact device
    from the live bug report (dev.wingrc.us, 2026-09-22) -- display_name
    (Alias -> Hostname -> natural_key) and last_login_user must survive
    the real dry-run -> approve -> DB round trip through the deployed HTTP
    layer, not just the importer's own unit tests, for the device that
    actually got stranded.
    """
    org = _org(db_session, fake_msp_admin)
    _set_credential(client)
    client.put(
        f"/orgs/{org.id}/integrations/liongard/environment",
        json={"liongard_environment_id": 8815},
    )
    _stub_liongard["devices"] = [
        {**_device_row("WL-DT26"), "Alias": "Jarrods Desktop", "LastLoginUser": "jarrod"},
        {**_device_row("WL-LT26"), "Alias": None, "LastLoginUser": "WINSORLABS\\jarrod.winsor"},
    ]
    # Isolate to devices -- the default identity fixture would otherwise
    # also show as NEW and contaminate the approved-count assertion below.
    _stub_liongard["identities"] = []
    dry_run = client.post(f"/orgs/{org.id}/integrations/liongard/sync/dry-run").json()
    assert _approve_all_new(client, org.id, dry_run["sync_result_id"]) == 2

    devices = {
        row.natural_key: row
        for row in db_session.scalars(
            select(ScopeEntity).where(
                ScopeEntity.org_id == org.id, ScopeEntity.entity_type == "device"
            )
        ).all()
    }
    aliased = devices["SN-WL-DT26"]
    assert aliased.attributes["display_name"] == "Jarrods Desktop"
    assert aliased.attributes["last_login_user"] == "jarrod"
    assert "responsible_contact_id" not in aliased.attributes

    unaliased = devices["SN-WL-LT26"]
    assert unaliased.attributes["display_name"] == "WL-LT26"  # falls back to Hostname
    assert unaliased.attributes["last_login_user"] == "WINSORLABS\\jarrod.winsor"
    assert "responsible_contact_id" not in unaliased.attributes
    assert unaliased.status == "active"


def test_first_sync_after_upgrade_shows_changed_once_then_second_sync_is_clean(
    client, db_session, fake_msp_admin, _stub_liongard
):
    """§4's exact scenario: a device applied *before* display_name existed
    (no display_name/last_login_user in its stored attributes -- the real
    shape of WinsorLabs' own already-applied rows) meets the new code for
    the first time. It must show CHANGED once, for the real reason
    (display_name newly populated) -- then a second sync, differing only
    in telemetry, must be clean. The first run alone proves nothing (it's
    supposed to show a real diff); the second run is the actual proof this
    slice's fix works.
    """
    org = _org(db_session, fake_msp_admin)
    _set_credential(client)
    client.put(
        f"/orgs/{org.id}/integrations/liongard/environment",
        json={"liongard_environment_id": 8815},
    )

    # Pre-existing row shaped exactly like a pre-this-slice apply: the
    # canonical fields the *original* D.2 device import already wrote
    # (make_oem/model/version/device_subtype/mac_addresses) plus raw
    # telemetry (attributes = dict(record) always stored it), but no
    # display_name/last_login_user keys at all -- both new this session.
    db_session.add(
        ScopeEntity(
            org_id=org.id,
            entity_type="device",
            natural_key="SN-WL-DT26",
            source="liongard",
            source_ref="liongard:environment=8815 (Acme Corp):pulled_at=2026-09-14",
            attributes={
                "Hostname": "WL-DT26",
                "Manufacturer": "Apple",
                "Model": "Macmini8,1",
                "OperatingSystem": "macOS Ventura 13.6.5",
                "Type": "desktop",
                "MACAddress": ["14:9d:99:8b:72:36"],
                "make_oem": "Apple",
                "model": "Macmini8,1",
                "version": "macOS Ventura 13.6.5",
                "device_subtype": "desktop",
                "mac_addresses": ["14:9d:99:8b:72:36"],
                "LastSeenTimelineID": 182036271,
                "LastSeen": "2026-09-14T19:11:39.520Z",
                "UpdatedOn": "2026-09-14T19:11:40.562Z",
                "AvailableStorage": 1663,
            },
        )
    )
    db_session.commit()

    _stub_liongard["devices"] = [
        {
            **_device_row("WL-DT26"),
            "Alias": "Jarrods Desktop",
            "LastLoginUser": "jarrod",
            "LastSeenTimelineID": 182036271,
            "LastSeen": "2026-09-14T19:11:39.520Z",
            "UpdatedOn": "2026-09-14T19:11:40.562Z",
            "AvailableStorage": 1663,
        }
    ]
    # Isolate this test to the device -- the default identities fixture
    # would otherwise also show as "new" every dry-run and contaminate
    # the summary assertions below, which are about the device only.
    _stub_liongard["identities"] = []

    first = client.post(f"/orgs/{org.id}/integrations/liongard/sync/dry-run").json()
    assert first["summary"]["changed"] == 1
    change = first["changes"][0]
    assert change["change_type"] == "changed"
    assert set(change["field_diffs"]) == {"display_name"}
    apply_result = client.post(
        f"/orgs/{org.id}/imports/workbook/apply", json={"changes": first["changes"]}
    )
    assert apply_result.status_code == 200

    # Second pull: telemetry has moved on (a real, live sync would never
    # return byte-identical telemetry), but nothing meaningful changed.
    _stub_liongard["devices"] = [
        {
            **_device_row("WL-DT26"),
            "Alias": "Jarrods Desktop",
            "LastLoginUser": "someone.else",  # even a login turnover...
            "LastSeenTimelineID": 182099999,
            "LastSeen": "2026-09-15T09:00:00.000Z",
            "UpdatedOn": "2026-09-15T09:00:01.000Z",
            "AvailableStorage": 1660,
        }
    ]
    second = client.post(f"/orgs/{org.id}/integrations/liongard/sync/dry-run").json()
    assert second["summary"] == {"new": 0, "changed": 0, "missing": 0, "unchanged": 1}
    assert second["changes"] == []


def test_second_dry_run_after_approval_reports_no_new_or_changed(
    client, db_session, fake_msp_admin
):
    """The re-run-after-accept check the task's own Verify section calls
    for: accepting a pull's entities, then re-running the identical pull,
    must not report spurious NEW/CHANGED rows -- that would mean either
    natural-key matching or the mac_addresses order-insensitivity in
    reconcile.py isn't actually wired through this path. "Accepting" is
    approval now, not apply -- both rows here are NEW.
    """
    org = _org(db_session, fake_msp_admin)
    _set_credential(client)
    client.put(
        f"/orgs/{org.id}/integrations/liongard/environment",
        json={"liongard_environment_id": 8815},
    )
    first = client.post(f"/orgs/{org.id}/integrations/liongard/sync/dry-run").json()
    assert _approve_all_new(client, org.id, first["sync_result_id"]) == 2

    second = client.post(f"/orgs/{org.id}/integrations/liongard/sync/dry-run").json()
    assert second["summary"]["new"] == 0
    assert second["summary"]["changed"] == 0
    assert second["changes"] == []
