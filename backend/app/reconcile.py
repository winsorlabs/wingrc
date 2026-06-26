"""Reconciliation: compare an incoming import against the current scope graph
and produce a reviewable diff. Nothing is written until an engineer confirms
the result — this is what keeps an automated feed from silently moving the
audit boundary, and what turns a manual monthly reconcile into a reviewed one.
"""

from __future__ import annotations

from .domain import (
    CanonicalEntity,
    ChangeType,
    EntityChange,
    EntityType,
    ReconcileResult,
)


def _field_diffs(
    current: CanonicalEntity, incoming: CanonicalEntity
) -> dict[str, tuple]:
    diffs: dict[str, tuple] = {}
    keys = set(current.attributes) | set(incoming.attributes)
    for k in keys:
        a = current.attributes.get(k)
        b = incoming.attributes.get(k)
        if str(a) != str(b):
            diffs[k] = (a, b)
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
