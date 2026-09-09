# CMMC Assessment Guide extraction pipeline

Generates `backend/app/seeds/cmmc_official_guidance.yaml` (official,
government-sourced per-objective assessment guidance) from the real CMMC
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
```

`extract_pdf.py` asserts it found exactly 110 practice-section headers
(the current CMMC L2 practice count) and fails loudly rather than
producing a silently-wrong result if the document structure has changed.

## What gets extracted, and at what grain

Checked against the real document rather than assumed (per this task's
explicit instruction):

- **"Potential Assessment Methods and Objects"** (Examine/Interview/Test)
  is **practice-level** — the guide does not subdivide it per determination
  statement. Present for all 110/110 practices.
- **"Potential Assessment Considerations"** bullets ARE tagged per
  objective letter in brackets (e.g. `[a]`, `[d,e,f]`) — genuinely
  **objective-level** content. 109/110 practices have at least one tagged
  bullet (232 bullets total, covering 240/316 objectives with real
  objective-specific text). One practice (AU.L2-3.3.4) has a Consideration
  bullet with no letter tag at all in the source, and is correctly excluded
  rather than guessed at. The one practice with zero Considerations
  bullets still gets its practice-wide Methods text.

`compose_guidance_yaml.py` combines these per objective: the tagged
Consideration text (when one exists) plus the practice-wide Methods text,
clearly labeled as practice-wide; when no Consideration is tagged to that
letter, an explicit sentence says so rather than presenting the fallback as
if it were objective-specific. Every one of the 316 objectives ends up
with non-empty official guidance.

## Text cleanup

pypdf's extraction of this particular PDF has a small number of
font-kerning artifacts (a stray space splitting a word, e.g. "L ayer" for
"Layer"). Found two ways: manual spot-check, and a dictionary sweep
(`pyspellchecker`) over every adjacent word pair in the corpus this
pipeline actually uses, flagging a pair only where both halves are unknown
words AND the concatenation is a real word. That found 18 genuine splits
(spot-checked against page context, zero false positives) — see
`extract_pdf.py`'s `KERNING_FIXES` dict for the literal, disclosed list.
Re-run the same dictionary sweep after any future re-extraction to catch
new ones rather than assuming the list is exhaustive forever.

## Verification performed when this was first generated (2026-09-09)

- Practice ID cross-check: all 110 `Control.control_id` values in
  `backend/app/seeds/cmmc_l2.yaml` matched exactly against the 110 practice
  headers found in the PDF (no extras either direction).
- Objective-key cross-check: compared each control's objective letter set
  in `cmmc_l2.yaml` against the letters listed under "ASSESSMENT
  OBJECTIVES" in the PDF for that practice. **Found a pre-existing data
  gap, unrelated to this pipeline**: `AC.L2-3.1.22`, `IA.L2-3.5.8`,
  `RA.L2-3.11.1`, and `SC.L2-3.13.8` are each missing one objective letter
  in `cmmc_l2.yaml` relative to the real NIST SP 800-171A objectives (e.g.
  `RA.L2-3.11.1` has only `[b]` seeded; the guide defines `[a]` and `[b]`).
  Not fixed here — adding a new objective to a control that already has
  live assessments needs a `control_state` backfill for every existing
  assessment, which is a separate, careful change. Flagged to Jarrod
  directly rather than folded into this pipeline. The guidance pipeline
  simply has nothing to attach to the missing letters yet.
- Text spot-checks against the source PDF (not just against the pipeline's
  own output) for `AC.L2-3.1.1`, `AC.L2-3.1.4`, `AC.L2-3.1.13`,
  `IA.L2-3.5.3` — see the session transcript for the side-by-side
  comparisons.
