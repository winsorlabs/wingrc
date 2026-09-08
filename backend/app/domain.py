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
