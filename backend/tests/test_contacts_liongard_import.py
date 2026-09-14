"""Integration tests for the Liongard-identities-to-contacts import
(routers/contacts.py's GET/POST .../contacts/import/liongard).

Selection-based, not a bulk sync: the GET endpoint lists this org's mapped
Liongard identities as candidates (cross-referenced against existing
contacts by normalized email); the POST endpoint creates/refreshes only
the subset an admin explicitly selected. Deliberately restricted to
msp_admin/consultant_admin, unlike the rest of this router.

connectors/liongard.py's pull_identities is monkeypatched at the module
level, same discipline as test_liongard_sync_api.py -- these tests never
make a real network call.

Run in-container:
    docker compose exec backend pytest tests/test_contacts_liongard_import.py -m integration -v
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
from app.models import AuditLog, Contact, Organization, ScopeEntity
from tests.conftest import _app_session, _authed, _grant, _make_fake_user

pytestmark = pytest.mark.integration

_CRED_BODY = {
    "config": {"instance_url": "https://myinstance.app.liongard.com"},
    "credential": {"access_key_id": "AKIDEXAMPLE", "access_key_secret": "s3cr3t-value"},
}

_ENVIRONMENTS = [liongard_module.LiongardEnvironment(id=8815, name="Acme Corp")]


def _identity_row(
    email: str | None = "ahmed@coopsys.com",
    display_name: str | None = "Ahmed Abdelrehim",
    username: str | None = "ahmed",
    phone: str | None = None,
    enabled: bool = True,
    identity_id: str = "id-1",
) -> dict:
    return {
        "ID": identity_id,
        "EnvironmentID": 8815,
        "InventoryState": "Inventory",
        "Email": email,
        "Username": username,
        "DisplayName": display_name,
        "FirstName": None,
        "LastName": None,
        "Phone": phone,
        "Type": "user",
        "Enabled": enabled,
    }


@pytest.fixture
def fake_msp_admin():
    return _make_fake_user()


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
        "identities": [_identity_row()],
    }
    monkeypatch.setattr(
        liongard_module, "list_environments", lambda config, credential: state["environments"]
    )
    monkeypatch.setattr(
        liongard_module,
        "pull_identities",
        lambda config, credential, environment_id: liongard_module.InventoryPull(
            records=state["identities"], total_count=len(state["identities"])
        ),
    )
    return state


def _org(db_session, fake_msp_admin) -> Organization:
    org = Organization(name=f"LiongardContactImportOrg-{uuid.uuid4().hex[:8]}")
    db_session.add(org)
    db_session.flush()
    _grant(db_session, fake_msp_admin, org_id=org.id)
    return org


def _set_credential(client):
    r = client.put("/integrations/liongard/credential", json=_CRED_BODY)
    assert r.status_code == 200


def _map_environment(client, org):
    r = client.put(
        f"/orgs/{org.id}/integrations/liongard/environment",
        json={"liongard_environment_id": 8815},
    )
    assert r.status_code == 200


def _ready_org(client, db_session, fake_msp_admin) -> Organization:
    org = _org(db_session, fake_msp_admin)
    _set_credential(client)
    _map_environment(client, org)
    return org


def _client_as(db_session, user):
    app.dependency_overrides[get_session] = _app_session(db_session)
    app.dependency_overrides[get_current_user] = _authed(db_session, user)
    return TestClient(app)


# ---------------------------------------------------------------------------
# Role gating
# ---------------------------------------------------------------------------


def test_customer_poc_gets_403_on_list(client, db_session, fake_msp_admin):
    org = _ready_org(client, db_session, fake_msp_admin)
    poc = _make_fake_user(role="customer_poc", email="poc@example.com")
    _grant(db_session, poc, org_id=org.id, role="customer_poc")
    poc_client = _client_as(db_session, poc)
    r = poc_client.get(f"/orgs/{org.id}/contacts/import/liongard")
    assert r.status_code == 403


def test_customer_poc_gets_403_on_import(client, db_session, fake_msp_admin):
    org = _ready_org(client, db_session, fake_msp_admin)
    poc = _make_fake_user(role="customer_poc", email="poc@example.com")
    _grant(db_session, poc, org_id=org.id, role="customer_poc")
    poc_client = _client_as(db_session, poc)
    r = poc_client.post(
        f"/orgs/{org.id}/contacts/import/liongard",
        json={"source_ref": "liongard:environment=8815 (x):pulled_at=now", "selections": []},
    )
    assert r.status_code == 403


def test_msp_engineer_gets_403(client, db_session, fake_msp_admin):
    """Import is restricted beyond the router's usual write gate -- an
    msp_engineer can create contacts by hand today but not through this
    bulk-adjacent, external-system-sourced path.
    """
    org = _ready_org(client, db_session, fake_msp_admin)
    engineer = _make_fake_user(role="msp_engineer", email="engineer@example.com")
    _grant(db_session, engineer, org_id=org.id, role="msp_engineer")
    eng_client = _client_as(db_session, engineer)
    r = eng_client.get(f"/orgs/{org.id}/contacts/import/liongard")
    assert r.status_code == 403


def test_consultant_admin_can_list(client, db_session, fake_msp_admin):
    org = _ready_org(client, db_session, fake_msp_admin)
    consultant = _make_fake_user(role="consultant_admin", email="consultant@example.com")
    _grant(db_session, consultant, org_id=org.id, role="consultant_admin")
    consultant_client = _client_as(db_session, consultant)
    r = consultant_client.get(f"/orgs/{org.id}/contacts/import/liongard")
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# GET: listing candidates
# ---------------------------------------------------------------------------


def test_list_without_mapping_400s(client, db_session, fake_msp_admin):
    org = _org(db_session, fake_msp_admin)
    _set_credential(client)
    r = client.get(f"/orgs/{org.id}/contacts/import/liongard")
    assert r.status_code == 400
    assert "mapped" in r.text


def test_list_without_credential_400s(client, db_session, fake_msp_admin):
    org = _org(db_session, fake_msp_admin)
    r = client.get(f"/orgs/{org.id}/contacts/import/liongard")
    assert r.status_code == 400


def test_list_returns_candidate_with_no_existing_match(client, db_session, fake_msp_admin):
    org = _ready_org(client, db_session, fake_msp_admin)
    r = client.get(f"/orgs/{org.id}/contacts/import/liongard")
    assert r.status_code == 200
    body = r.json()
    assert body["source_ref"].startswith("liongard:environment=8815")
    assert len(body["candidates"]) == 1
    c = body["candidates"][0]
    assert c["email"] == "ahmed@coopsys.com"
    assert c["name"] == "Ahmed Abdelrehim"
    assert c["has_email"] is True
    assert c["existing_contact"] is None


def test_list_flags_identity_without_email(client, db_session, fake_msp_admin, _stub_liongard):
    org = _ready_org(client, db_session, fake_msp_admin)
    _stub_liongard["identities"] = [
        _identity_row(email=None, username="svc-backup", display_name=None, identity_id="id-svc")
    ]
    r = client.get(f"/orgs/{org.id}/contacts/import/liongard")
    assert r.status_code == 200
    candidates = r.json()["candidates"]
    assert len(candidates) == 1
    assert candidates[0]["has_email"] is False
    assert candidates[0]["email"] is None
    # Still gets a usable name (falls back to Username) so it can be shown,
    # not silently dropped from the list.
    assert candidates[0]["name"] == "svc-backup"


def test_list_skips_identity_with_no_email_and_no_name_at_all(
    client, db_session, fake_msp_admin, _stub_liongard
):
    org = _ready_org(client, db_session, fake_msp_admin)
    _stub_liongard["identities"] = [
        _identity_row(email=None, username=None, display_name=None, identity_id="id-empty")
    ]
    r = client.get(f"/orgs/{org.id}/contacts/import/liongard")
    assert r.status_code == 200
    body = r.json()
    assert body["candidates"] == []
    assert len(body["warnings"]) == 1
    assert "no email, name, or username" in body["warnings"][0]


def test_list_matches_existing_contact_case_insensitively(
    client, db_session, fake_msp_admin, _stub_liongard
):
    org = _ready_org(client, db_session, fake_msp_admin)
    created = client.post(
        f"/orgs/{org.id}/contacts",
        json={"name": "Ahmed Existing", "email": "Ahmed@CoopSys.com", "affiliation": "customer"},
    )
    assert created.status_code == 201
    existing_id = created.json()["id"]

    _stub_liongard["identities"] = [_identity_row(email="AHMED@coopsys.com")]
    r = client.get(f"/orgs/{org.id}/contacts/import/liongard")
    assert r.status_code == 200
    candidates = r.json()["candidates"]
    assert len(candidates) == 1
    assert candidates[0]["existing_contact"] is not None
    assert candidates[0]["existing_contact"]["id"] == existing_id


def test_list_matches_legacy_mixed_case_row_defensively(client, db_session, fake_msp_admin):
    """Direct-inserted legacy row (bypassing the normalize-on-write
    validator, the way a pre-migration-0049 row could still exist if the
    backfill's collision guard skipped it) still matches via the GET
    endpoint's own defensive re-normalization.
    """
    org = _ready_org(client, db_session, fake_msp_admin)
    legacy = Contact(
        org_id=org.id, name="Legacy Row", email="Ahmed@CoopSys.com", affiliation="customer"
    )
    db_session.add(legacy)
    db_session.flush()

    r = client.get(f"/orgs/{org.id}/contacts/import/liongard")
    assert r.status_code == 200
    candidates = r.json()["candidates"]
    assert candidates[0]["existing_contact"]["id"] == str(legacy.id)


def test_list_never_writes_to_scope_entity(client, db_session, fake_msp_admin):
    org = _ready_org(client, db_session, fake_msp_admin)
    client.get(f"/orgs/{org.id}/contacts/import/liongard")
    rows = db_session.scalars(select(ScopeEntity).where(ScopeEntity.org_id == org.id)).all()
    assert rows == []


def test_list_surfaces_liongard_api_error_as_502(client, db_session, fake_msp_admin, monkeypatch):
    org = _ready_org(client, db_session, fake_msp_admin)

    def _boom(config, credential, environment_id):
        raise liongard_module.LiongardAPIError("Liongard rejected the key (HTTP 401).")

    monkeypatch.setattr(liongard_module, "pull_identities", _boom)
    r = client.get(f"/orgs/{org.id}/contacts/import/liongard")
    assert r.status_code == 502
    assert "401" in r.text


# ---------------------------------------------------------------------------
# POST: creating contacts
# ---------------------------------------------------------------------------


def _selection(**overrides) -> dict:
    base = {
        "email": "ahmed@coopsys.com",
        "name": "Ahmed Abdelrehim",
        "affiliation": "customer",
    }
    base.update(overrides)
    return base


def test_import_creates_contact_with_admin_picked_affiliation(client, db_session, fake_msp_admin):
    org = _ready_org(client, db_session, fake_msp_admin)
    source_ref = "liongard:environment=8815 (Acme Corp):pulled_at=2026-09-14T00:00:00+00:00"
    r = client.post(
        f"/orgs/{org.id}/contacts/import/liongard",
        json={"source_ref": source_ref, "selections": [_selection(affiliation="mssp")]},
    )
    assert r.status_code == 200
    results = r.json()["results"]
    assert len(results) == 1
    assert results[0]["outcome"] == "created"

    contact = db_session.get(Contact, uuid.UUID(results[0]["contact_id"]))
    assert contact.affiliation == "mssp"
    assert contact.email == "ahmed@coopsys.com"
    assert contact.source == "liongard"
    assert contact.source_ref == source_ref


def test_import_never_bulk_creates_unselected_identities(
    client, db_session, fake_msp_admin, _stub_liongard
):
    org = _ready_org(client, db_session, fake_msp_admin)
    _stub_liongard["identities"] = [
        _identity_row(email="a@x.com", display_name="A", identity_id="id-a"),
        _identity_row(email="b@x.com", display_name="B", identity_id="id-b"),
        _identity_row(email="c@x.com", display_name="C", identity_id="id-c"),
    ]
    listing = client.get(f"/orgs/{org.id}/contacts/import/liongard").json()
    assert len(listing["candidates"]) == 3

    r = client.post(
        f"/orgs/{org.id}/contacts/import/liongard",
        json={
            "source_ref": listing["source_ref"],
            "selections": [_selection(email="b@x.com", name="B", affiliation="msp")],
        },
    )
    assert r.status_code == 200
    rows = db_session.scalars(select(Contact).where(Contact.org_id == org.id)).all()
    assert len(rows) == 1
    assert rows[0].email == "b@x.com"


def test_import_new_contact_without_affiliation_is_rejected(client, db_session, fake_msp_admin):
    org = _ready_org(client, db_session, fake_msp_admin)
    r = client.post(
        f"/orgs/{org.id}/contacts/import/liongard",
        json={
            "source_ref": "liongard:environment=8815 (x):pulled_at=now",
            "selections": [{"email": "ahmed@coopsys.com", "name": "Ahmed"}],
        },
    )
    assert r.status_code == 422


def test_import_blank_email_selection_is_rejected(client, db_session, fake_msp_admin):
    org = _ready_org(client, db_session, fake_msp_admin)
    r = client.post(
        f"/orgs/{org.id}/contacts/import/liongard",
        json={
            "source_ref": "liongard:environment=8815 (x):pulled_at=now",
            "selections": [_selection(email="   ")],
        },
    )
    assert r.status_code == 422


def test_import_selection_matching_an_already_existing_email_is_skipped_not_duplicated(
    client, db_session, fake_msp_admin
):
    """Simulates a stale listing: a contact with this email was created by
    someone else after the GET ran but before the admin submitted. Must
    report a per-item skip, never raise, and never create a duplicate row.
    """
    org = _ready_org(client, db_session, fake_msp_admin)
    client.post(
        f"/orgs/{org.id}/contacts",
        json={"name": "Ahmed Manual", "email": "ahmed@coopsys.com", "affiliation": "customer"},
    )
    r = client.post(
        f"/orgs/{org.id}/contacts/import/liongard",
        json={
            "source_ref": "liongard:environment=8815 (x):pulled_at=now",
            "selections": [_selection()],
        },
    )
    assert r.status_code == 200
    assert r.json()["results"][0]["outcome"] == "skipped"
    rows = db_session.scalars(
        select(Contact).where(Contact.org_id == org.id, Contact.email == "ahmed@coopsys.com")
    ).all()
    assert len(rows) == 1


def test_import_writes_audit_event_on_create(client, db_session, fake_msp_admin):
    org = _ready_org(client, db_session, fake_msp_admin)
    client.post(
        f"/orgs/{org.id}/contacts/import/liongard",
        json={
            "source_ref": "liongard:environment=8815 (x):pulled_at=now",
            "selections": [_selection()],
        },
    )
    entry = db_session.scalars(
        select(AuditLog).where(AuditLog.action == "contact.create")
    ).first()
    assert entry is not None
    assert entry.context["via"] == "liongard_import"


# ---------------------------------------------------------------------------
# POST: refreshing an already-matched contact
# ---------------------------------------------------------------------------


def test_import_refresh_updates_only_selected_fields(client, db_session, fake_msp_admin):
    org = _ready_org(client, db_session, fake_msp_admin)
    existing = client.post(
        f"/orgs/{org.id}/contacts",
        json={
            "name": "Ahmed Old Name",
            "email": "ahmed@coopsys.com",
            "affiliation": "customer",
            "phone": "555-0000",
            "role_title": "Hand-typed Title",
        },
    ).json()

    r = client.post(
        f"/orgs/{org.id}/contacts/import/liongard",
        json={
            "source_ref": "liongard:environment=8815 (x):pulled_at=now",
            "selections": [
                _selection(
                    name="Ahmed New Name",
                    phone="555-9999",
                    contact_id=existing["id"],
                    refresh_fields=["name", "phone"],
                    affiliation=None,
                )
            ],
        },
    )
    assert r.status_code == 200
    assert r.json()["results"][0]["outcome"] == "refreshed"

    contact = db_session.get(Contact, uuid.UUID(existing["id"]))
    assert contact.name == "Ahmed New Name"
    assert contact.phone == "555-9999"
    # Never touched -- role_title has no Liongard source at all, and
    # affiliation is never refreshed from this path.
    assert contact.role_title == "Hand-typed Title"
    assert contact.affiliation == "customer"


def test_import_refresh_never_touches_source_provenance(client, db_session, fake_msp_admin):
    """A hand-created contact stays source='manual' even after accepting a
    Liongard-sourced field refresh -- source/source_ref describe how the
    row was CREATED, not its most recent sync, per Contact being a curated
    record rather than a Liongard cache.
    """
    org = _ready_org(client, db_session, fake_msp_admin)
    existing = client.post(
        f"/orgs/{org.id}/contacts",
        json={"name": "Ahmed", "email": "ahmed@coopsys.com", "affiliation": "customer"},
    ).json()
    assert existing["source"] == "manual"

    client.post(
        f"/orgs/{org.id}/contacts/import/liongard",
        json={
            "source_ref": "liongard:environment=8815 (x):pulled_at=now",
            "selections": [
                _selection(
                    name="Ahmed Updated",
                    contact_id=existing["id"],
                    refresh_fields=["name"],
                    affiliation=None,
                )
            ],
        },
    )
    contact = db_session.get(Contact, uuid.UUID(existing["id"]))
    assert contact.source == "manual"
    assert contact.source_ref is None


def test_import_refresh_with_no_fields_selected_is_unchanged_and_leaves_contact_untouched(
    client, db_session, fake_msp_admin
):
    org = _ready_org(client, db_session, fake_msp_admin)
    existing = client.post(
        f"/orgs/{org.id}/contacts",
        json={
            "name": "Ahmed Untouched",
            "email": "ahmed@coopsys.com",
            "affiliation": "customer",
            "phone": "555-0000",
        },
    ).json()

    r = client.post(
        f"/orgs/{org.id}/contacts/import/liongard",
        json={
            "source_ref": "liongard:environment=8815 (x):pulled_at=now",
            "selections": [
                _selection(
                    name="Ahmed Ignored",
                    phone="555-9999",
                    contact_id=existing["id"],
                    refresh_fields=[],
                    affiliation=None,
                )
            ],
        },
    )
    assert r.status_code == 200
    assert r.json()["results"][0]["outcome"] == "unchanged"
    contact = db_session.get(Contact, uuid.UUID(existing["id"]))
    assert contact.name == "Ahmed Untouched"
    assert contact.phone == "555-0000"


def test_import_refresh_rejects_unknown_refresh_field(client, db_session, fake_msp_admin):
    org = _ready_org(client, db_session, fake_msp_admin)
    existing = client.post(
        f"/orgs/{org.id}/contacts",
        json={"name": "Ahmed", "email": "ahmed@coopsys.com", "affiliation": "customer"},
    ).json()
    r = client.post(
        f"/orgs/{org.id}/contacts/import/liongard",
        json={
            "source_ref": "liongard:environment=8815 (x):pulled_at=now",
            "selections": [
                _selection(
                    contact_id=existing["id"],
                    refresh_fields=["affiliation"],
                    affiliation=None,
                )
            ],
        },
    )
    assert r.status_code == 422


def test_import_refresh_of_unknown_contact_id_is_skipped_not_500(
    client, db_session, fake_msp_admin
):
    org = _ready_org(client, db_session, fake_msp_admin)
    r = client.post(
        f"/orgs/{org.id}/contacts/import/liongard",
        json={
            "source_ref": "liongard:environment=8815 (x):pulled_at=now",
            "selections": [
                _selection(
                    contact_id=str(uuid.uuid4()), refresh_fields=["name"], affiliation=None
                )
            ],
        },
    )
    assert r.status_code == 200
    assert r.json()["results"][0]["outcome"] == "skipped"


# ---------------------------------------------------------------------------
# Pre-existing email-normalization bug fix (regular contact CRUD)
# ---------------------------------------------------------------------------


def test_create_contact_normalizes_email_case_for_dupe_check(client, db_session, fake_msp_admin):
    org = _ready_org(client, db_session, fake_msp_admin)
    r1 = client.post(
        f"/orgs/{org.id}/contacts",
        json={"name": "Alice", "email": "Alice@x.com", "affiliation": "customer"},
    )
    assert r1.status_code == 201
    assert r1.json()["email"] == "alice@x.com"

    r2 = client.post(
        f"/orgs/{org.id}/contacts",
        json={"name": "Alice Again", "email": "alice@x.com", "affiliation": "customer"},
    )
    assert r2.status_code == 409
