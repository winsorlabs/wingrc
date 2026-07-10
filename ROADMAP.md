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

---

## C — Assessment presentation layer and visual polish

**When:** After evidence-tasks and bundle export are complete — this layer
visualizes a stable, fully-populated data model. Building it early would mean
designing against incomplete data.

**What:** A dedicated dashboard/summary view for a completed or in-progress
assessment, plus a general visual-polish pass across all existing screens.

---

### C.1 — SPRS gauge

A single-number gauge showing the live SPRS score on a −204 to 110 scale.

- **Scale:** −204 (all controls unmet) to 110 (fully met). Not red-to-green
  — color alone misrepresents regulatory meaning.
- **Meaningful threshold lines:**
  - **88** — conditional-certification floor per 32 CFR Part 170. Below this,
    no POA&M plan qualifies for conditional CMMC Level 2 certification. This is
    the primary regulatory target; mark it prominently.
  - **110** — perfect score; mark as the upper bound.
  - Optionally mark **0** as a visual midpoint (neither fully clean nor worst
    case), but 88 is the line that matters to the customer.
- **Display:** score displayed numerically inside the gauge arc. Label the 88
  and 110 lines. The gauge should communicate urgency (below 88 = not
  conditionally certifiable) without implying a binary pass/fail.
- **Data source:** `assessment.sprs_score` (persisted) + live recompute on
  load via the same `computeSprsLive` rollup already in use. No new endpoint
  needed.

---

### C.2 — Family radar / spider chart

14-axis radar chart, one axis per NIST 800-171 control family (AC, AT, AU,
CM, IA, IR, MA, MP, PS, PE, RA, CA, SC, SI).

- **Each axis:** percentage of controls in that family that are fully met
  (all objectives ∈ {met, inherited}), using the same all-objectives-met
  rollup as SPRS scoring. This keeps the chart and the score consistent.
- **Purpose:** spatial visualization of where the deficiencies are concentrated.
  A family that is a small slice visually signals where work is needed; a full
  polygon means no gaps. This is faster to read than scrolling 14 family
  sections.
- **Rendering:** SVG-based; no charting library dependency if feasible (keeps
  the bundle small). If a library is needed, evaluate lightweight options
  (e.g. Recharts RadarChart) — avoid pulling in a full charting suite for one
  chart type.
- **Data source:** same `ControlStateRow[]` already loaded for the board —
  no additional API calls.

---

### C.3 — Assessment progress dashboard

A summary panel showing the current state of the assessment across four
dimensions:

1. **Overall completion:** `N / 110 controls fully met` + percentage bar.
   "Fully met" = same all-objectives rollup.

2. **Progress by weight tier:** three rows matching the board's tier summary,
   but laid out more prominently with counts and percentages:
   - 5-pt: N / 44 controls met (these are the certification-critical ones)
   - 3-pt: N / 14 controls met
   - 1-pt: N / 52 controls met

3. **Evidence status:** outstanding vs. collected evidence tasks (from
   `evidence_task` rows). Requires `evidence_task` data to be meaningful;
   placeholder until that feature is built.
   - Tasks pending / in-progress / completed / waived
   - Evidence artifacts attached (count of `evidence` rows linked to this
     assessment via `evidence_state_link`)

4. **Controls by responsibility:** breakdown of the 110 controls by
   responsibility assignment — `customer_owns` / `provider_satisfies` /
   `shared`. Shows how much of the assessment burden falls on the MSP vs.
   the customer. Useful for the CRM deliverable.
   Count at the control level (a control's responsibility = the most
   restrictive responsibility among its objectives, or the plurality — define
   the rollup rule when building).

---

### C.4 — Visual polish pass

At the same time as C.1–C.3, do a targeted pass on the existing screens:

- Typography, spacing, and color consistency across the board, drawer, and
  evidence sections.
- Mobile/narrow-viewport behavior for the filter bar and tier summary.
- Accessibility: keyboard navigation for filter chips, ARIA labels on the
  gauge and radar chart, sufficient contrast on status badges.
- Empty-state illustrations or copy for the case where no assessment exists
  yet, and for a freshly seeded board (all not_met, no products activated).

**Do not polish prematurely** — wait for the data model and feature set to
stabilize so the polish pass doesn't need to be redone.

---

### Sequencing note

Build order within C: C.1 (gauge) first — smallest scope, highest visual
impact. C.2 (radar) second. C.3 (dashboard) third — depends on evidence-task
data being present. C.4 (polish) last, as a sweep across all screens together.
