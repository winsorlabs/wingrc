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
    """
    state = {
        "environments": list(_ENVIRONMENTS),
        "devices": [_device_row()],
        "identities": [_identity_row()],
    }
    monkeypatch.setattr(
        liongard_module, "list_environments", lambda config, credential: state["environments"]
    )
    monkeypatch.setattr(
        liongard_module,
        "pull_device_profiles",
        lambda config, credential, environment_id: state["devices"],
    )
    monkeypatch.setattr(
        liongard_module,
        "pull_identities",
        lambda config, credential, environment_id: state["identities"],
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
