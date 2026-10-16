"""Adopting a template into an org's own lists -- roadmap L.7.

Templates live in the MSP's org, confined by RLS, and stay there (the
roadmap's L.7 entry says why). An org that wants one gets a **copy**: a
list of its own that records the template and the template version it was
copied from (`ListDefinition.template_list_id` / `template_list_version_id`)
and does not move when the template does. The caller reads the template
under the source org and writes the copy under the destination org; the
router checks the person holds membership in both, which is what makes
reaching across the boundary legitimate.

A copy's versions say why they exist like every other list's: v1 is
`adopted_from_template`, and taking a newer template version later writes
another version with the same cause, naming the version it moved from.

**Taking a newer template version never overwrites a person's work
unauthorized.** The template's rows are examples; once a tenant has
replaced them they are the tenant's, and an update carries them into the
new columns by name. What an update can overwrite is a definition field
(title, What goes here, Responsible, cadence, columns) a person changed
since the copy last took the template, or filled cells in a column the
template dropped. Either needs that exact edit named -- `list_key@vN`,
the form an import's overwrite takes (`ImportAction.edit_ref`) -- and the
version it replaces keeps every value.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from .list_library import (
    ListKeyTaken,
    add_tag,
    content_of,
    create_version,
    history_of,
    import_tag_changes,
    remove_tag,
    tag_removal_blockers,
    tags_of,
)
from .models import ListDefinition, ListVersion

CAUSE = "adopted_from_template"
TAG_VIA = "template_adoption"
DEFINITION_FIELDS = ("title", "description", "responsible", "review_cadence", "columns")


@dataclass(frozen=True)
class TemplateSnap:
    """A template's current version, copied out under the template's org so
    nothing about it is read again once the session moves to the copy's."""

    list_id: uuid.UUID
    org_id: uuid.UUID
    list_key: str
    version_id: uuid.UUID
    version_number: int
    content: dict
    control_keys: tuple[str, ...]

    def provenance(self) -> dict:
        return {
            "template_list_id": str(self.list_id),
            "template_org_id": str(self.org_id),
            "template_key": self.list_key,
            "template_version_id": str(self.version_id),
            "template_version_number": self.version_number,
            "template_title": self.content["title"],
        }


class NotATemplate(LookupError):
    def __init__(self, list_id: uuid.UUID):
        self.list_id = list_id
        super().__init__(f"{list_id} is not a template in that org")


class AlreadyAdopted(ValueError):
    def __init__(self, existing: ListDefinition):
        self.existing = existing
        super().__init__(f"this org already keeps a copy of that template ({existing.list_key})")


class UpdateRefused(ValueError):
    """Taking a template version was refused; nothing was written."""


def read_template(session: Session, org_id: uuid.UUID, list_id: uuid.UUID) -> TemplateSnap:
    """Call with the session scoped to `org_id`, the template's org."""
    d = session.get(ListDefinition, list_id)
    if d is None or d.org_id != org_id or not d.is_template:
        raise NotATemplate(list_id)
    v = session.get(ListVersion, d.current_version_id)
    return TemplateSnap(
        list_id=d.id, org_id=d.org_id, list_key=d.list_key, version_id=v.id,
        version_number=v.version_number, content=content_of(v),
        control_keys=tags_of(session, d.id),
    )


def copy_key(template_key: str) -> str:
    """One rule wherever the copy lands. A copy never shares its template's
    key: in the template's own org (an MSP that is also a tenant of itself)
    the two must coexist, and anywhere else an import of the same library
    would otherwise read the copy as its own sheet."""
    return f"{template_key[:116]}-own"


def existing_copy(
    session: Session, org_id: uuid.UUID, template_list_id: uuid.UUID
) -> ListDefinition | None:
    return session.scalar(
        select(ListDefinition).where(
            ListDefinition.org_id == org_id, ListDefinition.template_list_id == template_list_id
        )
    )


def adopt(
    session: Session, org_id: uuid.UUID, snap: TemplateSnap, saved_by: str
) -> ListDefinition:
    """The copy: definition, rows and tags as the template has them now.
    The rows are the template's examples (fill-in tokens), so the copy
    starts unfinished and is not populated (`list_library.is_populated`)
    until the org fills it in -- no second mechanism."""
    existing = existing_copy(session, org_id, snap.list_id)
    if existing is not None:
        raise AlreadyAdopted(existing)
    key = copy_key(snap.list_key)
    taken = session.scalar(
        select(ListDefinition).where(
            ListDefinition.org_id == org_id, ListDefinition.list_key == key
        )
    )
    if taken is not None:
        raise ListKeyTaken(taken)
    d = ListDefinition(
        org_id=org_id, list_key=key, is_template=False,
        template_list_id=snap.list_id, template_list_version_id=snap.version_id,
    )
    session.add(d)
    session.flush()
    create_version(
        session, d, snap.content,
        {"via": "api", "cause": CAUSE, "saved_by": saved_by} | snap.provenance(),
    )
    for k in snap.control_keys:
        add_tag(session, d, k, TAG_VIA, saved_by)
    return d


def last_adoption(history: list[ListVersion]) -> ListVersion:
    """The version that last took the template: v1, or a later update."""
    return [v for v in history if (v.provenance or {}).get("cause") == CAUSE][-1]


@dataclass
class UpdatePlan:
    copy: ListDefinition
    current: ListVersion
    snap: TemplateSnap
    based_on: int
    content: dict
    rows_from: str  # "template": still the examples it copied; "copy": the org's own
    changed_fields: list[str]
    columns_added: list[str]
    columns_removed: list[str]
    dropped_cells: int
    # Definition fields a person changed since the copy last took the template.
    person_changed: list[str]
    tag_add: tuple[str, ...] = ()
    tag_remove: tuple[str, ...] = ()
    edit: ListVersion | None = field(default=None)

    @property
    def edit_ref(self) -> str | None:
        """How to authorize replacing the person's edit: the list and the
        exact version, as an import names one."""
        return f"{self.copy.list_key}@v{self.edit.version_number}" if self.edit else None


def plan_update(session: Session, copy: ListDefinition, snap: TemplateSnap) -> UpdatePlan:
    history = history_of(session, copy.id)
    current = next(v for v in history if v.id == copy.current_version_id)
    adopted = last_adoption(history)
    mine, base, theirs = content_of(current), content_of(adopted), snap.content
    person_changed = [f for f in DEFINITION_FIELDS if mine[f] != base[f]]
    if mine["rows"] == base["rows"]:
        rows, rows_from, dropped = [list(r) for r in theirs["rows"]], "template", 0
    else:
        at = {c: i for i, c in enumerate(mine["columns"])}
        rows = [[r[at[c]] if c in at else "" for c in theirs["columns"]] for r in mine["rows"]]
        gone = [i for c, i in at.items() if c not in theirs["columns"]]
        dropped = sum(1 for r in mine["rows"] for i in gone if r[i].strip())
        rows_from = "copy"
    content = {f: theirs[f] for f in DEFINITION_FIELDS} | {"rows": rows}
    tag_add, tag_remove = import_tag_changes(session, copy, snap.control_keys, via=TAG_VIA)
    return UpdatePlan(
        copy=copy, current=current, snap=snap,
        based_on=(adopted.provenance or {})["template_version_number"],
        content=content, rows_from=rows_from,
        changed_fields=[f for f in (*DEFINITION_FIELDS, "rows") if mine[f] != content[f]],
        columns_added=[c for c in theirs["columns"] if c not in mine["columns"]],
        columns_removed=[c for c in mine["columns"] if c not in theirs["columns"]],
        dropped_cells=dropped, person_changed=person_changed,
        tag_add=tag_add, tag_remove=tag_remove,
        edit=current if (person_changed or dropped) else None,
    )


def take_update(
    session: Session,
    copy: ListDefinition,
    snap: TemplateSnap,
    saved_by: str,
    overwrite_edit: str | None = None,
) -> tuple[UpdatePlan, ListVersion]:
    """Moves the copy onto the template's current version, as a new
    version of the copy. Refused, writing nothing, when the copy is already
    there, when it would overwrite a person's edit not named exactly, when
    the name given is not that edit, or when the template stopped naming a
    practice that evidence rests on."""
    plan = plan_update(session, copy, snap)
    if snap.version_id == copy.template_list_version_id:
        raise UpdateRefused(
            f"This list is already based on template version {snap.version_number}."
        )
    if overwrite_edit is not None and overwrite_edit != plan.edit_ref:
        raise UpdateRefused(
            f"{overwrite_edit} is not an edit this update replaces"
            f" (it replaces {plan.edit_ref or 'none'}). Nothing was written."
        )
    if plan.edit is not None and overwrite_edit is None:
        what = list(plan.person_changed)
        if plan.dropped_cells:
            what.append(f"{plan.dropped_cells} filled cell(s) in columns the template dropped")
        raise UpdateRefused(
            f"Taking template version {snap.version_number} would replace the edit in "
            f"version {plan.edit.version_number} ({'; '.join(what)}). Name it to go ahead: "
            f"{plan.edit_ref}. Nothing was written."
        )
    blocked = [
        f"{k} ({'; '.join(b)})"
        for k in plan.tag_remove
        if (b := tag_removal_blockers(session, copy.id, k))
    ]
    if blocked:
        raise UpdateRefused(
            "the template no longer names a practice that evidence rests on: "
            f"{', '.join(blocked)}. Unlink that evidence first. Nothing was written."
        )
    provenance = {
        "via": "api", "cause": CAUSE, "saved_by": saved_by,
        "previous_template_version_number": plan.based_on,
        "based_on_version": plan.current.version_number,
        "rows_from": plan.rows_from,
        "changed_fields": plan.changed_fields,
    } | snap.provenance()
    if plan.edit is not None:
        provenance["replaces_edit_in_version"] = plan.edit.version_number
    if plan.dropped_cells:
        provenance["dropped_cells"] = plan.dropped_cells
    version = create_version(session, copy, plan.content, provenance)
    copy.template_list_version_id = snap.version_id
    session.flush()
    for k in plan.tag_add:
        add_tag(session, copy, k, TAG_VIA, saved_by)
    for k in plan.tag_remove:
        remove_tag(session, copy, k, TAG_VIA, saved_by)
    return plan, version


@dataclass(frozen=True)
class TemplateHead:
    template_list_id: uuid.UUID
    template_org_id: uuid.UUID
    based_on_version_number: int
    head_version_id: uuid.UUID
    head_version_number: int

    @property
    def update_available(self) -> bool:
        return self.head_version_number != self.based_on_version_number


def template_heads(session: Session) -> dict[uuid.UUID, TemplateHead]:
    """For the current org's copies: where each template stands now. A
    tenant cannot read the MSP's org, so this goes through
    `auth.list_template_heads()`, which returns version numbers, never
    content, and only for the org the session is scoped to."""
    return {
        r.list_id: TemplateHead(
            r.template_list_id, r.template_org_id, r.based_on_version_number,
            r.head_version_id, r.head_version_number,
        )
        for r in session.execute(text("SELECT * FROM auth.list_template_heads()"))
    }


@dataclass(frozen=True)
class Distribution:
    version_number: int
    copies: int
    this_org: bool


def template_distribution(session: Session) -> dict[uuid.UUID, list[Distribution]]:
    """For the current org's templates: how many copies sit at which
    version -- never whose. The MSP's own view of what it has distributed,
    which is what makes a template update actionable."""
    out: dict[uuid.UUID, list[Distribution]] = {}
    for r in session.execute(text("SELECT * FROM auth.list_template_adoptions()")):
        out.setdefault(r.template_list_id, []).append(
            Distribution(r.version_number, int(r.copies), bool(r.this_org))
        )
    return out
