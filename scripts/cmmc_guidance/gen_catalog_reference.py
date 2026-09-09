"""Generate backend/app/seeds/cmmc_catalog_reference.yaml -- the committed,
PDF-derived {practice_id: [objective_keys]} reference test_catalog_seed.py's
drift guard checks cmmc_l2.yaml against. Deliberately independent of
cmmc_l2.yaml itself (built from extracted.json, not from the catalog it's
meant to check) -- checking a file against a reference derived from itself
would never catch drift.

Usage:
    python scripts/cmmc_guidance/gen_catalog_reference.py path/to/extracted.json

Re-run this whenever extract_pdf.py is re-run against a revised guide, then
re-run reconcile_catalog.py and fix cmmc_l2.yaml before committing the new
reference -- committing a new reference without reconciling the catalog
first would just make the guard test pass against a moving target.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
OUT = REPO_ROOT / "backend" / "app" / "seeds" / "cmmc_catalog_reference.yaml"


def main() -> None:
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(1)
    extracted = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))

    reference = {pid: sorted(info["objectives"].keys()) for pid, info in extracted.items()}

    header = """\
# Authoritative {practice_id: [objective_keys]} reference, derived directly
# from the real CMMC Assessment Guide PDF -- NOT from cmmc_l2.yaml. Used
# only by tests/test_catalog_seed.py's drift guard
# (test_catalog_matches_authoritative_reference): that test fails if
# cmmc_l2.yaml's objective keys ever diverge from this file, catching the
# next silent catalog gap in CI instead of waiting for another unrelated
# task to notice (see docs/roadmap.md's 2026-09 catalog-reconciliation
# writeup for the incident this guards against: 4 objectives silently
# missing since the catalog was first authored).
#
# MECHANICALLY GENERATED -- do not hand-edit. Regenerate via
# scripts/cmmc_guidance/gen_catalog_reference.py against a fresh
# extract_pdf.py run if the guide is revised. Reconcile cmmc_l2.yaml
# against the new extraction (reconcile_catalog.py) BEFORE regenerating
# this file -- otherwise the guard test just starts checking against a
# moving target instead of the source of truth.
"""
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write(header)
        yaml.dump(reference, f, allow_unicode=False, default_flow_style=False, sort_keys=True)

    total = sum(len(v) for v in reference.values())
    print(f"practices: {len(reference)}  objectives: {total}")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
