"""List library persistence -- roadmap L.1.

One way to change a list's content: `create_version()`. The importer and
the edit API both go through it, so "editing creates a version, it never
mutates one" holds for every writer, including ones not written yet
(migration 0064's trigger backs it up in the database).

Import is idempotent: `diff_import()` compares each planned definition's
content against its current version and proposes create / new_version /
unchanged. Re-importing the same folder proposes nothing.
"""

from __future__ import annotations

import uuid
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .audit import log_event
from .list_templates import MERGES, ImportPlan, MergeRule, SheetResult
from .models import ListControlTag, ListDefinition, ListVersion

CONTENT_FIELDS = ("title", "description", "responsible", "review_cadence", "columns", "rows")


def content_of(version: ListVersion) -> dict:
    return {
        "title": version.title,
        "description": version.description or "",
        "responsible": version.responsible or "",
        "review_cadence": version.review_cadence or "",
        "columns": list(version.columns),
        "rows": [list(r) for r in version.rows],
    }


def _planned_content(s: SheetResult) -> dict:
    return {
        "title": s.title,
        "description": s.description,
        "responsible": s.responsible,
        "review_cadence": s.review_cadence,
        "columns": list(s.columns),
        "rows": [list(r) for r in s.rows],
    }


def create_version(
    session: Session, definition: ListDefinition, content: dict, provenance: dict
) -> ListVersion:
    next_number = (
        session.scalar(
            select(func.max(ListVersion.version_number)).where(
                ListVersion.list_id == definition.id
            )
        )
        or 0
    ) + 1
    version = ListVersion(
        list_id=definition.id,
        org_id=definition.org_id,
        version_number=next_number,
        provenance=provenance,
        **{k: content[k] for k in CONTENT_FIELDS},
    )
    session.add(version)
    session.flush()
    definition.current_version_id = version.id
    session.flush()
    return version


def set_tags(session: Session, definition: ListDefinition, control_keys: tuple[str, ...]) -> None:
    session.execute(delete(ListControlTag).where(ListControlTag.list_id == definition.id))
    for key in control_keys:
        session.add(
            ListControlTag(list_id=definition.id, org_id=definition.org_id, control_key=key)
        )
    session.flush()


def tags_of(session: Session, list_id: uuid.UUID) -> tuple[str, ...]:
    return tuple(
        session.scalars(
            select(ListControlTag.control_key)
            .where(ListControlTag.list_id == list_id)
            .order_by(ListControlTag.control_key)
        )
    )


@dataclass
class ImportAction:
    sheet: SheetResult
    action: str  # create | new_version | unchanged
    definition: ListDefinition | None = None
    changed_fields: tuple[str, ...] = ()


@dataclass
class ImportDiff:
    actions: list[ImportAction] = field(default_factory=list)

    def of(self, action: str) -> list[ImportAction]:
        return [a for a in self.actions if a.action == action]


def diff_import(session: Session, org_id: uuid.UUID, plan: ImportPlan) -> ImportDiff:
    existing = {
        d.list_key: d
        for d in session.scalars(select(ListDefinition).where(ListDefinition.org_id == org_id))
    }
    diff = ImportDiff()
    for s in plan.definitions:
        d = existing.get(s.list_key)
        if d is None:
            diff.actions.append(ImportAction(s, "create"))
            continue
        current = session.get(ListVersion, d.current_version_id) if d.current_version_id else None
        planned = _planned_content(s)
        have = content_of(current) if current else {}
        changed = tuple(k for k in CONTENT_FIELDS if have.get(k) != planned[k])
        if tuple(sorted(s.control_keys)) != tuple(sorted(tags_of(session, d.id))):
            changed += ("control_tags",)
        diff.actions.append(
            ImportAction(s, "new_version" if changed else "unchanged", d, changed)
        )
    return diff


def merge_rule_for(merged_from: list[str]) -> MergeRule | None:
    """The recorded merge that produced a list, found by the sheet its
    provenance says it absorbed. The mapping is the code-recorded decision
    (list_templates.MERGES) -- the import refuses any absorbed column it
    does not name, so there is no second, guessed mapping to disagree with."""
    for rule in MERGES:
        if f"{rule.absorbed[0]}#{rule.absorbed[1]}" in merged_from:
            return rule
    return None


def row_changes(before: list[list[str]], after: list[list[str]]) -> dict:
    """Rows added and removed between two versions, by content. Rows have no
    identity key (see ListVersion), so an edited row counts as one removed
    and one added -- the audit says what changed, not which row "it" was."""
    b = Counter(tuple(r) for r in before)
    a = Counter(tuple(r) for r in after)
    return {
        "rows_added": sum((a - b).values()),
        "rows_removed": sum((b - a).values()),
    }


def import_report(plan: ImportPlan, diff: ImportDiff) -> dict:
    """What an apply did and, as importantly, what it did not import -- kept
    so the library screen can say the set is incomplete instead of silently
    claiming to be the whole library. File paths only, relative to the
    library root: no cell content."""
    return {
        "not_imported": [
            {"source_ref": s.source_ref, "status": s.status, "reason": s.reason}
            for s in plan.sheets
            if s.status != "importable"
        ],
        "not_ingested": list(plan.not_ingested),
        "actions": {k: len(diff.of(k)) for k in ("create", "new_version", "unchanged")},
    }


def record_import(
    session: Session, org_id: uuid.UUID, plan: ImportPlan, diff: ImportDiff
) -> None:
    """Audits an apply, a no-op one included. The library screen reads the
    latest of these to say what was never imported and why
    (routers/list_definitions.py's import-report)."""
    log_event(
        session, org_id=org_id, action="list.import", entity_type="organization",
        entity_id=org_id, context={"via": "cli", **import_report(plan, diff)},
        actor="cli:lists-import", actor_type="system",
    )


def _provenance(s: SheetResult, changelog_root: Path | None) -> dict:
    changelogs = []
    for name in s.changelogs:
        text = ""
        if changelog_root is not None:
            p = changelog_root / name
            if p.is_file():
                text = p.read_text(encoding="utf-8", errors="replace")
        changelogs.append({"path": f"Changelog/Lists/{name}", "text": text})
    return {
        "via": "template_import",
        "source_file": f"New Lists/{s.file}",
        "source_sheet": s.sheet,
        "source_sha256": s.file_sha256,
        "merged_from": list(s.absorbed),
        "changelogs": changelogs,
    }


def apply_import(
    session: Session,
    org_id: uuid.UUID,
    plan: ImportPlan,
    changelog_root: Path | None = None,
) -> ImportDiff:
    """Writes what `diff_import` proposes. Templates: every definition the
    import creates is `is_template` -- the rows are illustrative examples
    (decision 2026-10-10)."""
    diff = diff_import(session, org_id, plan)
    for a in diff.actions:
        if a.action == "unchanged":
            continue
        s = a.sheet
        if a.action == "create":
            a.definition = ListDefinition(
                org_id=org_id,
                list_key=s.list_key,
                is_template=True,
                source_ref=s.source_ref,
                projection_view_id=s.projection_view_id,
            )
            session.add(a.definition)
            session.flush()
        if a.action == "create" or set(a.changed_fields) - {"control_tags"}:
            create_version(
                session, a.definition, _planned_content(s), _provenance(s, changelog_root)
            )
        set_tags(session, a.definition, s.control_keys)
    return diff
