"""Unit tests for the pure domain helpers in app/domain.py -- no DB needed.

Covers:
  - normalize_mac_address() across colon-, hyphen-, and unseparated-hex
    input, and rejection of malformed input.
  - DeviceSubtype vocabulary sanity (the exact set the task specified).
"""
from __future__ import annotations

import pytest

from app.domain import DeviceSubtype, normalize_mac_address


@pytest.mark.parametrize(
    "raw",
    [
        "00:1A:2B:3C:4D:5E",
        "00-1A-2B-3C-4D-5E",
        "001A2B3C4D5E",
        "00:1a:2b:3c:4d:5e",
        "  00:1a:2b:3c:4d:5e  ",
    ],
)
def test_normalize_mac_address_accepts_all_input_formats(raw):
    assert normalize_mac_address(raw) == "00:1a:2b:3c:4d:5e"


@pytest.mark.parametrize(
    "raw",
    [
        "not-a-mac",
        "00:1a:2b:3c:4d",  # too short
        "00:1a:2b:3c:4d:5e:6f",  # too long
        "gg:1a:2b:3c:4d:5e",  # non-hex chars
        "",
    ],
)
def test_normalize_mac_address_rejects_malformed_input(raw):
    with pytest.raises(ValueError):
        normalize_mac_address(raw)


def test_device_subtype_vocabulary():
    values = {s.value for s in DeviceSubtype}
    assert values == {
        "desktop",
        "laptop",
        "server",
        "printer",
        "scanner",
        "multifunction_device",
        "desk_phone",
        "mobile_phone",
        "tablet",
        "tv_display",
        "presentation_device",
        "network_device",
        "storage_device",
        "other",
    }
