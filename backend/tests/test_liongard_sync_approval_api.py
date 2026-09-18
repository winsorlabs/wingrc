"""Integration tests for D.3's second half: the persisted Liongard sync
result + asset/user onboarding approval API (routers/liongard_sync.py,
liongard_sync.py).

Reuses test_liongard_sync_api.py's exact monkeypatch/fixture shape for
connectors/liongard.py (list_environments/pull_device_profiles/
pull_identities never make a real network call) -- see that file's own
module docstring for why.

Run in-container:
    docker compose exec backend pytest tests/test_liongard_sync_approval_api.py -m integration -v
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
from app.models import (
    AssetApproval,
    AssetApprovalChecklistItem,
    AuditLog,
    Contact,
    ContactDocumentationRole,
    Framework,
    LiongardSyncResult,
    LiongardSyncResultChange,
    Organization,
    OrgProduct,
    Product,
    ScopeEntity,
)
from tests.conftest import _app_session, _authed, _grant, _make_fake_user

pytestmark = pytest.mark.integration

_CRED_BODY = {
    "config": {"instance_url": "https://myinstance.app.liongard.com"},
    "credential": {"access_key_id": "AKIDEXAMPLE", "access_key_secret": "s3cr3t-value"},
}

_ENVIRONMENTS = [liongard_module.LiongardEnvironment(id=8815, name="Acme Corp")]


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
            total_count=state["devices_total_count"] or len(state["devices"]),
        ),
    )
    monkeypatch.setattr(
        liongard_module,
        "pull_identities",
        lambda config, credential, environment_id: liongard_module.InventoryPull(
            records=state["identities"],
            total_count=state["identities_total_count"] or len(state["identities"]),
        ),
    )
    return state


def _org(db_session, fake_msp_admin) -> Organization:
    org = Organization(name=f"LiongardApprovalOrg-{uuid.uuid4().hex[:8]}")
    db_session.add(org)
    db_session.flush()
    _grant(db_session, fake_msp_admin, org_id=org.id)
    return org


def _set_credential(client):
    r = client.put("/integrations/liongard/credential", json=_CRED_BODY)
    assert r.status_code == 200


def _mapped_org(client, db_session, fake_msp_admin) -> Organization:
    org = _org(db_session, fake_msp_admin)
    _set_credential(client)
    client.put(
        f"/orgs/{org.id}/integrations/liongard/environment",
        json={"liongard_environment_id": 8815},
    )
    return org


def _sync_now(client, org) -> dict:
    r = client.post(f"/orgs/{org.id}/liongard-sync-results/sync-now")
    assert r.status_code == 201, r.text
    return r.json()


def _new_change(client, org, sync_result_id: str, entity_type: str) -> dict:
    detail = client.get(f"/orgs/{org.id}/liongard-sync-results/{sync_result_id}").json()
    return next(c for c in detail["changes"] if c["entity_type"] == entity_type)


# ---------------------------------------------------------------------------
# sync-now / persistence / supersede
# ---------------------------------------------------------------------------


def test_sync_now_persists_pending_review_result(client, db_session, fake_msp_admin):
    org = _mapped_org(client, db_session, fake_msp_admin)
    body = _sync_now(client, org)
    assert body["status"] == "pending_review"
    assert body["summary"]["new"] == 2

    rows = db_session.scalars(
        select(LiongardSyncResult).where(LiongardSyncResult.org_id == org.id)
    ).all()
    assert len(rows) == 1
    changes = db_session.scalars(
        select(LiongardSyncResultChange).where(
            LiongardSyncResultChange.sync_result_id == rows[0].id
        )
    ).all()
    assert len(changes) == 2
    assert all(c.resolution == "pending" for c in changes)


def test_new_entities_come_back_pending_approval(client, db_session, fake_msp_admin):
    org = _mapped_org(client, db_session, fake_msp_admin)
    body = _sync_now(client, org)
    detail = client.get(f"/orgs/{org.id}/liongard-sync-results/{body['id']}").json()
    for c in detail["changes"]:
        assert c["incoming"]["status"] == "pending_approval"


def test_sync_now_never_writes_scope_entity(client, db_session, fake_msp_admin):
    org = _mapped_org(client, db_session, fake_msp_admin)
    _sync_now(client, org)
    rows = db_session.scalars(select(ScopeEntity).where(ScopeEntity.org_id == org.id)).all()
    assert rows == []


def test_no_actionable_changes_marks_no_changes_status(
    client, db_session, fake_msp_admin, _stub_liongard
):
    org = _mapped_org(client, db_session, fake_msp_admin)
    _stub_liongard["devices"] = []
    _stub_liongard["identities"] = []
    body = _sync_now(client, org)
    assert body["status"] == "no_changes"


def test_second_sync_before_review_supersedes_the_first(client, db_session, fake_msp_admin):
    org = _mapped_org(client, db_session, fake_msp_admin)
    first = _sync_now(client, org)
    second = _sync_now(client, org)
    assert first["id"] != second["id"]

    db_session.expire_all()
    first_row = db_session.get(LiongardSyncResult, uuid.UUID(first["id"]))
    second_row = db_session.get(LiongardSyncResult, uuid.UUID(second["id"]))
    assert first_row.status == "superseded"
    assert second_row.status == "pending_review"

    listing = client.get(f"/orgs/{org.id}/liongard-sync-results").json()
    ids_and_status = {r["id"]: r["status"] for r in listing}
    assert ids_and_status[first["id"]] == "superseded"
    assert ids_and_status[second["id"]] == "pending_review"


def test_stale_result_never_repulls_live(client, db_session, fake_msp_admin, _stub_liongard):
    """Open a result, then change what the connector would return, then
    reopen it -- the persisted result must show what was observed at pull
    time, not the new live data."""
    org = _mapped_org(client, db_session, fake_msp_admin)
    body = _sync_now(client, org)
    first_view = client.get(f"/orgs/{org.id}/liongard-sync-results/{body['id']}").json()

    _stub_liongard["devices"] = [_device_row("A-DIFFERENT-HOST")]
    _stub_liongard["identities"] = []

    second_view = client.get(f"/orgs/{org.id}/liongard-sync-results/{body['id']}").json()
    assert second_view["changes"] == first_view["changes"]


# ---------------------------------------------------------------------------
# Approve / reject
# ---------------------------------------------------------------------------


def test_approve_writes_active_scope_entity_and_asset_approval(client, db_session, fake_msp_admin):
    org = _mapped_org(client, db_session, fake_msp_admin)
    body = _sync_now(client, org)
    change = _new_change(client, org, body["id"], "device")

    r = client.post(
        f"/orgs/{org.id}/liongard-sync-results/{body['id']}/changes/{change['id']}/approve",
        json={"checklist_confirmations": {}},
    )
    assert r.status_code == 200, r.text
    approval_out = r.json()
    assert approval_out["decision"] == "approved"

    entity = db_session.get(ScopeEntity, uuid.UUID(approval_out["scope_entity_id"]))
    assert entity.status == "active"
    assert entity.in_boundary is True

    approval = db_session.scalars(
        select(AssetApproval).where(AssetApproval.scope_entity_id == entity.id)
    ).one()
    assert approval.decision == "approved"
    assert approval.decided_by_name == fake_msp_admin.display_name

    change_row = db_session.get(LiongardSyncResultChange, uuid.UUID(change["id"]))
    assert change_row.resolution == "approved"

    audit = db_session.scalars(
        select(AuditLog).where(AuditLog.action == "asset_approval.approve")
    ).all()
    assert len(audit) == 1
    assert audit[0].actor == str(fake_msp_admin.id)


def test_reject_writes_active_out_of_boundary_scope_entity(client, db_session, fake_msp_admin):
    org = _mapped_org(client, db_session, fake_msp_admin)
    body = _sync_now(client, org)
    change = _new_change(client, org, body["id"], "device")

    r = client.post(
        f"/orgs/{org.id}/liongard-sync-results/{body['id']}/changes/{change['id']}/reject",
        json={"reason": "Personal device, not authorized for CUI."},
    )
    assert r.status_code == 200, r.text
    approval_out = r.json()
    assert approval_out["decision"] == "rejected"
    assert approval_out["rejection_reason"] == "Personal device, not authorized for CUI."

    entity = db_session.get(ScopeEntity, uuid.UUID(approval_out["scope_entity_id"]))
    assert entity.status == "active"
    assert entity.in_boundary is False


def test_reject_without_reason_422s(client, db_session, fake_msp_admin):
    org = _mapped_org(client, db_session, fake_msp_admin)
    body = _sync_now(client, org)
    change = _new_change(client, org, body["id"], "device")
    r = client.post(
        f"/orgs/{org.id}/liongard-sync-results/{body['id']}/changes/{change['id']}/reject",
        json={"reason": "   "},
    )
    assert r.status_code == 422


def test_approving_twice_422s(client, db_session, fake_msp_admin):
    org = _mapped_org(client, db_session, fake_msp_admin)
    body = _sync_now(client, org)
    change = _new_change(client, org, body["id"], "device")
    url = f"/orgs/{org.id}/liongard-sync-results/{body['id']}/changes/{change['id']}/approve"
    assert client.post(url, json={}).status_code == 200
    assert client.post(url, json={}).status_code == 422


def test_resolving_the_last_pending_change_marks_result_reviewed(
    client, db_session, fake_msp_admin
):
    org = _mapped_org(client, db_session, fake_msp_admin)
    body = _sync_now(client, org)
    device_change = _new_change(client, org, body["id"], "device")
    person_change = _new_change(client, org, body["id"], "person")

    client.post(
        f"/orgs/{org.id}/liongard-sync-results/{body['id']}/changes/{device_change['id']}/approve",
        json={},
    )
    db_session.expire_all()
    assert db_session.get(LiongardSyncResult, uuid.UUID(body["id"])).status == "pending_review"

    client.post(
        f"/orgs/{org.id}/liongard-sync-results/{body['id']}/changes/{person_change['id']}/reject",
        json={"reason": "Contractor left the client, never actually onboarded."},
    )
    db_session.expire_all()
    assert db_session.get(LiongardSyncResult, uuid.UUID(body["id"])).status == "reviewed"


def test_re_sync_after_rejection_does_not_re_flag_as_new(
    client, db_session, fake_msp_admin, _stub_liongard
):
    """Rejection is sticky -- the device exists in scope now (in_boundary=
    False), so the next sync reconciles it as unchanged/changed, never
    'new' again."""
    org = _mapped_org(client, db_session, fake_msp_admin)
    _stub_liongard["identities"] = []
    body = _sync_now(client, org)
    change = _new_change(client, org, body["id"], "device")
    client.post(
        f"/orgs/{org.id}/liongard-sync-results/{body['id']}/changes/{change['id']}/reject",
        json={"reason": "Not authorized."},
    )

    second = _sync_now(client, org)
    assert second["status"] == "no_changes"
    assert second["summary"]["new"] == 0


def test_checklist_confirmations_snapshot_activated_products(client, db_session, fake_msp_admin):
    org = _mapped_org(client, db_session, fake_msp_admin)
    fw = Framework(key=f"fw-{uuid.uuid4().hex[:6]}", name="Test FW", version="r2")
    db_session.add(fw)
    db_session.flush()
    product = Product(
        key=f"tool-{uuid.uuid4().hex[:6]}", name="FenixPyre", provider="FenixPyre Inc",
        category="EDR", asset_type="SPA", role="test", is_published=True,
        framework_id=fw.id,
    )
    db_session.add(product)
    db_session.flush()
    db_session.add(OrgProduct(org_id=org.id, product_id=product.id, status="active"))
    db_session.flush()

    body = _sync_now(client, org)
    detail = client.get(f"/orgs/{org.id}/liongard-sync-results/{body['id']}").json()
    assert {p["product_key"] for p in detail["checklist_products"]} == {product.key}

    change = _new_change(client, org, body["id"], "device")
    r = client.post(
        f"/orgs/{org.id}/liongard-sync-results/{body['id']}/changes/{change['id']}/approve",
        json={"checklist_confirmations": {product.key: True}},
    )
    approval_id = uuid.UUID(r.json()["id"])
    items = db_session.scalars(
        select(AssetApprovalChecklistItem).where(
            AssetApprovalChecklistItem.approval_id == approval_id
        )
    ).all()
    assert len(items) == 1
    assert items[0].product_key == product.key
    assert items[0].confirmed is True


def test_c3pao_assessor_cannot_approve(db_session, fake_msp_admin):
    org = Organization(name=f"AssessorOrg-{uuid.uuid4().hex[:8]}")
    db_session.add(org)
    db_session.flush()
    assessor = _make_fake_user(role="c3pao_assessor")
    _grant(db_session, assessor, org_id=org.id)

    app.dependency_overrides[get_session] = _app_session(db_session)
    app.dependency_overrides[get_current_user] = _authed(db_session, assessor)
    try:
        c = TestClient(app)
        r = c.post(f"/orgs/{org.id}/liongard-sync-results/sync-now")
        assert r.status_code == 403
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Notification routing (no-contact case)
# ---------------------------------------------------------------------------


def test_notify_candidates_includes_security_officer_and_it_admin(
    client, db_session, fake_msp_admin
):
    org = _mapped_org(client, db_session, fake_msp_admin)
    so = Contact(org_id=org.id, name="Sam SO", email="sam@example.com", affiliation="customer")
    it = Contact(org_id=org.id, name="Ivy IT", email="ivy@example.com", affiliation="msp")
    other = Contact(
        org_id=org.id, name="Other Contact", email="other@example.com", affiliation="msp"
    )
    db_session.add_all([so, it, other])
    db_session.flush()
    db_session.add_all([
        ContactDocumentationRole(contact_id=so.id, role="security_officer"),
        ContactDocumentationRole(contact_id=it.id, role="it_admin"),
    ])
    db_session.flush()

    from app import liongard_sync

    candidates = liongard_sync.notify_candidates_for_org(db_session, org.id)
    emails = {c.email for c in candidates}
    assert emails == {"sam@example.com", "ivy@example.com"}
