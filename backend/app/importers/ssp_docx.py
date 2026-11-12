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

A cell's text is read by `_cell_text`, not python-docx's `cell.text`,
which loses things without a word: it joins paragraphs and line breaks
alike with one newline, drops a bullet, and reads only a paragraph's
direct runs, so text inside a content control, a field or a smart tag
vanishes. What a statement body holds (tests/test_ssp_docx.py's
fidelity tests pin each case):
- every character of the cell's text as written: curly quotes, dashes
  and the source's own spacing, with no normalisation;
- a paragraph break as a blank line, a line break as a newline;
- a bulleted paragraph as `- `, a numbered one as `1. `, indented two
  spaces per level;
- each paragraph trimmed at its ends, and empty paragraphs dropped.
A cell this cannot read faithfully refuses the file, naming the row and
what it found: a nested table, a picture, an equation, a symbol-font
character, unaccepted tracked changes, a merged cell, or list numbering
other than bullets and digits. Arriving subtly wrong is worse than not
arriving, because review would read it as right.

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
from dataclasses import dataclass
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


@dataclass
class Preview:
    file_name: str
    rows: list[SourceRow]
    matches: list[RowMatch]

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


_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_UNREADABLE = {
    "tbl": "a nested table", "drawing": "a picture", "pict": "a picture",
    "object": "an embedded object", "sym": "a symbol-font character",
    "ins": "unaccepted tracked changes", "del": "unaccepted tracked changes",
    "moveFrom": "unaccepted tracked changes", "moveTo": "unaccepted tracked changes",
}
_MATH = "{http://schemas.openxmlformats.org/officeDocument/2006/math}"


class _Unreadable(Exception):
    pass


class _Lists:
    """List numbering, resolved from numbering.xml: what prefix a
    paragraph gets, counted per cell."""

    def __init__(self, doc):
        self._doc = doc
        self._levels: dict[tuple[str, int], tuple[str, int]] = {}
        try:
            numbering = doc.part.numbering_part.element
        except (KeyError, NotImplementedError):
            return
        abstract = {a.get(_W + "abstractNumId"): a for a in numbering.iter(_W + "abstractNum")}
        for num in numbering.iter(_W + "num"):
            ref = num.find(_W + "abstractNumId")
            a = abstract.get(ref.get(_W + "val")) if ref is not None else None
            if a is None:
                continue
            for lvl in a.iter(_W + "lvl"):
                fmt = lvl.find(_W + "numFmt")
                start = lvl.find(_W + "start")
                self._levels[(num.get(_W + "numId"), int(lvl.get(_W + "ilvl")))] = (
                    fmt.get(_W + "val") if fmt is not None else "decimal",
                    int(start.get(_W + "val")) if start is not None else 1,
                )

    def _num_pr(self, p):
        num_pr = p.find(f"{_W}pPr/{_W}numPr")
        if num_pr is not None:
            return num_pr
        style_el = p.find(f"{_W}pPr/{_W}pStyle")
        style_id = style_el.get(_W + "val") if style_el is not None else None
        styles = self._doc.styles.element
        while style_id:
            style = next((s for s in styles.iter(_W + "style")
                          if s.get(_W + "styleId") == style_id), None)
            if style is None:
                return None
            num_pr = style.find(f"{_W}pPr/{_W}numPr")
            if num_pr is not None:
                return num_pr
            based = style.find(_W + "basedOn")
            style_id = based.get(_W + "val") if based is not None else None
        return None

    def prefix(self, p, counters: dict) -> str | None:
        num_pr = self._num_pr(p)
        if num_pr is None:
            return None
        num_id_el, ilvl_el = num_pr.find(_W + "numId"), num_pr.find(_W + "ilvl")
        num_id = num_id_el.get(_W + "val") if num_id_el is not None else None
        if num_id in (None, "0"):
            return None
        ilvl = int(ilvl_el.get(_W + "val")) if ilvl_el is not None else 0
        fmt, start = self._levels.get((num_id, ilvl), ("bullet", 1))
        indent = "  " * ilvl
        for key in [k for k in counters if k[0] == num_id and k[1] > ilvl]:
            del counters[key]
        if fmt == "none":
            return indent
        if fmt == "bullet":
            return indent + "- "
        if fmt == "decimal":
            n = counters.get((num_id, ilvl), start - 1) + 1
            counters[(num_id, ilvl)] = n
            return f"{indent}{n}. "
        raise _Unreadable(f"list numbering in the format {fmt!r}")


def _block_paragraphs(container):
    for child in container.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            yield child
        elif tag in ("sdt", "customXml"):
            content = child.find(_W + "sdtContent") if tag == "sdt" else child
            if content is not None:
                yield from _block_paragraphs(content)
        elif tag == "tbl":
            raise _Unreadable(_UNREADABLE["tbl"])


def _paragraph_text(p) -> str:
    parts: list[str] = []
    for el in p.iter():
        if el.tag.startswith(_MATH):
            raise _Unreadable("an equation")
        tag = el.tag.rsplit("}", 1)[-1] if el.tag.startswith(_W) else None
        if tag in _UNREADABLE:
            raise _Unreadable(_UNREADABLE[tag])
        if tag == "t":
            parts.append(el.text or "")
        elif tag == "tab" and el.getparent().tag == _W + "r":
            parts.append("\t")
        elif tag in ("br", "cr"):
            parts.append("\n")
        elif tag == "noBreakHyphen":
            parts.append("\u2011")
    return "".join(parts)


def _cell_text(tc, lists: _Lists) -> str:
    """A cell's text as a statement body holds it (the module docstring)."""
    blocks: list[tuple[str, bool]] = []
    counters: dict = {}
    for p in _block_paragraphs(tc):
        prefix = lists.prefix(p, counters)
        text = _paragraph_text(p).strip()
        if text:
            blocks.append(((prefix or "") + text, prefix is not None))
    out = ""
    for i, (text, is_item) in enumerate(blocks):
        if i:
            out += "\n" if is_item and blocks[i - 1][1] else "\n\n"
        out += text
    return out


def _merged(tc) -> bool:
    span = tc.find(f"{_W}tcPr/{_W}gridSpan")
    return (tc.find(f"{_W}tcPr/{_W}vMerge") is not None
            or (span is not None and int(span.get(_W + "val", "1")) > 1))


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

    lists = _Lists(doc)
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
                tcs = tr._tr.findall(_W + "tc")
                try:
                    if len(tcs) != len(HEADER) or any(_merged(tc) for tc in tcs):
                        raise _Unreadable("a merged cell")
                    cells = [_cell_text(tc, lists) for tc in tcs[:4]]
                except _Unreadable as e:
                    raise SspNotRecognised(
                        f"{path.name}: the row after {len(rows)} read row(s) holds "
                        f"{e}, which this importer cannot read faithfully"
                    ) from e
                # RACI: only whether it is empty is read, never its content.
                cells.append("".join(t.text or "" for t in tcs[4].iter(_W + "t")))
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


def preview(path: Path, catalog: list[CatalogObjective]) -> Preview:
    rows = parse(path)
    matches = match(rows, catalog)
    return Preview(path.name, rows, matches)


def status_tally(rows: list[SourceRow]) -> list[tuple[str, int]]:
    tally = Counter(r.status or "(empty)" for r in rows)
    return sorted(tally.items(), key=lambda kv: (-kv[1], kv[0]))
