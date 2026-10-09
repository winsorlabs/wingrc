"""Project scope entities onto a ListView's columns -- the one place a
list cell's value is decided, shared by the .xlsx export (render.py) and
the on-screen list (routers/scope.py), so the two cannot disagree.

Pure: no DB, no storage.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from .catalog import NATURAL_KEY, SCOPE_CATEGORY, ListView
from .domain import CanonicalEntity, placeholder_reason


@dataclass(frozen=True)
class Cell:
    value: str
    # Set when the value is a `[PLACEHOLDER - reason]`: known absent, for
    # this stated reason -- distinct from an empty cell, which means
    # nothing was recorded at all.
    placeholder_reason: str | None = None


@dataclass(frozen=True)
class ProjectedRow:
    natural_key: str
    cells: tuple[Cell, ...]


def _blank(value: Any) -> bool:
    return value is None or value == "" or value == []


def _resolve(entity: CanonicalEntity, source: str) -> Any:
    if source == SCOPE_CATEGORY:
        return entity.scope_category.value if entity.scope_category else None
    if source == NATURAL_KEY:
        return entity.natural_key
    return entity.attributes.get(source)


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def cell_for(view: ListView, entity: CanonicalEntity, attr_key: str) -> Cell:
    for source in view.column_sources(attr_key):
        value = _resolve(entity, source)
        if not _blank(value):
            text = _stringify(value)
            return Cell(text, placeholder_reason(text))
    return Cell("")


def project(view: ListView, entities: list[CanonicalEntity]) -> list[ProjectedRow]:
    """Rows in the order given -- repo.list_entities() already totally
    orders them, which the export's determinism depends on.

    Out-of-boundary entities (in_boundary=False -- e.g. a device a reviewer
    rejected) are excluded: they were never authorized, and these are lists
    of authorized entities. Decommissioned ones stay, with their date --
    the Authorized-Entities workbook has a Decommissioned Date column for
    exactly that. Callers must report excluded_count() alongside, so the
    filter is visible to whoever assesses the list.
    """
    return [
        ProjectedRow(e.natural_key, tuple(cell_for(view, e, key) for key, _ in view.columns))
        for e in entities
        if e.entity_type == view.entity_type and e.in_boundary
    ]


def excluded_count(view: ListView, entities: list[CanonicalEntity]) -> int:
    """How many entities of this view's type project() left out as out of
    boundary."""
    return sum(1 for e in entities if e.entity_type == view.entity_type and not e.in_boundary)


def excluded_note(count: int) -> str:
    if not count:
        return ""
    noun = "entity" if count == 1 else "entities"
    return f"{count} {noun} excluded as out of the CUI boundary (in_boundary = false)."
