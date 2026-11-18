"""The SSP import review session: a customer's SSP read into one
assessment and decided row by row, over as many sittings as it takes
(docs/roadmap.md, "Importing implementation statements from a customer's
SSP", the review session; migration 0074).

The parse and the match are importers/ssp_docx.py, unchanged. This module
is everything after the match.

**What a decision writes, and what it never touches.** Accepting a row
writes one ImplementationStatement, `status = 'draft'`, on the objective
the person accepted it onto, and nothing else: control_state, its status
and the SPRS score are never read for writing here, and no recompute is
called. The source says "Met"; that is carried as the source's claim in
`grounded_in`, never as a status (tests/test_ssp_import_session.py
asserts all of it across a whole import).

**Each decision is its own request and its own commit.** There is no Save:
a reload loses nothing because nothing is held in the browser. Opening a
session writes nothing; the first write is a person accepting a row.

**The staleness rule.** A session may sit for days. Whether an objective
already has a statement is read when the row is decided, never from the
session's start. If it has one, from a person, from another row of this
file, from anywhere, accepting is refused as a conflict that names the
statement. The person then chooses: keep the existing statement (the row
is rejected, with that reason), or replace it, which must name the
statement and the `updated_at` they were shown. A statement edited after
that is a new conflict. Neither side ever wins by default. The unique
constraint `uq_implementation_statement_identity` makes this structural:
two statements on one objective cannot exist.

**What a row may be accepted onto.**
- `exact`: its matched objective, and only that.
- `ambiguous`, `family_mismatch`, `no_family`: the row's text is a
  catalog objective's text, word for word, but more than one objective
  or the wrong family has it. A person picks one of those candidates; none
  is chosen for them.
- `unmatched`: nothing. Its candidates are similarity guesses, shown for
  reading, and a near match is worse than no match (the importer's
  docstring). Its statement is written by hand if it belongs anywhere.
- A row with no statement text: nothing, because there is nothing to
  write.

**The file is not kept (Jarrod, 2026-11-18).** It is parsed from a
temporary file that is removed before the request returns. The session
pins it by SHA-256, name and size. A row keeps the statement text and the
Control cell; of Notes and RACI, only whether each was present.

**Provenance** is `grounded_in` on the statement, `PROVENANCE_KEYS`:
which session, file and row it came from, what the row's match was,
whether the person accepted it or edited it, who and when, and the
source's status claim, labelled as the source's.

**Visibility.** Every read is a `select()` filtered by org under the org
`require_org_access` set, never `session.get()`, which can answer from the
identity map without a query. An assessment or session RLS hides is "not
found", never read as one with nothing in it.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .audit import log_event
from .importers import ssp_docx
from .models import (
    Assessment,
    AssessmentObjective,
    Control,
    ImplementationStatement,
    SspImportRow,
    SspImportSession,
)
from .ssp_import import _catalog

IMPORTER = "ssp_docx"
MAX_BYTES = 20 * 1024 * 1024
_ZIP_MAGIC = b"PK\x03\x04"

# The kinds a person may accept onto one of the row's candidates: the
# row's text is some objective's catalog text, exactly. `unmatched` is not
# here: its candidates are guesses.
PICKABLE_KINDS = frozenset({"ambiguous", "family_mismatch", "no_family"})

PROVENANCE_KEYS = (
    "source", "importer", "session_id", "file_name", "file_sha256", "source_row",
    "source_heading", "source_control_text", "source_status_claim", "match_kind",
    "decision", "decided_by", "decided_at",
)

NO_FAMILY = "(no family heading)"


class Refused(Exception):
    """A request this module will not carry out. `status` is the HTTP
    status the router answers with; `detail` is a sentence, or a dict for a
    conflict the screen has to show."""

    def __init__(self, status: int, detail):
        super().__init__(detail if isinstance(detail, str) else detail.get("message"))
        self.status = status
        self.detail = detail


# ---------------------------------------------------------------------------
# Lookups, all by select() under the request's org
# ---------------------------------------------------------------------------


def _assessment(session: Session, org_id: uuid.UUID, assessment_id: uuid.UUID) -> Assessment:
    a = session.scalars(
        select(Assessment).where(Assessment.id == assessment_id, Assessment.org_id == org_id)
    ).first()
    if a is None:
        raise Refused(404, "Assessment not found")
    return a


def _session(
    session: Session, org_id: uuid.UUID, assessment_id: uuid.UUID, session_id: uuid.UUID,
    *, lock: bool = False,
) -> SspImportSession:
    q = select(SspImportSession).where(
        SspImportSession.id == session_id,
        SspImportSession.org_id == org_id,
        SspImportSession.assessment_id == assessment_id,
    )
    if lock:
        q = q.with_for_update()
    s = session.scalars(q).first()
    if s is None:
        raise Refused(404, "Import session not found")
    return s


def _open_session(session: Session, assessment_id: uuid.UUID) -> SspImportSession | None:
    return session.scalars(
        select(SspImportSession).where(
            SspImportSession.assessment_id == assessment_id, SspImportSession.status == "open"
        )
    ).first()


def _existing(
    session: Session, assessment_id: uuid.UUID, objective_id: uuid.UUID
) -> ImplementationStatement | None:
    return session.scalars(
        select(ImplementationStatement).where(
            ImplementationStatement.assessment_id == assessment_id,
            ImplementationStatement.objective_id == objective_id,
        )
    ).first()


def _now() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# Start
# ---------------------------------------------------------------------------


def start(
    session: Session, *, org_id: uuid.UUID, assessment_id: uuid.UUID, file_name: str,
    data: bytes, actor: str,
) -> SspImportSession:
    """Read the file, match it, and open a session holding its rows. The
    file itself is discarded before this returns."""
    assessment = _assessment(session, org_id, assessment_id)
    if not file_name.lower().endswith(".docx"):
        raise Refused(422, f"{file_name}: only a .docx SSP can be imported")
    if len(data) > MAX_BYTES:
        raise Refused(413, f"{file_name}: larger than {MAX_BYTES // (1024 * 1024)} MB")
    if not data.startswith(_ZIP_MAGIC):
        raise Refused(422, f"{file_name}: not a .docx (the file is not a Word package)")
    held = _open_session(session, assessment_id)
    if held is not None:
        raise Refused(409, {
            "reason": "open_session",
            "message": (f"This assessment already has an open import, of {held.file_name}. "
                        "Finish or discard it before starting another."),
            "session_id": str(held.id),
        })
    catalog = _catalog(session, assessment.framework_id)
    if not catalog:
        raise Refused(422, "This assessment's framework has no objectives to match against")

    fd, tmp = tempfile.mkstemp(suffix=".docx")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        path = Path(tmp)
        try:
            rows = ssp_docx.parse(path)
        except ssp_docx.SspNotRecognised as e:
            raise Refused(422, str(e).replace(path.name, file_name)) from e
    finally:
        os.unlink(tmp)
    if not rows:
        raise Refused(422, (f"{file_name}: the SSP table was found, but it holds no objective "
                            "rows, so there is nothing to import"))

    matches = ssp_docx.match(rows, catalog)
    by_ref = {o.ref: o for o in catalog}
    s = SspImportSession(
        org_id=org_id, assessment_id=assessment_id, status="open", file_name=file_name,
        file_sha256=hashlib.sha256(data).hexdigest(), file_size=len(data),
        importer=IMPORTER, started_by=actor,
    )
    session.add(s)
    try:
        session.flush()
    except IntegrityError as e:
        raise Refused(409, {"reason": "open_session",
                            "message": "This assessment already has an open import."}) from e
    for m in matches:
        r = m.row
        session.add(SspImportRow(
            session_id=s.id, org_id=org_id, number=r.number, heading=r.heading,
            family=r.family, control_text=r.control_text, source_status=r.status,
            notes_present=r.notes is not None, raci_present=r.raci_present,
            statement=r.statement, match_kind=m.kind,
            matched_objective_id=uuid.UUID(m.objective.objective_id) if m.objective else None,
            candidates=[
                {"objective_id": by_ref[ref].objective_id, "ref": ref, "score": score}
                for ref, score in m.candidates
            ],
            decision="pending",
        ))
    session.flush()
    log_event(
        session, org_id=org_id, action="ssp_import.start", entity_type="ssp_import_session",
        entity_id=s.id,
        after_value={"file_name": file_name, "file_sha256": s.file_sha256,
                     "file_size": s.file_size, "rows": len(matches),
                     "exact": sum(1 for m in matches if m.kind == "exact")},
        context={"via": "api", "assessment_id": str(assessment_id), "file_kept": False},
    )
    return s


# ---------------------------------------------------------------------------
# Reading a session
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExistingStatement:
    id: uuid.UUID
    body: str
    status: str
    updated_at: datetime
    from_this_session: bool


@dataclass(frozen=True)
class RowView:
    row: SspImportRow
    objective_ref: str | None
    objective_text: str | None
    decided_ref: str | None
    acceptable_onto: tuple[str, ...]  # objective ids, as strings
    not_acceptable: str | None
    existing: ExistingStatement | None  # on the matched objective, read now
    shares_objective_with: tuple[int, ...]  # other rows exact-matched to it


@dataclass(frozen=True)
class FamilyProgress:
    key: str
    heading: str
    total: int
    decided: int


@dataclass(frozen=True)
class SessionView:
    session: SspImportSession
    rows: list[RowView]
    families: list[FamilyProgress]
    written: int


def family_key(row: SspImportRow) -> str:
    return row.family or NO_FAMILY


def why_not_acceptable(row: SspImportRow) -> str | None:
    if not row.statement:
        return "The source row has no statement text, so there is nothing to write."
    if row.match_kind == "unmatched":
        return ("No objective's text matches this row. Its candidates are similarity "
                "guesses, and a near match is worse than none: write it by hand if it "
                "belongs anywhere.")
    return None


def acceptable_onto(row: SspImportRow) -> tuple[str, ...]:
    if why_not_acceptable(row):
        return ()
    if row.match_kind == "exact":
        return (str(row.matched_objective_id),)
    if row.match_kind in PICKABLE_KINDS:
        return tuple(c["objective_id"] for c in row.candidates)
    return ()


def progress(rows: list[SspImportRow]) -> list[FamilyProgress]:
    """Per family, in the order the families first appear in the file."""
    order: list[str] = []
    totals: dict[str, list] = {}
    for r in sorted(rows, key=lambda r: r.number):
        k = family_key(r)
        if k not in totals:
            order.append(k)
            totals[k] = [r.heading or NO_FAMILY, 0, 0]
        totals[k][1] += 1
        totals[k][2] += r.decision != "pending"
    return [FamilyProgress(k, *totals[k]) for k in order]


def view(
    session: Session, org_id: uuid.UUID, assessment_id: uuid.UUID, session_id: uuid.UUID
) -> SessionView:
    _assessment(session, org_id, assessment_id)
    s = _session(session, org_id, assessment_id, session_id)
    rows = list(session.scalars(
        select(SspImportRow).where(SspImportRow.session_id == s.id, SspImportRow.org_id == org_id)
        .order_by(SspImportRow.number)
    ))
    obj_ids = {r.matched_objective_id for r in rows if r.matched_objective_id}
    obj_ids |= {r.decided_objective_id for r in rows if r.decided_objective_id}
    labels = {}
    if obj_ids:
        for oid, key, text, cid in session.execute(
            select(AssessmentObjective.id, AssessmentObjective.objective_key,
                   AssessmentObjective.text, Control.control_id)
            .join(Control, AssessmentObjective.control_id == Control.id)
            .where(AssessmentObjective.id.in_(obj_ids))
        ):
            labels[oid] = (f"{cid}[{key}]", text)
    existing = {}
    if obj_ids:
        for st in session.scalars(
            select(ImplementationStatement).where(
                ImplementationStatement.assessment_id == assessment_id,
                ImplementationStatement.objective_id.in_(obj_ids),
            )
        ):
            existing[st.objective_id] = st
    written_ids = {r.statement_id for r in rows if r.statement_id}
    by_objective: dict[uuid.UUID, list[int]] = {}
    for r in rows:
        if r.matched_objective_id:
            by_objective.setdefault(r.matched_objective_id, []).append(r.number)

    out = []
    for r in rows:
        target = r.decided_objective_id or r.matched_objective_id
        st = existing.get(target) if target else None
        out.append(RowView(
            row=r,
            objective_ref=labels.get(r.matched_objective_id, (None, None))[0],
            objective_text=labels.get(r.matched_objective_id, (None, None))[1],
            decided_ref=labels.get(r.decided_objective_id, (None, None))[0],
            acceptable_onto=acceptable_onto(r),
            not_acceptable=why_not_acceptable(r),
            existing=ExistingStatement(st.id, st.body, st.status, st.updated_at,
                                       st.id in written_ids) if st else None,
            shares_objective_with=tuple(
                n for n in by_objective.get(r.matched_objective_id, []) if n != r.number
            ),
        ))
    return SessionView(s, out, progress(rows), len(written_ids))


def sessions(session: Session, org_id: uuid.UUID, assessment_id: uuid.UUID):
    """Every session on the assessment, newest first, with its counts:
    an abandoned one is listed, not hidden."""
    _assessment(session, org_id, assessment_id)
    counts = (
        select(SspImportRow.session_id,
               func.count().label("rows"),
               func.count().filter(SspImportRow.decision != "pending").label("decided"),
               func.count(SspImportRow.statement_id).label("written"))
        .where(SspImportRow.org_id == org_id)
        .group_by(SspImportRow.session_id).subquery()
    )
    return session.execute(
        select(SspImportSession, counts.c.rows, counts.c.decided, counts.c.written)
        .outerjoin(counts, counts.c.session_id == SspImportSession.id)
        .where(SspImportSession.org_id == org_id,
               SspImportSession.assessment_id == assessment_id)
        .order_by(SspImportSession.started_at.desc(), SspImportSession.id)
    ).all()


# ---------------------------------------------------------------------------
# Deciding a row
# ---------------------------------------------------------------------------

ACTIONS = ("accept", "edit", "reject", "keep_existing", "reopen")


def _provenance(s: SspImportSession, r: SspImportRow, decision: str, actor: str,
                at: datetime) -> dict:
    p = {
        "source": "ssp_import",
        "importer": s.importer,
        "session_id": str(s.id),
        "file_name": s.file_name,
        "file_sha256": s.file_sha256,
        "source_row": r.number,
        "source_heading": r.heading,
        "source_control_text": r.control_text,
        # The source document's claim about the control, never a status here.
        "source_status_claim": r.source_status,
        "match_kind": r.match_kind,
        "decision": decision,
        "decided_by": actor,
        "decided_at": at.isoformat(),
    }
    assert tuple(p) == PROVENANCE_KEYS
    return p


def _conflict(st: ImplementationStatement, ref: str) -> Refused:
    return Refused(409, {
        "reason": "conflict",
        "message": (f"{ref} already has a statement ({st.status}). Keep it, or replace it "
                    "with this row's text; nothing is written until you choose."),
        "statement": {"id": str(st.id), "body": st.body, "status": st.status,
                      "updated_at": st.updated_at.isoformat()},
    })


def _ref(session: Session, objective_id: uuid.UUID) -> str:
    key, cid = session.execute(
        select(AssessmentObjective.objective_key, Control.control_id)
        .join(Control, AssessmentObjective.control_id == Control.id)
        .where(AssessmentObjective.id == objective_id)
    ).one()
    return f"{cid}[{key}]"


def decide(
    session: Session, *, org_id: uuid.UUID, assessment_id: uuid.UUID,
    session_id: uuid.UUID, row_id: uuid.UUID, action: str, actor: str,
    objective_id: uuid.UUID | None = None, body: str | None = None,
    replace_statement_id: uuid.UUID | None = None,
    replace_updated_at: datetime | None = None,
) -> SspImportRow:
    if action not in ACTIONS:
        raise Refused(422, f"Unknown action {action!r}; one of {', '.join(ACTIONS)}")
    _assessment(session, org_id, assessment_id)
    s = _session(session, org_id, assessment_id, session_id, lock=True)
    if s.status != "open":
        raise Refused(409, f"This import was {s.status}; its rows can no longer be decided.")
    r = session.scalars(
        select(SspImportRow).where(SspImportRow.id == row_id, SspImportRow.session_id == s.id,
                                   SspImportRow.org_id == org_id)
    ).first()
    if r is None:
        raise Refused(404, "Row not found")
    at = _now()

    if action == "reopen":
        if r.decision != "rejected":
            raise Refused(409, ("Only a rejected row can be reopened. An accepted row has "
                                "written its draft; change the statement on the board."))
        before = {"decision": r.decision, "resolution": r.resolution}
        r.decision, r.resolution, r.decided_objective_id = "pending", None, None
        r.decided_by, r.decided_at = actor, at
        _log_row(session, s, r, "ssp_import.row_reopen", before)
        return r

    if r.decision != "pending":
        raise Refused(409, f"Row {r.number} is already {r.decision}.")

    if action == "reject":
        r.decision, r.decided_by, r.decided_at = "rejected", actor, at
        _log_row(session, s, r, "ssp_import.row_reject", {"decision": "pending"})
        return r

    allowed = acceptable_onto(r)
    if not allowed:
        raise Refused(422, why_not_acceptable(r) or "This row cannot be accepted.")
    if objective_id is None and r.match_kind == "exact":
        objective_id = r.matched_objective_id
    if objective_id is None:
        raise Refused(422, ("This row's text matches more than one objective, or one outside "
                            "its family: choose which objective it belongs to."))
    if str(objective_id) not in allowed:
        raise Refused(422, "That objective is not one this row can be accepted onto.")
    st = _existing(session, assessment_id, objective_id)

    if action == "keep_existing":
        if st is None:
            raise Refused(409, "There is no existing statement on that objective to keep.")
        r.decision, r.resolution = "rejected", "kept_existing"
        r.decided_objective_id, r.decided_by, r.decided_at = objective_id, actor, at
        _log_row(session, s, r, "ssp_import.row_keep_existing", {"decision": "pending"},
                 extra={"kept_statement_id": str(st.id)})
        return r

    if action == "edit":
        if body is None or not body.strip():
            raise Refused(422, "An edited statement cannot be empty.")
        text = body
    else:
        text = r.statement
    decision = "edited" if action == "edit" else "accepted"
    prov = _provenance(s, r, decision, actor, at)
    ref = _ref(session, objective_id)

    if st is not None:
        named = (replace_statement_id == st.id and replace_updated_at is not None
                 and replace_updated_at == st.updated_at)
        if not named:
            raise _conflict(st, ref)
        before = {"body": st.body, "status": st.status, "grounded_in": st.grounded_in}
        st.body, st.status, st.grounded_in = text, "draft", prov
        st.generation_model = st.generation_prompt_version = None
        session.flush()
        r.resolution = "replaced"
        log_event(
            session, org_id=org_id, action="implementation_statement.import_replace",
            entity_type="implementation_statement", entity_id=st.id,
            before_value=before, after_value={"body": text, "status": "draft"},
            context=_stmt_context(s, r, ref),
        )
    else:
        st = ImplementationStatement(
            org_id=org_id, objective_id=objective_id, assessment_id=assessment_id,
            body=text, status="draft", grounded_in=prov,
        )
        try:
            with session.begin_nested():
                session.add(st)
                session.flush()
        except IntegrityError:
            # Written by someone else between the read above and this insert.
            raise _conflict(_existing(session, assessment_id, objective_id), ref) from None
        log_event(
            session, org_id=org_id, action="implementation_statement.import",
            entity_type="implementation_statement", entity_id=st.id,
            after_value={"body": text, "status": "draft"},
            context=_stmt_context(s, r, ref),
        )
    r.decision, r.statement_id, r.decided_objective_id = decision, st.id, objective_id
    r.decided_by, r.decided_at = actor, at
    _log_row(session, s, r, f"ssp_import.row_{action}", {"decision": "pending"},
             extra={"statement_id": str(st.id), "objective": ref})
    return r


def _stmt_context(s: SspImportSession, r: SspImportRow, ref: str) -> dict:
    return {"via": "ssp_import", "assessment_id": str(s.assessment_id),
            "session_id": str(s.id), "source_row": r.number, "objective": ref}


def _log_row(session: Session, s: SspImportSession, r: SspImportRow, action: str,
             before: dict, extra: dict | None = None) -> None:
    session.flush()
    log_event(
        session, org_id=s.org_id, action=action, entity_type="ssp_import_row", entity_id=r.id,
        before_value=before,
        after_value={"decision": r.decision, "resolution": r.resolution, **(extra or {})},
        context={"via": "api", "session_id": str(s.id), "source_row": r.number,
                 "assessment_id": str(s.assessment_id)},
    )


# ---------------------------------------------------------------------------
# Closing
# ---------------------------------------------------------------------------


def close(
    session: Session, *, org_id: uuid.UUID, assessment_id: uuid.UUID,
    session_id: uuid.UUID, outcome: str, actor: str,
) -> SspImportSession:
    """`completed` once every row is decided; `discarded` at any time. A
    discard writes nothing further and removes nothing: drafts already
    written stay, and the session says how many."""
    if outcome not in ("completed", "discarded"):
        raise Refused(422, "outcome must be 'completed' or 'discarded'")
    _assessment(session, org_id, assessment_id)
    s = _session(session, org_id, assessment_id, session_id, lock=True)
    if s.status != "open":
        raise Refused(409, f"This import is already {s.status}.")
    pending, written = session.execute(
        select(func.count().filter(SspImportRow.decision == "pending"),
               func.count(SspImportRow.statement_id))
        .where(SspImportRow.session_id == s.id, SspImportRow.org_id == org_id)
    ).one()
    if outcome == "completed" and pending:
        raise Refused(409, (f"{pending} row(s) are still undecided. Decide them, or discard "
                            "the import to leave them."))
    s.status, s.closed_by, s.closed_at = outcome, actor, _now()
    session.flush()
    verb = "complete" if outcome == "completed" else "discard"
    log_event(
        session, org_id=org_id, action=f"ssp_import.{verb}",
        entity_type="ssp_import_session", entity_id=s.id,
        before_value={"status": "open"},
        after_value={"status": outcome, "undecided_rows": pending, "drafts_written": written},
        context={"via": "api", "assessment_id": str(assessment_id)},
    )
    return s
