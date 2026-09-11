"""Unit tests for importers/liongard.py's raw-Liongard -> CanonicalEntity
mapping (D.2). Pure functions, no DB, no network -- mirrors the existing
unit-test style for importers/workbook.py's canonical-enrichment helpers.

Fixture records below are trimmed from real example payloads captured in
Liongard's own Postman collection (docs.liongard.com/reference/
postman-collection), not invented field names -- see connectors/liongard.py's
module docstring for where that collection lives and why it was the source
of truth rather than the (schema-less) published reference pages.
"""

from __future__ import annotations

from app.domain import EntityStatus, EntityType, Source
from app.importers.liongard import (
    build_source_ref,
    device_profile_to_canonical,
    devices_to_canonical,
    identities_to_canonical,
    identity_to_canonical,
)

_DEVICE_RECORD = {
    "ID": "8c46bdf5-da44-4460-aa7d-23fc928e1363",
    "EnvironmentID": 11282,
    "InventoryState": "Inventory",
    "Hostname": "SBX-Mini-01",
    "OperatingSystem": "macOS Ventura 13.6.5",
    "OSVersion": "macOS Ventura 13.6.5",
    "MACAddress": ["14:9d:99:8b:72:36"],
    "SerialNumber": "H2WH90ACPJJ9",
    "Manufacturer": "Apple",
    "Model": "Macmini8,1",
    "Category": "compute",
    "Type": "desktop",
    "AssetTagNumber": None,
    "LastLoginUser": "sbxadmin",
}

_IDENTITY_RECORD = {
    "ID": "87f05e00-7955-4f86-aab1-37f41184551f",
    "EnvironmentID": 8815,
    "InventoryState": "Inventory",
    "Email": "aabdelrehim@coopsys.com",
    "Username": "aabdelrehim_coopsys.com#EXT#@liongard.onmicrosoft.com",
    "Type": "user",
    "Enabled": True,
    "Status": "inactive",
    "DisplayName": "Ahmed Abdelrehim",
}


def test_device_maps_canonical_attributes_alongside_raw():
    entity, warnings = device_profile_to_canonical(_DEVICE_RECORD, "liongard:test")
    assert warnings == []
    assert entity is not None
    assert entity.entity_type is EntityType.DEVICE
    assert entity.source is Source.LIONGARD
    assert entity.source_ref == "liongard:test"
    # Canonical keys present alongside the raw Liongard field names.
    assert entity.attributes["make_oem"] == "Apple"
    assert entity.attributes["model"] == "Macmini8,1"
    assert entity.attributes["version"] == "macOS Ventura 13.6.5"
    assert entity.attributes["Manufacturer"] == "Apple"  # raw field preserved
    assert entity.attributes["mac_addresses"] == ["14:9d:99:8b:72:36"]
    assert entity.attributes["device_subtype"] == "desktop"


def test_device_natural_key_prefers_serial_over_hostname():
    entity, _ = device_profile_to_canonical(_DEVICE_RECORD, "liongard:test")
    assert entity is not None
    assert entity.natural_key == "H2WH90ACPJJ9"


def test_device_natural_key_falls_back_to_hostname_without_serial():
    record = {**_DEVICE_RECORD, "SerialNumber": None}
    entity, _ = device_profile_to_canonical(record, "liongard:test")
    assert entity is not None
    assert entity.natural_key == "SBX-Mini-01"


def test_device_with_neither_serial_nor_hostname_is_skipped():
    record = {**_DEVICE_RECORD, "SerialNumber": None, "Hostname": None}
    entity, warnings = device_profile_to_canonical(record, "liongard:test")
    assert entity is None
    assert len(warnings) == 1
    assert "neither SerialNumber nor Hostname" in warnings[0]


def test_device_never_sets_responsible_contact_id():
    """Liongard's device schema has no authoritative owner field --
    LastLoginUser is telemetry, not an ownership assignment. See the
    importer's own docstring for the full reasoning.
    """
    entity, _ = device_profile_to_canonical(_DEVICE_RECORD, "liongard:test")
    assert entity is not None
    assert "responsible_contact_id" not in entity.attributes


def test_unrecognized_device_type_maps_to_other_with_warning():
    record = {**_DEVICE_RECORD, "Type": "smart-fridge"}
    entity, warnings = device_profile_to_canonical(record, "liongard:test")
    assert entity is not None
    assert entity.attributes["device_subtype"] == "other"
    assert entity.attributes["device_subtype_other"] == "smart-fridge"
    assert any("smart-fridge" in w for w in warnings)


def test_malformed_mac_address_skipped_with_warning_not_dropping_device():
    record = {**_DEVICE_RECORD, "MACAddress": ["14:9d:99:8b:72:36", "not-a-mac"]}
    entity, warnings = device_profile_to_canonical(record, "liongard:test")
    assert entity is not None
    assert entity.attributes["mac_addresses"] == ["14:9d:99:8b:72:36"]
    assert any("not-a-mac" in w for w in warnings)


def test_identity_maps_to_person_with_email_natural_key():
    entity, warnings = identity_to_canonical(_IDENTITY_RECORD, "liongard:test")
    assert warnings == []
    assert entity is not None
    assert entity.entity_type is EntityType.PERSON
    assert entity.natural_key == "aabdelrehim@coopsys.com"
    assert entity.source is Source.LIONGARD
    assert entity.attributes["Type"] == "user"
    assert entity.attributes["DisplayName"] == "Ahmed Abdelrehim"


def test_identity_falls_back_to_username_without_email():
    record = {**_IDENTITY_RECORD, "Email": ""}
    entity, _ = identity_to_canonical(record, "liongard:test")
    assert entity is not None
    assert entity.natural_key == "aabdelrehim_coopsys.com#EXT#@liongard.onmicrosoft.com"


def test_identity_falls_back_to_display_name_without_email_or_username():
    record = {**_IDENTITY_RECORD, "Email": "", "Username": ""}
    entity, _ = identity_to_canonical(record, "liongard:test")
    assert entity is not None
    assert entity.natural_key == "Ahmed Abdelrehim"


def test_identity_with_nothing_usable_is_skipped():
    record = {**_IDENTITY_RECORD, "Email": "", "Username": "", "DisplayName": ""}
    entity, warnings = identity_to_canonical(record, "liongard:test")
    assert entity is None
    assert len(warnings) == 1


def test_disabled_identity_maps_to_decommissioned():
    record = {**_IDENTITY_RECORD, "Enabled": False}
    entity, _ = identity_to_canonical(record, "liongard:test")
    assert entity is not None
    assert entity.status is EntityStatus.DECOMMISSIONED


def test_devices_to_canonical_collects_skip_warnings_under_sentinel_key():
    records = [_DEVICE_RECORD, {**_DEVICE_RECORD, "SerialNumber": None, "Hostname": None}]
    entities, warnings = devices_to_canonical(records, "liongard:test")
    assert len(entities) == 1
    assert ("_skipped", "_skipped") in warnings
    assert len(warnings[("_skipped", "_skipped")]) == 1


def test_identities_to_canonical_key_warnings_by_entity_key():
    records = [_IDENTITY_RECORD]
    entities, warnings = identities_to_canonical(records, "liongard:test")
    assert len(entities) == 1
    assert warnings == {}  # no warnings for a clean identity record


def test_build_source_ref_identifies_environment_and_time():
    ref = build_source_ref(8815, "Acme Corp", "2026-09-11T00:00:00+00:00")
    assert "8815" in ref
    assert "Acme Corp" in ref
    assert "2026-09-11" in ref
