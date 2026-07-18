"""Integration tests for the Onboarding Wizard v1 backend.

Covers:
  - Organization profile GET/PATCH (partial-update semantics, null-clearing)
  - Logo upload (happy path, invalid MIME, magic-byte mismatch, size cap, old key deleted)
  - System description GET (404 before create), PUT (create + replace), JSONB roundtrip
  - Contact CRUD (create, read, update, delete)
  - Documentation roles: add, remove, multi-role, duplicate rejection, invalid role
  - Cross-org isolation: contacts scoped to org_id
  - Onboarding status: computed completion flags, non-blocking
  - contacts-separable-from-users: Contact model has no user_id FK

Run in-container:
    docker compose exec backend pytest tests/test_onboarding.py -v
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.auth import get_current_user
from app.db import get_session
from app.main import app
from app.models import (
    AuditLog,
    Contact,
    ContactDocumentationRole,
    Organization,
    SystemDescription,
)
from app.storage import StorageClient, get_storage_client

# ---------------------------------------------------------------------------
# In-memory storage
# ---------------------------------------------------------------------------


class InMemoryStorageClient(StorageClient):
    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.deleted: list[str] = []

    def upload_file(self, key: str, data: bytes, content_type: str) -> None:
        self.files[key] = data

    def presigned_url(
        self, key: str, expires_in: int = 300, download_filename: str | None = None
    ) -> str:
        url = f"http://fake-storage/{key}"
        if download_filename:
            url += f"?download_filename={download_filename}"
        return url

    def delete_file(self, key: str) -> None:
        self.deleted.append(key)
        self.files.pop(key, None)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def storage():
    return InMemoryStorageClient()


@pytest.fixture
def client(db_session, storage, fake_msp_admin):
    app.dependency_overrides[get_session] = lambda: db_session
    app.dependency_overrides[get_storage_client] = lambda: storage
    app.dependency_overrides[get_current_user] = lambda: fake_msp_admin
    yield TestClient(app)
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


def _org(db_session, *, name: str | None = None) -> Organization:
    org = Organization(name=name or f"TestOrg-{uuid.uuid4().hex[:8]}")
    db_session.add(org)
    db_session.flush()
    return org


def _contact(db_session, org: Organization, **kwargs) -> Contact:
    c = Contact(
        org_id=org.id,
        name=kwargs.get("name", "Alice Smith"),
        email=kwargs.get("email", f"{uuid.uuid4().hex[:8]}@example.com"),
        affiliation=kwargs.get("affiliation", "msp"),
        phone=kwargs.get("phone"),
        role_title=kwargs.get("role_title"),
        notes=kwargs.get("notes"),
    )
    db_session.add(c)
    db_session.flush()
    return c


# Minimal valid PNG: magic bytes only (real PNG would have IHDR/IDAT chunks,
# but the magic-byte check passes on the first 4 bytes alone)
_FAKE_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
_FAKE_JPEG = b"\xff\xd8\xff" + b"\x00" * 100
_FAKE_WEBP = b"RIFF" + b"\x00" * 4 + b"WEBP" + b"\x00" * 50


# ---------------------------------------------------------------------------
# Profile tests
# ---------------------------------------------------------------------------


def test_org_profile_get_returns_defaults(client, db_session):
    org = _org(db_session)
    r = client.get(f"/orgs/{org.id}/profile")
    assert r.status_code == 200
    data = r.json()
    assert data["id"] == str(org.id)
    assert data["name"] == org.name
    assert data["address_line1"] is None
    assert data["country"] == "US"  # server_default 'US' fires on DB INSERT


def test_org_profile_patch_updates_fields(client, db_session):
    org = _org(db_session)
    r = client.patch(
        f"/orgs/{org.id}/profile",
        json={
            "industry": "Defense",
            "address_line1": "123 Main St",
            "city": "Anytown",
            "state_or_province": "VA",
            "postal_code": "22041",
            "phone_primary": "703-555-0100",
        },
    )
    assert r.status_code == 200
    data = r.json()
    assert data["industry"] == "Defense"
    assert data["address_line1"] == "123 Main St"
    assert data["city"] == "Anytown"
    assert data["phone_primary"] == "703-555-0100"


def test_org_profile_patch_is_partial(client, db_session):
    """PATCH with one field must not overwrite other fields."""
    org = _org(db_session)
    client.patch(
        f"/orgs/{org.id}/profile",
        json={"industry": "Defense", "city": "Arlington"},
    )
    # Now only patch phone — city must survive
    r = client.patch(f"/orgs/{org.id}/profile", json={"phone_primary": "703-555-0200"})
    assert r.status_code == 200
    data = r.json()
    assert data["city"] == "Arlington"
    assert data["phone_primary"] == "703-555-0200"


def test_org_profile_patch_null_clears_field(client, db_session):
    """Explicitly sending null in PATCH body clears that field."""
    org = _org(db_session)
    client.patch(f"/orgs/{org.id}/profile", json={"industry": "Defense"})
    r = client.patch(f"/orgs/{org.id}/profile", json={"industry": None})
    assert r.status_code == 200
    assert r.json()["industry"] is None


def test_org_profile_patch_404_unknown_org(client, db_session):
    r = client.patch(f"/orgs/{uuid.uuid4()}/profile", json={"industry": "Defense"})
    assert r.status_code == 404


def test_org_profile_patch_audit_log(client, db_session):
    org = _org(db_session)
    client.patch(f"/orgs/{org.id}/profile", json={"industry": "Defense"})
    entry = db_session.scalars(
        select(AuditLog).where(
            AuditLog.entity_id == org.id,
            AuditLog.action == "org.profile.update",
        )
    ).first()
    assert entry is not None
    assert entry.after_value == {"industry": "Defense"}


# ---------------------------------------------------------------------------
# Logo upload tests
# ---------------------------------------------------------------------------


def test_logo_upload_sets_storage_key(client, db_session, storage):
    org = _org(db_session, name="Acme MSP")
    r = client.post(
        f"/orgs/{org.id}/logo",
        files={"file": ("logo.png", _FAKE_PNG, "image/png")},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["logo_storage_key"].startswith(f"{org.id}/logos/")
    assert data["logo_storage_key"].endswith(".png")
    assert data["logo_url"].startswith("http://fake-storage/")
    # Download forces attachment (not inline render) with a sensible name —
    # not the random UUID-based storage key.
    assert "download_filename=Acme MSP.png" in data["logo_url"]

    # Confirm key persisted on org
    db_session.refresh(org)
    assert org.logo_storage_key == data["logo_storage_key"]

    # File actually reached storage
    assert data["logo_storage_key"] in storage.files


def test_logo_upload_replaces_old_logo(client, db_session, storage):
    org = _org(db_session)
    r1 = client.post(
        f"/orgs/{org.id}/logo",
        files={"file": ("logo.png", _FAKE_PNG, "image/png")},
    )
    old_key = r1.json()["logo_storage_key"]

    r2 = client.post(
        f"/orgs/{org.id}/logo",
        files={"file": ("logo.webp", _FAKE_WEBP, "image/webp")},
    )
    assert r2.status_code == 200
    new_key = r2.json()["logo_storage_key"]
    assert new_key != old_key
    assert old_key in storage.deleted


def test_logo_upload_invalid_mime(client, db_session):
    org = _org(db_session)
    r = client.post(
        f"/orgs/{org.id}/logo",
        files={"file": ("report.pdf", b"%PDF-1.4 body", "application/pdf")},
    )
    assert r.status_code == 422


def test_logo_upload_magic_byte_mismatch(client, db_session):
    """File claims image/png but bytes are garbage."""
    org = _org(db_session)
    r = client.post(
        f"/orgs/{org.id}/logo",
        files={"file": ("logo.png", b"not a png at all", "image/png")},
    )
    assert r.status_code == 422


def test_logo_upload_too_large(client, db_session):
    org = _org(db_session)
    # Create a fake PNG header followed by 11 MB of zeros
    oversized = _FAKE_PNG + b"\x00" * (11 * 1024 * 1024)
    r = client.post(
        f"/orgs/{org.id}/logo",
        files={"file": ("big.png", oversized, "image/png")},
    )
    assert r.status_code == 422


def test_logo_appears_in_profile_get(client, db_session):
    org = _org(db_session, name="Acme MSP")
    client.post(
        f"/orgs/{org.id}/logo",
        files={"file": ("logo.png", _FAKE_PNG, "image/png")},
    )
    r = client.get(f"/orgs/{org.id}/profile")
    assert r.status_code == 200
    data = r.json()
    assert data["logo_storage_key"] is not None
    # _build_profile_out's own presigned_url call also forces attachment —
    # not just the upload-response one.
    assert "download_filename=Acme MSP.png" in data["logo_url"]
    assert data["logo_url"] is not None


# ---------------------------------------------------------------------------
# System description tests
# ---------------------------------------------------------------------------

_SD_BASE = {
    "system_name": "ACME CUI System",
    "system_type": "major_application",
    "operational_status": "operational",
}


def test_system_description_404_before_create(client, db_session):
    org = _org(db_session)
    r = client.get(f"/orgs/{org.id}/system-description")
    assert r.status_code == 404


def test_system_description_put_creates(client, db_session):
    org = _org(db_session)
    r = client.put(f"/orgs/{org.id}/system-description", json=_SD_BASE)
    assert r.status_code == 200
    data = r.json()
    assert data["system_name"] == "ACME CUI System"
    assert data["system_type"] == "major_application"
    assert data["operational_status"] == "operational"
    assert data["org_id"] == str(org.id)


def test_system_description_get_after_put(client, db_session):
    org = _org(db_session)
    client.put(f"/orgs/{org.id}/system-description", json=_SD_BASE)
    r = client.get(f"/orgs/{org.id}/system-description")
    assert r.status_code == 200
    assert r.json()["system_name"] == "ACME CUI System"


def test_system_description_put_replaces(client, db_session):
    """Second PUT fully replaces the first (upsert)."""
    org = _org(db_session)
    client.put(f"/orgs/{org.id}/system-description", json=_SD_BASE)
    r = client.put(
        f"/orgs/{org.id}/system-description",
        json={**_SD_BASE, "system_name": "Updated System"},
    )
    assert r.status_code == 200
    assert r.json()["system_name"] == "Updated System"

    # Only one row in DB
    rows = db_session.scalars(
        select(SystemDescription).where(SystemDescription.org_id == org.id)
    ).all()
    assert len(rows) == 1


def test_system_description_jsonb_roundtrip(client, db_session):
    org = _org(db_session)
    body = {
        **_SD_BASE,
        "cui_categories": ["CUI//PRVCY", "CUI//CTI"],
        "cui_storage_locations": [
            {"type": "gcc_high", "description": "SharePoint GCC High"},
        ],
        "external_connections": [
            {"name": "Azure AD", "direction": "bidirectional", "purpose": "Identity"},
        ],
        "cui_flow_description": "CUI flows from customer endpoints to SharePoint.",
    }
    r = client.put(f"/orgs/{org.id}/system-description", json=body)
    assert r.status_code == 200
    data = r.json()
    assert data["cui_categories"] == ["CUI//PRVCY", "CUI//CTI"]
    assert data["cui_storage_locations"][0]["type"] == "gcc_high"
    assert data["external_connections"][0]["name"] == "Azure AD"
    assert "SharePoint" in data["cui_flow_description"]


def test_system_description_invalid_system_type(client, db_session):
    org = _org(db_session)
    r = client.put(
        f"/orgs/{org.id}/system-description",
        json={**_SD_BASE, "system_type": "not_a_type"},
    )
    assert r.status_code == 422


def test_system_description_invalid_operational_status(client, db_session):
    org = _org(db_session)
    r = client.put(
        f"/orgs/{org.id}/system-description",
        json={**_SD_BASE, "operational_status": "retired"},
    )
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# Contact CRUD tests
# ---------------------------------------------------------------------------


def test_contact_create_and_list(client, db_session):
    org = _org(db_session)
    r = client.post(
        f"/orgs/{org.id}/contacts",
        json={
            "name": "Bob Jones",
            "email": "bob@example.com",
            "affiliation": "customer",
            "phone": "571-555-0100",
            "role_title": "IT Director",
        },
    )
    assert r.status_code == 201
    data = r.json()
    assert data["name"] == "Bob Jones"
    assert data["email"] == "bob@example.com"
    assert data["affiliation"] == "customer"
    assert data["documentation_roles"] == []

    r2 = client.get(f"/orgs/{org.id}/contacts")
    assert r2.status_code == 200
    contacts = r2.json()
    assert len(contacts) == 1
    assert contacts[0]["id"] == data["id"]


def test_contact_get_single(client, db_session):
    org = _org(db_session)
    c = _contact(db_session, org)
    r = client.get(f"/orgs/{org.id}/contacts/{c.id}")
    assert r.status_code == 200
    assert r.json()["id"] == str(c.id)


def test_contact_patch_updates_fields(client, db_session):
    org = _org(db_session)
    c = _contact(db_session, org)
    r = client.patch(
        f"/orgs/{org.id}/contacts/{c.id}",
        json={"name": "Alice B. Smith", "notes": "Primary SSP contact"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["name"] == "Alice B. Smith"
    assert data["notes"] == "Primary SSP contact"
    assert data["affiliation"] == "msp"  # unchanged


def test_contact_patch_is_partial(client, db_session):
    org = _org(db_session)
    c = _contact(db_session, org, phone="703-555-0100")
    r = client.patch(f"/orgs/{org.id}/contacts/{c.id}", json={"role_title": "CISO"})
    assert r.status_code == 200
    data = r.json()
    assert data["role_title"] == "CISO"
    assert data["phone"] == "703-555-0100"  # unchanged


def test_contact_patch_invalid_affiliation(client, db_session):
    org = _org(db_session)
    c = _contact(db_session, org)
    r = client.patch(f"/orgs/{org.id}/contacts/{c.id}", json={"affiliation": "alien"})
    assert r.status_code == 422


def test_contact_delete(client, db_session):
    org = _org(db_session)
    c = _contact(db_session, org)
    r = client.delete(f"/orgs/{org.id}/contacts/{c.id}")
    assert r.status_code == 204

    r2 = client.get(f"/orgs/{org.id}/contacts/{c.id}")
    assert r2.status_code == 404


def test_contact_duplicate_email_rejected(client, db_session):
    org = _org(db_session)
    email = "dup@example.com"
    client.post(
        f"/orgs/{org.id}/contacts",
        json={"name": "Alice", "email": email, "affiliation": "msp"},
    )
    r = client.post(
        f"/orgs/{org.id}/contacts",
        json={"name": "Alice 2", "email": email, "affiliation": "customer"},
    )
    assert r.status_code == 409


def test_contact_invalid_affiliation_rejected(client, db_session):
    org = _org(db_session)
    r = client.post(
        f"/orgs/{org.id}/contacts",
        json={"name": "X", "email": "x@example.com", "affiliation": "vendor"},
    )
    assert r.status_code == 422


def test_contact_create_audit_log(client, db_session):
    org = _org(db_session)
    r = client.post(
        f"/orgs/{org.id}/contacts",
        json={"name": "Bob", "email": "bob@acme.com", "affiliation": "msp"},
    )
    contact_id = r.json()["id"]
    entry = db_session.scalars(
        select(AuditLog).where(
            AuditLog.entity_id == uuid.UUID(contact_id),
            AuditLog.action == "contact.create",
        )
    ).first()
    assert entry is not None
    assert entry.after_value["email"] == "bob@acme.com"


# ---------------------------------------------------------------------------
# Documentation role tests
# ---------------------------------------------------------------------------


def test_add_role_to_contact(client, db_session):
    org = _org(db_session)
    c = _contact(db_session, org)
    r = client.post(
        f"/orgs/{org.id}/contacts/{c.id}/roles",
        json={"role": "security_officer"},
    )
    assert r.status_code == 201
    data = r.json()
    assert data["role"] == "security_officer"
    assert data["contact_id"] == str(c.id)


def test_contact_with_roles_appears_in_list(client, db_session):
    org = _org(db_session)
    c = _contact(db_session, org)
    client.post(f"/orgs/{org.id}/contacts/{c.id}/roles", json={"role": "it_admin"})

    r = client.get(f"/orgs/{org.id}/contacts")
    contacts = r.json()
    assert len(contacts) == 1
    roles = contacts[0]["documentation_roles"]
    assert len(roles) == 1
    assert roles[0]["role"] == "it_admin"


def test_contact_multiple_roles(client, db_session):
    """One person can hold multiple documentation roles."""
    org = _org(db_session)
    c = _contact(db_session, org)
    client.post(f"/orgs/{org.id}/contacts/{c.id}/roles", json={"role": "president"})
    client.post(
        f"/orgs/{org.id}/contacts/{c.id}/roles",
        json={"role": "authorizing_official"},
    )
    client.post(
        f"/orgs/{org.id}/contacts/{c.id}/roles",
        json={"role": "system_owner"},
    )

    r = client.get(f"/orgs/{org.id}/contacts/{c.id}")
    roles = {ro["role"] for ro in r.json()["documentation_roles"]}
    assert roles == {"president", "authorizing_official", "system_owner"}


def test_duplicate_role_rejected(client, db_session):
    org = _org(db_session)
    c = _contact(db_session, org)
    client.post(
        f"/orgs/{org.id}/contacts/{c.id}/roles", json={"role": "security_officer"}
    )
    r = client.post(
        f"/orgs/{org.id}/contacts/{c.id}/roles", json={"role": "security_officer"}
    )
    assert r.status_code == 409


def test_invalid_role_rejected(client, db_session):
    org = _org(db_session)
    c = _contact(db_session, org)
    r = client.post(
        f"/orgs/{org.id}/contacts/{c.id}/roles",
        json={"role": "not_a_real_role"},
    )
    assert r.status_code == 422


def test_remove_role(client, db_session):
    org = _org(db_session)
    c = _contact(db_session, org)
    client.post(
        f"/orgs/{org.id}/contacts/{c.id}/roles", json={"role": "security_officer"}
    )
    r = client.delete(
        f"/orgs/{org.id}/contacts/{c.id}/roles/security_officer"
    )
    assert r.status_code == 204

    r2 = client.get(f"/orgs/{org.id}/contacts/{c.id}")
    assert r2.json()["documentation_roles"] == []


def test_remove_nonexistent_role_returns_404(client, db_session):
    org = _org(db_session)
    c = _contact(db_session, org)
    r = client.delete(f"/orgs/{org.id}/contacts/{c.id}/roles/it_admin")
    assert r.status_code == 404


def test_remove_invalid_role_returns_422(client, db_session):
    org = _org(db_session)
    c = _contact(db_session, org)
    r = client.delete(f"/orgs/{org.id}/contacts/{c.id}/roles/not_a_role")
    assert r.status_code == 422


def test_contact_delete_cascades_roles(client, db_session):
    """Deleting a contact must cascade to its documentation roles."""
    org = _org(db_session)
    c = _contact(db_session, org)
    client.post(f"/orgs/{org.id}/contacts/{c.id}/roles", json={"role": "it_admin"})

    client.delete(f"/orgs/{org.id}/contacts/{c.id}")

    # Role row must be gone too
    orphan = db_session.scalars(
        select(ContactDocumentationRole).where(
            ContactDocumentationRole.contact_id == c.id
        )
    ).first()
    assert orphan is None


def test_role_notes_roundtrip(client, db_session):
    org = _org(db_session)
    c = _contact(db_session, org)
    r = client.post(
        f"/orgs/{org.id}/contacts/{c.id}/roles",
        json={"role": "cui_user", "notes": "Handles export-controlled drawings"},
    )
    assert r.status_code == 201
    r2 = client.get(f"/orgs/{org.id}/contacts/{c.id}")
    role = next(ro for ro in r2.json()["documentation_roles"] if ro["role"] == "cui_user")
    assert role["notes"] == "Handles export-controlled drawings"


# ---------------------------------------------------------------------------
# Cross-org isolation
# ---------------------------------------------------------------------------


def test_contacts_scoped_to_org(client, db_session):
    """Contacts from org A must not appear in org B's list."""
    org_a = _org(db_session)
    org_b = _org(db_session)
    _contact(db_session, org_a, email="alice@a.com")
    _contact(db_session, org_b, email="bob@b.com")

    r_a = client.get(f"/orgs/{org_a.id}/contacts")
    r_b = client.get(f"/orgs/{org_b.id}/contacts")

    emails_a = {c["email"] for c in r_a.json()}
    emails_b = {c["email"] for c in r_b.json()}
    assert emails_a == {"alice@a.com"}
    assert emails_b == {"bob@b.com"}


def test_contact_get_from_wrong_org_returns_404(client, db_session):
    org_a = _org(db_session)
    org_b = _org(db_session)
    c = _contact(db_session, org_a)
    r = client.get(f"/orgs/{org_b.id}/contacts/{c.id}")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Onboarding status
# ---------------------------------------------------------------------------


def test_onboarding_status_empty_org(client, db_session):
    org = _org(db_session)
    r = client.get(f"/orgs/{org.id}/onboarding-status")
    assert r.status_code == 200
    data = r.json()
    assert data["profile"]["complete"] is False
    assert len(data["profile"]["missing_fields"]) > 0
    assert data["system_description"]["complete"] is False
    assert data["personnel"]["complete"] is False
    assert data["personnel"]["contact_count"] == 0
    assert data["personnel"]["roles_covered"] == []


def test_onboarding_status_profile_complete(client, db_session):
    org = _org(db_session)
    client.patch(
        f"/orgs/{org.id}/profile",
        json={
            "industry": "Defense",
            "address_line1": "123 Main St",
            "city": "Anytown",
            "state_or_province": "VA",
            "postal_code": "22041",
            "phone_primary": "703-555-0100",
        },
    )
    r = client.get(f"/orgs/{org.id}/onboarding-status")
    assert r.json()["profile"]["complete"] is True
    assert r.json()["profile"]["missing_fields"] == []


def test_onboarding_status_partial_profile(client, db_session):
    org = _org(db_session)
    client.patch(f"/orgs/{org.id}/profile", json={"industry": "Defense"})
    r = client.get(f"/orgs/{org.id}/onboarding-status")
    data = r.json()
    assert data["profile"]["complete"] is False
    assert "address_line1" in data["profile"]["missing_fields"]


def test_onboarding_status_system_description_complete(client, db_session):
    org = _org(db_session)
    client.put(f"/orgs/{org.id}/system-description", json=_SD_BASE)
    r = client.get(f"/orgs/{org.id}/onboarding-status")
    assert r.json()["system_description"]["complete"] is True


def test_onboarding_status_personnel_complete(client, db_session):
    org = _org(db_session)
    c = _contact(db_session, org)
    client.post(f"/orgs/{org.id}/contacts/{c.id}/roles", json={"role": "security_officer"})
    r = client.get(f"/orgs/{org.id}/onboarding-status")
    data = r.json()
    assert data["personnel"]["complete"] is True
    assert data["personnel"]["contact_count"] == 1
    assert "security_officer" in data["personnel"]["roles_covered"]


def test_onboarding_status_contacts_without_roles_not_complete(client, db_session):
    """Contacts that have no documentation roles don't satisfy personnel completion."""
    org = _org(db_session)
    _contact(db_session, org)
    r = client.get(f"/orgs/{org.id}/onboarding-status")
    data = r.json()
    assert data["personnel"]["complete"] is False
    assert data["personnel"]["contact_count"] == 1


def test_onboarding_status_non_blocking(client, db_session):
    """Status endpoint is purely informational — no access gates in the response."""
    org = _org(db_session)
    # Even with everything missing, it returns 200, not an error
    r = client.get(f"/orgs/{org.id}/onboarding-status")
    assert r.status_code == 200
    # The API does not return any 'blocked' or 'required' field
    data = r.json()
    assert "blocked" not in data
    assert "required" not in data


# ---------------------------------------------------------------------------
# contacts-separable-from-future-users
# ---------------------------------------------------------------------------


def test_contacts_separable_from_future_users():
    """Documentation roles are data attributes on Contact with no user-identity concept.

    The Contact model must not have a user_id column. When auth/RBAC lands, the
    user table will carry a nullable contact_id FK — contact never references user.
    ContactDocumentationRole similarly has no user coupling.

    This test documents and enforces the architectural seam.
    """
    assert not hasattr(Contact, "user_id"), (
        "Contact must not have a user_id column — auth links via user.contact_id, "
        "not contact.user_id. See roadmap auth/RBAC slice."
    )
    assert not hasattr(ContactDocumentationRole, "user_id"), (
        "ContactDocumentationRole must not reference user. "
        "Authentication is decoupled from documentation roles."
    )
    # The table column set must not include user_id
    contact_cols = {c.name for c in Contact.__table__.columns}
    role_cols = {c.name for c in ContactDocumentationRole.__table__.columns}
    assert "user_id" not in contact_cols
    assert "user_id" not in role_cols
