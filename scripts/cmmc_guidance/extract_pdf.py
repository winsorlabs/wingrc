"""Parse the official CMMC Assessment Guide Level 2 PDF into structured
per-practice data. Stage 1 of 2 -- see compose_guidance_yaml.py for stage 2,
and README.md in this directory for the full pipeline and provenance.

Usage:
    pip install pypdf pyspellchecker   # not app runtime deps -- ad hoc only
    python scripts/cmmc_guidance/extract_pdf.py path/to/AssessmentGuideL2v2.pdf

Writes extracted.json next to the input PDF: for each of the 110 CMMC L2
practices, its "ASSESSMENT OBJECTIVES [NIST SP 800-171A]" determination
statements (per-objective-letter text -- the authoritative source for
reconciling backend/app/seeds/cmmc_l2.yaml's objective count/keys/text,
see reconcile_catalog.py), the "Potential Assessment Methods and Objects"
(Examine/Interview/Test -- practice-level, per the guide's own structure),
and "Potential Assessment Considerations" bullets tagged by objective
letter (objective-level). Every word in the output is pypdf-extracted text
from the real PDF, mechanically cleaned of PDF-kerning artifacts (a small,
disclosed literal substitution list below) -- never paraphrased or filled
in from memory.

Re-run this whenever the Assessment Guide is revised (a new document
version): re-download the PDF, re-run this script, then
compose_guidance_yaml.py, then diff the result against
backend/app/seeds/cmmc_official_guidance.yaml before committing.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

try:
    import pypdf
except ImportError:
    print("pip install pypdf pyspellchecker", file=sys.stderr)
    raise

EXPECTED_PRACTICE_COUNT = 110

HEADER_RE = re.compile(r"^([A-Z]{2}\.L2-3\.\d+\.\d+)\s+–\s+([A-Z0-9 &,'\-\[\]]+?)\s*$")
TAG_RE = re.compile(r"\[([a-z](?:\s*,\s*[a-z])*)\]")

# pypdf's text extraction occasionally splits a word across a font-kerning
# boundary as a stray space (a PDF rendering artifact, not real text) --
# and the same word can split at a different point in different places
# (font kerning is position/context-dependent), so "secu rity" and
# "se curity" are both real, independent artifacts, not a typo in one of
# them. Found two ways: (1) manual spot-check while reviewing extracted
# text against the source PDF, and (2) a dictionary sweep (pyspellchecker)
# over every adjacent word pair in the corpus actually used (Considerations
# + Methods, and later extended to cover the ASSESSMENT OBJECTIVES text
# too -- run it again over any newly-extracted section before trusting the
# output), flagging a pair only where BOTH halves are unknown words AND the
# concatenation IS a known word. Zero false positives across every sweep
# run so far (each spot-checked against page context), and a re-scan after
# applying fixes always comes back empty. Fixed as literal, disclosed
# substitutions --
# never a general-purpose regex that could silently mangle a legitimate
# two-word phrase elsewhere in the corpus.
KERNING_FIXES = {
    "L ayer": "Layer",
    "conflict of interest o r an": "conflict of interest or an",
    "FIPS -validated": "FIPS-validated",
    "Organiza tional": "Organizational",
    "Organizat ional": "Organizational",
    "Organizatio nal": "Organizational",
    "analy sis": "analysis",
    "associ ated": "associated",
    "boundar ies": "boundaries",
    "combina tion": "combination",
    "commun ications": "communications",
    "doc umentation": "documentation",
    "in cident": "incident",
    "phy sical": "physical",
    "pr ocedures": "procedures",
    "procedu res": "procedures",
    "secu rity": "security",
    "se curity": "security",
    "sy stem": "system",
    "un successful": "unsuccessful",
    "us ers": "users",
    "wo rk": "work",
    # Found 2026-09-10: the original dictionary sweep excluded any
    # candidate second-word shorter than 2 characters, which missed every
    # split whose second half is a single letter -- these 8 only surfaced
    # once that exclusion was removed and the sweep re-run over the
    # ASSESSMENT OBJECTIVES text too. Same root cause, same verification
    # (spot-checked against page context, zero false positives).
    "acces s": "access",
    "m echanisms": "mechanisms",
    "reportin g": "reporting",
    "s ecurity": "security",
    "s upporting": "supporting",
    "securit y": "security",
    "severit y": "severity",
    "w ith": "with",
}


def fix_kerning(text: str) -> str:
    for bad, good in KERNING_FIXES.items():
        text = text.replace(bad, good)
    # Whitespace immediately inside parentheses is never intentional in
    # this document's prose -- a safe general rule (unlike the word-internal
    # splits above, which are literal fixes only).
    text = re.sub(r"\(\s+", "(", text)
    text = re.sub(r"\s+\)", ")", text)
    return text


def extract_section(chunk: str, start_marker: str, end_markers: list[str]) -> str | None:
    """Text between start_marker (exclusive) and the first of end_markers
    found after it (exclusive); None if start_marker isn't present."""
    si = chunk.find(start_marker)
    if si == -1:
        return None
    si += len(start_marker)
    ei = len(chunk)
    for em in end_markers:
        j = chunk.find(em, si)
        if j != -1:
            ei = min(ei, j)
    return chunk[si:ei].strip()


def parse_considerations(block: str | None) -> list[dict]:
    """'Potential Assessment Considerations' is a bulleted list ('• ...'),
    each item tagged with the objective letter(s) it addresses, e.g. '[a]'
    or '[d,e,f]' -- usually at the end of the sentence, but occasionally
    mid-sentence with a trailing clarifying note after the tag, so the tag
    is searched for anywhere in the item rather than anchored to the end.
    Items with no tag are skipped -- can't be attributed to a specific
    objective (verified: exactly one practice, AU.L2-3.3.4, has an
    untagged Consideration bullet in the source; excluded correctly rather
    than guessed at)."""
    if not block:
        return []
    items = []
    for raw_item in block.split("•"):
        item = " ".join(raw_item.split()).strip()
        if not item:
            continue
        m = TAG_RE.search(item)
        if not m:
            continue
        letters = [x.strip() for x in m.group(1).split(",")]
        text = (item[: m.start()] + item[m.end() :]).strip()
        text = re.sub(r"\s+([?.,;])", r"\1", text)
        items.append({"letters": letters, "text": fix_kerning(text)})
    return items


OBJ_TAG_RE = re.compile(r"\[([a-z])\]\s*")


def parse_objectives(block: str | None) -> dict[str, str]:
    """'ASSESSMENT OBJECTIVES [NIST SP 800-171A] / Determine if:' is a
    semicolon-separated list, each item starting with its own '[x]' tag
    (never multiple letters on one item, unlike Considerations bullets).
    Returns {letter: text} with the tag and trailing connector word
    ("and") / punctuation stripped, whitespace collapsed, kerning-fixed.
    This is the authoritative per-objective determination-statement text
    -- used to reconcile against backend/app/seeds/cmmc_l2.yaml's
    hand-maintained objective text, not to replace it wholesale (the
    catalog's own phrasing is a deliberate, independently-styled rewrite
    in places; reconciliation compares meaning, not verbatim wording)."""
    if not block:
        return {}
    text = " ".join(block.split())
    tags = list(OBJ_TAG_RE.finditer(text))
    out: dict[str, str] = {}
    for i, m in enumerate(tags):
        letter = m.group(1)
        start = m.end()
        end = tags[i + 1].start() if i + 1 < len(tags) else len(text)
        item = text[start:end].strip()
        # Trailing list connectors/punctuation before the next tag or EOL:
        # "...are identified; and" / "...are identified; " / "...are identified."
        item = re.sub(r"[;.]?\s*and\s*$", "", item)
        item = item.rstrip(";. ").strip()
        out[letter] = fix_kerning(item)
    return out


def parse_methods(block: str | None) -> dict:
    """'Potential Assessment Methods and Objects' -- Examine / Interview /
    Test, each a bracketed SELECT FROM list. Practice-level: the guide does
    not further subdivide this section per objective letter."""
    out = {}
    if not block:
        return out
    for label in ("Examine", "Interview", "Test"):
        m = re.search(rf"{label}\s*\n?\s*\[SELECT FROM:\s*(.+?)\]\.", block, re.DOTALL)
        if m:
            out[label.lower()] = fix_kerning(" ".join(m.group(1).split()))
    return out


def main() -> None:
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(1)
    pdf_path = Path(sys.argv[1])
    out_path = pdf_path.parent / "extracted.json"

    reader = pypdf.PdfReader(str(pdf_path))
    lines: list[tuple[int, str]] = []
    for page_num, page in enumerate(reader.pages, start=1):
        for line in page.extract_text().split("\n"):
            lines.append((page_num, line))

    starts = []
    for i, (pg, line) in enumerate(lines):
        m = HEADER_RE.match(line.strip())
        if m:
            starts.append((i, m.group(1), m.group(2).strip(), pg))

    if len(starts) != EXPECTED_PRACTICE_COUNT:
        print(
            f"ERROR: expected {EXPECTED_PRACTICE_COUNT} practice section headers, "
            f"found {len(starts)}. The document structure may have changed -- "
            "inspect before trusting the output.",
            file=sys.stderr,
        )
        sys.exit(1)

    results = {}
    for idx, (start_i, pid, title_caps, pg) in enumerate(starts):
        end_i = starts[idx + 1][0] if idx + 1 < len(starts) else len(lines)
        chunk = "\n".join(l for (_, l) in lines[start_i:end_i])

        objectives_block = extract_section(
            chunk, "Determine if:", ["POTENTIAL ASSESSMENT METHODS AND OBJECTS", "DISCUSSION"]
        )
        methods_block = extract_section(
            chunk, "POTENTIAL ASSESSMENT METHODS AND OBJECTS", ["DISCUSSION", "FURTHER DISCUSSION"]
        )
        considerations_block = extract_section(
            chunk, "Potential Assessment Considerations", ["KEY REFERENCES"]
        )
        results[pid] = {
            "title_caps": title_caps,
            "start_page": pg,
            "objectives": parse_objectives(objectives_block),
            "methods": parse_methods(methods_block),
            "considerations": parse_considerations(considerations_block),
        }

    out_path.write_text(json.dumps(results, indent=2), encoding="utf-8")

    n_with_methods = sum(1 for r in results.values() if r["methods"])
    n_with_considerations = sum(1 for r in results.values() if r["considerations"])
    total_bullets = sum(len(r["considerations"]) for r in results.values())
    total_objectives = sum(len(r["objectives"]) for r in results.values())
    print(f"practices: {len(results)}")
    print(f"total objectives (determination statements): {total_objectives}")
    print(f"practices with Examine/Interview/Test methods: {n_with_methods}")
    print(f"practices with tagged Considerations bullets: {n_with_considerations}")
    print(f"total tagged Considerations bullets: {total_bullets}")
    print(f"wrote {out_path}")


if __name__ == "__main__":
    main()
