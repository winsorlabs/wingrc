"""Extract the ODP Values tables from the DoD CIO memorandum of 10 April
2025, "Department of Defense Organization-Defined Parameters for National
Institute of Standards and Technology Special Publication 800-171 Revision
3" (docs/reference/OrgDefinedParmsNISTSP800-171.pdf), into
backend/app/seeds/odp_dod_rev3.json. README.md in this directory has the
provenance and what the output is for.

Usage:
    pip install pdfplumber==0.11.7   # not an app dependency -- ad hoc only
    python scripts/odp/extract_odp.py docs/reference/OrgDefinedParmsNISTSP800-171.pdf
    python scripts/odp/extract_odp.py <pdf> --check   # exit 1 if the output would change

From the tables, not a regex over the text. Each "ODP Values" table has
three columns -- ODP Identifier | ODP Assignment Text | ODP Value -- drawn
with horizontal rules only, so the columns are split by word position
against the table's own header row, and the rows by the rules.

What is done to the text, stated so it can be checked; nothing else is
changed -- no spelling, case or punctuation:
- A cell's lines are joined with a single space, except that a line ending
  in a hyphen joins the next with nothing between ("organization-" +
  "defined" is "organization-defined"). Every line-end hyphen in the memo
  is a real one: all 52 fall at a compound ("security-relevant",
  "re-authentication"), none mid-word, so Word was not hyphenating.
- Runs of spaces collapse to one (the memo double-spaces after a full
  stop in places).
- In a value, a line that starts an item ("1) ", "a) ", "1. ", "(i) ")
  starts a new line, so a list stays a list; how far an item is indented
  is not kept.
- A footnote mark, printed as a small raised digit, is written as that
  superscript character (U+00B9 for 1), not merged into the word as a
  plain digit. The footnotes themselves are kept, verbatim.
- A row the page break split (no identifier, no assignment text) is the
  previous row's value continuing, and is appended to it.

The requirement text's own [Assignment: ...] and [Selection ...: ...]
markers are extracted the same way and must equal each table's assignment
text, which checks the joining against a second copy of the same words.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

try:
    import pdfplumber
except ImportError:
    print("pip install pdfplumber==0.11.7", file=sys.stderr)
    raise

OUT = Path(__file__).resolve().parents[2] / "backend" / "app" / "seeds" / "odp_dod_rev3.json"
IDENT = re.compile(r"^03\.\d{2}\.\d{2}(\.[a-z])?(\.\d{2})?$")
IDENT_IN_TEXT = re.compile(r"\((03\.[0-9]{2}\.[0-9]{2}(?:\.[a-z])?(?:\.[0-9]{2})?)\)")
HEADING = re.compile(r"^(3\.\d{1,2}\.\d{1,2}) (\S.*)$")
FOOTNOTE = re.compile(r"^(\d) (\S.*)$")
ITEM = re.compile(r"^((\d{1,2}|[a-z])\)|\d{1,2}\.|\([ivx]+\)) ")
PAGE_NUMBER = re.compile(r"^\d{1,2}$")
# Where the memo's table and its own requirement text differ, checked by eye
# against the page (2026-11-05). The table is the source for every row; the
# difference is the document's, not the extraction's.
KNOWN_DIFFERENCES = {
    # The table quotes only the assignment nested inside the selection.
    "03.01.08.b": (
        "[Assignment: organization-defined time period]",
        "[Selection (one or more): lock the account or node for an [Assignment: "
        "organization-defined time period]; lock the account or node until released by an "
        "administrator; delay next logon prompt; notify system administrator; take other action]",
    ),
    # "nondisclosure" in the table, "non-disclosure" in the requirement text.
    "03.12.05.a": (
        "[Selection (one or more): interconnection security agreements; information exchange "
        "security agreements; memoranda of understanding or agreement; service-level agreements; "
        "user agreements; nondisclosure agreements; other types of agreements]",
        "[Selection (one or more): interconnection security agreements; information exchange "
        "security agreements; memoranda of understanding or agreement; service-level agreements; "
        "user agreements; non-disclosure agreements; other types of agreements]",
    ),
}
# Lines in a cell are about 12.6 points apart; a footnote mark sits about 3
# points above its line.
LINE_TOLERANCE = 4.0
BODY_SIZE = 9.5
SUPERSCRIPT = str.maketrans(
    "0123456789", "⁰¹²³⁴⁵⁶⁷⁸⁹"
)


def join_lines(lines: list[str]) -> str:
    out = ""
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if not out:
            out = line
        elif out.endswith("-"):
            out += line
        else:
            out += " " + line
    return out


def join_value(lines: list[tuple[str, float]]) -> str:
    """A value's lines: an item starts a new line, and so does a line back at
    the left margin in a list whose wrapped lines are indented (03.03.01.a's
    closing "For additional guidance" line). In a list whose wraps return to
    the margin (03.16.03.a), a margin line is a wrap like any other."""
    margin = min((x for _, x in lines), default=0.0)
    items = [i for i, (t, _) in enumerate(lines) if ITEM.match(t)]
    hanging = any(
        x > margin + 5 and not ITEM.match(t) for i, (t, x) in enumerate(lines) if i > min(items, default=len(lines))
    )
    paragraphs: list[list[str]] = []
    for i, (text, x) in enumerate(lines):
        starts = ITEM.match(text) or (
            hanging and items and i > items[0] and x <= margin + 1 and not ITEM.match(text)
        )
        if starts or not paragraphs:
            paragraphs.append([text])
        else:
            paragraphs[-1].append(text)
    return "\n".join(join_lines(p) for p in paragraphs)


def lines_of(words: list[dict]) -> list[str]:
    return [t for t, _ in placed_lines(words)]


def placed_lines(words: list[dict]) -> list[tuple[str, float]]:
    """Words in reading order, grouped into lines by their top edge. A small
    digit touching the word before it is a footnote mark."""
    lines: list[list[dict]] = []
    for w in sorted(words, key=lambda w: (w["top"], w["x0"])):
        line = next((ln for ln in lines if abs(ln[0]["top"] - w["top"]) <= LINE_TOLERANCE), None)
        if line is None:
            lines.append([w])
        else:
            line.append(w)
    lines.sort(key=lambda ln: ln[0]["top"])
    out = []
    for line in lines:
        text, prev = "", None
        for x in sorted(line, key=lambda w: w["x0"]):
            # Words split only at spaces, or where the size changes: one that
            # touches the word before it was printed without a space.
            touching = prev is not None and x["x0"] - prev["x1"] < 1.0
            if x["size"] < BODY_SIZE and x["text"].isdigit() and touching:
                text += x["text"].translate(SUPERSCRIPT)
            elif touching:
                text += x["text"]
            else:
                text += (" " if text else "") + x["text"]
            prev = x
        out.append((text, min(w["x0"] for w in line)))
    return out


def markers_in(text: str) -> dict[str, str]:
    """Each "(03.xx.xx...)" in the requirement text, with the bracketed
    marker that closes just before it, nested brackets included."""
    found = {}
    for m in IDENT_IN_TEXT.finditer(text):
        end = m.start()
        while end > 0 and text[end - 1] == " ":
            end -= 1
        if end == 0 or text[end - 1] != "]":
            continue
        depth, i = 0, end - 1
        while i >= 0:
            depth += {"]": 1, "[": -1}.get(text[i], 0)
            if depth == 0:
                break
            i -= 1
        found[m.group(1)] = text[i:end]
    return found


def column_starts(header: list[dict]) -> tuple[float, float]:
    """x where the assignment-text and value columns begin: the "ODP"
    printed before "Assignment" and before "Value" in the header row."""
    ordered = sorted(header, key=lambda w: (w["x0"], w["top"]))
    starts = {}
    for i, w in enumerate(ordered):
        if w["text"] in ("Assignment", "Value"):
            odp = [p for p in ordered[:i] if p["text"] == "ODP" and w["x0"] - p["x0"] < 40]
            starts[w["text"]] = max(p["x0"] for p in odp) if odp else w["x0"]
    return starts["Assignment"] - 3, starts["Value"] - 3


def extract(pdf_path: Path) -> dict:
    raw = pdf_path.read_bytes()
    rows: list[dict] = []
    markers: dict[str, str] = {}
    footnotes: dict[str, str] = {}
    family = requirement = None
    with pdfplumber.open(pdf_path) as pdf:
        meta = pdf.metadata
        page_count = len(pdf.pages)
        for page_no, page in enumerate(pdf.pages, 1):
            tables = page.find_tables()
            in_table = [t.bbox for t in tables]
            body = [
                w for w in page.extract_words(extra_attrs=["size"])
                if not any(b[0] <= w["x0"] <= b[2] and b[1] <= w["top"] <= b[3] for b in in_table)
            ]
            body_lines = lines_of(body)
            for i, line in enumerate(body_lines):
                m = HEADING.match(line)
                if m and page_no > 1:
                    requirement = f"{m.group(1)} {m.group(2)}"
                    if i > 0 and not HEADING.match(body_lines[i - 1]):
                        family = body_lines[i - 1]
                m = FOOTNOTE.match(line)
                if m and m.group(1) not in footnotes and ":" in m.group(2):
                    # It runs on until the page number under it.
                    rest = []
                    for nxt in body_lines[i + 1:]:
                        if PAGE_NUMBER.match(nxt) or FOOTNOTE.match(nxt):
                            break
                        rest.append(nxt)
                    footnotes[m.group(1)] = join_lines([line, *rest])
            markers |= markers_in(join_lines(body_lines))
            for t in tables:
                header, *data = t.rows
                words = page.crop(t.bbox).extract_words(extra_attrs=["size"])
                head = [w for w in words if w["top"] < header.bbox[3]]
                text_x, value_x = column_starts(head)
                for r in data:
                    cell = [w for w in words if r.bbox[1] <= w["top"] < r.bbox[3]]
                    ident = join_lines(lines_of([w for w in cell if w["x0"] < text_x]))
                    assignment = join_lines(
                        lines_of([w for w in cell if text_x <= w["x0"] < value_x])
                    )
                    value_lines = placed_lines([w for w in cell if w["x0"] >= value_x])
                    if not ident and not assignment and rows:
                        rows[-1]["value_lines"] += value_lines
                        rows[-1]["pages"].append(page_no)
                        continue
                    rows.append({
                        "identifier": ident,
                        "assignment_text": assignment,
                        "value_lines": value_lines,
                        "requirement": requirement,
                        "family": family,
                        "pages": [page_no],
                    })
    for r in rows:
        r["dod_value"] = join_value(r.pop("value_lines"))
    return {
        "source": {
            "file": "docs/reference/OrgDefinedParmsNISTSP800-171.pdf",
            "sha256": hashlib.sha256(raw).hexdigest(),
            "title": meta.get("Title"),
            "pages": page_count,
            "extractor": "scripts/odp/extract_odp.py, pdfplumber 0.11.7",
            "footnotes": [footnotes[k] for k in sorted(footnotes)],
        },
        "rows": rows,
        "markers": markers,
    }


def verify(data: dict) -> list[str]:
    problems = []
    ids = [r["identifier"] for r in data["rows"]]
    for r in data["rows"]:
        if not IDENT.match(r["identifier"]):
            problems.append(
                f"p{r['pages'][0]}: identifier {r['identifier']!r} is not of the 03.xx.xx form"
            )
        if not r["dod_value"]:
            problems.append(f"{r['identifier']}: no value")
        want = data["markers"].get(r["identifier"])
        if want is None:
            problems.append(f"{r['identifier']}: no marker in the requirement text")
        elif (r["assignment_text"], want) == KNOWN_DIFFERENCES.get(r["identifier"]):
            continue
        elif want != r["assignment_text"]:
            problems.append(f"{r['identifier']}: table {r['assignment_text']!r} != text {want!r}")
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        problems.append(f"duplicate identifiers: {dupes}")
    missing = sorted(set(data["markers"]) - set(ids))
    if missing:
        problems.append(f"in the requirement text but in no table: {missing}")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf", type=Path)
    ap.add_argument("--check", action="store_true", help="exit 1 if the output would change")
    args = ap.parse_args()
    data = extract(args.pdf)
    problems = verify(data)
    for p in problems:
        print("PROBLEM", p, file=sys.stderr)
    del data["markers"]
    text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    print(f"{len(data['rows'])} rows from {data['source']['pages']} pages", file=sys.stderr)
    if args.check:
        same = OUT.exists() and OUT.read_text(encoding="utf-8") == text
        print("unchanged" if same else "WOULD CHANGE", file=sys.stderr)
        return 0 if same and not problems else 1
    if problems:
        return 1
    OUT.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {OUT}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
