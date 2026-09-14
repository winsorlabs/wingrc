"""Maps raw Liongard device-profile/identity records onto `CanonicalEntity`,
the third writer into `scope_entity.attributes`' canonical vocabulary
(ROADMAP.md D.2) -- alongside the manual Add Asset UI and
`importers/workbook.py`'s workbook import.

connectors/liongard.py owns the HTTP pull and returns raw Liongard JSON
records (its own module docstring has the exact schema, sourced from
Liongard's Postman collection since the published reference docs don't show
response bodies). This module is deliberately pure/DB-light -- the one
DB-aware step is `existing_asset_tags`, used the same way
`importers/workbook.py:resolve_canonical_device_attributes` uses existing
tags, to warn on collisions without a DB constraint.

Natural key convergence with the workbook path: workbook devices key on
"Serial # or Asset Tag" (falling back to a Name column) --
`importers/workbook.py:_natural_key`. Liongard distinguishes SerialNumber
(hardware serial) from AssetTagNumber (physical sticker) as two separate
fields, so this mirrors that precedent as closely as the data allows:
SerialNumber preferred (the more stable hardware identity), Hostname as
fallback. The intent is the same physical asset scoped through both paths
converging on one scope_entity row rather than duplicating -- reconcile.py
keys purely on (entity_type, natural_key.strip().lower()), so this only
actually converges if the same value was captured on both sides; that's a
data-quality property of the source systems, not something this module can
force.

No canonical PERSON attribute vocabulary exists anywhere in this codebase
today (only DEVICE/SOFTWARE has one -- see routers/scope.py's
DeviceSoftwareAttributes). Identity/PERSON records therefore write
Liongard's own field names straight through as attributes, the same way
workbook-imported users keep their raw "First Name"/"Last Name" columns --
inventing a canonical PERSON schema is out of scope for this task.

**Display name vs. natural key (2026-09-17):** before this, the UI had no
concept of a device display name at all -- it rendered `natural_key`
(SerialNumber-first, Hostname fallback) as if it were a name, so Jarrod
saw serial numbers in the name column. The fix is NOT to change the
natural key to Hostname: hostnames get renamed and machines get reimaged,
serials don't, and keying reconcile on Hostname would turn a rename into
a MISSING + NEW pair -- an asset silently leaving and re-entering the
audit boundary. Identity and label are kept separate instead:
`device_profile_to_canonical()` now always writes a `display_name`
attribute (Alias -> Hostname -> natural_key, see
`_device_display_name()`'s own docstring for the ordering rationale) on
top of the unchanged natural key. This lives as a canonical attribute
(`routers/scope.py:DeviceSoftwareAttributes`), not a `scope_entity`
column -- consistent with every other display-ish field (`make_oem`,
`model`, `asset_tag`) already living there, and the frontend already
reads `attributes` generically for any entity type, so nothing about
this is Liongard-specific despite Liongard being the only writer that
populates it automatically today. Workbook import and manual entry don't
set it (unchanged in this slice) -- an entity with no `display_name`
renders its natural key exactly as before, via the frontend's own
fallback, not a backend default.

**A real, pre-existing diff-noise bug, found while checking `reconcile.py`
per this slice's own §3, reported here rather than fixed:**
`device_profile_to_canonical()`'s `attributes = dict(record)` stores the
*entire* raw Liongard record, including fields that change on every pull
regardless of whether the device itself changed at all (`LastSeen`,
`LastSeenTimelineID`, `LastSeenEventID`, `UpdatedOn`, `AvailableStorage`,
nested `Inspectors` entries, etc. -- confirmed against two real device
records, 2026-09-17). `reconcile.py:_field_diffs()` compares the full
union of `current.attributes`/`incoming.attributes` keys with no
exclusion list, so **every applied Liongard device will report CHANGED
on every subsequent sync**, purely from telemetry drift -- the review
diff becomes noise nobody reads, exactly the failure class D.2's own
MAC-address-ordering fix (`reconcile.py:_comparable()`) already guards
against for list-valued attributes, but this is broader: whole keys, not
just element order. **Not fixed in this slice** -- `reconcile.py` is
shared across every source (workbook, manual, Liongard), so a fix needs
its own deliberate scope decision, not a quick patch here. Recommended
direction: compare a defined allowlist of *meaningful* attributes (the
canonical vocabulary plus a short list of source fields worth tracking)
rather than the raw union of everything -- safer than a volatile-fields
denylist, since an unrecognized future field defaults to NOT producing
diff noise instead of defaulting to producing it until someone notices
and adds it to an exclude list. Left as a named, described gap
(docs/roadmap.md) for a dedicated slice.
"""

from __future__ import annotations

from typing import Any

from ..domain import (
    CanonicalEntity,
    DeviceSubtype,
    EntityStatus,
    EntityType,
    Source,
    normalize_mac_address,
)

_DEVICE_SUBTYPE_LOOKUP: dict[str, DeviceSubtype] = {s.value: s for s in DeviceSubtype}

# Liongard's own field -> our canonical device attribute key. Not a full
# alias table like workbook.py's raw-header map -- Liongard already uses
# machine-friendly field names, so there's no header text to normalize,
# just a rename onto the shared vocabulary.
_DEVICE_CANONICAL_FIELDS: dict[str, str] = {
    "make_oem": "Manufacturer",
    "model": "Model",
    # OperatingSystem (e.g. "macOS Ventura 13.6.5") over the narrower
    # OSVersion -- same "the field an MSP actually monitors for
    # patch-level purposes" reasoning as workbook.py's OS -> version
    # mapping. OSVersion is preserved verbatim under its own raw key for
    # anyone who needs the narrower value.
    "version": "OperatingSystem",
    "asset_tag": "AssetTagNumber",
    # Telemetry, not an ownership assignment -- see
    # device_profile_to_canonical()'s own docstring for why this must
    # never feed responsible_contact_id. Promoted to an explicit canonical
    # key (rather than left as the raw LastLoginUser for readers to know
    # about) so the asset drawer can surface it labeled correctly.
    "last_login_user": "LastLoginUser",
}


def _warn(
    warnings: dict[tuple[str, str], list[str]], entity: CanonicalEntity, message: str
) -> None:
    warnings.setdefault(entity.key(), []).append(message)


def _resolve_device_subtype(attributes: dict[str, Any]) -> str | None:
    """Map Liongard's `Type` field ("desktop", "server", ...) onto
    DeviceSubtype. Unrecognized values never drop the device -- they land
    under device_subtype="other" with the original text preserved in
    device_subtype_other, same discipline as
    importers/workbook.py:_resolve_device_subtype. Returns a warning
    string, or None.
    """
    raw = attributes.get("Type")
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
        f'Liongard device Type "{raw_str}" is not in the known vocabulary -- '
        'recorded as device_subtype="other" with the original text preserved.'
    )


def _resolve_mac_addresses(attributes: dict[str, Any]) -> list[str]:
    """Liongard's MACAddress field is already a list, unlike the workbook's
    single free-text cell -- just normalize each entry. Malformed entries
    are skipped with a warning, never silently dropped from the warning,
    matching importers/workbook.py:_resolve_mac_addresses.
    """
    raw = attributes.get("MACAddress")
    if not raw:
        return []
    if not isinstance(raw, list):
        raw = [raw]
    macs: list[str] = []
    warnings: list[str] = []
    for token in raw:
        if not token:
            continue
        try:
            macs.append(normalize_mac_address(str(token)))
        except ValueError:
            warnings.append(f'MACAddress value "{token}" is not a valid MAC -- skipped.')
    if macs:
        attributes["mac_addresses"] = macs
    return warnings


def _device_natural_key(record: dict[str, Any]) -> str:
    serial = record.get("SerialNumber")
    if serial and str(serial).strip():
        return str(serial).strip()
    return str(record.get("Hostname") or "").strip()


def _device_display_name(record: dict[str, Any], natural_key: str) -> str:
    """Alias -> Hostname -> natural_key (2026-09-17 decision).

    Deliberately NOT the natural key -- see this module's own docstring
    and device_profile_to_canonical()'s for why SerialNumber/Hostname stay
    the reconcile identity (a hostname rename must never look like the
    asset leaving and a new one appearing). display_name is a pure label,
    read nowhere reconcile.py compares on.

    Alias first: a human named the device *in Liongard*, deliberately --
    the strongest signal of "what people actually call this thing" this
    source can offer, stronger than Hostname (assigned by IT tooling/OS
    defaults, not usually chosen to be meaningful to a person). Falls back
    to Hostname (the field Jarrod asked for, and what's actually usually
    populated -- real tenant data checked 2026-09-17: only one of two real
    Inventory-state devices had an Alias set). Falls back to natural_key
    last, matching current behavior exactly when neither exists -- never
    blank.
    """
    alias = record.get("Alias")
    if alias and str(alias).strip():
        return str(alias).strip()
    hostname = record.get("Hostname")
    if hostname and str(hostname).strip():
        return str(hostname).strip()
    return natural_key


def device_profile_to_canonical(
    record: dict[str, Any], source_ref: str
) -> tuple[CanonicalEntity | None, list[str]]:
    """One Liongard DeviceProfiles row -> CanonicalEntity(DEVICE).

    Returns (None, warnings) if the record has neither a SerialNumber nor a
    Hostname to key on -- there is nothing to reconcile against, so it's
    skipped rather than written with a blank/None natural key (which would
    collide every such device into one row).

    responsible_contact_id is deliberately left unset: unlike the
    workbook's "Owner / Primary User" column, Liongard's device-profile
    schema has no field that represents an assigned owner -- LastLoginUser
    is telemetry (who last logged in), not an ownership assignment, and
    mapping it to responsible_contact_id would silently misattribute
    ownership to whoever happened to log in last. An engineer can set
    ownership manually after apply via the existing PATCH /scope endpoint,
    same as any manually-added asset. Promoted to the canonical
    `last_login_user` key (see _DEVICE_CANONICAL_FIELDS) purely as an
    informational, clearly-telemetry field in the asset drawer -- never as
    an ownership candidate. This restates and does not relax that rule.
    """
    natural_key = _device_natural_key(record)
    warnings: list[str] = []
    if not natural_key:
        return None, [
            "Liongard device record has neither SerialNumber nor Hostname -- skipped "
            f"(EnvironmentID={record.get('EnvironmentID')!r})."
        ]

    attributes: dict[str, Any] = dict(record)
    attributes["display_name"] = _device_display_name(record, natural_key)
    for canonical_key, liongard_field in _DEVICE_CANONICAL_FIELDS.items():
        value = record.get(liongard_field)
        if value:
            attributes[canonical_key] = value

    subtype_warning = _resolve_device_subtype(attributes)
    if subtype_warning:
        warnings.append(subtype_warning)
    warnings.extend(_resolve_mac_addresses(attributes))

    entity = CanonicalEntity(
        entity_type=EntityType.DEVICE,
        natural_key=natural_key,
        attributes=attributes,
        status=EntityStatus.ACTIVE,
        in_boundary=True,
        source=Source.LIONGARD,
        source_ref=source_ref,
    )
    return entity, warnings


def _identity_natural_key(record: dict[str, Any]) -> str:
    email = record.get("Email")
    if email and str(email).strip():
        return str(email).strip()
    username = record.get("Username")
    if username and str(username).strip():
        return str(username).strip()
    return str(record.get("DisplayName") or "").strip()


def identity_to_canonical(
    record: dict[str, Any], source_ref: str
) -> tuple[CanonicalEntity | None, list[str]]:
    """One Liongard Identities row -> CanonicalEntity(PERSON).

    Natural key: Email (Liongard's own account-grouping key -- "Identities
    are groups of accounts with the same email address", per Liongard's
    docs) preferred, Username fallback (several inspectors -- e.g. Duo --
    return identities with a blank Email and a populated Username), then
    DisplayName as a last resort. Returns (None, warnings) if none of the
    three yield anything to key on.

    All of Type ("user"/"service"/"shared"/None), Privileged, Enabled,
    Status and AccountActivity are preserved verbatim under their Liongard
    names -- this connector doesn't attempt to bifurcate service accounts
    into a different EntityType; ROADMAP.md D.2 asks for identities to map
    to EntityType.PERSON, full stop, so that classification work is left
    for whoever needs it next rather than guessed at here.
    """
    natural_key = _identity_natural_key(record)
    if not natural_key:
        return None, [
            "Liongard identity record has no Email, Username, or DisplayName -- skipped "
            f"(EnvironmentID={record.get('EnvironmentID')!r})."
        ]

    entity = CanonicalEntity(
        entity_type=EntityType.PERSON,
        natural_key=natural_key,
        attributes=dict(record),
        status=(
            EntityStatus.DECOMMISSIONED if record.get("Enabled") is False else EntityStatus.ACTIVE
        ),
        in_boundary=True,
        source=Source.LIONGARD,
        source_ref=source_ref,
    )
    return entity, []


def _identity_display_name(record: dict[str, Any]) -> str:
    display_name = record.get("DisplayName")
    if display_name and str(display_name).strip():
        return str(display_name).strip()
    first, last = record.get("FirstName"), record.get("LastName")
    if first or last:
        return f"{first or ''} {last or ''}".strip()
    email = record.get("Email")
    if email and str(email).strip():
        return str(email).strip()
    return str(record.get("Username") or "").strip()


def identity_to_contact_fields(record: dict[str, Any]) -> dict[str, Any]:
    """One raw Liongard identity record -> prefill fields for the Contact
    import wizard (routers/contacts.py). A DIFFERENT mapping from
    identity_to_canonical() above -- that one feeds scope_entity (the scope
    graph); this one feeds Contact (RACI/documentation-role/SPRS-submitter/
    review-cycle target). The two models are deliberately not merged (see
    this module's own docstring and Contact's docstring in models.py), so
    this function must never be called from anywhere identity_to_canonical
    is, and must never write to scope_entity.

    Field reliability, checked against a real tenant (Goodwin-Bradley
    environment, 24 live Inventory-state identities, 2026-09-14) rather than
    assumed from Liongard's docs: Email/FirstName/LastName/DisplayName are
    reliably present (100% of records sampled); Phone is essentially never
    populated (0/24). No job-title-equivalent field exists anywhere in the
    schema -- role_title is therefore always None here; the Contact import
    wizard offers it as a plain admin-typed field with no Liongard prefill,
    never a silently-set value.

    email is intentionally not defaulted to "" or similar -- a missing
    email must surface to the caller as None so the import UI can flag the
    identity as "no email, can't become a contact" rather than silently
    treat it as importable. Never returns None itself; even an identity
    with no email still gets a best-effort name so it can be listed and
    visibly explained, per the task's "filter or flag clearly, don't fail
    the whole import."
    """
    email = record.get("Email")
    email = str(email).strip() if email and str(email).strip() else None
    phone = record.get("Phone")
    phone = str(phone).strip() if phone and str(phone).strip() else None
    return {
        "email": email,
        "name": _identity_display_name(record),
        "phone": phone,
        "role_title": None,
    }


def devices_to_canonical(
    records: list[dict[str, Any]], source_ref: str
) -> tuple[list[CanonicalEntity], dict[tuple[str, str], list[str]]]:
    entities: list[CanonicalEntity] = []
    warnings: dict[tuple[str, str], list[str]] = {}
    skipped: list[str] = []
    for record in records:
        entity, entity_warnings = device_profile_to_canonical(record, source_ref)
        if entity is None:
            skipped.extend(entity_warnings)
            continue
        entities.append(entity)
        for w in entity_warnings:
            _warn(warnings, entity, w)
    if skipped:
        # Skipped-entirely rows have no entity to key warnings against --
        # surfaced under a sentinel key the caller can render separately,
        # same "never silently drop" discipline as every per-row warning.
        warnings[("_skipped", "_skipped")] = skipped
    return entities, warnings


def identities_to_canonical(
    records: list[dict[str, Any]], source_ref: str
) -> tuple[list[CanonicalEntity], dict[tuple[str, str], list[str]]]:
    entities: list[CanonicalEntity] = []
    warnings: dict[tuple[str, str], list[str]] = {}
    skipped: list[str] = []
    for record in records:
        entity, entity_warnings = identity_to_canonical(record, source_ref)
        if entity is None:
            skipped.extend(entity_warnings)
            continue
        entities.append(entity)
        for w in entity_warnings:
            _warn(warnings, entity, w)
    if skipped:
        warnings.setdefault(("_skipped", "_skipped"), []).extend(skipped)
    return entities, warnings


def build_source_ref(environment_id: int, environment_name: str | None, pulled_at: str) -> str:
    """Provenance string for entities from one Liongard sync -- identifies
    the actual pull (which Environment, when), not an incidental artifact
    like a temp filename (see importers/workbook.py's own history of that
    mistake, called out in ROADMAP.md D.2).
    """
    label = environment_name or str(environment_id)
    return f"liongard:environment={environment_id} ({label}):pulled_at={pulled_at}"
