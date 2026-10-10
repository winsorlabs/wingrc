"""The one derivation of a scope entity's natural key, for every importer.

`reconcile.py` matches on (entity_type, natural_key), so two importers that
derive keys differently write the same real thing as two rows. That is how
WL-DT26 split (2026-10-09): Liongard saw ASUS's "System Serial Number"
placeholder and fell through to the hostname, the workbook importer took the
Serial column verbatim, and a re-import would have created a second device.
Copying the rule into the second importer would only reset the clock on the
next drift; both importers call these functions instead, and
tests/test_natural_key.py asserts they agree on the same device.

Pure: no DB.
"""

from __future__ import annotations

import re
from typing import Any

from .domain import placeholder_reason

# BIOS/OEM placeholder values a manufacturer ships when no real serial was
# ever programmed into the board -- "System Serial Number" (ASUS) is the
# one confirmed live (WinsorLabs' WL-DT26, 2026-09-24: see
# docs/roadmap.md's device-identity entry for the full survey), but this
# is a known, common OEM-firmware quirk across vendors generally, not a
# closed set specific to this one tenant. Treated as "no serial at all"
# rather than a real identity: two devices sharing one of these would
# otherwise collide onto a single scope_entity row and one would silently
# vanish from the boundary. Expected to grow as real tenants turn up more
# of them -- add to it here, in one place. (Migration 0058 keeps its own
# frozen copy, as migrations must.)
PLACEHOLDER_SERIALS = frozenset(
    {
        "system serial number",
        "to be filled by o.e.m.",
        "default string",
        "none",
        "0123456789",
        "not applicable",
    }
)

# A human annotating a placeholder in a workbook -- "System Serial Number
# (not set by OEM - recommend correcting)" -- is still that placeholder.
_TRAILING_NOTE = re.compile(r"\s*\(.*\)\s*$")


def is_placeholder_serial(value: Any) -> bool:
    if value is None:
        return False
    text = _TRAILING_NOTE.sub("", str(value)).strip().lower()
    return text in PLACEHOLDER_SERIALS


def identifying_value(value: Any) -> str | None:
    """The stripped value, or None when it carries no identity: blank, a
    `[PLACEHOLDER - reason]` cell, or the bare `[PLACEHOLDER]` token.
    A stated gap is a fact about the record, never the record's identity --
    two devices whose serial is "[PLACEHOLDER - not in Datto RMM export]"
    are two devices."""
    if value is None:
        return None
    text = str(value).strip()
    if not text or placeholder_reason(text) is not None or text.lower() == "[placeholder]":
        return None
    return text


def device_natural_key(serial: Any, hostname: Any) -> str:
    """Serial preferred (the stable hardware identity); hostname when the
    serial is absent, a stated gap, or an OEM placeholder. "" when neither
    identifies anything -- callers skip such a row and say so."""
    s = identifying_value(serial)
    if s is not None and not is_placeholder_serial(s):
        return s
    return identifying_value(hostname) or ""


def person_natural_key(
    email: Any = None, username: Any = None, display_name: Any = None
) -> str:
    """Email preferred (stable, and unique where a name is neither),
    username, then display name. A name-keyed person never merges into an
    email-keyed one: the keys differ, so reconcile cannot match them, and
    the workbook dry-run surfaces the pair as a possible match instead."""
    for value in (email, username, display_name):
        v = identifying_value(value)
        if v is not None:
            return v
    return ""
