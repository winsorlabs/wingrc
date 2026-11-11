"""Read implementation statements out of a customer's existing SSP (.docx)
and match each to an 800-171A assessment objective. Nothing is written
anywhere -- the caller decides what to do with the result, as
importers/document.py says of its own.

This is a table parse, not an AI extraction. document.py exists for
unstructured vendor PDFs, where a model has to infer coverage. The SSP
shape recognised here is already structured: under a family heading
("8.1 Access Control"), a table whose columns are Control, Status, Notes,
Implementation Statement and RACI, one row per assessment objective, the
Control cell holding the objective's 800-171A text. Every cell is a known
field, so extraction is deterministic: the same file gives the same rows,
which is testable in a way a model call is not.

One shape, recognised or refused. A file without that table is an error
naming the file, never a best-effort guess: other SSPs will not look like
this one (docs/roadmap.md, "Importing implementation statements from a
customer's SSP").

What is read, and what is not:
- **Implementation Statement** is the narrative a statement's body would
  come from.
- **Status** is read to be reported, never imported. A source that says
  "Met" is another document's claim about a system WinGRC has not
  assessed; nothing here can move control_state or SPRS.
- **Notes** is a second narrative. Counted, not imported, and never
  joined to the statement: that would invent a paragraph nobody wrote.
- **RACI** holds names and email addresses. Only whether a cell is empty
  is read; its content never leaves this module.
- `-` is the source's empty marker and reads as empty.

Matching is exact after normalisation (case, whitespace, curly quotes, a
trailing period) and must be unique within the row's family heading. A
wrong match files one customer's statement under the wrong control in a
document an assessor reads, which is worse than no match, so everything
else goes to review with ranked candidates and is never accepted.
"""

from __future__ import annotations

import difflib
import re
import unicodedata
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

HEADER = ("control", "status", "notes", "implementation statement", "raci")

FAMILIES = {
    "access control": "AC",
    "awareness and training": "AT",
    "audit and accountability": "AU",
    "configuration management": "CM",
    "identification and authentication": "IA",
    "incident response": "IR",
    "maintenance": "MA",
    "media protection": "MP",
    "personnel security": "PS",
    "physical protection": "PE",
    "risk assessment": "RA",
    "security assessment": "CA",
    "system and communications protection": "SC",
    "system and information integrity": "SI",
}

_EMPTY_MARKERS = {"", "-", "–", "—"}
_HEADING = re.compile(r"^(?:\d+(?:\.\d+)*\.?\s+)?(?P<name>[a-z ]+?)(?:\s*\((?P<code>[a-z]{2})\))?$")

# Near-duplicate threshold for statements under one control, after
# whitespace is collapsed. Reported for a person to judge, never applied.
NEAR_DUPLICATE_RATIO = 0.95


class SspNotRecognised(ValueError):
    """The file is not the SSP shape this importer reads."""


@dataclass(frozen=True)
class SourceRow:
    number: int  # 1-based, across every recognised table in document order
    heading: str | None
    family: str | None
    control_text: str
    status: str | None
    notes: str | None
    statement: str | None
    raci_present: bool


@dataclass(frozen=True)
class CatalogObjective:
    objective_id: str
    ref: str  # "AC.L2-3.1.1[a]"
    control_id: str  # "AC.L2-3.1.1"
    family: str  # "AC"
    text: str


@dataclass(frozen=True)
class RowMatch:
    row: SourceRow
    # exact | family_mismatch | ambiguous | unmatched | no_family
    kind: str
    objective: CatalogObjective | None = None
    candidates: tuple[tuple[str, float], ...] = ()


@dataclass(frozen=True)
class DuplicateGroup:
    control_id: str
    refs: tuple[str, ...]
    identical: bool  # identical once whitespace is collapsed
    lowest_ratio: float


@dataclass
class Preview:
    file_name: str
    rows: list[SourceRow]
    matches: list[RowMatch]
    duplicates: list[DuplicateGroup] = field(default_factory=list)

    def of(self, kind: str) -> list[RowMatch]:
        return [m for m in self.matches if m.kind == kind]


def normalise(text: str) -> str:
    s = unicodedata.normalize("NFKC", text)
    s = s.translate({0x2018: "'", 0x2019: "'", 0x201C: '"', 0x201D: '"'})
    s = re.sub(r"\s+", " ", s).strip().casefold()
    return s.rstrip(".").rstrip()


def _cell(text: str) -> str | None:
    t = re.sub(r"\s+", " ", text).strip()
    return None if t in _EMPTY_MARKERS else text.strip()


def _family_of(heading: str) -> str | None:
    m = _HEADING.match(normalise(heading))
    if not m:
        return None
    code = FAMILIES.get(m.group("name").strip())
    if code and m.group("code") and m.group("code").upper() != code:
        return None
    return code


def parse(path: Path) -> list[SourceRow]:
    """Every objective row in the file, in document order, or
    SspNotRecognised naming the file."""
    from docx import Document
    from docx.opc.exceptions import PackageNotFoundError
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    try:
        doc = Document(str(path))
    except (PackageNotFoundError, zipfile.BadZipFile, KeyError, ValueError) as e:
        raise SspNotRecognised(f"{path.name}: not a .docx this importer can open ({e})") from e

    rows: list[SourceRow] = []
    heading: str | None = None
    family: str | None = None
    tables_seen = 0
    for child in doc.element.body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            text = Paragraph(child, doc).text.strip()
            code = _family_of(text) if text else None
            if code:
                heading, family = text, code
        elif tag == "tbl":
            table = Table(child, doc)
            if not table.rows:
                continue
            header = tuple(normalise(c.text) for c in table.rows[0].cells)
            if header != HEADER:
                continue
            tables_seen += 1
            for tr in table.rows[1:]:
                cells = [c.text for c in tr.cells]
                control = _cell(cells[0])
                if control is None:
                    continue
                rows.append(SourceRow(
                    number=len(rows) + 1,
                    heading=heading,
                    family=family,
                    control_text=control,
                    status=_cell(cells[1]),
                    notes=_cell(cells[2]),
                    statement=_cell(cells[3]),
                    raci_present=_cell(cells[4]) is not None,
                ))
    if not tables_seen:
        raise SspNotRecognised(
            f"{path.name}: no table with the columns Control, Status, Notes, "
            "Implementation Statement, RACI. This importer reads that one SSP "
            "shape and refuses anything else rather than guess."
        )
    return rows


def match(rows: list[SourceRow], catalog: list[CatalogObjective]) -> list[RowMatch]:
    by_text: dict[str, list[CatalogObjective]] = {}
    for o in catalog:
        by_text.setdefault(normalise(o.text), []).append(o)
    out: list[RowMatch] = []
    for row in rows:
        hits = sorted(by_text.get(normalise(row.control_text), []), key=lambda o: o.ref)
        if row.family is None:
            out.append(RowMatch(row, "no_family", candidates=_rank(row, catalog, hits)))
            continue
        in_family = [o for o in hits if o.family == row.family]
        if len(in_family) == 1:
            out.append(RowMatch(row, "exact", objective=in_family[0]))
        elif len(in_family) > 1:
            out.append(RowMatch(row, "ambiguous", candidates=_exact(in_family)))
        elif hits:
            out.append(RowMatch(row, "family_mismatch", candidates=_exact(hits)))
        else:
            out.append(RowMatch(row, "unmatched", candidates=_rank(row, catalog, [])))
    return out


def _exact(objectives: list[CatalogObjective]) -> tuple[tuple[str, float], ...]:
    return tuple((o.ref, 1.0) for o in objectives)


def _rank(row: SourceRow, catalog: list[CatalogObjective], hits: list[CatalogObjective]):
    if hits:
        return _exact(hits)
    target = normalise(row.control_text)
    pool = [o for o in catalog if row.family is None or o.family == row.family] or catalog
    scored = sorted(
        ((round(difflib.SequenceMatcher(None, target, normalise(o.text)).ratio(), 2), o.ref)
         for o in pool),
        key=lambda s: (-s[0], s[1]),
    )
    return tuple((ref, score) for score, ref in scored[:3])


def duplicates(matches: list[RowMatch]) -> list[DuplicateGroup]:
    """Statements under one control that are the same paragraph, or nearly.
    Reported for a person to collapse or keep; never applied."""
    by_control: dict[str, list[tuple[str, str]]] = {}
    for m in matches:
        if m.kind == "exact" and m.row.statement:
            flat = re.sub(r"\s+", " ", m.row.statement).strip()
            by_control.setdefault(m.objective.control_id, []).append((m.objective.ref, flat))
    groups: list[DuplicateGroup] = []
    for control_id in sorted(by_control):
        items = sorted(by_control[control_id])
        used: set[int] = set()
        for i, (ref_i, text_i) in enumerate(items):
            if i in used:
                continue
            refs, lowest, identical = [ref_i], 1.0, True
            for j in range(i + 1, len(items)):
                if j in used:
                    continue
                ref_j, text_j = items[j]
                ratio = difflib.SequenceMatcher(None, text_i, text_j).ratio()
                if text_j == text_i or ratio >= NEAR_DUPLICATE_RATIO:
                    used.add(j)
                    refs.append(ref_j)
                    identical = identical and text_j == text_i
                    lowest = min(lowest, ratio)
            if len(refs) > 1:
                groups.append(DuplicateGroup(control_id, tuple(refs), identical, round(lowest, 3)))
    return groups


def preview(path: Path, catalog: list[CatalogObjective]) -> Preview:
    rows = parse(path)
    matches = match(rows, catalog)
    return Preview(path.name, rows, matches, duplicates(matches))


def status_tally(rows: list[SourceRow]) -> list[tuple[str, int]]:
    tally = Counter(r.status or "(empty)" for r in rows)
    return sorted(tally.items(), key=lambda kv: (-kv[1], kv[0]))
