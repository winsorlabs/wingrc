"""Importer for the Authorized-Entities workbook.

Parses the four tabs into `CanonicalEntity` records, inferring CMMC scope
category where the source provides it. Raw column values are preserved under
their original headers so the renderer can reproduce the list faithfully.

The same shape of importer will exist for CSV, Liongard and Datto RMM — each
one's only job is: source rows -> List[CanonicalEntity]. Reconciliation and
rendering are shared downstream.
"""

from __future__ import annotations

import re
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
    DeviceSubtype,
    EntityStatus,
    EntityType,
    ScopeCategory,
    Source,
    normalize_mac_address,
)
from ..models import Contact, ScopeEntity

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
    # The workbook's one column conflates serial number and physical asset
    # tag ("Serial # or Asset Tag") -- it's also already _natural_key()'s
    # identity source for devices (see above), and is mapped here too so the
    # same value is queryable under the canonical `asset_tag` key without
    # changing natural_key semantics.
    "asset_tag": "Serial # or Asset Tag",
}

_DEVICE_SUBTYPE_LOOKUP: dict[str, DeviceSubtype] = {s.value: s for s in DeviceSubtype}
_MAC_SPLIT_RE = re.compile(r"[,;/]|\s{2,}|\s+and\s+", re.IGNORECASE)


def _add_canonical_device_aliases(attributes: dict[str, Any]) -> None:
    for canonical_key, raw_header in _DEVICE_CANONICAL_ALIASES.items():
        if attributes.get(canonical_key):
            continue  # never clobber an existing canonical value
        raw_value = attributes.get(raw_header)
        if raw_value:
            attributes[canonical_key] = raw_value


def _resolve_device_subtype(attributes: dict[str, Any]) -> str | None:
    """Resolve the raw "Device Subtype" cell to the canonical vocabulary.

    An unrecognized value (a Liongard device class this vocabulary doesn't
    cover yet, or a typo) is never silently dropped -- it lands under
    `device_subtype=other` with the original text preserved verbatim in
    `device_subtype_other`, same discipline as the unresolved-owner warning.
    Returns a warning string, or None if nothing needs flagging.
    """
    if attributes.get("device_subtype"):
        return None  # never clobber an existing canonical value
    raw = attributes.get("Device Subtype")
    if not raw:
        return None
    raw_str = str(raw).strip()
    match = _DEVICE_SUBTYPE_LOOKUP.get(raw_str.lower().replace(" ", "_"))
    if match is not None:
        attributes["device_subtype"] = match.value
        return None
    attributes["device_subtype"] = DeviceSubtype.OTHER.value
    attributes["device_subtype_other"] = raw_str
    return (
        f'Device Subtype "{raw_str}" is not in the known vocabulary -- '
        'recorded as device_subtype="other" with the original text preserved.'
    )


def _resolve_mac_addresses(attributes: dict[str, Any]) -> list[str]:
    """Parse the raw "Mac Address" cell (single value, or several separated
    by comma/semicolon/slash/whitespace -- a laptop has wifi + ethernet +
    dock/USB adapter) into the canonical `mac_addresses` list, each entry
    normalized to lowercase colon-separated form. Returns warnings for any
    token that isn't a valid MAC -- skipped, never silently dropped from the
    warning, so a malformed legacy cell doesn't block the whole import.
    """
    if attributes.get("mac_addresses"):
        return []  # never clobber an existing canonical value
    raw = attributes.get("Mac Address")
    if not raw:
        return []
    tokens = [t.strip() for t in _MAC_SPLIT_RE.split(str(raw)) if t.strip()]
    macs: list[str] = []
    warnings: list[str] = []
    for token in tokens:
        try:
            macs.append(normalize_mac_address(token))
        except ValueError:
            warnings.append(f'Mac Address value "{token}" is not a valid MAC -- skipped.')
    if macs:
        attributes["mac_addresses"] = macs
    return warnings


def resolve_canonical_device_attributes(
    session: Session, org_id: uuid.UUID, entities: list[CanonicalEntity]
) -> dict[tuple[str, str], list[str]]:
    """Enrich workbook-imported DEVICE entities in place with the canonical
    make_oem/model/version/asset_tag/device_subtype/mac_addresses/
    responsible_contact_id attribute keys.

    Call this once, after parse_workbook() and before reconcile() -- both
    routers/scope.py's dry-run endpoint and cli.py's `seed` command reconcile
    against these enriched attributes, and dry-run's response (echoed back
    unmodified by the frontend to /imports/workbook/apply) carries the
    enrichment through to apply without a second resolution pass.

    responsible_contact_id is set ONLY on a real match: "Owner / Primary
    User" is looked up by exact, case-insensitive name against this org's
    actual Contact rows. Zero or ambiguous (>1 same-name) matches leave the
    field unset -- never a raw string stuffed into the UUID slot (that would
    put two different data shapes -- a resolved contact vs. free text -- under
    the one field the renderer treats as "resolved contact"), and never a
    guessed match. A spreadsheet cell is also never auto-created as a new
    Contact: that would silently add rows to the Personnel section the SSP's
    own Personnel page renders, from data an engineer never reviewed as a
    person record.

    Duplicate asset tags are surfaced as a warning rather than enforced by a
    DB constraint -- same "warn, don't block" discipline as the
    owner-resolution above -- so one bad row in an otherwise-good import
    batch never fails the whole apply. Checked both within the incoming
    batch and against this org's existing devices (excluding a device being
    re-imported against itself, which isn't a duplicate).

    Returns a dict of human-readable warnings for every unresolved owner,
    unrecognized subtype, malformed MAC, and duplicate asset tag, keyed by
    `entity.key()` (the same (entity_type, natural_key.strip().lower())
    tuple reconcile.py and CanonicalEntity.key() use) -- so a caller can
    attach one to the matching dry-run change row instead of the mismatch
    disappearing silently.
    """
    contacts_by_name: dict[str, list[uuid.UUID]] = {}
    for c in session.scalars(select(Contact).where(Contact.org_id == org_id)):
        contacts_by_name.setdefault(c.name.strip().lower(), []).append(c.id)

    existing_tags: dict[str, str] = {}
    for row in session.scalars(
        select(ScopeEntity).where(
            ScopeEntity.org_id == org_id, ScopeEntity.entity_type == EntityType.DEVICE.value
        )
    ):
        tag = (row.attributes or {}).get("asset_tag")
        if tag:
            existing_tags[str(tag).strip().lower()] = row.natural_key

    warnings: dict[tuple[str, str], list[str]] = {}

    def _warn(entity: CanonicalEntity, message: str) -> None:
        warnings.setdefault(entity.key(), []).append(message)

    devices = [e for e in entities if e.entity_type is EntityType.DEVICE]
    for entity in devices:
        _add_canonical_device_aliases(entity.attributes)
        subtype_warning = _resolve_device_subtype(entity.attributes)
        if subtype_warning:
            _warn(entity, subtype_warning)
        for mac_warning in _resolve_mac_addresses(entity.attributes):
            _warn(entity, mac_warning)

        if not entity.attributes.get("responsible_contact_id"):
            owner_raw = entity.attributes.get("Owner / Primary User")
            if owner_raw:
                matches = contacts_by_name.get(str(owner_raw).strip().lower(), [])
                if len(matches) == 1:
                    entity.attributes["responsible_contact_id"] = str(matches[0])
                else:
                    reason = (
                        "no contact with that name"
                        if not matches
                        else "multiple contacts share that name"
                    )
                    _warn(
                        entity,
                        f'Owner/Primary User "{owner_raw}" could not be resolved to a '
                        f"contact ({reason}) -- responsible_contact_id left unset.",
                    )

    tag_owners_incoming: dict[str, list[str]] = {}
    for entity in devices:
        tag = entity.attributes.get("asset_tag")
        if tag:
            tag_owners_incoming.setdefault(str(tag).strip().lower(), []).append(
                entity.natural_key
            )

    for entity in devices:
        tag = entity.attributes.get("asset_tag")
        if not tag:
            continue
        tag_norm = str(tag).strip().lower()
        other_incoming = [
            nk for nk in tag_owners_incoming.get(tag_norm, []) if nk != entity.natural_key
        ]
        if other_incoming:
            _warn(
                entity,
                f'Asset tag "{tag}" is also used by {", ".join(sorted(set(other_incoming)))} '
                "in this same import -- asset tags are expected to be unique.",
            )
        existing_owner = existing_tags.get(tag_norm)
        if existing_owner and existing_owner != entity.natural_key:
            _warn(
                entity,
                f'Asset tag "{tag}" is already used by existing device '
                f'"{existing_owner}" -- asset tags are expected to be unique.',
            )

    return warnings
