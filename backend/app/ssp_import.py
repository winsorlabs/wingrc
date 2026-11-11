"""Preview importing a customer's SSP into one assessment's implementation
statements: read, match, report, write nothing (docs/roadmap.md,
"Importing implementation statements from a customer's SSP").

The parse and the match are importers/ssp_docx.py, with no database. This
module adds the target: it names an org and an assessment, refuses if it
cannot see them, reads the assessment's framework catalog and its
existing statements, and says what a write would do. It never writes:
control_state, status and SPRS are not touched, and neither is
implementation_statement (tests/test_ssp_import.py asserts all four).

Visibility is checked before anything is reported (the `sprs-rescore`
lesson, docs/roadmap.md, "A zero nobody checked"). The org is set through
rls.set_current_org, so as `wingrc_app` the command sees that org's rows.
The assessment the caller named must then be visible in it: a hidden one
is refused, never read as "no existing statements". The catalog has no
RLS, and an empty one is refused too, since every row would then read as
unmatched.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from .importers import ssp_docx
from .models import (
    Assessment,
    AssessmentObjective,
    Control,
    ImplementationStatement,
    Organization,
)
from .rls import set_current_org


class TargetNotVisible(RuntimeError):
    """The named org or assessment cannot be seen, or does not exist."""


@dataclass(frozen=True)
class Plan:
    would_create: tuple[str, ...]  # objective refs
    already_has_statement: tuple[str, ...]  # left alone
    two_rows_one_objective: tuple[str, ...]  # neither written
    no_statement_text: tuple[str, ...]  # matched, nothing to write


@dataclass(frozen=True)
class Result:
    org_name: str
    assessment_name: str
    file_sha256: str
    preview: ssp_docx.Preview
    plan: Plan


def _catalog(session: Session, framework_id: uuid.UUID) -> list[ssp_docx.CatalogObjective]:
    rows = session.execute(
        select(AssessmentObjective.id, AssessmentObjective.objective_key,
               AssessmentObjective.text, Control.control_id, Control.family)
        .join(Control, AssessmentObjective.control_id == Control.id)
        .where(Control.framework_id == framework_id)
        .order_by(Control.control_id, AssessmentObjective.objective_key, AssessmentObjective.id)
    ).all()
    return [
        ssp_docx.CatalogObjective(str(oid), f"{cid}[{key}]", cid, fam, text)
        for oid, key, text, cid, fam in rows
    ]


def preview_import(
    session: Session, path: Path, *, org_id: uuid.UUID, assessment_id: uuid.UUID
) -> Result:
    set_current_org(session, org_id)
    # select(), not session.get(): get() answers from the identity map
    # without a query, so it could "see" a row RLS would hide.
    org = session.scalars(select(Organization).where(Organization.id == org_id)).first()
    if org is None:
        raise TargetNotVisible(f"no organization {org_id}")
    assessment = session.scalars(
        select(Assessment).where(Assessment.id == assessment_id)
    ).first()
    if assessment is None or assessment.org_id != org_id:
        raise TargetNotVisible(
            f"assessment {assessment_id} is not visible in {org.name} ({org_id}): it does "
            "not exist, belongs to another org, or row-level security hides it"
        )
    catalog = _catalog(session, assessment.framework_id)
    if not catalog:
        raise TargetNotVisible(
            f"the framework of assessment {assessment_id} has no objectives to match against"
        )

    preview = ssp_docx.preview(path, catalog)
    existing = set(
        str(oid) for oid in session.scalars(
            select(ImplementationStatement.objective_id)
            .where(ImplementationStatement.assessment_id == assessment_id)
        )
    )
    exact = preview.of("exact")
    per_objective: dict[str, int] = {}
    for m in exact:
        per_objective[m.objective.objective_id] = per_objective.get(m.objective.objective_id, 0) + 1
    create, has, twice, empty = set(), set(), set(), set()
    for m in exact:
        o = m.objective
        if per_objective[o.objective_id] > 1:
            twice.add(o.ref)
        elif not m.row.statement:
            empty.add(o.ref)
        elif o.objective_id in existing:
            has.add(o.ref)
        else:
            create.add(o.ref)
    plan = Plan(*(tuple(sorted(s)) for s in (create, has, twice, empty)))
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    session.rollback()
    return Result(org.name, assessment.name, digest, preview, plan)


_KIND_TITLES = (
    ("family_mismatch", "matched an objective outside the row's family heading -- NOT accepted"),
    ("ambiguous", "matched more than one objective in the family -- NOT accepted"),
    ("no_family", "under no recognised family heading -- NOT accepted"),
    ("unmatched", "matched no objective -- NOT accepted"),
)


def render(result: Result) -> list[str]:
    """The report, one line per list item. A function of the file and the
    target alone: no clock, no path, nothing that differs between runs."""
    p = result.preview
    exact = p.of("exact")
    rejected = len(p.rows) - len(exact)
    lines = [
        f"Source: {p.file_name} (SHA-256 {result.file_sha256})",
        f"Target: {result.org_name} / {result.assessment_name}",
        f"Rows: {len(p.rows)}. Matched exactly: {len(exact)} "
        f"({_pct(len(exact), len(p.rows))}). NOT matched: {rejected}.",
        "",
        "Rows per family heading:",
    ]
    per_family: dict[str, list[int]] = {}
    for m in p.matches:
        key = m.row.heading or "(no family heading)"
        counts = per_family.setdefault(key, [0, 0])
        counts[0] += 1
        counts[1] += m.kind == "exact"
    for heading in sorted(per_family):
        total, ok = per_family[heading]
        lines.append(f"  {heading}: {total} row(s), {ok} matched")

    for kind, title in _KIND_TITLES:
        found = p.of(kind)
        lines += ["", f"{len(found)} row(s) {title}:"]
        for m in found:
            lines.append(f"  row {m.row.number} [{m.row.heading or '-'}]: {m.row.control_text}")
            if m.candidates:
                lines.append("    candidates: " + ", ".join(
                    f"{ref} ({score:.2f})" for ref, score in m.candidates))

    lines += ["", f"{len(exact)} row(s) matched exactly:"]
    for m in exact:
        lines.append(f"  row {m.row.number} -> {m.objective.ref}")

    lines += ["", f"{len(p.duplicates)} group(s) of duplicate statements under one control "
              f"(identical, or at least {ssp_docx.NEAR_DUPLICATE_RATIO:.0%} alike); "
              "reported for a person to judge, never collapsed:"]
    for g in p.duplicates:
        how = ("identical" if g.identical
               else f"near-identical, lowest similarity {g.lowest_ratio:.3f}")
        lines.append(f"  {g.control_id}: {', '.join(g.refs)} ({how})")

    lines += ["", "What the source's Status column says (the source's claim, never imported):"]
    for status, n in ssp_docx.status_tally(p.rows):
        lines.append(f"  {status}: {n}")
    lines.append("  None of it is imported: no control_state, status or SPRS changes.")
    notes = sum(1 for r in p.rows if r.notes)
    raci = sum(1 for r in p.rows if r.raci_present)
    lines.append(f"Notes column: {notes} row(s) with text; not imported in this slice.")
    lines.append(
        f"RACI column: {raci} row(s) with content (names, emails); not read, not imported."
    )

    plan = result.plan
    lines += [
        "",
        "What a write would do (nothing is written by this command):",
        f"  create a draft statement: {len(plan.would_create)}",
        f"  objective already has a statement, left alone: {len(plan.already_has_statement)}"
        + _refs(plan.already_has_statement),
        f"  two source rows for one objective, neither written: {len(plan.two_rows_one_objective)}"
        + _refs(plan.two_rows_one_objective),
        f"  matched with no statement text: {len(plan.no_statement_text)}"
        + _refs(plan.no_statement_text),
        "",
        "PREVIEW ONLY -- nothing was written.",
    ]
    return lines


def _pct(n: int, d: int) -> str:
    return f"{n / d:.0%}" if d else "n/a"


def _refs(refs: tuple[str, ...]) -> str:
    return f" ({', '.join(refs)})" if refs else ""
