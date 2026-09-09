# CMMC Assessment Guide extraction pipeline

Generates `backend/app/seeds/cmmc_official_guidance.yaml` (official,
government-sourced per-objective assessment guidance) and
`cmmc_catalog_reference.yaml` (the authoritative objective-key reference
CI's drift guard checks `cmmc_l2.yaml` against) from the real CMMC
Assessment Guide Level 2 PDF. Not app runtime code — a maintenance tool,
re-run only when the guide is revised.

## Why this exists

`AssessmentObjective.official_guidance` must be sourced from the actual
CMMC Assessment Guide Level 2 PDF, never written from memory (same
standard as ROADMAP.md item B's POA&M rule: "do NOT encode from memory").
This pipeline downloads nothing itself — you supply the PDF — but does the
extraction, parsing, and cross-checking mechanically and reproducibly, so
the result is diffable and re-derivable rather than hand-typed.

## Source document

- CMMC Assessment Guide – Level 2, Version 2.13 (September 2024)
- `https://dodcio.defense.gov/Portals/0/Documents/CMMC/AssessmentGuideL2v2.pdf`
- SHA-256 of the exact file used to generate the current
  `cmmc_official_guidance.yaml`: `0dcaba1626a0d23893981d74dd3f0f2338fff54cece67d81bc811ce76392d867`
- US Government work — public domain, quoting it is fine.
- **The DoD CIO site blocks direct `curl`/`requests` downloads (Akamai WAF,
  403).** Fetch it with a real browser session instead (Chrome, navigate to
  the URL directly, use the PDF viewer's download button) — that's how the
  hash above was obtained.

## Pipeline

```
pip install pypdf pyspellchecker   # ad hoc only, not added to backend's deps

python scripts/cmmc_guidance/extract_pdf.py path/to/AssessmentGuideL2v2.pdf
# -> writes extracted.json next to the PDF

python scripts/cmmc_guidance/compose_guidance_yaml.py path/to/extracted.json
# -> writes backend/app/seeds/cmmc_official_guidance.yaml

python scripts/cmmc_guidance/reconcile_catalog.py path/to/extracted.json
# -> read-only report: diffs cmmc_l2.yaml's practices/objective-keys/text
#    against the PDF. Fix cmmc_l2.yaml by hand until this reports zero
#    issues -- BEFORE regenerating the reference below, or the guard test
#    just starts checking against a moving target instead of the source
#    of truth.

python scripts/cmmc_guidance/gen_catalog_reference.py path/to/extracted.json
# -> writes backend/app/seeds/cmmc_catalog_reference.yaml, the committed
#    reference tests/test_catalog_seed.py::
#    test_catalog_matches_authoritative_reference checks cmmc_l2.yaml
#    against on every CI run.
```

`extract_pdf.py` asserts it found exactly 110 practice-section headers
(the current CMMC L2 practice count) and fails loudly rather than
producing a silently-wrong result if the document structure has changed.

## What gets extracted, and at what grain

Checked against the real document rather than assumed (per this task's
explicit instruction):

- **"ASSESSMENT OBJECTIVES [NIST SP 800-171A]" / "Determine if:"** is the
  authoritative per-objective determination-statement text — 320 across
  110 practices. This is what `reconcile_catalog.py` checks `cmmc_l2.yaml`
  against and what `gen_catalog_reference.py` freezes into the committed
  drift-guard reference; it is NOT fed into `cmmc_official_guidance.yaml`
  (that stays sourced from Methods + Considerations below).
- **"Potential Assessment Methods and Objects"** (Examine/Interview/Test)
  is **practice-level** — the guide does not subdivide it per determination
  statement. Present for all 110/110 practices.
- **"Potential Assessment Considerations"** bullets ARE tagged per
  objective letter in brackets (e.g. `[a]`, `[d,e,f]`) — genuinely
  **objective-level** content. 109/110 practices have at least one tagged
  bullet (232 bullets total, covering 240/320 objectives with real
  objective-specific text). One practice (AU.L2-3.3.4) has a Consideration
  bullet with no letter tag at all in the source, and is correctly excluded
  rather than guessed at. The one practice with zero Considerations
  bullets still gets its practice-wide Methods text.

`compose_guidance_yaml.py` combines Methods + Considerations per objective:
the tagged Consideration text (when one exists) plus the practice-wide
Methods text, clearly labeled as practice-wide; when no Consideration is
tagged to that letter, an explicit sentence says so rather than presenting
the fallback as if it were objective-specific. Every one of the 320
objectives ends up with non-empty official guidance.

## Text cleanup

pypdf's extraction of this particular PDF has a small number of
font-kerning artifacts (a stray space splitting a word, e.g. "L ayer" for
"Layer" — and the same word can split at a different point elsewhere,
e.g. "secu rity" vs. "se curity", since kerning is position-dependent, not
a typo in one of them). Found two ways: manual spot-check, and a
dictionary sweep (`pyspellchecker`) over every adjacent word pair in the
corpus this pipeline actually uses, flagging a pair only where both halves
are unknown words AND the concatenation is a real word. That found 19
genuine splits across two sweeps — 18 over Methods + Considerations
(2026-09-09), one more ("se curity") once the objectives-text parsing was
added and the sweep re-run over it (2026-09-10) — spot-checked against
page context, zero false positives either time. See `extract_pdf.py`'s
`KERNING_FIXES` dict for the literal, disclosed list. Re-run the same
dictionary sweep over any newly-extracted section before trusting it,
rather than assuming the list is exhaustive forever.

## Verification history

**2026-09-09, first generated.** Practice ID cross-check: all 110
`Control.control_id` values in `cmmc_l2.yaml` matched exactly against the
110 practice headers found in the PDF. Objective-key cross-check (letters
only, no text comparison yet) found a pre-existing gap: `AC.L2-3.1.22`,
`IA.L2-3.5.8`, `RA.L2-3.11.1`, and `SC.L2-3.13.8` were each missing one
objective letter (316 seeded vs. 320 real). Flagged, not fixed, that day —
see the next entry. Text spot-checks against the source PDF for
`AC.L2-3.1.1`, `AC.L2-3.1.4`, `AC.L2-3.1.13`, `IA.L2-3.5.3`.

**2026-09-10, full reconciliation + fix.** Extended `extract_pdf.py` to
also parse the ASSESSMENT OBJECTIVES block (previously only Methods +
Considerations), wrote `reconcile_catalog.py` for a genuine practice /
key / text diff (not just a count), and ran it across all 110 practices —
not just the 4 already-known gaps, per the explicit instruction to check
whether the count mismatch was a symptom of something larger. Result:
**110/110 practices matched, 0 extra/mis-keyed objectives, exactly the 4
known missing objectives, and one text defect traceable to the same root
cause** (`IA.L2-3.5.8`'s existing `[a]` row had both real objectives'
text concatenated together with a stray "and [b]" fragment, rather than
a second row ever existing). Root cause: git history
(`853039ae8`, 2026-07-04) shows the catalog was already rewritten once
against this exact PDF, with that commit's own message flagging 6 *other*
controls needing manual fixing because "pdftotext concatenated the
ASSESSMENT OBJECTIVES header with requirement text on same line" — the
same structural hazard appears at all 4 gap locations, so this is very
likely that pass's known fragility striking 4 more practices its manual
review didn't catch, not a new bug (the original extraction script no
longer exists to confirm definitively). Also caught a second, independent
kerning split ("se curity") that only the newly-added objectives-text
parsing exposed — added to `KERNING_FIXES` alongside the existing
"secu rity" entry.

Fixed `cmmc_l2.yaml` (4 new objectives + the `IA.L2-3.5.8` split),
regenerated `cmmc_official_guidance.yaml`, added the 4/5 new
`cmmc_practitioner_notes.yaml` entries, and generated
`cmmc_catalog_reference.yaml` + the CI drift guard so the next gap fails a
PR instead of waiting for another unrelated task to notice. See
`docs/roadmap.md`'s 2026-09-10 entry for the `control_state` backfill this
also required (existing assessments needed a row for each new objective;
`engine.py:backfill_missing_control_states`, dry-run by default).
