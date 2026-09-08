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
  - An unresolved Owner/Primary User is surfaced as a warning keyed by
    entity.key(), not just silently dropped.

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

    warnings = resolve_canonical_device_attributes(db_session, org.id, [entity])

    assert entity.attributes["responsible_contact_id"] == str(contact.id)
    assert warnings == {}


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

    warnings = resolve_canonical_device_attributes(db_session, org.id, [entity])

    assert "responsible_contact_id" not in entity.attributes
    key = entity.key()
    assert key in warnings
    assert len(warnings[key]) == 1
    assert "SPA" in warnings[key][0]
    assert "no contact with that name" in warnings[key][0]


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

    warnings = resolve_canonical_device_attributes(db_session, org.id, [entity])

    assert "responsible_contact_id" not in entity.attributes
    key = entity.key()
    assert key in warnings
    assert "multiple contacts share that name" in warnings[key][0]


@pytest.mark.integration
def test_asset_tag_aliased_from_serial_or_asset_tag_column(db_session):
    org = _org(db_session)
    entity = CanonicalEntity(
        entity_type=EntityType.DEVICE,
        natural_key="ASSET-0001",
        attributes={"Serial # or Asset Tag": "ASSET-0001"},
    )

    resolve_canonical_device_attributes(db_session, org.id, [entity])

    assert entity.attributes["asset_tag"] == "ASSET-0001"


@pytest.mark.integration
def test_device_subtype_resolves_known_vocabulary(db_session):
    org = _org(db_session)
    entity = CanonicalEntity(
        entity_type=EntityType.DEVICE,
        natural_key="SRV-FILE01",
        attributes={"Device Subtype": "server"},
    )

    warnings = resolve_canonical_device_attributes(db_session, org.id, [entity])

    assert entity.attributes["device_subtype"] == "server"
    assert "device_subtype_other" not in entity.attributes
    assert warnings == {}


@pytest.mark.integration
def test_device_subtype_unrecognized_falls_back_to_other(db_session):
    """An unrecognized Liongard device class (or a typo) must never be
    silently dropped -- it lands under device_subtype=other with the
    original text preserved, and a warning is raised."""
    org = _org(db_session)
    entity = CanonicalEntity(
        entity_type=EntityType.DEVICE,
        natural_key="WEIRD-001",
        attributes={"Device Subtype": "Smart Fridge"},
    )

    warnings = resolve_canonical_device_attributes(db_session, org.id, [entity])

    assert entity.attributes["device_subtype"] == "other"
    assert entity.attributes["device_subtype_other"] == "Smart Fridge"
    key = entity.key()
    assert key in warnings
    assert "Smart Fridge" in warnings[key][0]


@pytest.mark.integration
def test_mac_addresses_parsed_and_normalized_from_single_cell(db_session):
    org = _org(db_session)
    entity = CanonicalEntity(
        entity_type=EntityType.DEVICE,
        natural_key="WS-0001",
        attributes={"Mac Address": "00:11:22:33:44:55, AA-BB-CC-DD-EE-01"},
    )

    warnings = resolve_canonical_device_attributes(db_session, org.id, [entity])

    assert entity.attributes["mac_addresses"] == [
        "00:11:22:33:44:55",
        "aa:bb:cc:dd:ee:01",
    ]
    assert warnings == {}


@pytest.mark.integration
def test_mac_addresses_skips_malformed_token_with_warning(db_session):
    org = _org(db_session)
    entity = CanonicalEntity(
        entity_type=EntityType.DEVICE,
        natural_key="WS-0002",
        attributes={"Mac Address": "00:11:22:33:44:55, not-a-mac"},
    )

    warnings = resolve_canonical_device_attributes(db_session, org.id, [entity])

    assert entity.attributes["mac_addresses"] == ["00:11:22:33:44:55"]
    key = entity.key()
    assert key in warnings
    assert "not-a-mac" in warnings[key][0]


@pytest.mark.integration
def test_duplicate_asset_tag_within_import_batch_warns_both(db_session):
    org = _org(db_session)
    e1 = CanonicalEntity(
        entity_type=EntityType.DEVICE,
        natural_key="WS-0001",
        attributes={"Serial # or Asset Tag": "ASSET-DUP"},
    )
    e2 = CanonicalEntity(
        entity_type=EntityType.DEVICE,
        natural_key="WS-0002",
        attributes={"Serial # or Asset Tag": "ASSET-DUP"},
    )

    warnings = resolve_canonical_device_attributes(db_session, org.id, [e1, e2])

    assert e1.key() in warnings
    assert e2.key() in warnings
    assert "ASSET-DUP" in warnings[e1.key()][0]


@pytest.mark.integration
def test_duplicate_asset_tag_against_existing_device_warns(db_session):
    from app.models import ScopeEntity

    org = _org(db_session)
    db_session.add(
        ScopeEntity(
            org_id=org.id,
            entity_type="device",
            natural_key="WS-EXISTING",
            attributes={"asset_tag": "ASSET-DUP"},
        )
    )
    db_session.flush()

    entity = CanonicalEntity(
        entity_type=EntityType.DEVICE,
        natural_key="WS-NEW",
        attributes={"Serial # or Asset Tag": "ASSET-DUP"},
    )

    warnings = resolve_canonical_device_attributes(db_session, org.id, [entity])

    key = entity.key()
    assert key in warnings
    assert any("WS-EXISTING" in w for w in warnings[key])


@pytest.mark.integration
def test_reimporting_same_device_asset_tag_is_not_a_duplicate(db_session):
    """Re-importing the same device (same natural_key) against itself must
    not be flagged -- only a *different* device sharing the tag is a real
    duplicate."""
    from app.models import ScopeEntity

    org = _org(db_session)
    db_session.add(
        ScopeEntity(
            org_id=org.id,
            entity_type="device",
            natural_key="WS-0001",
            attributes={"asset_tag": "ASSET-0001"},
        )
    )
    db_session.flush()

    entity = CanonicalEntity(
        entity_type=EntityType.DEVICE,
        natural_key="WS-0001",
        attributes={"Serial # or Asset Tag": "ASSET-0001"},
    )

    warnings = resolve_canonical_device_attributes(db_session, org.id, [entity])

    assert warnings == {}


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
