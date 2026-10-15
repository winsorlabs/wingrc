"""List library persistence -- roadmap L.1.

One way to change a list's content: `create_version()`. The importer and
the edit API both go through it, so "editing creates a version, it never
mutates one" holds for every writer, including ones not written yet
(migration 0064's trigger backs it up in the database).

Import is idempotent: `diff_import()` compares each planned definition's
content against its current version and proposes create / new_version /
unchanged. Re-importing the same folder proposes nothing.

Every version says why it exists (`provenance["cause"]`, `CAUSES`): a
person's edit, the importer reading a changed source, or the importer
itself changing while the source did not. The last recurs -- every
importer change that alters what it reads writes a version of every list
it touches -- and without the distinction it reads as someone having
changed the template.
"""

from __future__ import annotations

import os
import uuid
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
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


# Why a list version exists. Stored on every version written from
# 2026-10-14 on, and derived the same way (`version_causes`) for the ones
# before, from what each recorded -- the history is append-only, so the
# derivation cannot drift.
CAUSES = {
    "edit": "a person saved it",
    "first_import": "the first import of its source",
    "source_changed": "the import read a changed source",
    "importer_changed": "WinGRC's importer changed; the source did not",
    "source_reapplied": "the import put the unchanged source back over a later edit",
}


def import_cause(
    source_sha256: str, sheet_sha256: str, content: dict, last_import: ListVersion | None
) -> str:
    """Why an import writes a new version, judged against the last version
    an import wrote. Same source in and different content out means the
    importer changed. Compares the sheet's own cells when both versions
    recorded them, else the workbook file (versions before 2026-10-14)."""
    if last_import is None:
        return "first_import"
    prior = last_import.provenance or {}
    if sheet_sha256 and prior.get("sheet_sha256"):
        same_source = sheet_sha256 == prior["sheet_sha256"]
    else:
        same_source = source_sha256 == prior.get("source_sha256")
    if not same_source:
        return "source_changed"
    return "source_reapplied" if content == content_of(last_import) else "importer_changed"


def version_causes(history: Sequence[ListVersion]) -> dict[uuid.UUID, str]:
    """Each version's cause, oldest first: the stored one, else derived."""
    out: dict[uuid.UUID, str] = {}
    last_import = None
    for v in sorted(history, key=lambda v: v.version_number):
        p = v.provenance or {}
        if p.get("via") == "api":
            out[v.id] = p.get("cause") or "edit"
            continue
        out[v.id] = p.get("cause") or import_cause(
            p.get("source_sha256", ""), p.get("sheet_sha256", ""), content_of(v), last_import
        )
        last_import = v
    return out


def history_of(session: Session, list_id: uuid.UUID) -> list[ListVersion]:
    return list(
        session.scalars(
            select(ListVersion)
            .where(ListVersion.list_id == list_id)
            .order_by(ListVersion.version_number, ListVersion.id)
        )
    )


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


@dataclass(frozen=True)
class ListVersionSnap:
    """One list version, copied out for an export (roadmap L.3).

    Named by `version_id`, never by the list: a list moves on, a version
    does not (migration 0064 refuses an UPDATE of list_version). Everything
    a rendered workbook shows comes from here, and nothing here is a
    wall-clock time, so the workbook is a function of the version alone.
    """

    list_id: str
    list_key: str
    is_template: bool
    control_keys: tuple[str, ...]
    version_id: str
    version_number: int
    title: str
    description: str
    responsible: str
    review_cadence: str
    columns: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]
    saved_at: datetime
    saved_by: str | None

    @property
    def filename(self) -> str:
        return f"{self.list_key}_v{self.version_number}.xlsx"


def snap_version(
    definition: ListDefinition, version: ListVersion, control_keys: tuple[str, ...]
) -> ListVersionSnap:
    return ListVersionSnap(
        list_id=str(definition.id),
        list_key=definition.list_key,
        is_template=definition.is_template,
        control_keys=tuple(control_keys),
        version_id=str(version.id),
        version_number=version.version_number,
        title=version.title,
        description=version.description or "",
        responsible=version.responsible or "",
        review_cadence=version.review_cadence or "",
        columns=tuple(version.columns),
        rows=tuple(tuple(r) for r in version.rows),
        saved_at=version.created_at,
        saved_by=(version.provenance or {}).get("saved_by"),
    )


@dataclass
class ImportAction:
    sheet: SheetResult
    action: str  # create | new_version | unchanged | conflict
    definition: ListDefinition | None = None
    changed_fields: tuple[str, ...] = ()
    version: ListVersion | None = None  # what an apply wrote
    # conflict: the person's edit the import would replace
    edit: ListVersion | None = None

    @property
    def edit_ref(self) -> str:
        """How an operator names this edit to authorize replacing it:
        the list and the exact version. A later save is a different edit,
        which the same authorization does not cover."""
        return f"{self.sheet.list_key}@v{self.edit.version_number}"


class ImportRefused(ValueError):
    """An apply named an edit to overwrite that is not a current conflict."""


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
        if set(changed) - {"control_tags"} and (current.provenance or {}).get("via") == "api":
            # A person edited this list since it was imported. The import has
            # a source for the template, not for their edit -- the same rule
            # as a sync never overwriting a field it has no source for -- so
            # it is a conflict, skipped unless that exact edit is named.
            diff.actions.append(ImportAction(s, "conflict", d, changed, edit=current))
            continue
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
        "actions": {
            k: len(diff.of(k)) for k in ("create", "new_version", "unchanged", "conflict")
        },
        # Lists left as they were because a person edited them since import.
        "conflicts": [
            {
                "list_key": a.sheet.list_key,
                "edit": a.edit_ref,
                "edit_version_id": str(a.edit.id),
                "saved_by": (a.edit.provenance or {}).get("saved_by"),
                "saved_at": a.edit.created_at.isoformat(),
                "changed_fields": list(a.changed_fields),
            }
            for a in diff.of("conflict")
        ],
        # Edits an apply replaced, each named by the operator.
        "overwrote_edits": [
            a.edit_ref for a in diff.actions if a.edit is not None and a.action == "new_version"
        ],
    }


def record_import(
    session: Session, org_id: uuid.UUID, plan: ImportPlan, diff: ImportDiff
) -> None:
    """Audits an apply, a no-op one included. The library screen reads the
    latest of these to say what was never imported and why
    (routers/list_definitions.py's import-report). Each version written is
    named with its cause, so the row says whether the source or the
    importer changed."""
    versions = [
        {
            "list_key": a.definition.list_key,
            "version_id": str(a.version.id),
            "version_number": a.version.version_number,
            "cause": a.version.provenance["cause"],
            "changed_fields": list(a.changed_fields),
        }
        for a in diff.actions
        if a.version is not None
    ]
    log_event(
        session, org_id=org_id, action="list.import", entity_type="organization",
        entity_id=org_id,
        context={
            "via": "cli", "importer_build": _build(), "versions": versions,
            **import_report(plan, diff),
        },
        actor="cli:lists-import", actor_type="system",
    )


def _build() -> str:
    return os.environ.get("WINGRC_BUILD_SHA", "unknown")


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
        "sheet_sha256": s.sheet_sha256,
        "importer_build": _build(),
        "merged_from": list(s.absorbed),
        "changelogs": changelogs,
    }


def apply_import(
    session: Session,
    org_id: uuid.UUID,
    plan: ImportPlan,
    changelog_root: Path | None = None,
    overwrite_edits: frozenset[str] = frozenset(),
) -> ImportDiff:
    """Writes what `diff_import` proposes. Templates: every definition the
    import creates is `is_template` -- the rows are illustrative examples
    (decision 2026-10-10).

    A conflict -- a list a person edited since import -- is skipped, and
    written only when its exact edit is named in `overwrite_edits`
    (`ImportAction.edit_ref`, "list_key@vN", as the dry run prints it).
    There is no "all": each replaced edit is authorized by name, as an
    asset is approved one at a time. A name that is not a current conflict
    -- a typo, or an edit someone has since saved over -- refuses the whole
    apply before anything is written; it is never guessed at."""
    diff = diff_import(session, org_id, plan)
    conflicts = {a.edit_ref: a for a in diff.of("conflict")}
    unknown = sorted(set(overwrite_edits) - set(conflicts))
    if unknown:
        raise ImportRefused(
            f"not a current conflict: {', '.join(unknown)}; current conflicts: "
            f"{', '.join(sorted(conflicts)) or 'none'}. Nothing was written."
        )
    for ref in overwrite_edits:
        conflicts[ref].action = "new_version"
    for a in diff.actions:
        if a.action in ("unchanged", "conflict"):
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
            content = _planned_content(s)
            history = [] if a.action == "create" else history_of(session, a.definition.id)
            imports = [v for v in history if (v.provenance or {}).get("via") != "api"]
            last_import = imports[-1] if imports else None
            provenance = _provenance(s, changelog_root) | {
                "cause": import_cause(s.file_sha256, s.sheet_sha256, content, last_import),
                "changed_fields": list(a.changed_fields),
            }
            if last_import is not None:
                provenance["previous_import_version"] = last_import.version_number
            if history and (history[-1].provenance or {}).get("via") == "api":
                # Says so plainly: an import is about to replace a person's edit.
                provenance["replaces_edit_in_version"] = history[-1].version_number
            a.version = create_version(session, a.definition, content, provenance)
        set_tags(session, a.definition, s.control_keys)
    return diff
