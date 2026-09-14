"""Unit tests for reconcile.py's meaningful-attributes allowlist
(2026-09-18) -- pure functions, no DB, no network.

The core claim under test, in both directions (see reconcile.py's own
module docstring for the full reasoning):
  - telemetry-only drift on a DEVICE/SOFTWARE entity must NOT report
    CHANGED (the bug this slice fixes -- reproduced here as the exact
    inversion of the prior session's live reproduction).
  - a real change to any meaningful attribute MUST still report CHANGED
    (the dangerous direction of an allowlist that's too narrow).
"""

from __future__ import annotations

import pytest

from app.domain import CanonicalEntity, ChangeType, EntityStatus, EntityType, Source
from app.reconcile import reconcile

_REAL_DEVICE_ATTRIBUTES = {
    # Meaningful (compared) canonical keys.
    "make_oem": "ASUS",
    "model": "System Product Name",
    "version": "Microsoft Windows 11 Business",
    "device_subtype": "desktop",
    "asset_tag": None,
    "mac_addresses": ["08:bf:b8:6b:b2:f3"],
    "display_name": "Jarrods Desktop",
    "responsible_contact_id": None,
    # Not compared, deliberately (telemetry).
    "last_login_user": "jarrod",
    # Raw, volatile Liongard fields -- change on every pull regardless of
    # whether the device itself changed.
    "LastSeenTimelineID": 182036271,
    "LastSeen": "2026-09-14T19:11:39.520Z",
    "UpdatedOn": "2026-09-14T19:11:40.562Z",
    "AvailableStorage": 1663,
    "LastLoginUser": "jarrod",
    "Hostname": "WL-DT26",
    "Manufacturer": "ASUS",
}


def _device(attributes: dict, natural_key: str = "System Serial Number") -> CanonicalEntity:
    return CanonicalEntity(
        entity_type=EntityType.DEVICE,
        natural_key=natural_key,
        attributes=attributes,
        status=EntityStatus.ACTIVE,
        source=Source.LIONGARD,
    )


def test_telemetry_only_drift_reconciles_to_unchanged():
    """The exact reproduction from the prior session, inverted: before this
    fix, this reconciled to CHANGED with those four fields as the diff.
    """
    current = _device(_REAL_DEVICE_ATTRIBUTES)
    incoming = _device(
        {
            **_REAL_DEVICE_ATTRIBUTES,
            "LastSeenTimelineID": 182099999,
            "LastSeen": "2026-09-15T09:00:00.000Z",
            "UpdatedOn": "2026-09-15T09:00:01.000Z",
            "AvailableStorage": 1660,
        }
    )
    result = reconcile([current], [incoming])
    assert len(result.changes) == 1
    assert result.changes[0].change_type == ChangeType.UNCHANGED
    assert result.changes[0].field_diffs == {}


@pytest.mark.parametrize(
    ("field", "old_value", "new_value"),
    [
        ("make_oem", "ASUS", "Dell"),
        ("model", "System Product Name", "Latitude 5420"),
        ("version", "Microsoft Windows 11 Business", "Microsoft Windows 11 Pro"),
        ("device_subtype", "desktop", "laptop"),
        ("asset_tag", None, "ASSET-042"),
        ("display_name", "Jarrods Desktop", "Reception Desktop"),
        ("responsible_contact_id", None, "11111111-1111-1111-1111-111111111111"),
    ],
)
def test_real_change_to_each_meaningful_attribute_reports_changed(field, old_value, new_value):
    """An allowlist that's too narrow silently stops detecting real drift
    -- the dangerous direction of this bug. Each meaningful attribute is
    tested individually so a future edit that drops one from the allowlist
    fails here, not in production.
    """
    current = _device(_REAL_DEVICE_ATTRIBUTES)
    incoming = _device({**_REAL_DEVICE_ATTRIBUTES, field: new_value})
    result = reconcile([current], [incoming])
    assert result.changes[0].change_type == ChangeType.CHANGED
    assert field in result.changes[0].field_diffs
    assert result.changes[0].field_diffs[field] == (old_value, new_value)


def test_mac_address_change_reports_changed():
    current = _device(_REAL_DEVICE_ATTRIBUTES)
    incoming = _device({**_REAL_DEVICE_ATTRIBUTES, "mac_addresses": ["aa:bb:cc:dd:ee:ff"]})
    result = reconcile([current], [incoming])
    assert result.changes[0].change_type == ChangeType.CHANGED
    assert "mac_addresses" in result.changes[0].field_diffs


def test_mac_address_reordering_alone_stays_unchanged():
    """_comparable()'s order-insensitivity (D.2) still applies within the
    allowlist -- not accidentally broken by scoping the comparison down.
    """
    current = _device({**_REAL_DEVICE_ATTRIBUTES, "mac_addresses": ["aa:bb:cc", "dd:ee:ff"]})
    incoming = _device({**_REAL_DEVICE_ATTRIBUTES, "mac_addresses": ["dd:ee:ff", "aa:bb:cc"]})
    result = reconcile([current], [incoming])
    assert result.changes[0].change_type == ChangeType.UNCHANGED


def test_last_login_user_change_alone_reconciles_to_unchanged():
    """Deliberate exclusion (2026-09-18): telemetry that changes whenever a
    different person logs in must not, on its own, flag a device for
    review -- see reconcile.py's own module docstring for the trade-off
    this creates (the stored value only refreshes on a genuinely
    meaningful re-apply).
    """
    current = _device(_REAL_DEVICE_ATTRIBUTES)
    incoming = _device({**_REAL_DEVICE_ATTRIBUTES, "last_login_user": "someone.else"})
    result = reconcile([current], [incoming])
    assert result.changes[0].change_type == ChangeType.UNCHANGED
    assert "last_login_user" not in result.changes[0].field_diffs


def test_last_login_user_change_combined_with_a_real_change_still_reports_it():
    """The allowlist just excludes last_login_user from the comparison --
    it doesn't hide it from the diff once the row is CHANGED for some
    other, real reason.
    """
    current = _device(_REAL_DEVICE_ATTRIBUTES)
    incoming = _device(
        {**_REAL_DEVICE_ATTRIBUTES, "last_login_user": "someone.else", "model": "New Model"}
    )
    result = reconcile([current], [incoming])
    assert result.changes[0].change_type == ChangeType.CHANGED
    assert "model" in result.changes[0].field_diffs
    assert "last_login_user" not in result.changes[0].field_diffs


def test_software_entity_uses_the_same_allowlist_as_device():
    current = CanonicalEntity(
        entity_type=EntityType.SOFTWARE,
        natural_key="Adobe Acrobat",
        attributes={"version": "23.0", "UpdatedOn": "2026-09-14T00:00:00Z"},
    )
    incoming_unchanged = CanonicalEntity(
        entity_type=EntityType.SOFTWARE,
        natural_key="Adobe Acrobat",
        attributes={"version": "23.0", "UpdatedOn": "2026-09-15T00:00:00Z"},
    )
    result = reconcile([current], [incoming_unchanged])
    assert result.changes[0].change_type == ChangeType.UNCHANGED

    incoming_changed = CanonicalEntity(
        entity_type=EntityType.SOFTWARE,
        natural_key="Adobe Acrobat",
        attributes={"version": "24.0", "UpdatedOn": "2026-09-15T00:00:00Z"},
    )
    result = reconcile([current], [incoming_changed])
    assert result.changes[0].change_type == ChangeType.CHANGED
    assert set(result.changes[0].field_diffs) == {"version"}


def test_person_entity_has_no_allowlist_and_still_compares_everything():
    """PERSON has no defined canonical vocabulary (out of scope, see
    reconcile.py's own docstring) -- unchanged from before this fix, on
    purpose. A raw-field-only change must still report CHANGED, proving
    the fallback is real and not accidentally narrowed too.
    """
    current = CanonicalEntity(
        entity_type=EntityType.PERSON,
        natural_key="ahmed@coopsys.com",
        attributes={"Email": "ahmed@coopsys.com", "Department": "Engineering"},
    )
    incoming = CanonicalEntity(
        entity_type=EntityType.PERSON,
        natural_key="ahmed@coopsys.com",
        attributes={"Email": "ahmed@coopsys.com", "Department": "Sales"},
    )
    result = reconcile([current], [incoming])
    assert result.changes[0].change_type == ChangeType.CHANGED
    assert result.changes[0].field_diffs == {"Department": ("Engineering", "Sales")}


def test_new_and_missing_change_types_are_unaffected_by_the_allowlist():
    incoming_only = _device(_REAL_DEVICE_ATTRIBUTES)
    result = reconcile([], [incoming_only])
    assert result.changes[0].change_type == ChangeType.NEW

    current_only = _device(_REAL_DEVICE_ATTRIBUTES)
    result = reconcile([current_only], [])
    assert result.changes[0].change_type == ChangeType.MISSING
