"""Reconcile backend/app/seeds/cmmc_l2.yaml against the authoritative PDF
extraction (extract_pdf.py's output). Read-only -- reports a diff, changes
nothing. Run this after any hand-edit to cmmc_l2.yaml's objectives, and
whenever the guide itself is re-extracted.

Usage:
    python scripts/cmmc_guidance/reconcile_catalog.py path/to/extracted.json

Checks, per practice:
  - practice id present in both catalog and PDF
  - objective key set matches (missing / extra keys)
  - objective text matches after normalization (case-insensitive, trailing
    connector words and punctuation stripped, whitespace collapsed) --
    the catalog's own phrasing is a deliberate rewrite in places (full
    sentences, capitalized, period-terminated) rather than the PDF's
    semicolon-joined list style, so this compares meaning, not verbatim
    wording.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CMMC_L2_YAML = REPO_ROOT / "backend" / "app" / "seeds" / "cmmc_l2.yaml"


def normalize(text: str) -> str:
    t = text.strip().lower()
    t = re.sub(r"[;.,]?\s*and\s*$", "", t)
    t = t.rstrip(";.,").strip()
    t = re.sub(r"\s+", " ", t)
    return t


def main() -> None:
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(1)
    extracted = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    catalog = yaml.safe_load(CMMC_L2_YAML.read_text(encoding="utf-8"))

    catalog_ids = {c["id"] for c in catalog["controls"]}
    pdf_ids = set(extracted.keys())

    missing_practices = sorted(pdf_ids - catalog_ids)
    extra_practices = sorted(catalog_ids - pdf_ids)

    missing_objectives: list[tuple[str, str, str]] = []  # (pid, key, pdf_text)
    extra_objectives: list[tuple[str, str, str]] = []  # (pid, key, catalog_text)
    text_mismatches: list[tuple[str, str, str, str]] = []  # (pid, key, catalog, pdf)

    for ctrl in catalog["controls"]:
        pid = ctrl["id"]
        if pid not in extracted:
            continue
        pdf_objs: dict[str, str] = extracted[pid]["objectives"]
        catalog_objs = {o["key"]: o["text"] for o in ctrl.get("objectives", [])}

        for key, pdf_text in pdf_objs.items():
            if key not in catalog_objs:
                missing_objectives.append((pid, key, pdf_text))
        for key, cat_text in catalog_objs.items():
            if key not in pdf_objs:
                extra_objectives.append((pid, key, cat_text))
            else:
                if normalize(cat_text) != normalize(pdf_objs[key]):
                    text_mismatches.append((pid, key, cat_text, pdf_objs[key]))

    print("=" * 70)
    print(f"Catalog practices: {len(catalog_ids)}  PDF practices: {len(pdf_ids)}")
    print(f"Catalog objectives: {sum(len(c.get('objectives', [])) for c in catalog['controls'])}"
          f"  PDF objectives: {sum(len(o) for o in (e['objectives'] for e in extracted.values()))}")
    print("=" * 70)

    print(f"\nPractices in PDF but not catalog: {len(missing_practices)}")
    for pid in missing_practices:
        print(f"  {pid}")

    print(f"\nPractices in catalog but not PDF: {len(extra_practices)}")
    for pid in extra_practices:
        print(f"  {pid}")

    print(f"\nObjectives in PDF but missing from catalog: {len(missing_objectives)}")
    for pid, key, text in missing_objectives:
        print(f"  {pid}[{key}]: {text}")

    print(f"\nObjectives in catalog but not in PDF (extra/mis-keyed): {len(extra_objectives)}")
    for pid, key, text in extra_objectives:
        print(f"  {pid}[{key}]: {text}")

    print(f"\nObjective text mismatches (same key, different meaning): {len(text_mismatches)}")
    for pid, key, cat_text, pdf_text in text_mismatches:
        print(f"  {pid}[{key}]")
        print(f"    catalog: {cat_text}")
        print(f"    pdf:     {pdf_text}")

    total_issues = (
        len(missing_practices) + len(extra_practices) + len(missing_objectives)
        + len(extra_objectives) + len(text_mismatches)
    )
    print(f"\n{'=' * 70}\nTOTAL ISSUES: {total_issues}")
    sys.exit(1 if total_issues else 0)


if __name__ == "__main__":
    main()
