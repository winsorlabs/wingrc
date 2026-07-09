# WinGRC Roadmap

Queued features, not yet built. Each item notes what needs to be verified or
decided before implementation begins.

---

## A — Board filtering / sorting by SPRS weight and CMMC Level

**What:** Let users filter and sort the control board by SPRS deduction weight
(1 / 3 / 5 pt) and by CMMC level (L1 vs L2), so engineers can triage
highest-impact gaps first.

**Why:** A fresh assessment shows all 110 controls as not_met. The 5-point
controls (e.g. SI, IA family) drive the most score damage; L1 controls are a
subset that must pass for basic certification. Sorting by weight surfaces the
quickest path to a meaningful SPRS improvement.

**Data model prerequisite:** `control.is_level_1: bool` — check whether this
flag is already in the catalog seed data. NIST 800-171 L1 maps to the 17
FAR 52.204-21 practices; the CMMC model document is the authoritative source.
If not present, add a column + seed value per control.

**Implementation sketch:**
- Add `is_level_1` to `Control` model and catalog seed if missing (migration)
- Expose `is_level_1` and `sprs_weight` in `ControlStateOut` (weight already
  added in the SPRS scoring work)
- Frontend: filter chips (All / L1 only / L2 only) + sort toggle (weight ↓)
  in the board topbar, applied client-side over the already-loaded `rows`

---

## B — POA&M eligibility as an encoded compliance rule

**What:** Per-control `poam_eligible` determination encoded from the DoD CMMC
scoping rule, plus an aggregate check that evaluates whether a complete POA&M
plan meets the conditional-certification criteria.

**Compliance rule (requires source verification before coding):**

Per DoD CMMC Assessment Process (CAP) and the CMMC Final Rule (32 CFR Part 170):

- **5-point controls are never POA&M-eligible.** A 5-point finding is an
  automatic disqualifier for conditional certification.
- **1-point and 3-point controls** may be POA&M-able, subject to enumerated
  exceptions (specific practices the DoD has listed as non-deferrable regardless
  of weight). The exact exception list must be sourced from the current CAP
  documentation — do NOT encode from memory.
- **Score floor:** the assessment score must be ≥ 88 / 110 at time of
  certification to be eligible for conditional certification with open POA&Ms.
- **Closure deadline:** open POA&M items must be closed within 180 days of
  conditional certification.

**Treat this like `coverage_basis`:** the eligibility rule is a
compliance judgment, not a product decision. Before building:

1. Pull the current CMMC Final Rule text and CAP documentation
2. Enumerate the non-deferrable exception practices explicitly
3. Have a C3PAO or legal review the encoded rule before it influences any
   customer deliverable

**Implementation sketch (future):**
- `control.poam_eligible: bool` in the catalog (seeded per-control after
  source verification)
- `PoamEligibility` domain type + `check_poam_plan(control_states, score)`
  pure function in `assessment.py` — returns whether a set of open findings
  meets the ≥88 floor, no 5-pt findings, no non-deferrable exceptions, all
  within 180-day window
- POA&M view: flag ineligible controls visually; aggregate banner when
  conditional certification criteria are (not) met
- Deliverable: include eligibility determination in the assessment bundle export
