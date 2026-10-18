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

A list can also be built by hand (L.2.2, `create_by_hand`): the same
definition schema the template header block fills, with its own cause --
or adopted from a template (L.7, `list_adoption`), a copy that records the
template version it came from.

Tags are not version content. Each records who put it there
(`ListControlTag.added_via`) and every change is a `ListTagEvent`, so a
list's history answers "since when, and who said so" for its tags as it
does for its rows. An import manages only the tags it added. Removing a
tag is refused while evidence rests on it (`tag_removal_blockers`, which
reads the evidence links L.4 makes -- list_evidence.py).
"""

from __future__ import annotations

import os
import re
import uuid
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .audit import log_event
from .list_templates import MERGES, ImportPlan, MergeRule, SheetResult, slug
from .models import (
    Assessment,
    AssessmentObjective,
    Control,
    ControlState,
    Evidence,
    EvidenceStateLink,
    ListControlTag,
    ListDefinition,
    ListTagEvent,
    ListVersion,
)

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
    # Not first_import: there was no source. Saying so is the point of a cause.
    "created_by_hand": "a person built it in WinGRC, from nothing",
    # A copy of a template, or a later move onto a newer template version
    # (list_adoption). Its provenance names the template and the version.
    "adopted_from_template": "a person copied it from a template",
}

# A template's example rows carry a fill-in token: `[PLACEHOLDER]`, or one
# of the family the real library also uses -- `[CUSTOMER_NAME]`,
# `[CUSTOMER]`, `[SERVER]`, `[DATE]`, `[CUI_ACCESS_GROUP]` -- often inside
# a value ("[CUSTOMER]-Admin-Role", "jdoe@[CUSTOMER]"). Any such token
# anywhere in a cell leaves the row an example. Not a token:
# `[PLACEHOLDER - reason]`, a filled document's "knowably absent, and here
# is why" (docs/PLAN-list-library.md section 3), and lowercase brackets such
# as an objective's `[a]`, which are real values.
EXAMPLE_TOKEN = re.compile(r"\[[A-Z][A-Z0-9_]*\]")


def is_example_row(row: Sequence[str]) -> bool:
    return any(EXAMPLE_TOKEN.search(cell) for cell in row)


def example_rows(rows: Sequence[Sequence[str]]) -> int:
    return sum(1 for r in rows if is_example_row(r))


def is_populated(rows: Sequence[Sequence[str]]) -> bool:
    """PLAN section 5's definition, stated once: a list with rows, none of
    which is still a template's example. Only a populated list may be
    offered as evidence -- list_evidence.not_offered_reason uses this, and
    nothing restates it (L.4). An adopted copy starts unpopulated because
    its rows are the template's examples, and becomes a candidate when the
    org has filled them in."""
    return bool(rows) and example_rows(rows) == 0


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


@dataclass(frozen=True)
class RestingLink:
    """One live evidence link a tag removal would narrow, as fields, so a
    screen writes its own sentence (L.10's finding: a sentence built for a
    log names the list by key and says the fact three times on screen).
    Shared by lists and documents (item X.1)."""

    version_number: int
    objective_key: str  # in full: "PE.L2-3.10.6[b]"
    assessment_name: str

    def __str__(self) -> str:
        return (
            f"version {self.version_number} linked to {self.objective_key} "
            f"in assessment '{self.assessment_name}'"
        )


class TagRemovalRefused(ValueError):
    """Removing this tag would narrow what an assessment rests on. The
    message is for the log, the CLI and the 409's `message`; a screen
    composes its own from `links`."""

    def __init__(self, subject: str, tag: str, links: list[RestingLink]):
        self.subject, self.tag, self.links = subject, tag, links
        self.blockers = [str(link) for link in links]
        super().__init__(
            f"{tag} cannot be removed from {subject}: evidence rests on it "
            f"({'; '.join(self.blockers)}). Unlink that evidence first."
        )


def tag_removal_blockers(session: Session, list_id: uuid.UUID, control_key: str) -> list[str]:
    """`tag_removal_links` as the sentences an import's or adoption's
    refusal joins -- an empty list means the tag may go."""
    return [str(link) for link in tag_removal_links(session, list_id, control_key)]


def tag_removal_links(
    session: Session, list_id: uuid.UUID, control_key: str
) -> list[RestingLink]:
    """The evidence links that rest on this list serving `control_key`, each
    named so the refusal can say which -- an empty list means the tag may go.

    The rule (L.2.2, decided before any link existed): a list linked as
    evidence covers the controls it is tagged with (docs/PLAN-list-library
    .md section 5), so removing a tag while a link to that control's state
    exists would silently narrow what an assessment rests on. Adding is
    always safe; removing is refused while such a link exists, by a person,
    an import and an adopted copy taking a template version alike.

    A link is every non-archived EvidenceStateLink from evidence naming any
    version of this list (L.4, list_evidence.py) to a control_state of
    `control_key`'s practice. Archived links do not block: the only thing
    that archives a list link is moving it forward, which leaves a live one
    in its place -- a product's deactivation never does (engine.py)."""
    rows = session.execute(
        select(
            ListVersion.version_number, Assessment.name, AssessmentObjective.objective_key,
        )
        .select_from(EvidenceStateLink)
        .join(Evidence, Evidence.id == EvidenceStateLink.evidence_id)
        .join(ListVersion, ListVersion.id == Evidence.source_list_version_id)
        .join(ControlState, ControlState.id == EvidenceStateLink.control_state_id)
        .join(Assessment, Assessment.id == ControlState.assessment_id)
        .join(AssessmentObjective, AssessmentObjective.id == ControlState.objective_id)
        .join(Control, Control.id == AssessmentObjective.control_id)
        .where(
            ListVersion.list_id == list_id,
            Control.control_id == control_key,
            EvidenceStateLink.is_archived.is_(False),
        )
        .order_by(Assessment.name, AssessmentObjective.objective_key, EvidenceStateLink.id)
    ).all()
    return [
        RestingLink(r.version_number, f"{control_key}[{r.objective_key}]", r.name) for r in rows
    ]


def tag_origins(session: Session, list_id: uuid.UUID) -> dict[str, str]:
    return dict(
        session.execute(
            select(ListControlTag.control_key, ListControlTag.added_via)
            .where(ListControlTag.list_id == list_id)
            .order_by(ListControlTag.control_key)
        ).all()
    )


def _tag_event(
    session: Session, definition: ListDefinition, key: str, change: str, via: str,
    saved_by: str | None,
) -> ListTagEvent:
    current = (
        session.get(ListVersion, definition.current_version_id)
        if definition.current_version_id
        else None
    )
    event = ListTagEvent(
        list_id=definition.id, org_id=definition.org_id, control_key=key, change=change,
        via=via, saved_by=saved_by, at_version=current.version_number if current else None,
    )
    session.add(event)
    return event


def add_tag(
    session: Session, definition: ListDefinition, key: str, via: str, saved_by: str | None
) -> ListTagEvent | None:
    """Adds one tag, recorded. None when the list already carries it."""
    if key in tag_origins(session, definition.id):
        return None
    session.add(
        ListControlTag(
            list_id=definition.id, org_id=definition.org_id, control_key=key, added_via=via
        )
    )
    event = _tag_event(session, definition, key, "added", via, saved_by)
    session.flush()
    return event


def remove_tag(
    session: Session, definition: ListDefinition, key: str, via: str, saved_by: str | None
) -> ListTagEvent | None:
    """Removes one tag, recorded -- refused while evidence rests on it.
    None when the list does not carry it."""
    if key not in tag_origins(session, definition.id):
        return None
    links = tag_removal_links(session, definition.id, key)
    if links:
        raise TagRemovalRefused(definition.list_key, key, links)
    session.execute(
        delete(ListControlTag).where(
            ListControlTag.list_id == definition.id, ListControlTag.control_key == key
        )
    )
    event = _tag_event(session, definition, key, "removed", via, saved_by)
    session.flush()
    return event


def import_tag_changes(
    session: Session,
    definition: ListDefinition,
    control_keys: tuple[str, ...],
    via: str = "template_import",
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """What an import changes in a list's tags: (add, remove). It adds what
    the source's row 2 names and the list lacks, and removes only tags an
    import added that row 2 no longer names. A person's tag is never the
    import's to remove -- before 2026-10-15 every import replaced the whole
    set, so the next one would have silently dropped it. `via` is whose
    tags these are: an adopted copy taking a newer template version manages
    the template's (`template_adoption`) by the same rule."""
    have = tag_origins(session, definition.id)
    add = tuple(k for k in control_keys if k not in have)
    remove = tuple(k for k, origin in have.items() if origin == via and k not in control_keys)
    return add, remove


def tag_events(session: Session, list_id: uuid.UUID) -> list[ListTagEvent]:
    return list(
        session.scalars(
            select(ListTagEvent)
            .where(ListTagEvent.list_id == list_id)
            .order_by(ListTagEvent.seq)
        )
    )


class ListKeyTaken(ValueError):
    def __init__(self, existing: ListDefinition):
        self.existing = existing
        super().__init__(f"this org already has a list keyed {existing.list_key!r}")


def create_by_hand(
    session: Session,
    org_id: uuid.UUID,
    content: dict,
    control_keys: tuple[str, ...],
    saved_by: str,
    origin_note: str = "",
) -> ListDefinition:
    """A list a person builds in WinGRC: the fields the template header
    block holds (title, row 2's practices, What goes here, Responsible,
    Review cadence, row 10's columns), and nothing an import would not.

    Keyed as an import keys a sheet -- the slugged name -- so one key space
    serves both. v1 is `created_by_hand` and `via: api`: it is a person's
    content, so an import that later produces the same key finds an edit
    and reports a conflict instead of writing over it.

    `origin_note` is a person's free text about where the content came from
    ("lifted from our old SharePoint sheet"). It is not lineage: an
    imported list's lineage is `source_file` and `source_sha256`, which the
    product computed, and the two are kept apart on purpose."""
    key = slug(content["title"])
    existing = session.scalar(
        select(ListDefinition).where(
            ListDefinition.org_id == org_id, ListDefinition.list_key == key
        )
    )
    if existing is not None:
        raise ListKeyTaken(existing)
    definition = ListDefinition(org_id=org_id, list_key=key, is_template=False)
    session.add(definition)
    session.flush()
    provenance = {"via": "api", "cause": "created_by_hand", "saved_by": saved_by}
    if origin_note.strip():
        provenance["origin_note"] = origin_note.strip()
    create_version(session, definition, content, provenance)
    for k in control_keys:
        add_tag(session, definition, k, "api", saved_by)
    return definition


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
        if any(import_tag_changes(session, d, s.control_keys)):
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
    blocked = [
        f"{a.definition.list_key}: {k} ({'; '.join(b)})"
        for a in diff.of("new_version")
        for k in import_tag_changes(session, a.definition, a.sheet.control_keys)[1]
        if (b := tag_removal_blockers(session, a.definition.id, k))
    ]
    if blocked:
        raise ImportRefused(
            "the source no longer names a practice that evidence rests on: "
            f"{', '.join(blocked)}. Unlink that evidence first. Nothing was written."
        )
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
        add, remove = import_tag_changes(session, a.definition, s.control_keys)
        for k in add:
            add_tag(session, a.definition, k, "template_import", None)
        for k in remove:
            remove_tag(session, a.definition, k, "template_import", None)
    return diff
