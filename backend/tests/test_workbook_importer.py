"""Unit/integration tests for importers/workbook.py's canonical-attribute
enrichment (resolve_canonical_device_attributes).

Covers:
  - Raw workbook headers (Make/Model/OS) get mirrored onto the canonical
    make_oem/model/version keys the manual-entry Assets UI and the SSP
    bundle's Component/Asset Inventory section read.
  - Raw headers are left untouched (catalog.AUTHORIZED_DEVICES depends on
    them for faithful list round-trip).
  - An existing canonical value is never clobbered by a re-import.
  - responsible_contact_id resolves ONLY on an exact, unambiguous
    case-insensitive name match against a real Contact row for the org --
    never a raw string written into the UUID slot, and never a guess when
    zero or more than one contact shares that name.
  - Non-DEVICE entity types are left untouched.

Run in-container:
    docker compose exec backend pytest tests/test_workbook_importer.py -v
"""
from __future__ import annotations

import uuid

import pytest

from app.domain import CanonicalEntity, EntityType
from app.importers.workbook import resolve_canonical_device_attributes
from app.models import Contact, Organization


def _org(db_session) -> Organization:
    org = Organization(name=f"WorkbookImporterOrg-{uuid.uuid4().hex[:8]}")
    db_session.add(org)
    db_session.flush()
    return org


@pytest.mark.integration
def test_device_gets_canonical_aliases_from_raw_headers(db_session):
    org = _org(db_session)
    entity = CanonicalEntity(
        entity_type=EntityType.DEVICE,
        natural_key="ASSET-0001",
        attributes={"Make": "Dell", "Model": "Latitude 7440", "OS": "Windows 11 Pro 24H2"},
    )

    resolve_canonical_device_attributes(db_session, org.id, [entity])

    assert entity.attributes["make_oem"] == "Dell"
    assert entity.attributes["model"] == "Latitude 7440"
    assert entity.attributes["version"] == "Windows 11 Pro 24H2"


@pytest.mark.integration
def test_raw_headers_preserved_untouched(db_session):
    """catalog.AUTHORIZED_DEVICES.columns reads these exact raw keys to
    reproduce the original workbook faithfully -- the enrichment must add
    canonical keys alongside, never replace or remove the raw ones."""
    org = _org(db_session)
    entity = CanonicalEntity(
        entity_type=EntityType.DEVICE,
        natural_key="ASSET-0001",
        attributes={
            "Make": "Dell",
            "Model": "Latitude 7440",
            "OS": "Windows 11 Pro 24H2",
            "Serial # or Asset Tag": "ASSET-0001",
        },
    )

    resolve_canonical_device_attributes(db_session, org.id, [entity])

    assert entity.attributes["Make"] == "Dell"
    assert entity.attributes["Model"] == "Latitude 7440"
    assert entity.attributes["OS"] == "Windows 11 Pro 24H2"
    assert entity.attributes["Serial # or Asset Tag"] == "ASSET-0001"


@pytest.mark.integration
def test_existing_canonical_value_not_clobbered(db_session):
    """A manually-corrected canonical value (e.g. via AssetDrawer.tsx) must
    survive a re-import that would otherwise re-derive it from the raw
    header."""
    org = _org(db_session)
    entity = CanonicalEntity(
        entity_type=EntityType.DEVICE,
        natural_key="ASSET-0001",
        attributes={"Make": "Dell", "make_oem": "Dell Technologies (corrected)"},
    )

    resolve_canonical_device_attributes(db_session, org.id, [entity])

    assert entity.attributes["make_oem"] == "Dell Technologies (corrected)"


@pytest.mark.integration
def test_responsible_contact_resolves_on_exact_unambiguous_match(db_session):
    org = _org(db_session)
    contact = Contact(org_id=org.id, name="Jane Smith", email="jane@example.com", affiliation="msp")
    db_session.add(contact)
    db_session.flush()

    entity = CanonicalEntity(
        entity_type=EntityType.DEVICE,
        natural_key="ASSET-0001",
        attributes={"Owner / Primary User": "jane smith"},  # case-insensitive match
    )

    resolve_canonical_device_attributes(db_session, org.id, [entity])

    assert entity.attributes["responsible_contact_id"] == str(contact.id)


@pytest.mark.integration
def test_responsible_contact_unset_when_no_match(db_session):
    """This is the real-world case for samples/authorized-entities.example.xlsx
    today: its "Owner / Primary User" column holds scope-category text
    ("CUI Asset", "SPA"), not a person's name -- zero contacts match, and
    responsible_contact_id must stay unset rather than writing that raw
    string into a UUID column."""
    org = _org(db_session)
    entity = CanonicalEntity(
        entity_type=EntityType.DEVICE,
        natural_key="ASSET-0003",
        attributes={"Owner / Primary User": "SPA"},
    )

    resolve_canonical_device_attributes(db_session, org.id, [entity])

    assert "responsible_contact_id" not in entity.attributes


@pytest.mark.integration
def test_responsible_contact_unset_when_ambiguous(db_session):
    org = _org(db_session)
    db_session.add_all(
        [
            Contact(org_id=org.id, name="Jane Smith", email="jane1@example.com", affiliation="msp"),
            Contact(
                org_id=org.id,
                name="Jane Smith",
                email="jane2@example.com",
                affiliation="customer",
            ),
        ]
    )
    db_session.flush()

    entity = CanonicalEntity(
        entity_type=EntityType.DEVICE,
        natural_key="ASSET-0001",
        attributes={"Owner / Primary User": "Jane Smith"},
    )

    resolve_canonical_device_attributes(db_session, org.id, [entity])

    assert "responsible_contact_id" not in entity.attributes


@pytest.mark.integration
def test_non_device_entity_types_untouched(db_session):
    org = _org(db_session)
    entity = CanonicalEntity(
        entity_type=EntityType.PERSON,
        natural_key="Ada Lovelace",
        attributes={"Make": "should not be touched"},
    )

    resolve_canonical_device_attributes(db_session, org.id, [entity])

    assert "make_oem" not in entity.attributes
