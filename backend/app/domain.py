"""Domain core for the WinGRC scope graph.

This module is intentionally free of any database or web-framework dependency.
It defines the canonical representation of a *scope entity* — the single
source-of-truth record from which every CMMC "list" is later projected — plus
the small set of value types used across importers, reconciliation and
rendering. Keeping this layer pure makes the whole import -> reconcile ->
render loop unit-testable without standing up Postgres.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class EntityType(StrEnum):
    """The kinds of authorized entity that live in the CUI boundary.

    These map directly to the tabs of the Authorized-Entities workbook:
    users, processes, devices and external services.
    """

    PERSON = "person"
    PROCESS = "process"
    DEVICE = "device"
    EXTERNAL_SERVICE = "external_service"
    # Reserved for later modules; not yet imported from the workbook.
    SOFTWARE = "software"
    FACILITY = "facility"
    DATA_STORE = "data_store"


class ScopeCategory(StrEnum):
    """CMMC asset categorization. This is the scoping engine: an entity's
    category decides which lists it appears in and which controls apply.
    """

    CUI_ASSET = "CUI Asset"
    SPA = "SPA"  # Security Protection Asset
    CRMA = "CRMA"  # Contractor Risk Managed Asset
    SPECIALIZED = "Specialized Asset"
    ESP = "ESP"  # External Service Provider
    CSP = "CSP"  # Cloud Service Provider
    OUT_OF_SCOPE = "Out of Scope"
    UNCLASSIFIED = "Unclassified"


class EntityStatus(StrEnum):
    ACTIVE = "active"
    DECOMMISSIONED = "decommissioned"
    # D.3 second half: a connector-observed entity WinGRC has not yet had
    # a human formally accept into the CUI boundary. Set only by
    # liongard_sync.py's pull path for a brand-new entity -- never by
    # workbook import (human-reviewed at upload time already) and never
    # written directly by the scheduled sync job itself (which never
    # touches scope_entity at all -- see liongard_sync.py's own module
    # docstring). Cleared to ACTIVE on approval, left at ACTIVE with
    # in_boundary=False on rejection (see AssetApproval's own docstring
    # for why rejection asserts "not in boundary," not "gone" -- the
    # device exists on the network either way; WinGRC has no authority to
    # assert otherwise).
    PENDING_APPROVAL = "pending_approval"


class Source(StrEnum):
    """Where a record came from. Stored per-entity so generated lists are
    defensible: 'this list was generated from live RMM data on <date>'.
    """

    MANUAL = "manual"
    WORKBOOK = "workbook"
    CSV = "csv"
    LIONGARD = "liongard"
    DATTO_RMM = "datto_rmm"
    ENTRA = "entra"


class DeviceSubtype(StrEnum):
    """Controlled vocabulary for the canonical `device_subtype` attribute.

    A StrEnum (not free text) because three writers populate this field --
    manual entry, workbook import, and the future Liongard connector
    (roadmap D.2) -- and free text drifts ("Laptop" / "laptop" / "Notebook")
    exactly the way raw attribute keys used to before canonicalization.
    `OTHER` plus the companion `device_subtype_other` free-text field (see
    routers/scope.py's DeviceSoftwareAttributes) exists so a Liongard device
    class this vocabulary doesn't yet cover is never silently dropped or
    forced into a wrong bucket.
    """

    DESKTOP = "desktop"
    LAPTOP = "laptop"
    SERVER = "server"
    PRINTER = "printer"
    SCANNER = "scanner"
    MULTIFUNCTION_DEVICE = "multifunction_device"
    DESK_PHONE = "desk_phone"
    MOBILE_PHONE = "mobile_phone"
    TABLET = "tablet"
    TV_DISPLAY = "tv_display"
    PRESENTATION_DEVICE = "presentation_device"
    NETWORK_DEVICE = "network_device"
    STORAGE_DEVICE = "storage_device"
    OTHER = "other"


# Subtypes that commonly land in CMMC's "Specialized Asset" category (IoT/OT,
# GFE, restricted systems, test equipment) -- surfaced in the UI as a
# *suggestion* only. Mirrors the "candidates, never auto-met" rule: scoping
# decisions stay with the engineer, so nothing here writes scope_category.
SPECIALIZED_ASSET_SUGGESTED_SUBTYPES: frozenset[DeviceSubtype] = frozenset(
    {
        DeviceSubtype.TV_DISPLAY,
        DeviceSubtype.PRESENTATION_DEVICE,
        DeviceSubtype.DESK_PHONE,
        DeviceSubtype.PRINTER,
        DeviceSubtype.SCANNER,
        DeviceSubtype.MULTIFUNCTION_DEVICE,
    }
)


# The canonical DEVICE/SOFTWARE attribute vocabulary -- single source of
# truth for both routers/scope.py:DeviceSoftwareAttributes (validation;
# kept in sync by test_domain_attribute_vocabulary.py rather than
# hand-copied, since Pydantic field declarations can't be generated
# directly from a plain frozenset without losing each field's own type/
# validator) and reconcile.py's meaningful-attributes allowlist (what
# actually gets *compared* for CHANGED detection, 2026-09-18 -- see that
# module's own docstring for why an allowlist, not a denylist). One list
# describing this vocabulary, not two that drift.
DEVICE_SOFTWARE_CANONICAL_ATTRIBUTES: frozenset[str] = frozenset(
    {
        "make_oem",
        "model",
        "version",
        "responsible_contact_id",
        "device_subtype",
        "device_subtype_other",
        "asset_tag",
        "mac_addresses",
        "display_name",
        "last_login_user",
        # Liongard's Hostname, kept alongside display_name (Alias-first --
        # see importers/liongard.py:_device_display_name's own docstring,
        # unchanged by this) rather than replacing it, so both are visible
        # (2026-09-24, Jarrod's own live example: WinGRC showed "Jarrods
        # Desktop" over a BIOS placeholder serial with the Hostname
        # nowhere to be found; Liongard itself shows both DEVICE ALIAS and
        # HOST NAME as separate fields). Comparable, not just canonical --
        # falls into DEVICE_SOFTWARE_COMPARABLE_ATTRIBUTES below by
        # subtraction, deliberately: a hostname rename is a real,
        # meaningful change a reviewer should see as CHANGED, unlike
        # last_login_user telemetry.
        "hostname",
    }
)

# The subset of the canonical vocabulary reconcile.py actually compares.
# last_login_user is deliberately excluded: it's telemetry that changes
# legitimately whenever a different person logs into a device, and
# comparing it would reintroduce the exact per-sync noise problem this
# allowlist exists to solve -- see reconcile.py's own module docstring for
# the accepted trade-off this creates (its stored value only refreshes
# when a row is otherwise re-applied for a genuinely meaningful reason,
# not on every sync).
DEVICE_SOFTWARE_COMPARABLE_ATTRIBUTES: frozenset[str] = (
    DEVICE_SOFTWARE_CANONICAL_ATTRIBUTES - {"last_login_user"}
)


# The canonical PERSON attribute vocabulary -- closes the gap
# reconcile.py's own docstring names: PERSON entities compared every raw
# attribute key because no canonical PERSON vocabulary existed (deliberately
# out of scope for D.2's Liongard-contacts-import slice). Deliberately
# minimal, derived from a real record rather than assumed: identity_to_
# contact_fields()'s own docstring in importers/liongard.py already
# recorded, against a real tenant (Goodwin-Bradley, 24 live Inventory-state
# identities, 2026-09-14), that Email/FirstName/LastName/DisplayName are
# reliably present (100% of records sampled) -- Username and Enabled were
# not independently measured in that same study, but are the obvious
# remaining candidates the product needs (Enabled drives EntityStatus
# already; Username is Liongard's own fallback identity key when Email is
# blank -- see importers/liongard.py:_identity_natural_key). No job-title,
# phone, or other HR-style field: Phone was checked and found essentially
# never populated (0/24 in that same study), and nothing downstream reads
# the others -- this is not a speculative schema for fields nothing uses.
#
# AccountActivity/LastLogin/LastSeen -- the person equivalent of DEVICE's
# last_login_user telemetry problem -- are deliberately NOT part of this
# vocabulary at all, not canonicalized-then-excluded the way DEVICE's
# last_login_user is: nothing downstream reads a canonical "last active"
# value for a person today, so there's no reason to invent one just to
# immediately exclude it. They stay under their raw Liongard field names in
# `attributes` for provenance, exactly like any other un-canonicalized
# field, and are therefore never compared by construction.
PERSON_CANONICAL_ATTRIBUTES: frozenset[str] = frozenset(
    {"email", "display_name", "username", "enabled"}
)

# Every canonical PERSON attribute is meaningful (unlike DEVICE_SOFTWARE's
# last_login_user) -- comparable and canonical are the same set today. Kept
# as its own name, not just PERSON_CANONICAL_ATTRIBUTES reused directly in
# reconcile.py, so a future telemetry-style PERSON field could be added to
# the canonical vocabulary (e.g. if something eventually needs to *display*
# last-activity data) and excluded from comparison here without also
# touching reconcile.py.
PERSON_COMPARABLE_ATTRIBUTES: frozenset[str] = PERSON_CANONICAL_ATTRIBUTES


_MAC_HEX_CHARS = "0123456789abcdef"


def normalize_mac_address(raw: str) -> str:
    """Canonicalize a MAC address to lowercase, colon-separated form.

    Accepts colon-separated (00:1a:2b:...), hyphen-separated (00-1a-2b-...)
    and bare hex (001a2b...) input -- the formats a mix of manual entry,
    workbook cells and RMM APIs actually produce -- so the same physical NIC
    always normalizes to one value regardless of source. Raises ValueError
    for anything that isn't 12 hex digits once separators are stripped.
    """
    stripped = raw.strip().lower().replace(":", "").replace("-", "")
    if len(stripped) != 12 or any(c not in _MAC_HEX_CHARS for c in stripped):
        raise ValueError(f"Not a valid MAC address: {raw!r}")
    return ":".join(stripped[i : i + 2] for i in range(0, 12, 2))


@dataclass
class CanonicalEntity:
    """One authorized entity in the scope graph.

    `attributes` preserves the raw, source-keyed values (e.g. the original
    workbook column headers) so a list can be rendered back out faithfully.
    The normalized fields on top (category, status, natural_key) are what the
    scoping engine and reconciler operate on.
    """

    entity_type: EntityType
    natural_key: str
    attributes: dict[str, Any] = field(default_factory=dict)
    scope_category: ScopeCategory | None = None
    status: EntityStatus = EntityStatus.ACTIVE
    in_boundary: bool = True
    source: Source = Source.MANUAL
    source_ref: str | None = None

    def key(self) -> tuple[str, str]:
        """Identity used by the reconciler: (type, natural key)."""
        return (self.entity_type.value, self.natural_key.strip().lower())


class ChangeType(StrEnum):
    NEW = "new"  # present in incoming, absent in current
    CHANGED = "changed"  # present in both, attributes differ
    MISSING = "missing"  # present in current, absent in incoming
    UNCHANGED = "unchanged"


@dataclass
class EntityChange:
    change_type: ChangeType
    entity_type: EntityType
    natural_key: str
    incoming: CanonicalEntity | None = None
    current: CanonicalEntity | None = None
    field_diffs: dict[str, tuple[Any, Any]] = field(default_factory=dict)


@dataclass
class ReconcileResult:
    """The diff an engineer reviews before anything touches the scope graph.

    Imports never overwrite blindly — an automated feed must not silently move
    the audit boundary. The engineer confirms this result, then it is applied.
    """

    changes: list[EntityChange] = field(default_factory=list)

    def of(self, *types: ChangeType) -> list[EntityChange]:
        return [c for c in self.changes if c.change_type in types]

    def summary(self) -> dict[str, int]:
        counts: dict[str, int] = {ct.value: 0 for ct in ChangeType}
        for c in self.changes:
            counts[c.change_type.value] += 1
        return counts
