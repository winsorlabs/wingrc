"""Compose backend/app/seeds/cmmc_official_guidance.yaml from extracted.json.
Stage 2 of 2 -- see extract_pdf.py for stage 1 and README.md for the full
pipeline and provenance.

Usage:
    python scripts/cmmc_guidance/compose_guidance_yaml.py path/to/extracted.json

Mechanical composition only: every word in the output originates in
extracted.json, which originates in the real PDF text (extract_pdf.py).
No paraphrase, no fill-in from memory.

Per-objective body:
  - if the guide tags a Potential Assessment Consideration bullet to this
    objective letter: that bullet's text (verbatim, tag stripped), followed
    by the practice-wide Examine/Interview/Test methods, clearly labeled
    as practice-wide.
  - if not: an explicit statement that the guide has no objective-specific
    consideration for this determination statement, followed by the same
    practice-wide methods.
Every objective in every practice gets a non-empty body, because every
practice has Examine/Interview/Test text (110/110, verified by extract_pdf.py).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CMMC_L2_YAML = REPO_ROOT / "backend" / "app" / "seeds" / "cmmc_l2.yaml"
OUT = REPO_ROOT / "backend" / "app" / "seeds" / "cmmc_official_guidance.yaml"

GUIDE_VERSION = "2.13"
GUIDE_DATE = "September 2024"
GUIDE_URL = "https://dodcio.defense.gov/Portals/0/Documents/CMMC/AssessmentGuideL2v2.pdf"
GUIDE_SHA256 = "0dcaba1626a0d23893981d74dd3f0f2338fff54cece67d81bc811ce76392d867"


def methods_sentence(methods: dict) -> str:
    parts = []
    for label in ("examine", "interview", "test"):
        if methods.get(label):
            parts.append(f"{label.capitalize()}: {methods[label]}.")
    return " ".join(parts)


def compose_objective(okey: str, methods_text: str, considerations: list[dict]) -> str:
    matches = [c["text"] for c in considerations if okey in c["letters"]]
    methods_line = (
        "Potential assessment methods for this practice as a whole "
        "(the guide does not further subdivide these by determination statement) "
        f"-- {methods_text}"
    )
    if matches:
        return f"{' '.join(matches)}\n\n{methods_line}"
    return (
        "The Assessment Guide does not provide an objective-specific consideration "
        f"for [{okey}]; it is covered only by the practice-wide assessment methods below.\n\n"
        f"{methods_line}"
    )


def main() -> None:
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(1)
    extracted = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    catalog = yaml.safe_load(CMMC_L2_YAML.read_text(encoding="utf-8"))

    out_data = {}
    missing = []
    for ctrl in catalog["controls"]:
        pid = ctrl["id"]
        if pid not in extracted:
            missing.append(pid)
            continue
        info = extracted[pid]
        methods_text = methods_sentence(info["methods"])
        considerations = info["considerations"]
        out_data[pid] = {
            obj["key"]: compose_objective(obj["key"], methods_text, considerations)
            for obj in ctrl.get("objectives", [])
        }

    if missing:
        print(f"ERROR: practices in catalog but not in extraction: {missing}", file=sys.stderr)
        sys.exit(1)

    header = f"""\
# Official CMMC Assessment Guide Level 2 content, per objective.
#
# MECHANICALLY GENERATED -- do not hand-edit. Regenerate via
# scripts/cmmc_guidance/extract_pdf.py + compose_guidance_yaml.py against
# the real PDF if the guide is revised (see that directory's README.md).
# Every value traces to actual document text: the "Potential Assessment
# Considerations" bullet tagged to this objective letter (verbatim, bracket
# tag stripped) plus the practice-wide "Potential Assessment Methods and
# Objects" (Examine/Interview/Test) text -- both extracted from the real
# PDF via pypdf text extraction, not paraphrased or recalled from training
# data.
#
# Source: CMMC Assessment Guide - Level 2, Version {GUIDE_VERSION} ({GUIDE_DATE})
#   {GUIDE_URL}
#   SHA-256: {GUIDE_SHA256}
#   (DoD CIO is the stated Author in the PDF's own metadata, confirming
#   this is the government-published document, not a third-party mirror)
#
# Structure: {{practice_id: {{objective_key: text}}}}. seed_catalog.py
# derives the citation string (official_guidance_source) from practice_id +
# objective_key + the GUIDE_VERSION/GUIDE_DATE constants above at seed
# time, rather than repeating a near-identical citation 316 times here.
#
# Coverage: every practice has Examine/Interview/Test text (110/110), so
# every objective gets a non-empty body. 109/110 practices have at least
# one objective-specific Considerations bullet; the rest fall back to the
# practice-wide methods, explicitly labeled as such rather than fabricating
# per-objective specificity the source document doesn't provide.
"""
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write(header)
        # allow_unicode=False (not True): PyYAML then \\uXXXX-escapes any
        # non-ASCII character (en dashes, section signs, etc -- both the
        # PDF text and this header contain them) inside double-quoted
        # scalars instead of writing raw UTF-8 bytes, matching
        # cmmc_l2.yaml's existing ASCII-clean convention (see that file's
        # header and tests/test_catalog_seed.py's
        # test_guidance_text_loads_clean, which enforces it for the
        # catalog file and would have caught this the same way it was
        # caught here: allow_unicode=True was tried first and produced raw
        # UTF-8 en dashes throughout, reverted).
        yaml.dump(out_data, f, allow_unicode=False, default_flow_style=False, width=100, sort_keys=False)

    n_objs = sum(len(v) for v in out_data.values())
    print(f"practices: {len(out_data)}")
    print(f"objectives: {n_objs}")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
