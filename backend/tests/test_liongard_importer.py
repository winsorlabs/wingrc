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

# Captured live against Jarrod's real WinsorLabs tenant (2026-09-17), not
# synthetic -- every field name here is real, confirmed Liongard output,
# used to guard _unrecognized_device_attributes()'s known-field set
# against drifting out of sync with what Liongard actually returns.
_REAL_WINSORLABS_DEVICE_RECORD = {
    "ID": "562bbd95-95ad-45fe-bedf-3d7353ec1b59",
    "EnvironmentID": 5912,
    "FirstSeenTimelineID": 168296565,
    "LastSeenTimelineID": 182036271,
    "InventoryState": "Inventory",
    "Hostname": "WL-DT26",
    "OperatingSystem": "Microsoft Windows 11 Business",
    "OSVersion": "10.0.26200",
    "InternalIP": '{"10.10.24.39"}',
    "MACAddress": ["08:BF:B8:6B:B2:F3"],
    "SerialNumber": "System Serial Number",
    "DomainRole": "Standalone Workstation",
    "Manufacturer": "ASUS",
    "Model": "System Product Name",
    "ExternalIP": '{"71.161.231.19"}',
    "HardwareID": None,
    "Antivirus": ["Windows Defender"],
    "EDR": ["Senteon Agent", "Huntress Rio", "Datto RMM", "Microsoft Defender Core Service"],
    "Firmware": "3854",
    "LastSeen": "2026-09-14T19:11:39.520Z",
    "LastLogin": "2026-09-08T17:23:23.000Z",
    "LastLoginUser": "jarrod",
    "Alias": "Jarrods Desktop",
    "Class": "standard",
    "Status": "active",
    "Category": "compute",
    "Type": "desktop",
    "Role": None,
    "Location": None,
    "LocationManaged": None,
    "Physical": True,
    "HostServer": None,
    "ClusterName": None,
    "DataCenter": None,
    "VirtualizationSoftware": None,
    "HypervisorVersion": None,
    "Purpose": None,
    "PurchaseDate": None,
    "WarrantyExpiration": None,
    "EOLDate": None,
    "ManagedDevice": True,
    "AssetTagNumber": None,
    "LastReviewDate": None,
    "Inspectors": [
        {"ID": 8, "Name": "office365-inspector", "Alias": "Microsoft 365"},
        {"ID": 73, "Name": "datto-rmm-inspector", "Alias": "Datto RMM"},
    ],
    "CreatedOn": "2026-05-18T20:03:44.619Z",
    "CreatedBy": None,
    "UpdatedOn": "2026-09-14T19:11:40.562Z",
    "UpdatedBy": None,
    "FirstSeenEventID": None,
    "LastSeenEventID": None,
    "AvailableStorage": 1663,
    "WinElevenReady": "Compatible",
    "Interfaces": [
        {
            "ipAddress": ["10.10.24.39"],
            "macAddress": "08:BF:B8:6B:B2:F3",
            "subnetMask": "255.255.255.0",
            "defaultGateway": "10.10.24.1",
        }
    ],
    "DeletedOn": None,
    "LicenseExpiration": None,
    "PrimarySubnetCidr": "10.10.24.0/24",
    "DefaultGateway": "10.10.24.1",
    "LastUpdated": None,
    "ReverseDNSHostname": "wl-dt26.tail9a1f6.ts.net",
    "NetworkRole": None,
    "DaysSincePurchaseDate": None,
    "Tags": [],
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
    # _DEVICE_RECORD has no Alias, so display_name falls back to Hostname --
    # see the dedicated display_name tests below for the full fallback chain.
    assert entity.attributes["display_name"] == "SBX-Mini-01"
    assert entity.attributes["last_login_user"] == "sbxadmin"
    assert entity.attributes["LastLoginUser"] == "sbxadmin"  # raw field preserved too


def test_device_display_name_prefers_alias_over_hostname():
    record = {**_DEVICE_RECORD, "Alias": "Sandbox Mini"}
    entity, _ = device_profile_to_canonical(record, "liongard:test")
    assert entity is not None
    assert entity.attributes["display_name"] == "Sandbox Mini"
    # The natural key is untouched by display_name -- still serial-first.
    assert entity.natural_key == "H2WH90ACPJJ9"


def test_device_display_name_falls_back_to_hostname_without_alias():
    record = {**_DEVICE_RECORD, "Alias": None}
    entity, _ = device_profile_to_canonical(record, "liongard:test")
    assert entity is not None
    assert entity.attributes["display_name"] == "SBX-Mini-01"


def test_device_display_name_falls_back_to_natural_key_without_alias_or_hostname():
    record = {**_DEVICE_RECORD, "Alias": None, "Hostname": None}
    entity, _ = device_profile_to_canonical(record, "liongard:test")
    assert entity is not None
    # Natural key falls back to Hostname when SerialNumber is present, so
    # this exercises the true "nothing better than the identity itself"
    # case: SerialNumber present (stays the natural key), no Hostname, no
    # Alias -- display_name must not be blank, it mirrors the natural key.
    assert entity.natural_key == "H2WH90ACPJJ9"
    assert entity.attributes["display_name"] == "H2WH90ACPJJ9"


def test_device_blank_alias_is_treated_as_absent():
    """Liongard can return an empty-string Alias, not just a missing/null
    one -- must not become a blank display_name.
    """
    record = {**_DEVICE_RECORD, "Alias": "   "}
    entity, _ = device_profile_to_canonical(record, "liongard:test")
    assert entity is not None
    assert entity.attributes["display_name"] == "SBX-Mini-01"


def test_device_with_only_known_fields_produces_no_unknown_attribute_warning():
    """_DEVICE_RECORD's fields are all real, observed Liongard fields (see
    this module's own docstring) -- must not spuriously warn.
    """
    entity, warnings = device_profile_to_canonical(_DEVICE_RECORD, "liongard:test")
    assert entity is not None
    assert warnings == []


def test_real_winsorlabs_device_produces_no_unknown_attribute_warning():
    """Against real, live-captured data (not a synthetic fixture) -- if
    this ever starts warning, it means either Liongard changed its schema
    (extend the known-field sets) or the known-field sets drifted from
    reality. Also pins down display_name/natural_key against the exact
    real record this whole slice was designed around.
    """
    entity, warnings = device_profile_to_canonical(_REAL_WINSORLABS_DEVICE_RECORD, "liongard:test")
    assert entity is not None
    assert warnings == []
    assert entity.natural_key == "System Serial Number"
    assert entity.attributes["display_name"] == "Jarrods Desktop"
    assert entity.attributes["last_login_user"] == "jarrod"


def test_device_with_a_genuinely_new_field_is_surfaced_not_silently_dropped():
    """The allowlist's own safety net (2026-09-18): a field this module has
    never seen must be named in a warning, not silently excluded from the
    diff forever with nothing indicating it exists.
    """
    record = {**_DEVICE_RECORD, "BrandNewLiongardField": "surprise", "AnotherNewOne": 42}
    entity, warnings = device_profile_to_canonical(record, "liongard:test")
    assert entity is not None
    assert len(warnings) == 1
    assert "2 attribute(s) not recognized" in warnings[0]
    assert "AnotherNewOne" in warnings[0]
    assert "BrandNewLiongardField" in warnings[0]


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
    importer's own docstring for the full reasoning. Promoting
    LastLoginUser to the canonical last_login_user key (2026-09-17) must
    not relax this -- tested explicitly, not just by omission, since it's
    the exact mistake this whole rule exists to prevent.
    """
    entity, _ = device_profile_to_canonical(_DEVICE_RECORD, "liongard:test")
    assert entity is not None
    assert "responsible_contact_id" not in entity.attributes
    # last_login_user is populated (telemetry) but responsible_contact_id
    # stays absent regardless -- the two must never be conflated.
    assert entity.attributes["last_login_user"] == "sbxadmin"
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


def test_identity_maps_canonical_person_attributes_alongside_raw():
    """domain.py:PERSON_CANONICAL_ATTRIBUTES -- the fix that closes
    reconcile.py's PERSON noise gap. Canonical keys must sit alongside the
    raw Liongard field names, not replace them (matches
    test_device_maps_canonical_attributes_alongside_raw's own pattern)."""
    entity, warnings = identity_to_canonical(_IDENTITY_RECORD, "liongard:test")
    assert warnings == []
    assert entity is not None
    assert entity.attributes["email"] == "aabdelrehim@coopsys.com"
    assert entity.attributes["Email"] == "aabdelrehim@coopsys.com"  # raw preserved too
    assert entity.attributes["username"] == "aabdelrehim_coopsys.com#EXT#@liongard.onmicrosoft.com"
    assert entity.attributes["enabled"] is True
    assert entity.attributes["display_name"] == "Ahmed Abdelrehim"


def test_identity_disabled_maps_enabled_false_not_dropped():
    """The is-not-None check in identity_to_canonical(), not a truthy
    check: enabled=False is a real, meaningful value that a truthy field
    mapping (matching _DEVICE_CANONICAL_FIELDS' own `if value:` pattern)
    would have silently dropped."""
    record = {**_IDENTITY_RECORD, "Enabled": False}
    entity, _ = identity_to_canonical(record, "liongard:test")
    assert entity is not None
    assert entity.attributes["enabled"] is False


def test_identity_with_a_genuinely_new_field_is_surfaced_not_silently_dropped():
    """PERSON's counterpart to test_device_with_a_genuinely_new_field_is_
    surfaced_not_silently_dropped -- the allowlist's own safety net."""
    record = {**_IDENTITY_RECORD, "SomeNewLiongardField": "unexpected"}
    entity, warnings = identity_to_canonical(record, "liongard:test")
    assert entity is not None
    assert len(warnings) == 1
    assert "SomeNewLiongardField" in warnings[0]


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
