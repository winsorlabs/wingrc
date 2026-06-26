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
from enum import Enum
from typing import Any


class EntityType(str, Enum):
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


class ScopeCategory(str, Enum):
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


class EntityStatus(str, Enum):
    ACTIVE = "active"
    DECOMMISSIONED = "decommissioned"


class Source(str, Enum):
    """Where a record came from. Stored per-entity so generated lists are
    defensible: 'this list was generated from live RMM data on <date>'.
    """

    MANUAL = "manual"
    WORKBOOK = "workbook"
    CSV = "csv"
    LIONGARD = "liongard"
    DATTO_RMM = "datto_rmm"
    ENTRA = "entra"


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


class ChangeType(str, Enum):
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
