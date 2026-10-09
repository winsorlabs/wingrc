"""Reconciliation: compare an incoming import against the current scope graph
and produce a reviewable diff. Nothing is written until an engineer confirms
the result — this is what keeps an automated feed from silently moving the
audit boundary, and what turns a manual monthly reconcile into a reviewed one.

**Meaningful-attributes allowlist (2026-09-18).** `_field_diffs()` used to
compare the full union of `current.attributes`/`incoming.attributes` keys.
For DEVICE/SOFTWARE that meant comparing the *entire* raw Liongard record
(`importers/liongard.py:device_profile_to_canonical()` stores it wholesale
for provenance), including fields that change on every single pull
regardless of whether the device itself changed at all --
`LastSeenTimelineID`, `LastSeen`, `UpdatedOn`, `AvailableStorage`, etc.
Reproduced directly before this fix: two pulls of the identical device,
differing only in those fields, reconciled to CHANGED. At a real
environment's size that's every device, every sync, permanently --
exactly the failure this fix closes.

**Allowlist, not a denylist, deliberately.** A denylist fails open: a new
Liongard field appears, nobody excludes it, the noise silently returns. An
allowlist fails closed -- an unrecognized field is simply never compared,
which is the safe default for a compliance tool. The cost (a genuinely
new, meaningful field goes undetected until someone adds it to the
allowlist) is real and is NOT left invisible:
`importers/liongard.py:device_profile_to_canonical()` separately warns
when a pull contains attribute keys outside both the allowlist and its own
known-volatile-field list, surfaced in the dry-run result per row -- see
that function's own docstring. That warning is a Liongard-specific
concern (only Liongard's importer knows Liongard's own field names), kept
out of this module on purpose; this module only needs the allowlist
itself, not the reasoning behind every individual field name.

**Per entity type, not a single flat list.** DEVICE/SOFTWARE compare
`domain.py:DEVICE_SOFTWARE_COMPARABLE_ATTRIBUTES` (derived from
`routers/scope.py:DeviceSoftwareAttributes` -- one list, not two that
drift, kept in sync by test_domain_attribute_vocabulary.py). PERSON
compares `domain.py:PERSON_COMPARABLE_ATTRIBUTES` (added once a canonical
PERSON vocabulary existed to compare against -- see that constant's own
docstring for what it covers and why it's deliberately minimal). PROCESS,
EXTERNAL_SERVICE, FACILITY, and DATA_STORE still fall back to comparing
every attribute key -- none of them have a canonical vocabulary defined
yet, so there is nothing narrower to compare against; the identical
telemetry-diff-noise problem DEVICE/SOFTWARE and PERSON have both had
fixed remains open for those, a named, deliberate gap, not an oversight.

**last_login_user is canonical but NOT compared**, on purpose: it's
telemetry that changes legitimately whenever a different person logs into
a device, and comparing it would reintroduce exactly the noise problem
described above, just at a lower frequency. Accepted trade-off: its
*stored* value (still written on every apply -- see the module-level note
above about storing the full raw record regardless of what gets compared)
only actually refreshes in the database when the row is re-applied for
some OTHER, genuinely meaningful reason. A device whose only drift, sync
over sync, is who last logged in will not surface as CHANGED, and its
stored last_login_user can go stale until something else about it
changes. Judged acceptable because the field is explicitly presented
(asset drawer) as "last observed as of the most recent sync that touched
this record," never a live value, and never anything authoritative
(ownership, compliance status) hangs on its freshness.
"""

from __future__ import annotations

from .domain import (
    CONNECTOR_SOURCES,
    DEVICE_SOFTWARE_COMPARABLE_ATTRIBUTES,
    OPERATOR_OVERLAY_ATTRIBUTES,
    PERSON_COMPARABLE_ATTRIBUTES,
    CanonicalEntity,
    ChangeType,
    EntityChange,
    EntityStatus,
    EntityType,
    ReconcileResult,
    connector_supplied_fields,
)

# Entity types with a defined "meaningful attributes" allowlist. Absent
# entity types compare every attribute key -- see this module's own
# docstring for why that's the correct, deliberate fallback rather than a
# gap to close later.
_COMPARABLE_ATTRIBUTES_BY_ENTITY_TYPE: dict[EntityType, frozenset[str]] = {
    EntityType.DEVICE: DEVICE_SOFTWARE_COMPARABLE_ATTRIBUTES,
    EntityType.SOFTWARE: DEVICE_SOFTWARE_COMPARABLE_ATTRIBUTES,
    EntityType.PERSON: PERSON_COMPARABLE_ATTRIBUTES,
}


def _comparable(value: object) -> object:
    """Normalize a value for equality comparison so list-valued attributes
    (e.g. mac_addresses) don't report a spurious CHANGED diff purely because
    two otherwise-identical imports returned their elements in a different
    order -- the single most likely source of noise in the review diff once
    an attribute is list-shaped rather than scalar.
    """
    if isinstance(value, list):
        return sorted(str(v) for v in value)
    return str(value)


def _field_diffs(
    current: CanonicalEntity, incoming: CanonicalEntity
) -> dict[str, tuple]:
    diffs: dict[str, tuple] = {}
    keys = set(current.attributes) | set(incoming.attributes)
    allowlist = _COMPARABLE_ATTRIBUTES_BY_ENTITY_TYPE.get(current.entity_type)
    if allowlist is not None:
        # Overlay fields an import actually carries are real, reviewable
        # changes (a workbook setting Location); the allowlist alone would
        # hide them.
        keys = (keys & allowlist) | (OPERATOR_OVERLAY_ATTRIBUTES & set(incoming.attributes))
    # An overlay field the import does not carry is kept by repo.upsert(),
    # so its absence is not a change -- reporting it would mark every
    # hand-annotated device CHANGED on every sync.
    keys -= OPERATOR_OVERLAY_ATTRIBUTES - set(incoming.attributes)
    for k in keys:
        a = current.attributes.get(k)
        b = incoming.attributes.get(k)
        if _comparable(a) != _comparable(b):
            diffs[k] = (a, b)
    # Status is otherwise not compared (a sync's default status=active says
    # nothing; repo.upsert() keeps the row's). The one status an import can
    # genuinely assert is decommissioned -- a workbook's Decommissioned Date
    # -- and without this a row whose only change was gaining that date
    # reconciled as UNCHANGED and was never applied.
    if (
        incoming.status == EntityStatus.DECOMMISSIONED
        and current.status != EntityStatus.DECOMMISSIONED
    ):
        diffs["status"] = (current.status.value, incoming.status.value)
    return diffs


def reconcile(
    current: list[CanonicalEntity],
    incoming: list[CanonicalEntity],
) -> ReconcileResult:
    """Diff incoming entities against current ones, keyed by (type, natural key)."""
    cur_by_key = {e.key(): e for e in current}
    inc_by_key = {e.key(): e for e in incoming}

    result = ReconcileResult()

    for key, inc in inc_by_key.items():
        cur = cur_by_key.get(key)
        etype = EntityType(key[0])
        if cur is None:
            result.changes.append(
                EntityChange(
                    change_type=ChangeType.NEW,
                    entity_type=etype,
                    natural_key=inc.natural_key,
                    incoming=inc,
                )
            )
        else:
            diffs = _field_diffs(cur, inc)
            result.changes.append(
                EntityChange(
                    change_type=ChangeType.CHANGED if diffs else ChangeType.UNCHANGED,
                    entity_type=etype,
                    natural_key=inc.natural_key,
                    incoming=inc,
                    current=cur,
                    field_diffs=diffs,
                )
            )

    for key, cur in cur_by_key.items():
        if key not in inc_by_key:
            result.changes.append(
                EntityChange(
                    change_type=ChangeType.MISSING,
                    entity_type=EntityType(key[0]),
                    natural_key=cur.natural_key,
                    current=cur,
                )
            )

    return result


def source_precedence_conflicts(result: ReconcileResult) -> dict[tuple[str, str], list[str]]:
    """For a non-connector import (a workbook) against connector-sourced
    rows: drop from each CHANGED row's diff every field a connector supplied,
    and return a conflict message per dropped field, keyed like
    CanonicalEntity.key(). repo.upsert() keeps those fields regardless; this
    makes the dry-run say so instead of showing an edit that won't happen."""
    conflicts: dict[tuple[str, str], list[str]] = {}
    for c in result.changes:
        if c.change_type != ChangeType.CHANGED or c.current is None or c.incoming is None:
            continue
        if c.current.source not in CONNECTOR_SOURCES or c.incoming.source in CONNECTOR_SOURCES:
            continue
        owned = connector_supplied_fields(c.entity_type, c.current.attributes)
        for k in sorted(owned & set(c.field_diffs)):
            current_value, incoming_value = c.field_diffs.pop(k)
            if incoming_value in (None, "", []):
                continue  # the import is silent on it, not disagreeing; the merge keeps it
            conflicts.setdefault(c.current.key(), []).append(
                f"Conflict, not applied: {c.current.source.value} supplies {k}="
                f"{current_value!r}; the import says {incoming_value!r}."
            )
    return conflicts


def possible_person_matches(
    result: ReconcileResult, current: list[CanonicalEntity]
) -> dict[tuple[str, str], list[str]]:
    """A NEW person keyed by name whose name matches an existing person keyed
    by something else (an email). Never merged -- the keys differ, so
    reconcile cannot -- only surfaced for a human to confirm."""
    by_name: dict[str, list[str]] = {}
    for e in current:
        if e.entity_type != EntityType.PERSON:
            continue
        names = {
            str(e.attributes.get("display_name") or ""),
            f"{e.attributes.get('First Name') or ''} {e.attributes.get('Last Name') or ''}",
        }
        for n in names:
            n = " ".join(n.lower().split())
            if n and n != e.natural_key.strip().lower():
                by_name.setdefault(n, []).append(e.natural_key)
    matches: dict[tuple[str, str], list[str]] = {}
    for c in result.changes:
        if c.change_type != ChangeType.NEW or c.entity_type != EntityType.PERSON or not c.incoming:
            continue
        for existing_key in by_name.get(" ".join(c.natural_key.lower().split()), []):
            matches.setdefault(c.incoming.key(), []).append(
                f"Possible match: existing person {existing_key!r} has this name. "
                "Not merged -- confirm by hand before applying."
            )
    return matches
