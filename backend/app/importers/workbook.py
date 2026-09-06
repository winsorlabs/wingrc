"""Importer for the Authorized-Entities workbook.

Parses the four tabs into `CanonicalEntity` records, inferring CMMC scope
category where the source provides it. Raw column values are preserved under
their original headers so the renderer can reproduce the list faithfully.

The same shape of importer will exist for CSV, Liongard and Datto RMM — each
one's only job is: source rows -> List[CanonicalEntity]. Reconciliation and
rendering are shared downstream.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import openpyxl
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..catalog import (
    AUTHORIZED_DEVICES,
    AUTHORIZED_PROCESSES,
    AUTHORIZED_USERS,
    EXTERNAL_SERVICES,
    ListView,
)
from ..domain import (
    CanonicalEntity,
    EntityStatus,
    EntityType,
    ScopeCategory,
    Source,
)
from ..models import Contact

_PLACEHOLDER_TOKENS = ("[placeholder]",)
_CATEGORY_LOOKUP = {c.value.lower(): c for c in ScopeCategory}


def _is_placeholder(value: Any) -> bool:
    return isinstance(value, str) and any(
        t in value.lower() for t in _PLACEHOLDER_TOKENS
    )


def _infer_category(attributes: dict[str, Any]) -> ScopeCategory | None:
    """Look for a known CMMC category token in the columns that tend to hold it."""
    for col in ("Asset Type", "Owner / Primary User"):
        raw = attributes.get(col)
        if isinstance(raw, str):
            cat = _CATEGORY_LOOKUP.get(raw.strip().lower())
            if cat:
                return cat
    return None


def _header_row_index(rows: list[tuple], view: ListView) -> int:
    """Find the row whose cells match the view's first column header."""
    first_header = view.columns[0][1]
    for i, row in enumerate(rows):
        if row and any(
            isinstance(c, str) and c.strip() == first_header for c in row
        ):
            return i
    raise ValueError(f"Header row not found for sheet {view.sheet_title!r}")


def _natural_key(view: ListView, attributes: dict[str, Any]) -> str:
    if view is AUTHORIZED_USERS:
        return f"{attributes.get('First Name', '')} {attributes.get('Last Name', '')}".strip()
    if view is AUTHORIZED_PROCESSES:
        return str(attributes.get("Process Name", "")).strip()
    if view is AUTHORIZED_DEVICES:
        serial = attributes.get("Serial # or Asset Tag")
        return str(serial or attributes.get("Name", "")).strip()
    if view is EXTERNAL_SERVICES:
        return str(attributes.get("Name", "")).strip()
    return str(next(iter(attributes.values()), "")).strip()


def _entity_type(view: ListView) -> EntityType:
    return view.entity_type


def parse_workbook(path: str | Path, source_ref: str | None = None) -> list[CanonicalEntity]:
    """Parse every supported tab of the workbook into canonical entities.

    `source_ref` overrides the provenance filename stamped on each entity
    (default: the basename of `path`). Callers that read the real uploaded
    file from disk (the CLI) never need this. Callers that read it via a
    temp file (the API's dry-run endpoint) must pass the real uploaded
    filename here, or every entity's provenance would point at a
    meaningless generated temp name instead of the file the user actually
    uploaded.
    """
    wb = openpyxl.load_workbook(path, data_only=True)
    views_by_sheet = {
        v.sheet_title: v
        for v in (
            AUTHORIZED_USERS,
            AUTHORIZED_PROCESSES,
            AUTHORIZED_DEVICES,
            EXTERNAL_SERVICES,
        )
    }
    entities: list[CanonicalEntity] = []

    for sheet_title, view in views_by_sheet.items():
        if sheet_title not in wb.sheetnames:
            continue
        ws = wb[sheet_title]
        rows = list(ws.iter_rows(values_only=True))
        try:
            header_idx = _header_row_index(rows, view)
        except ValueError:
            continue

        header_row = rows[header_idx]
        # Map each source column position to the header text we care about.
        positions = {
            j: str(cell).strip()
            for j, cell in enumerate(header_row)
            if isinstance(cell, str) and cell.strip()
        }

        for row in rows[header_idx + 1 :]:
            if not row or all(c is None for c in row):
                continue
            attributes = {
                positions[j]: row[j]
                for j in positions
                if j < len(row) and row[j] is not None
            }
            if not attributes:
                continue
            # Skip illustrative placeholder rows.
            if any(_is_placeholder(v) for v in attributes.values()):
                continue

            natural_key = _natural_key(view, attributes)
            if not natural_key:
                continue

            category = _infer_category(attributes)
            decommissioned = bool(attributes.get("Decommissioned Date"))

            entities.append(
                CanonicalEntity(
                    entity_type=_entity_type(view),
                    natural_key=natural_key,
                    attributes=attributes,
                    scope_category=category,
                    status=(
                        EntityStatus.DECOMMISSIONED
                        if decommissioned
                        else EntityStatus.ACTIVE
                    ),
                    in_boundary=True,
                    source=Source.WORKBOOK,
                    source_ref=source_ref or str(Path(path).name),
                )
            )

    return entities


# ---------------------------------------------------------------------------
# Canonical-key enrichment (post-parse, DB-aware)
# ---------------------------------------------------------------------------
#
# parse_workbook() above stays pure and DB-free -- it only ever produces raw,
# source-keyed attributes (e.g. "Make"/"Model"/"OS"), preserved verbatim so
# catalog.AUTHORIZED_DEVICES can round-trip a faithful CMMC list back out
# (its `columns` reference those exact raw header strings). But the newer
# manual-entry Assets UI (frontend/src/components/AssetDrawer.tsx) and
# downstream consumers -- notably the SSP bundle's Component/Asset Inventory
# section (docs/pdf_ssp_template_spec.md's Addendum 2) -- read a different,
# normalized key set: make_oem/model/version/responsible_contact_id (see
# routers/scope.py's DeviceSoftwareAttributes). A workbook-imported device
# previously never got those keys at all, so it showed up as an all-N/A row
# everywhere that reads the canonical schema despite the equivalent data
# existing under the raw headers. This step adds the canonical keys
# ALONGSIDE the raw ones (never replaces or removes a raw header) so both
# consumers keep working from the same attributes dict.

_DEVICE_CANONICAL_ALIASES: dict[str, str] = {
    "make_oem": "Make",
    "model": "Model",
    # OS, not BIOS FW Ver: "version" is read here as the tracked/patched
    # software version for the asset (the field an MSP actually monitors
    # day to day for vulnerability/patch-level purposes), not firmware.
    # BIOS FW Ver remains available under its own raw key for anyone who
    # needs it; this mapping can change if that reading turns out wrong.
    "version": "OS",
}


def _add_canonical_device_aliases(attributes: dict[str, Any]) -> None:
    for canonical_key, raw_header in _DEVICE_CANONICAL_ALIASES.items():
        if attributes.get(canonical_key):
            continue  # never clobber an existing canonical value
        raw_value = attributes.get(raw_header)
        if raw_value:
            attributes[canonical_key] = raw_value


def resolve_canonical_device_attributes(
    session: Session, org_id: uuid.UUID, entities: list[CanonicalEntity]
) -> list[CanonicalEntity]:
    """Enrich workbook-imported DEVICE entities in place with the canonical
    make_oem/model/version/responsible_contact_id attribute keys, then
    return `entities` (same list, for call-site convenience).

    Call this once, after parse_workbook() and before reconcile() -- both
    routers/scope.py's dry-run endpoint and cli.py's `seed` command reconcile
    against these enriched attributes, and dry-run's response (echoed back
    unmodified by the frontend to /imports/workbook/apply) carries the
    enrichment through to apply without a second resolution pass.

    responsible_contact_id is set ONLY on a real match: "Owner / Primary
    User" is looked up by exact, case-insensitive name against this org's
    actual Contact rows. Zero or ambiguous (>1 same-name) matches leave the
    field unset -- never a raw string stuffed into the UUID slot, and never
    a guessed match.
    """
    contacts_by_name: dict[str, list[uuid.UUID]] = {}
    for c in session.scalars(select(Contact).where(Contact.org_id == org_id)):
        contacts_by_name.setdefault(c.name.strip().lower(), []).append(c.id)

    for entity in entities:
        if entity.entity_type is not EntityType.DEVICE:
            continue
        _add_canonical_device_aliases(entity.attributes)
        if entity.attributes.get("responsible_contact_id"):
            continue
        owner_raw = entity.attributes.get("Owner / Primary User")
        if not owner_raw:
            continue
        matches = contacts_by_name.get(str(owner_raw).strip().lower(), [])
        if len(matches) == 1:
            entity.attributes["responsible_contact_id"] = str(matches[0])

    return entities
