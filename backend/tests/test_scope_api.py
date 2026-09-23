"""Integration tests for routers/scope.py (G.5): manual scope-entity CRUD,
workbook dry-run/apply over HTTP, and the CLI/API apply-parity regression
test docs/PLAN-gui-restructure.md's G.5 section calls for.

Run in-container:
    docker compose exec backend pytest tests/test_scope_api.py -m integration -v
"""
from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import repo
from app.auth import get_current_user
from app.db import get_session
from app.domain import ChangeType
from app.importers.workbook import parse_workbook, resolve_canonical_device_attributes
from app.main import app
from app.models import (
    AssetApproval,
    AssetApprovalChecklistItem,
    AuditLog,
    Organization,
    ScopeEntity,
)
from app.reconcile import reconcile
from tests.conftest import _app_session, _authed, _grant, _make_fake_user

SAMPLE = Path(__file__).resolve().parents[2] / "samples" / "authorized-entities.example.xlsx"


@pytest.fixture
def client(db_session, fake_msp_admin):
    app.dependency_overrides[get_session] = _app_session(db_session)
    app.dependency_overrides[get_current_user] = _authed(db_session, fake_msp_admin)
    yield TestClient(app)
    app.dependency_overrides.clear()


def _org(db_session, fake_msp_admin) -> Organization:
    org = Organization(name=f"ScopeTestOrg-{uuid.uuid4().hex[:8]}")
    db_session.add(org)
    db_session.flush()
    _grant(db_session, fake_msp_admin, org_id=org.id)
    return org


# ---------------------------------------------------------------------------
# Manual CRUD
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_create_device_round_trips_via_get(client, db_session, fake_msp_admin):
    org = _org(db_session, fake_msp_admin)
    r = client.post(
        f"/orgs/{org.id}/scope",
        json={
            "entity_type": "device",
            "natural_key": "LAPTOP-042",
            "scope_category": "CUI Asset",
            "attributes": {"make_oem": "Dell", "model": "Latitude 7420", "version": "BIOS 1.4"},
        },
    )
    assert r.status_code == 201
    body = r.json()
    assert body["entity_type"] == "device"
    assert body["natural_key"] == "LAPTOP-042"
    assert body["attributes"]["make_oem"] == "Dell"

    listed = client.get(f"/orgs/{org.id}/scope").json()
    assert len(listed) == 1
    assert listed[0]["id"] == body["id"]


@pytest.mark.integration
def test_create_software_with_responsible_contact(client, db_session, fake_msp_admin):
    org = _org(db_session, fake_msp_admin)
    contact_id = str(uuid.uuid4())
    r = client.post(
        f"/orgs/{org.id}/scope",
        json={
            "entity_type": "software",
            "natural_key": "Microsoft 365 E3",
            "attributes": {"version": "current", "responsible_contact_id": contact_id},
        },
    )
    assert r.status_code == 201
    assert r.json()["attributes"]["responsible_contact_id"] == contact_id


@pytest.mark.integration
def test_create_device_rejects_invalid_responsible_contact_id(client, db_session, fake_msp_admin):
    org = _org(db_session, fake_msp_admin)
    r = client.post(
        f"/orgs/{org.id}/scope",
        json={
            "entity_type": "device",
            "natural_key": "LAPTOP-043",
            "attributes": {"responsible_contact_id": "not-a-uuid"},
        },
    )
    assert r.status_code == 422


@pytest.mark.integration
def test_create_device_normalizes_mac_addresses(client, db_session, fake_msp_admin):
    org = _org(db_session, fake_msp_admin)
    r = client.post(
        f"/orgs/{org.id}/scope",
        json={
            "entity_type": "device",
            "natural_key": "LAPTOP-046",
            "attributes": {
                "mac_addresses": ["00-1A-2B-3C-4D-5E", "001a2b3c4d99"],
                "device_subtype": "laptop",
                "asset_tag": "  ASSET-046  ",
            },
        },
    )
    assert r.status_code == 201
    attrs = r.json()["attributes"]
    assert attrs["mac_addresses"] == ["00:1a:2b:3c:4d:5e", "00:1a:2b:3c:4d:99"]
    assert attrs["device_subtype"] == "laptop"
    assert attrs["asset_tag"] == "ASSET-046"


@pytest.mark.integration
def test_create_device_rejects_invalid_mac_address(client, db_session, fake_msp_admin):
    org = _org(db_session, fake_msp_admin)
    r = client.post(
        f"/orgs/{org.id}/scope",
        json={
            "entity_type": "device",
            "natural_key": "LAPTOP-047",
            "attributes": {"mac_addresses": ["not-a-mac"]},
        },
    )
    assert r.status_code == 422


@pytest.mark.integration
def test_create_device_rejects_unknown_subtype(client, db_session, fake_msp_admin):
    org = _org(db_session, fake_msp_admin)
    r = client.post(
        f"/orgs/{org.id}/scope",
        json={
            "entity_type": "device",
            "natural_key": "LAPTOP-048",
            "attributes": {"device_subtype": "smart_fridge"},
        },
    )
    assert r.status_code == 422


@pytest.mark.integration
def test_create_device_other_subtype_with_free_text(client, db_session, fake_msp_admin):
    org = _org(db_session, fake_msp_admin)
    r = client.post(
        f"/orgs/{org.id}/scope",
        json={
            "entity_type": "device",
            "natural_key": "IOT-001",
            "attributes": {"device_subtype": "other", "device_subtype_other": "Smart Fridge"},
        },
    )
    assert r.status_code == 201
    attrs = r.json()["attributes"]
    assert attrs["device_subtype"] == "other"
    assert attrs["device_subtype_other"] == "Smart Fridge"


@pytest.mark.integration
def test_create_rejects_unknown_entity_type(client, db_session, fake_msp_admin):
    org = _org(db_session, fake_msp_admin)
    r = client.post(
        f"/orgs/{org.id}/scope",
        json={"entity_type": "bogus", "natural_key": "X"},
    )
    assert r.status_code == 422


@pytest.mark.integration
def test_post_same_natural_key_upserts_not_duplicates(client, db_session, fake_msp_admin):
    org = _org(db_session, fake_msp_admin)
    payload = {"entity_type": "device", "natural_key": "LAPTOP-044", "attributes": {"model": "A"}}
    r1 = client.post(f"/orgs/{org.id}/scope", json=payload)
    payload["attributes"] = {"model": "B"}
    r2 = client.post(f"/orgs/{org.id}/scope", json=payload)
    assert r1.json()["id"] == r2.json()["id"]

    listed = client.get(f"/orgs/{org.id}/scope").json()
    assert len(listed) == 1
    assert listed[0]["attributes"]["model"] == "B"


@pytest.mark.integration
def test_patch_merges_attributes_not_replaces(client, db_session, fake_msp_admin):
    org = _org(db_session, fake_msp_admin)
    created = client.post(
        f"/orgs/{org.id}/scope",
        json={
            "entity_type": "device",
            "natural_key": "LAPTOP-045",
            "attributes": {"make_oem": "Dell", "model": "Latitude 7420"},
        },
    ).json()

    patched = client.patch(
        f"/orgs/{org.id}/scope/{created['id']}",
        json={"attributes": {"model": "Latitude 7430"}},
    )
    assert patched.status_code == 200
    attrs = patched.json()["attributes"]
    assert attrs["make_oem"] == "Dell"  # untouched
    assert attrs["model"] == "Latitude 7430"  # merged in


@pytest.mark.integration
def test_patch_unknown_id_404s(client, db_session, fake_msp_admin):
    org = _org(db_session, fake_msp_admin)
    r = client.patch(f"/orgs/{org.id}/scope/{uuid.uuid4()}", json={"status": "decommissioned"})
    assert r.status_code == 404


@pytest.mark.integration
def test_delete_removes_from_list(client, db_session, fake_msp_admin):
    org = _org(db_session, fake_msp_admin)
    created = client.post(
        f"/orgs/{org.id}/scope",
        json={"entity_type": "software", "natural_key": "Acrobat DC"},
    ).json()

    r = client.delete(f"/orgs/{org.id}/scope/{created['id']}")
    assert r.status_code == 204

    listed = client.get(f"/orgs/{org.id}/scope").json()
    assert listed == []


# ---------------------------------------------------------------------------
# Workbook dry-run over HTTP
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_dry_run_returns_incoming_data_for_new_rows(client, db_session, fake_msp_admin):
    org = _org(db_session, fake_msp_admin)
    with open(SAMPLE, "rb") as f:
        r = client.post(
            f"/orgs/{org.id}/imports/workbook/dry-run",
            files={
                "file": (
                    "authorized-entities.example.xlsx",
                    f.read(),
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
        )
    assert r.status_code == 200
    body = r.json()
    new_changes = [c for c in body["changes"] if c["change_type"] == "new"]
    assert len(new_changes) > 0
    assert all(c["incoming"] is not None for c in new_changes)
    # Sanity: at least one device row carries real attribute data, not an
    # empty dict — this is what apply actually needs to write.
    device_changes = [c for c in new_changes if c["entity_type"] == "device"]
    assert any(c["incoming"]["attributes"] for c in device_changes)
    # Canonical keys land in the response device rows read from the sample
    # workbook's raw Make/Model/OS headers.
    assert all(c["incoming"]["attributes"].get("make_oem") for c in device_changes)
    assert all(c["incoming"]["attributes"].get("model") for c in device_changes)
    assert all(c["incoming"]["attributes"].get("version") for c in device_changes)


@pytest.mark.integration
def test_dry_run_resolves_new_device_fields_from_sample_workbook(
    client, db_session, fake_msp_admin
):
    """Round-trip: samples/authorized-entities.example.xlsx now carries a
    Device Subtype column, and Mac Address values with more than one NIC --
    confirm the dry-run response lands them under the canonical
    device_subtype/asset_tag/mac_addresses keys."""
    org = _org(db_session, fake_msp_admin)
    with open(SAMPLE, "rb") as f:
        r = client.post(
            f"/orgs/{org.id}/imports/workbook/dry-run",
            files={
                "file": (
                    "authorized-entities.example.xlsx",
                    f.read(),
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
        )
    assert r.status_code == 200
    device_changes = {
        c["natural_key"]: c
        for c in r.json()["changes"]
        if c["entity_type"] == "device"
    }
    # natural_key for devices is the "Serial # or Asset Tag" cell (see
    # importers/workbook.py:_natural_key), not the "Name" column.
    ws0001 = device_changes["ASSET-0001"]["incoming"]["attributes"]
    assert ws0001["device_subtype"] == "laptop"
    assert ws0001["asset_tag"] == "ASSET-0001"
    assert ws0001["mac_addresses"] == ["00:11:22:33:44:55", "aa:bb:cc:dd:ee:01"]
    assert device_changes["ASSET-0002"]["incoming"]["attributes"]["device_subtype"] == "server"
    fw_attrs = device_changes["ASSET-0003"]["incoming"]["attributes"]
    assert fw_attrs["device_subtype"] == "network_device"


@pytest.mark.integration
def test_dry_run_surfaces_unresolved_owner_warning(client, db_session, fake_msp_admin):
    """The sample workbook's "Owner / Primary User" column holds scope-
    category text ("CUI Asset", "SPA"), not a person's name -- every device
    row should come back with an unresolved-owner warning rather than the
    mismatch disappearing silently."""
    org = _org(db_session, fake_msp_admin)
    with open(SAMPLE, "rb") as f:
        r = client.post(
            f"/orgs/{org.id}/imports/workbook/dry-run",
            files={
                "file": (
                    "authorized-entities.example.xlsx",
                    f.read(),
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
        )
    assert r.status_code == 200
    device_changes = [c for c in r.json()["changes"] if c["entity_type"] == "device"]
    assert device_changes
    for c in device_changes:
        assert c["warnings"], f"expected a warning for {c['natural_key']!r}, got none"
        assert "could not be resolved to a" in c["warnings"][0]


# ---------------------------------------------------------------------------
# Apply == CLI parity (G.5's own exit-criteria bar)
# ---------------------------------------------------------------------------


def _serialize_scope_rows(db_session, org_id: uuid.UUID) -> list[dict]:
    """Every scope_entity row for one org, normalized for comparison —
    drops id/org_id/created_at/updated_at/last_verified_at, which are
    expected to differ between the two orgs being compared here.
    """
    rows = db_session.scalars(select(ScopeEntity).where(ScopeEntity.org_id == org_id)).all()
    out = [
        {
            "entity_type": r.entity_type,
            "natural_key": r.natural_key,
            "scope_category": r.scope_category,
            "status": r.status,
            "in_boundary": r.in_boundary,
            "source": r.source,
            "source_ref": r.source_ref,
            "attributes": r.attributes,
        }
        for r in rows
    ]
    return sorted(out, key=lambda e: (e["entity_type"], e["natural_key"]))


@pytest.mark.integration
def test_api_apply_matches_cli_seed_apply(client, db_session, fake_msp_admin):
    # -- "CLI" side: the exact functions cli.py's `seed --apply` calls,
    # invoked directly against this test's own session/org.
    cli_org = Organization(name=f"ScopeCliOrg-{uuid.uuid4().hex[:8]}")
    db_session.add(cli_org)
    db_session.flush()

    incoming = parse_workbook(SAMPLE)
    # cli.py's real `seed` command calls this same enrichment step between
    # parse_workbook() and reconcile() — replicate it here too, or this
    # "parity" test would compare the API's enriched dry-run against a CLI
    # side that never got the canonical make_oem/model/version/
    # responsible_contact_id keys, and legitimately fail.
    resolve_canonical_device_attributes(db_session, cli_org.id, incoming)
    current = repo.list_entities(db_session, cli_org.id)
    result = reconcile(current, incoming)
    for c in result.of(ChangeType.NEW, ChangeType.CHANGED):
        repo.upsert(db_session, cli_org.id, c.incoming)
    db_session.flush()

    # -- API side: dry-run then apply the confirmed diff over HTTP, against
    # a fresh, otherwise-identical (empty) org.
    api_org = _org(db_session, fake_msp_admin)
    with open(SAMPLE, "rb") as f:
        dry_run = client.post(
            f"/orgs/{api_org.id}/imports/workbook/dry-run",
            files={
                "file": (
                    "authorized-entities.example.xlsx",
                    f.read(),
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
        ).json()

    applied = client.post(
        f"/orgs/{api_org.id}/imports/workbook/apply",
        json={"changes": dry_run["changes"]},
    )
    assert applied.status_code == 200
    new_or_changed = [c for c in dry_run["changes"] if c["change_type"] in ("new", "changed")]
    assert applied.json()["applied"] == len(new_or_changed)

    assert _serialize_scope_rows(db_session, api_org.id) == _serialize_scope_rows(
        db_session, cli_org.id
    )


@pytest.mark.integration
def test_apply_ignores_missing_rows(client, db_session, fake_msp_admin):
    """MISSING (present in scope, absent from the workbook) rows are never
    auto-deleted by apply, matching cli.py's own seed --apply — only
    ChangeType.NEW/CHANGED are ever written, per the "candidates, never
    auto-met" spirit even though this isn't a control-state mutation.
    """
    org = _org(db_session, fake_msp_admin)
    pre_existing = client.post(
        f"/orgs/{org.id}/scope",
        json={"entity_type": "device", "natural_key": "Not In Workbook"},
    ).json()

    with open(SAMPLE, "rb") as f:
        dry_run = client.post(
            f"/orgs/{org.id}/imports/workbook/dry-run",
            files={
                "file": (
                    "authorized-entities.example.xlsx",
                    f.read(),
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
        ).json()

    missing = [c for c in dry_run["changes"] if c["change_type"] == "missing"]
    assert any(c["natural_key"] == "Not In Workbook" for c in missing)

    client.post(f"/orgs/{org.id}/imports/workbook/apply", json={"changes": dry_run["changes"]})

    listed = {e["id"]: e for e in client.get(f"/orgs/{org.id}/scope").json()}
    assert pre_existing["id"] in listed  # still present -- never auto-deleted


# ---------------------------------------------------------------------------
# Acceptance record on the asset (2026-09-23): GET .../scope/{id}/approvals
# ---------------------------------------------------------------------------


def _device(db_session, org: Organization, natural_key: str = "SN-APPROVAL-TEST") -> ScopeEntity:
    row = ScopeEntity(
        org_id=org.id, entity_type="device", natural_key=natural_key, source="liongard",
    )
    db_session.add(row)
    db_session.flush()
    return row


def _approval(
    db_session, org: Organization, entity: ScopeEntity, *,
    decision: str = "approved", decided_by_name: str = "Jane Reviewer",
    rejection_reason: str | None = None,
) -> AssetApproval:
    approval = AssetApproval(
        org_id=org.id, scope_entity_id=entity.id, decision=decision,
        decided_by=str(uuid.uuid4()), decided_by_name=decided_by_name,
        rejection_reason=rejection_reason,
    )
    db_session.add(approval)
    db_session.flush()
    return approval


@pytest.mark.integration
def test_approvals_empty_for_asset_with_no_record(client, db_session, fake_msp_admin):
    org = _org(db_session, fake_msp_admin)
    entity = _device(db_session, org)
    db_session.commit()

    r = client.get(f"/orgs/{org.id}/scope/{entity.id}/approvals")
    assert r.status_code == 200
    assert r.json() == []


@pytest.mark.integration
def test_approvals_404_for_unknown_asset(client, db_session, fake_msp_admin):
    org = _org(db_session, fake_msp_admin)
    r = client.get(f"/orgs/{org.id}/scope/{uuid.uuid4()}/approvals")
    assert r.status_code == 404


@pytest.mark.integration
def test_approved_asset_shows_reviewer_and_timestamp(client, db_session, fake_msp_admin):
    org = _org(db_session, fake_msp_admin)
    entity = _device(db_session, org)
    _approval(db_session, org, entity, decision="approved", decided_by_name="Jane Reviewer")
    db_session.commit()

    r = client.get(f"/orgs/{org.id}/scope/{entity.id}/approvals")
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1
    assert body[0]["decision"] == "approved"
    assert body[0]["decided_by_name"] == "Jane Reviewer"
    assert body[0]["decided_at"] is not None
    assert body[0]["rejection_reason"] is None


@pytest.mark.integration
def test_rejected_asset_shows_the_reason(client, db_session, fake_msp_admin):
    org = _org(db_session, fake_msp_admin)
    entity = _device(db_session, org)
    _approval(
        db_session, org, entity, decision="rejected", decided_by_name="Jane Reviewer",
        rejection_reason="Personal device, not authorized for CUI.",
    )
    db_session.commit()

    r = client.get(f"/orgs/{org.id}/scope/{entity.id}/approvals")
    body = r.json()
    assert body[0]["decision"] == "rejected"
    assert body[0]["rejection_reason"] == "Personal device, not authorized for CUI."


@pytest.mark.integration
def test_approval_includes_checklist_snapshot(client, db_session, fake_msp_admin):
    org = _org(db_session, fake_msp_admin)
    entity = _device(db_session, org)
    approval = _approval(db_session, org, entity)
    db_session.add(AssetApprovalChecklistItem(
        approval_id=approval.id, org_id=org.id,
        product_key="rocketcyber", product_name="RocketCyber", confirmed=True,
    ))
    db_session.add(AssetApprovalChecklistItem(
        approval_id=approval.id, org_id=org.id,
        product_key="datto-rmm", product_name="Datto RMM", confirmed=False,
    ))
    db_session.commit()

    body = client.get(f"/orgs/{org.id}/scope/{entity.id}/approvals").json()
    checklist = {c["product_key"]: c for c in body[0]["checklist"]}
    assert checklist["rocketcyber"]["confirmed"] is True
    assert checklist["datto-rmm"]["confirmed"] is False


@pytest.mark.integration
def test_approval_name_is_the_stored_snapshot_not_a_live_join(client, db_session, fake_msp_admin):
    """§2's own instruction, asserted directly: decided_by_name is
    rendered exactly as stored at decision time, never re-derived from a
    live user lookup. fake_msp_admin's own current display_name is "Test
    Admin" (tests/conftest.py) -- the approval below is stored under a
    different name entirely, and that stored name is what must come back,
    proving no live join happened.
    """
    org = _org(db_session, fake_msp_admin)
    entity = _device(db_session, org)
    _approval(db_session, org, entity, decided_by_name="Former Name Co-Worker")
    db_session.commit()

    body = client.get(f"/orgs/{org.id}/scope/{entity.id}/approvals").json()
    assert body[0]["decided_by_name"] == "Former Name Co-Worker"
    assert body[0]["decided_by_name"] != fake_msp_admin.display_name


@pytest.mark.integration
def test_approval_history_most_recent_first_never_hides_earlier_decisions(
    client, db_session, fake_msp_admin
):
    """An asset can accumulate more than one asset_approval row (the model
    allows it; a future re-approval slice will produce it) -- not
    reachable through today's UI, constructed directly here. A second
    decision must never silently hide the first: both come back, most
    recent first.
    """
    org = _org(db_session, fake_msp_admin)
    entity = _device(db_session, org)
    first = _approval(db_session, org, entity, decision="rejected", rejection_reason="Not yet.")
    db_session.commit()
    second = _approval(
        db_session, org, entity, decision="approved", decided_by_name="Later Reviewer"
    )
    db_session.commit()

    body = client.get(f"/orgs/{org.id}/scope/{entity.id}/approvals").json()
    assert [a["id"] for a in body] == [str(second.id), str(first.id)]
    assert body[0]["decision"] == "approved"
    assert body[1]["decision"] == "rejected"


@pytest.mark.integration
def test_c3pao_assessor_can_read_approvals(db_session, fake_msp_admin):
    org = _org(db_session, fake_msp_admin)
    entity = _device(db_session, org)
    _approval(db_session, org, entity)
    db_session.commit()

    assessor = _make_fake_user(role="c3pao_assessor")
    _grant(db_session, assessor, org_id=org.id)
    app.dependency_overrides[get_session] = _app_session(db_session)
    app.dependency_overrides[get_current_user] = _authed(db_session, assessor)
    try:
        c = TestClient(app)
        r = c.get(f"/orgs/{org.id}/scope/{entity.id}/approvals")
        assert r.status_code == 200
        assert len(r.json()) == 1
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Delete preserves the acceptance record (migration 0057, ON DELETE RESTRICT)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_delete_refused_when_asset_has_an_approval_record(client, db_session, fake_msp_admin):
    org = _org(db_session, fake_msp_admin)
    entity = _device(db_session, org)
    _approval(db_session, org, entity)
    db_session.commit()

    r = client.delete(f"/orgs/{org.id}/scope/{entity.id}")
    assert r.status_code == 409
    assert "approval" in r.text.lower()

    # Still there -- refused, not partially applied.
    assert db_session.get(ScopeEntity, entity.id) is not None
    assert db_session.scalars(
        select(AssetApproval).where(AssetApproval.scope_entity_id == entity.id)
    ).first() is not None
    # The pre-commit audit log entry for the attempted delete must roll
    # back with everything else, not linger as a record of a delete that
    # never actually happened.
    assert db_session.scalars(
        select(AuditLog).where(
            AuditLog.action == "scope_entity.delete", AuditLog.entity_id == entity.id
        )
    ).first() is None


@pytest.mark.integration
def test_delete_still_works_for_an_asset_with_no_approval_record(
    client, db_session, fake_msp_admin
):
    """Regression: the RESTRICT change must not affect the ordinary case
    (no asset_approval row at all) -- same behavior as
    test_delete_removes_from_list above, named explicitly here as the
    control case for the RESTRICT change."""
    org = _org(db_session, fake_msp_admin)
    entity = _device(db_session, org)
    db_session.commit()

    r = client.delete(f"/orgs/{org.id}/scope/{entity.id}")
    assert r.status_code == 204
