"""A populated list, linked as evidence for the practices it is tagged with
-- roadmap L.4.

**The link names a list version, never the list.** Linking writes (or
reuses) one Evidence row per list version, `kind='reference'`,
`artifact_type='list'`, `source_list_version_id` set (migration 0067), and
an EvidenceStateLink to the objective. Editing the list afterwards writes
a new version and leaves the link where it was: today's state never
rewrites yesterday's record. The drift is said, never fixed silently --
"List version 4 is available. This evidence stays on version 2 until
someone moves it forward." -- in the words L.7 uses for a newer template
version. Moving forward is a deliberate act and writes a new link; the old
one is archived, not changed, so the record of what was linked survives.

**Offered, never attached.** A list is a candidate for an objective when
it is tagged with the objective's practice and `list_library.is_populated`
says its rows are real (that function is the one rule; nothing here
restates it). A person attaches it. Nothing here reads or writes
control_state.status or the SPRS score: attaching evidence is not claiming
a practice is met.

**Confined to the tag set.** A list covers the practices it is tagged
with, so it links only to those, and removing a tag while a link rests on
it is refused (`list_library.tag_removal_blockers`, which reads the links
made here).

**A product's deactivation leaves these links alone.** A list link exists
because a person attached a list, not because a tool claimed coverage, so
`engine.deactivate_org_product` archives only links that are not to a list
version. Were it otherwise, deactivating any tool covering the same
objective would archive the link, and the tag it rests on would become
removable at that moment -- the failure the tag rule exists to prevent.

**Every link change is in the list's own history** (L.10): each link, move
forward and unlink writes a `list_link_event` beside the audit row, so the
list's History answers "was this ever attached?" for anyone who can read
the list -- not only for whoever can read the audit log.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from .audit import log_event
from .list_library import example_rows, is_populated
from .models import (
    Assessment,
    AssessmentObjective,
    Control,
    ControlState,
    Evidence,
    EvidenceStateLink,
    ListControlTag,
    ListDefinition,
    ListLinkEvent,
    ListVersion,
)


class ListLinkRefused(ValueError):
    """The list cannot be linked here, and this says why."""


def not_offered_reason(definition: ListDefinition, version: ListVersion | None) -> str | None:
    """Why a list is not offered as evidence, or None when it is. The
    decision is `is_populated`'s; this only says it in words, because an
    adopted copy starts unpopulated by design and "my list isn't in the
    picker" must not read as the feature being broken."""
    if definition.is_template:
        return "a template: its rows are examples. Adopt it into an org and fill it in"
    rows = version.rows if version is not None else []
    if is_populated(rows):
        return None
    if not rows:
        return "it has no rows yet"
    n, total = example_rows(rows), len(rows)
    return f"still showing template examples ({n} of {total} row{'' if total == 1 else 's'})"


def drift_text(linked_version: int, current_version: int) -> str | None:
    """L.7's phrasing for a newer template version, for a link."""
    if linked_version == current_version:
        return None
    return (
        f"List version {current_version} is available. This evidence stays on version "
        f"{linked_version} until someone moves it forward."
    )


def control_key_for(session: Session, cs: ControlState) -> tuple[str, str]:
    """(practice, objective key) for a state. Shared with document_evidence.py,
    as is live_link_row: the two are all the list and document paths share
    verbatim (item X)."""
    row = session.execute(
        select(Control.control_id, AssessmentObjective.objective_key)
        .join(AssessmentObjective, AssessmentObjective.control_id == Control.id)
        .where(AssessmentObjective.id == cs.objective_id)
    ).one()
    return row.control_id, row.objective_key


def _live_list_links(session: Session, cs_ids: list[uuid.UUID]):
    """(link, evidence, version) for every live list link on these states."""
    if not cs_ids:
        return []
    return session.execute(
        select(EvidenceStateLink, Evidence, ListVersion)
        .join(Evidence, Evidence.id == EvidenceStateLink.evidence_id)
        .join(ListVersion, ListVersion.id == Evidence.source_list_version_id)
        .where(
            EvidenceStateLink.control_state_id.in_(cs_ids),
            EvidenceStateLink.is_archived.is_(False),
        )
        .order_by(ListVersion.list_id, ListVersion.version_number, EvidenceStateLink.id)
    ).all()


@dataclass(frozen=True)
class Candidate:
    """One list tagged with an objective's practice, as that objective's
    evidence section shows it."""

    list_id: uuid.UUID
    list_key: str
    title: str
    current_version_id: uuid.UUID | None
    current_version_number: int | None
    not_offered: str | None
    linked_version_id: uuid.UUID | None
    linked_version_number: int | None
    evidence_id: uuid.UUID | None

    @property
    def drift(self) -> str | None:
        if self.linked_version_number is None or self.current_version_number is None:
            return None
        return drift_text(self.linked_version_number, self.current_version_number)


def candidates(session: Session, org_id: uuid.UUID, cs: ControlState) -> list[Candidate]:
    """The org's lists tagged with this objective's practice, each with why
    it is not offered (if it is not) and the version linked here (if one
    is). Templates included, so the reason can be said."""
    control_key, _ = control_key_for(session, cs)
    rows = session.execute(
        select(ListDefinition, ListVersion)
        .join(ListControlTag, ListControlTag.list_id == ListDefinition.id)
        .outerjoin(ListVersion, ListVersion.id == ListDefinition.current_version_id)
        .where(ListDefinition.org_id == org_id, ListControlTag.control_key == control_key)
        .order_by(ListDefinition.is_template, ListDefinition.list_key, ListDefinition.id)
    ).all()
    linked = {v.list_id: (v, e) for _, e, v in _live_list_links(session, [cs.id])}
    out = []
    for d, v in rows:
        lv, ev = linked.get(d.id, (None, None))
        out.append(Candidate(
            list_id=d.id, list_key=d.list_key, title=v.title if v else d.list_key,
            current_version_id=v.id if v else None,
            current_version_number=v.version_number if v else None,
            not_offered=not_offered_reason(d, v),
            linked_version_id=lv.id if lv else None,
            linked_version_number=lv.version_number if lv else None,
            evidence_id=ev.id if ev else None,
        ))
    return out


def _offerable(
    session: Session, org_id: uuid.UUID, cs: ControlState, list_id: uuid.UUID
) -> tuple[ListDefinition, ListVersion, str, str]:
    d = session.get(ListDefinition, list_id)
    if d is None or d.org_id != org_id:
        raise LookupError("list not found")
    control_key, objective_key = control_key_for(session, cs)
    tagged = session.scalar(
        select(ListControlTag.id).where(
            ListControlTag.list_id == d.id, ListControlTag.control_key == control_key
        )
    )
    if tagged is None:
        raise ListLinkRefused(
            f"this list is not tagged with {control_key}; a list is evidence only for "
            "the practices it is tagged with. Tag it first, if it serves this practice"
        )
    v = session.get(ListVersion, d.current_version_id) if d.current_version_id else None
    reason = not_offered_reason(d, v)
    if reason:
        raise ListLinkRefused(f"not offered as evidence: {reason}")
    return d, v, control_key, objective_key


def _evidence_for(session: Session, org_id: uuid.UUID, v: ListVersion) -> Evidence:
    """One Evidence row per list version, shared by every objective it is
    linked to -- capture once, reference many."""
    ev = session.scalar(
        select(Evidence)
        .where(Evidence.org_id == org_id, Evidence.source_list_version_id == v.id)
        .order_by(Evidence.created_at, Evidence.id)
        .limit(1)
    )
    if ev is None:
        d = session.get(ListDefinition, v.list_id)
        ev = Evidence(
            org_id=org_id, kind="reference", artifact_type="list", title=v.title,
            reference_location=f"List library: {d.list_key}, version {v.version_number}",
            # When the version was saved: that is when this record was captured.
            collected_at=v.created_at, source_list_version_id=v.id,
        )
        session.add(ev)
        session.flush()
    return ev


def live_link_row(session: Session, ev: Evidence, cs: ControlState) -> EvidenceStateLink:
    existing = session.scalar(
        select(EvidenceStateLink).where(
            EvidenceStateLink.evidence_id == ev.id, EvidenceStateLink.control_state_id == cs.id
        )
    )
    if existing is not None:
        # Linked at this version before, moved off it, and now back: the
        # same row returns to live (uq_evidence_state_link allows only one).
        existing.is_archived = False
        existing.archived_at = None
        return existing
    link = EvidenceStateLink(evidence_id=ev.id, control_state_id=cs.id)
    session.add(link)
    return link


def _audit(session, org_id, action, cs, d, v, control_key, objective_key, **extra):
    log_event(
        session, org_id=org_id, action=action, entity_type="control_state", entity_id=cs.id,
        after_value={
            "list_id": str(d.id), "list_key": d.list_key, "list_version_id": str(v.id),
            "version_number": v.version_number, "control_key": control_key,
            "objective_key": objective_key, **extra,
        },
        context={"via": "api", "assessment_id": str(cs.assessment_id)},
    )


def _record(
    session, org_id, change, cs, d, v, control_key, objective_key, saved_by, from_versions=None
):
    a = session.get(Assessment, cs.assessment_id)
    session.add(ListLinkEvent(
        list_id=d.id, org_id=org_id, change=change, list_version_id=v.id,
        version_number=v.version_number, from_version_numbers=from_versions,
        assessment_id=a.id, assessment_name=a.name, control_key=control_key,
        objective_key=objective_key, saved_by=saved_by,
    ))


def link(
    session: Session, org_id: uuid.UUID, cs: ControlState, list_id: uuid.UUID,
    saved_by: str | None = None,
) -> EvidenceStateLink:
    """Attach the list's current version to this objective."""
    d, v, control_key, objective_key = _offerable(session, org_id, cs, list_id)
    for _, _, lv in _live_list_links(session, [cs.id]):
        if lv.list_id == d.id:
            if lv.id == v.id:
                raise ListLinkRefused(f"version {v.version_number} is already linked here")
            raise ListLinkRefused(
                f"version {lv.version_number} is linked here; move it forward to version "
                f"{v.version_number} instead"
            )
    row = live_link_row(session, _evidence_for(session, org_id, v), cs)
    session.flush()
    _audit(session, org_id, "evidence.list_link", cs, d, v, control_key, objective_key)
    _record(session, org_id, "linked", cs, d, v, control_key, objective_key, saved_by)
    session.flush()
    return row


def move_forward(
    session: Session, org_id: uuid.UUID, cs: ControlState, list_id: uuid.UUID,
    saved_by: str | None = None,
) -> EvidenceStateLink:
    """Move this objective's link onto the list's current version: a new
    live link, and the old one archived -- never rewritten."""
    d, v, control_key, objective_key = _offerable(session, org_id, cs, list_id)
    old = [(lk, lv) for lk, _, lv in _live_list_links(session, [cs.id]) if lv.list_id == d.id]
    if not old:
        raise ListLinkRefused("this list is not linked here")
    if any(lv.id == v.id for _, lv in old):
        raise ListLinkRefused(f"already at version {v.version_number}")
    now = datetime.now(UTC)
    for lk, _ in old:
        lk.is_archived = True
        lk.archived_at = now
    row = live_link_row(session, _evidence_for(session, org_id, v), cs)
    session.flush()
    _audit(
        session, org_id, "evidence.list_move_forward", cs, d, v, control_key, objective_key,
        from_version_numbers=[lv.version_number for _, lv in old],
    )
    _record(
        session, org_id, "moved_forward", cs, d, v, control_key, objective_key, saved_by,
        from_versions=sorted(lv.version_number for _, lv in old),
    )
    session.flush()
    return row


def unlink(
    session: Session, org_id: uuid.UUID, cs: ControlState, list_id: uuid.UUID,
    saved_by: str | None = None,
) -> None:
    """Remove this list from this objective's evidence. The live link goes;
    links archived by an earlier move forward stay as the record."""
    d = session.get(ListDefinition, list_id)
    if d is None or d.org_id != org_id:
        raise LookupError("list not found")
    live = [(lk, lv) for lk, _, lv in _live_list_links(session, [cs.id]) if lv.list_id == d.id]
    if not live:
        raise LookupError("this list is not linked here")
    control_key, objective_key = control_key_for(session, cs)
    for lk, lv in live:
        ev_id = lk.evidence_id
        session.delete(lk)
        session.flush()
        _audit(session, org_id, "evidence.list_unlink", cs, d, lv, control_key, objective_key)
        _record(session, org_id, "unlinked", cs, d, lv, control_key, objective_key, saved_by)
        # As routers/evidence.py's unlink does: a row nothing links to goes.
        # It holds no bytes, and the version it named is still in the list.
        if session.scalar(
            select(EvidenceStateLink.id).where(EvidenceStateLink.evidence_id == ev_id).limit(1)
        ) is None:
            session.delete(session.get(Evidence, ev_id))
    session.flush()


@dataclass(frozen=True)
class LinkView:
    """One live link, as the list page and an assessment's bundle picker
    show it."""

    list_id: uuid.UUID
    list_key: str
    title: str
    current_version_number: int | None
    linked_version_id: uuid.UUID
    linked_version_number: int
    assessment_id: uuid.UUID
    assessment_name: str
    control_state_id: uuid.UUID
    control_key: str
    objective_key: str

    @property
    def drift(self) -> str | None:
        if self.current_version_number is None:
            return None
        return drift_text(self.linked_version_number, self.current_version_number)


def links(
    session: Session, org_id: uuid.UUID, *, list_id: uuid.UUID | None = None,
    assessment_id: uuid.UUID | None = None,
) -> list[LinkView]:
    """Every live list link in the org, narrowed to one list or one
    assessment."""
    Current = ListVersion.__table__.alias("current_version")
    q = (
        select(
            ListDefinition.id, ListDefinition.list_key, Current.c.title,
            Current.c.version_number, ListVersion.id.label("linked_id"),
            ListVersion.version_number.label("linked_number"), Assessment.id.label("a_id"),
            Assessment.name, ControlState.id.label("cs_id"), Control.control_id,
            AssessmentObjective.objective_key,
        )
        .select_from(EvidenceStateLink)
        .join(Evidence, Evidence.id == EvidenceStateLink.evidence_id)
        .join(ListVersion, ListVersion.id == Evidence.source_list_version_id)
        .join(ListDefinition, ListDefinition.id == ListVersion.list_id)
        .outerjoin(Current, Current.c.id == ListDefinition.current_version_id)
        .join(ControlState, ControlState.id == EvidenceStateLink.control_state_id)
        .join(Assessment, Assessment.id == ControlState.assessment_id)
        .join(AssessmentObjective, AssessmentObjective.id == ControlState.objective_id)
        .join(Control, Control.id == AssessmentObjective.control_id)
        .where(Evidence.org_id == org_id, EvidenceStateLink.is_archived.is_(False))
        .order_by(
            ListDefinition.list_key, ListDefinition.id, Assessment.name, Assessment.id,
            Control.control_id, AssessmentObjective.objective_key, EvidenceStateLink.id,
        )
    )
    if list_id is not None:
        q = q.where(ListDefinition.id == list_id)
    if assessment_id is not None:
        q = q.where(Assessment.id == assessment_id)
    return [
        LinkView(
            list_id=r.id, list_key=r.list_key, title=r.title or r.list_key,
            current_version_number=r.version_number, linked_version_id=r.linked_id,
            linked_version_number=r.linked_number, assessment_id=r.a_id,
            assessment_name=r.name, control_state_id=r.cs_id, control_key=r.control_id,
            objective_key=r.objective_key,
        )
        for r in session.execute(q).all()
    ]


def link_events(session: Session, list_id: uuid.UUID) -> list[ListLinkEvent]:
    """The list's evidence history, oldest first."""
    return list(session.scalars(
        select(ListLinkEvent).where(ListLinkEvent.list_id == list_id).order_by(ListLinkEvent.seq)
    ))
