"""A published document, attached as evidence for the objectives it is
tagged with -- roadmap item X, giving documents L.4's shape.

**Offered, never attached** (CLAUDE.md's product invariants). Until X,
publishing a version linked its Evidence row to every tagged objective in
every in_progress assessment, and a republish archived those links and
made new ones on the new version. Nobody chose either. Now publish writes
the Evidence row and nothing else; a document with an approved version is
*offered* on each objective it is tagged with, and a person attaches it.

**The link names a document version and stays there.** Approving version 3
leaves a link on version 2 where it is: a person put it there, and someone
approving a policy for reasons unrelated to this objective must not undo
that. Today's state never rewrites yesterday's record.

**A superseded version is said as superseded, not as drift.** A list's
older version was true when it was linked and stays a true record. A
superseded policy may no longer be in force, which is a different fact
for an assessor, so it is said in those words (`superseded_text`), here
and in the bundle. It still moves only when a person moves it forward:
a new link, the old one archived.

**Which links a person makes, as a rule.** Ask two things of any path
that links evidence: is the link created by a person's own act, and does
it move an existing link? A link is right only if the first is yes and the
second is no. Attaching here passes; moving forward is a person's act
whose whole purpose is the move; publish passed neither, and republish
moved links. `review_cycles.close_cycle` passes both (see its test,
`test_closing_a_review_cycle_is_a_persons_act_and_moves_nothing`).

**History is the audit log, on the version.** Each attach, move and
removal is an audit row against the document version, so the document's
History (`routers/documents.py:_document_events`) shows it to every role
that can read the document. That is N.2's decision -- no parallel history
table -- and the reason L.10 needed `list_link_event` (the audit log was
not readable where the list was) does not hold here.

Nothing here reads or writes control_state.status or the SPRS score:
evidence attached is not a practice met.

`control_key_for` and `live_link_row` are list_evidence's: the two paths
share those and nothing else verbatim (item X counts the rest).

**Tags (X.1).** A document is tagged per objective, where a list is
tagged per practice: a policy paragraph can satisfy one objective and not
its siblings. Typing a practice expands to its objectives
(`resolve_tag_key`), so a policy covering a whole practice is typed once,
and each objective is still its own tag, removable alone. Removing one is
refused while a live link rests on it (`tag_removal_links`), as for lists;
the refusal is returned as fields (`list_library.RestingLink`) and the
screen writes the sentence.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from .audit import log_event
from .list_evidence import control_key_for, live_link_row
from .list_library import RestingLink
from .list_templates import practice_key
from .models import (
    Assessment,
    AssessmentObjective,
    Control,
    ControlState,
    Document,
    DocumentObjectiveTag,
    DocumentVersion,
    Evidence,
    EvidenceStateLink,
)


class DocumentLinkRefused(ValueError):
    """The document cannot be attached here, and this says why."""


NOT_APPROVED = "no version of it has been approved yet. Publish one first"
# Since X.1 a tag a live link rests on cannot be removed, so this is said
# only of a tag removed before X.1. Kept: such links can exist.
NOT_TAGGED = "it is no longer tagged with this objective"


def superseded_text(linked_version: int, approved_version: int | None) -> str:
    """What a link on a superseded version says. Deliberately not
    list_evidence.drift_text's "version N is available"."""
    approved = (
        f"version {approved_version} is the approved one"
        if approved_version is not None
        else "no version is approved now"
    )
    return (
        f"Version {linked_version}, attached here, is superseded: {approved}, and "
        f"version {linked_version} may no longer be in force. This evidence stays on "
        f"version {linked_version} until someone moves it forward."
    )


def _approved(session: Session, document_ids: list[uuid.UUID]) -> dict[uuid.UUID, DocumentVersion]:
    """The approved version of each document, where there is one (at most
    one per document: publish supersedes the rest)."""
    if not document_ids:
        return {}
    return {
        v.document_id: v
        for v in session.scalars(
            select(DocumentVersion).where(
                DocumentVersion.document_id.in_(document_ids),
                DocumentVersion.status == "approved",
            ).order_by(DocumentVersion.document_id, DocumentVersion.version_number)
        )
    }


def _live_document_links(session: Session, cs_ids: list[uuid.UUID]):
    """(link, evidence, version) for every live document link on these states."""
    if not cs_ids:
        return []
    return session.execute(
        select(EvidenceStateLink, Evidence, DocumentVersion)
        .join(Evidence, Evidence.id == EvidenceStateLink.evidence_id)
        .join(DocumentVersion, DocumentVersion.id == Evidence.source_document_version_id)
        .where(
            EvidenceStateLink.control_state_id.in_(cs_ids),
            EvidenceStateLink.is_archived.is_(False),
        )
        .order_by(
            DocumentVersion.document_id, DocumentVersion.version_number, EvidenceStateLink.id
        )
    ).all()


def _tagged(session: Session, document_id: uuid.UUID, cs: ControlState) -> bool:
    return session.scalar(
        select(DocumentObjectiveTag.id).where(
            DocumentObjectiveTag.document_id == document_id,
            DocumentObjectiveTag.objective_id == cs.objective_id,
        )
    ) is not None


@dataclass(frozen=True)
class Candidate:
    """One document on an objective, as its evidence section shows it:
    tagged with the objective, attached to it, or both."""

    document_id: uuid.UUID
    doc_id: str
    title: str
    approved_version_id: uuid.UUID | None
    approved_version_number: int | None
    not_offered: str | None
    linked_version_id: uuid.UUID | None
    linked_version_number: int | None
    linked_superseded: bool
    evidence_id: uuid.UUID | None

    @property
    def superseded(self) -> str | None:
        if self.linked_version_number is None or not self.linked_superseded:
            return None
        return superseded_text(self.linked_version_number, self.approved_version_number)


def candidates(session: Session, org_id: uuid.UUID, cs: ControlState) -> list[Candidate]:
    """The org's documents tagged with this objective, and any attached to
    it -- an attached document stays listed after its tag is removed, or
    the link would be there and unseen."""
    linked = {
        v.document_id: (v, e) for _, e, v in _live_document_links(session, [cs.id])
    }
    tagged_ids = set(session.scalars(
        select(DocumentObjectiveTag.document_id)
        .join(Document, Document.id == DocumentObjectiveTag.document_id)
        .where(Document.org_id == org_id, DocumentObjectiveTag.objective_id == cs.objective_id)
    ))
    docs = list(session.scalars(
        select(Document)
        .where(Document.org_id == org_id, Document.id.in_(tagged_ids | set(linked)))
        .order_by(Document.doc_id, Document.id)
    ))
    approved = _approved(session, [d.id for d in docs])
    out = []
    for d in docs:
        a = approved.get(d.id)
        lv, ev = linked.get(d.id, (None, None))
        out.append(Candidate(
            document_id=d.id, doc_id=d.doc_id, title=d.title,
            approved_version_id=a.id if a else None,
            approved_version_number=a.version_number if a else None,
            not_offered=(
                NOT_TAGGED if d.id not in tagged_ids else NOT_APPROVED if a is None else None
            ),
            linked_version_id=lv.id if lv else None,
            linked_version_number=lv.version_number if lv else None,
            linked_superseded=lv is not None and lv.status == "superseded",
            evidence_id=ev.id if ev else None,
        ))
    return out


def _document(session: Session, org_id: uuid.UUID, document_id: uuid.UUID) -> Document:
    doc = session.get(Document, document_id)
    if doc is None or doc.org_id != org_id:
        raise LookupError("document not found")
    return doc


def _offerable(
    session: Session, org_id: uuid.UUID, cs: ControlState, document_id: uuid.UUID
) -> tuple[Document, DocumentVersion, str, str]:
    doc = _document(session, org_id, document_id)
    control_key, objective_key = control_key_for(session, cs)
    if not _tagged(session, doc.id, cs):
        raise DocumentLinkRefused(
            f"this document is not tagged with {control_key}[{objective_key}]; a document is "
            "evidence only for the objectives it is tagged with. Tag it first, if it serves "
            "this objective"
        )
    v = _approved(session, [doc.id]).get(doc.id)
    if v is None:
        raise DocumentLinkRefused(f"not offered as evidence: {NOT_APPROVED}")
    return doc, v, control_key, objective_key


def _evidence_for(
    session: Session, org_id: uuid.UUID, doc: Document, v: DocumentVersion
) -> Evidence:
    """The Evidence row publish wrote for this version. Before X the general
    evidence remove button deleted it with the last link, so one missing is
    written again, dated by the approval it records."""
    ev = session.scalar(
        select(Evidence)
        .where(Evidence.org_id == org_id, Evidence.source_document_version_id == v.id)
        .order_by(Evidence.created_at, Evidence.id)
        .limit(1)
    )
    if ev is None:
        ev = Evidence(
            org_id=org_id, kind="reference", title=doc.title, artifact_type="document",
            reference_location=doc.doc_id, collected_at=v.approved_at or datetime.now(UTC),
            source_document_version_id=v.id,
        )
        session.add(ev)
        session.flush()
    return ev


def _audit(session, org_id, action, cs, doc, v, control_key, objective_key, **extra):
    a = session.get(Assessment, cs.assessment_id)
    log_event(
        session, org_id=org_id, action=action, entity_type="document_version", entity_id=v.id,
        after_value={
            "document_id": str(doc.id), "doc_id": doc.doc_id, "version_number": v.version_number,
            "control_state_id": str(cs.id), "assessment_id": str(a.id),
            "assessment_name": a.name, "control_key": control_key,
            "objective_key": objective_key, **extra,
        },
        context={"via": "api", "assessment_id": str(cs.assessment_id)},
    )


def _live_for(session: Session, cs: ControlState, document_id: uuid.UUID):
    return [
        (lk, dv) for lk, _, dv in _live_document_links(session, [cs.id])
        if dv.document_id == document_id
    ]


def link(
    session: Session, org_id: uuid.UUID, cs: ControlState, document_id: uuid.UUID
) -> EvidenceStateLink:
    """Attach the document's approved version to this objective."""
    doc, v, control_key, objective_key = _offerable(session, org_id, cs, document_id)
    for _, lv in _live_for(session, cs, doc.id):
        if lv.id == v.id:
            raise DocumentLinkRefused(f"version {v.version_number} is already attached here")
        raise DocumentLinkRefused(
            f"version {lv.version_number} is attached here; move it forward to version "
            f"{v.version_number} instead"
        )
    row = live_link_row(session, _evidence_for(session, org_id, doc, v), cs)
    session.flush()
    _audit(session, org_id, "evidence.document_link", cs, doc, v, control_key, objective_key)
    session.flush()
    return row


def move_forward(
    session: Session, org_id: uuid.UUID, cs: ControlState, document_id: uuid.UUID
) -> EvidenceStateLink:
    """Move this objective's link onto the approved version: a new live
    link, and the old one archived -- never rewritten."""
    doc, v, control_key, objective_key = _offerable(session, org_id, cs, document_id)
    old = _live_for(session, cs, doc.id)
    if not old:
        raise DocumentLinkRefused("this document is not attached here")
    if any(lv.id == v.id for _, lv in old):
        raise DocumentLinkRefused(f"already at version {v.version_number}")
    now = datetime.now(UTC)
    for lk, _ in old:
        lk.is_archived = True
        lk.archived_at = now
    row = live_link_row(session, _evidence_for(session, org_id, doc, v), cs)
    session.flush()
    _audit(
        session, org_id, "evidence.document_move_forward", cs, doc, v, control_key,
        objective_key, from_version_numbers=sorted(lv.version_number for _, lv in old),
    )
    session.flush()
    return row


def unlink(
    session: Session, org_id: uuid.UUID, cs: ControlState, document_id: uuid.UUID
) -> None:
    """Remove this document from this objective's evidence. Only the link
    goes: the Evidence row is the record that the version was published,
    and `delete_document`'s refusal rests on it."""
    doc = _document(session, org_id, document_id)
    live = _live_for(session, cs, doc.id)
    if not live:
        raise LookupError("this document is not attached here")
    control_key, objective_key = control_key_for(session, cs)
    for lk, lv in live:
        session.delete(lk)
        session.flush()
        _audit(
            session, org_id, "evidence.document_unlink", cs, doc, lv, control_key, objective_key
        )
    session.flush()


# ---------------------------------------------------------------------------
# Tags (item X.1): what a person types, and what a removal would narrow
# ---------------------------------------------------------------------------

_OBJECTIVE_SUFFIX = re.compile(r"^(.*?)\s*\[\s*([A-Za-z0-9]{1,5})\s*\]$")


class TagKeyRefused(ValueError):
    """What was typed names no single objective set, and this says which."""


@dataclass(frozen=True)
class TaggedObjective:
    objective_id: uuid.UUID
    objective_key: str  # in full: "AC.L2-3.1.1[a]"
    text: str


def resolve_tag_key(session: Session, org_id: uuid.UUID, typed: str) -> list[TaggedObjective]:
    """The objectives a typed key names. 'AC.L2-3.1.1[a]' or '3.1.1[a]' is
    one objective; 'AC.L2-3.1.1' or '3.1.1' is the practice, expanded to
    each of its objectives -- a convenience for the person typing, while
    the tags stored stay one per objective, each removable on its own.
    Resolved in the frameworks this org has assessments in (any, when it
    has none). Refused, naming what was typed, when it is no practice,
    names an objective the practice does not have, or matches more than
    one of those frameworks."""
    raw = typed.strip()
    m = _OBJECTIVE_SUFFIX.match(raw)
    practice_text, letter = (m.group(1), m.group(2).lower()) if m else (raw, None)
    try:
        practice = practice_key(practice_text)
    except ValueError:
        raise TagKeyRefused(
            f"{raw!r} is not an objective or practice like AC.L2-3.1.1[a] or 3.1.1"
        ) from None
    rows = session.execute(
        select(AssessmentObjective, Control.framework_id)
        .join(Control, Control.id == AssessmentObjective.control_id)
        .where(Control.control_id == practice)
        .order_by(AssessmentObjective.objective_key, AssessmentObjective.id)
    ).all()
    assessed = set(
        session.scalars(select(Assessment.framework_id).where(Assessment.org_id == org_id)).all()
    )
    if assessed:
        rows = [r for r in rows if r[1] in assessed]
    if letter is not None:
        rows = [r for r in rows if r[0].objective_key == letter]
    if not rows:
        what = f"{practice}[{letter}]" if letter else practice
        raise TagKeyRefused(f"{raw!r}: {what} is not in the catalog")
    if len({fw for _, fw in rows}) > 1:
        raise TagKeyRefused(f"{raw!r} matches {practice} in more than one framework")
    return [TaggedObjective(o.id, f"{practice}[{o.objective_key}]", o.text) for o, _ in rows]


def objective_named(session: Session, objective: AssessmentObjective) -> TaggedObjective:
    control_key = session.scalar(
        select(Control.control_id).where(Control.id == objective.control_id)
    )
    key = f"{control_key}[{objective.objective_key}]"
    return TaggedObjective(objective.id, key, objective.text)


def tagged_objectives(session: Session, document_id: uuid.UUID) -> list[TaggedObjective]:
    rows = session.execute(
        select(AssessmentObjective, Control.control_id)
        .join(DocumentObjectiveTag, DocumentObjectiveTag.objective_id == AssessmentObjective.id)
        .join(Control, Control.id == AssessmentObjective.control_id)
        .where(DocumentObjectiveTag.document_id == document_id)
        .order_by(Control.sequence_order, AssessmentObjective.objective_key, AssessmentObjective.id)
    ).all()
    return [TaggedObjective(o.id, f"{c}[{o.objective_key}]", o.text) for o, c in rows]


def tag_removal_links(
    session: Session, document_id: uuid.UUID, objective_id: uuid.UUID
) -> list[RestingLink]:
    """L.4's `tag_removal_blockers` rule, for documents: the live links
    from any version of this document onto `objective_id`, in any
    assessment. Removing the tag while one exists would leave a chosen
    link resting on nothing ("it is no longer tagged with this
    objective"), so the removal is refused until it is detached. Archived
    links do not block: only moving forward archives a document link, and
    it leaves a live one in its place."""
    rows = session.execute(
        select(DocumentVersion.version_number, Assessment.name, Control.control_id,
               AssessmentObjective.objective_key)
        .select_from(EvidenceStateLink)
        .join(Evidence, Evidence.id == EvidenceStateLink.evidence_id)
        .join(DocumentVersion, DocumentVersion.id == Evidence.source_document_version_id)
        .join(ControlState, ControlState.id == EvidenceStateLink.control_state_id)
        .join(Assessment, Assessment.id == ControlState.assessment_id)
        .join(AssessmentObjective, AssessmentObjective.id == ControlState.objective_id)
        .join(Control, Control.id == AssessmentObjective.control_id)
        .where(
            DocumentVersion.document_id == document_id,
            ControlState.objective_id == objective_id,
            EvidenceStateLink.is_archived.is_(False),
        )
        .order_by(Assessment.name, DocumentVersion.version_number, EvidenceStateLink.id)
    ).all()
    return [
        RestingLink(r.version_number, f"{r.control_id}[{r.objective_key}]", r.name)
        for r in rows
    ]
