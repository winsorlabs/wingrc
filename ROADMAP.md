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

---

## D — Automated evidence collection via tool integrations

**What:** Extend the evidence-task system so tasks can be satisfied by
connectors pulling from tool APIs or MCP servers, not just manual upload or
reference. The connector fills the existing `evidence_task` / `evidence` models
— same data model, different collection path.

**Tools (priority order):**
1. **Liongard** — existing pipeline experience; populates scope lists
   (users, hardware, software) that feed document-type objectives. First.
2. **Datto RMM** — asset/policy data for scope and CM/SI objectives. Second.
3. **RocketCyber / Kaseya SIEM** — log retention, event coverage; satisfies
   AU-family tasks directly.
4. **Heimdal** — EDR telemetry; SI.3.14.x monitoring objectives.
5. **Senteon** — configuration hardening; CM-family baselines.
6. **CyberHoot** — security awareness training records; AT.3.2.x.
7. **RoboShadow** — vulnerability/asset scan; RA and SI objectives.
8. **Microsoft 365** — identity, MFA, conditional access; IA-family evidence.
9. **Domotz** — network topology; AC and SC scoping evidence.

**Architecture:**
- A connector is a Python class implementing a minimal interface:
  `collect(task: EvidenceTask, credentials: dict) -> Evidence`. It runs in
  the tenant's environment with the MSP's own API keys — never platform keys.
- Connector output writes into the existing `evidence` + `evidence_state_link`
  tables and marks the task `collected`. No new schema required for the first
  connectors.
- Credentials are stored in the tenant's own vault (or passed via config); the
  platform never holds third-party API keys.

**Constraints:**
- BYO-credentials: MSP supplies their own API keys via tenant config. The
  platform operator has no access to customer tool credentials.
- CUI data-handling: connectors must support a local-execution mode for
  CUI-sensitive tenants (air-gapped or GCC High deployments). Data must not
  transit a commercial cloud on its way from the tool to the evidence store.
- Per-connector effort: each tool API is bespoke. Stub the interface first;
  build connectors independently as separate, testable units.
- Liongard and Datto RMM share scope-list patterns with the existing scope
  module — reuse that parsing logic rather than duplicating it.

**Not in scope:** auto-confirming control_state status from connector output.
A connector populates evidence; an engineer still confirms the state. This
preserves the "candidates, never auto-met" rule from CLAUDE.md.

---

## E — Objective tips (MSP-flavored evidence examples)

**What:** A per-objective advisory field giving concrete, MSP-scaled examples
of what satisfies the objective: e.g., "Excel sheet mapping user accounts to
job role" for AC.L2-3.1.1[a], or "Screenshot of Entra ID conditional access
policy" for IA.L2-3.5.3. Shown in the objective panel next to statement and
evidence entry.

**Why:** The official Discussion text in NIST 800-171A is written for C3PAO
evaluators, not MSP engineers doing their first self-assessment. Tips translate
requirements into the MSP's tool vocabulary and scale (10–200 endpoints, not
enterprise data centers).

**Data model:**
- `assessment_objective.tips: text | null` — nullable free-text column on the
  existing objective row. One tips field per objective; no new table needed.
- Migration: add column. Seed: populate hand-authored tips for the highest-value
  objectives first (5-pt controls, L1 controls, any objective where evidence
  type is "screenshot" and the target tool is common in the MSP stack).

**Display:** rendered as an advisory callout in the ControlDrawer objective
panel, visually distinct from the compliance Discussion text. Label clearly as
"Tip" or "MSP guidance" — not a compliance determination.

**Future evolution:** AI-generation from objective text + active tool stack
(e.g., "Given you run RocketCyber and Entra ID, here's what AU.L2-3.3.1[a]
looks like in your environment"). Hand-authored tips ship first; AI-generated
tips are a later refinement using the same field.

**Review bar:** advisory content, not a compliance determination. Internal
review before shipping; no C3PAO sign-off required for the initial set.

---

## F — Template document library

**What:** A library of reusable policy, procedure, plan, and list templates,
each with a stable document ID (e.g. `AC-POL-001`). Templates tag to the
objectives they satisfy (many-to-many). Selecting a template from the library
attaches it to all its tagged objectives simultaneously, speeding statement
and evidence preparation.

**Document IDs:** stable, versionable identifiers scoped to the tenant's
library (`<family>-<type>-<seq>`, e.g. `AC-POL-001`, `IA-PROC-002`). IDs
appear on the document itself and in the assessment bundle export, keying
deliverables back to objectives.

**Objective tagging (many-to-many):** one template satisfies multiple
objectives (e.g., an Access Control Policy covers AC.L2-3.1.1 through
AC.L2-3.1.22); one objective may be addressed by multiple templates. Reuses
the same dedup pattern as evidence minimization.

**Data model sketch:**
```
template_document
  id              UUID PK
  org_id          UUID FK (tenant-scoped; shared MSP seed rows use a null org_id)
  doc_id          text        e.g. "AC-POL-001"
  doc_type        text        policy | procedure | plan | list | template
  title           text
  body            text        Markdown or rich text
  version         text
  reviewed_at     datetime

template_objective_link
  template_id     UUID FK → template_document
  objective_id    UUID FK → assessment_objective
  UNIQUE(template_id, objective_id)
```

**Seed library:** ship the MSP's existing template set (Winsors Labs baseline
docs) as the initial seed, tagged to their objectives. This makes the feature
immediately useful without requiring the MSP to author from scratch.

**Bundle export integration:** when generating the assessment bundle, include
tagged templates by document ID, mapped to the objectives they satisfy in the
SSP appendix.

**Sequencing:** build after evidence-tasks (F depends on the objective-linking
pattern being established). Template body editing and versioning are in scope;
a full document-authoring UI is a later pass.

---

## G — Ongoing Compliance Tasks (continuous compliance engine)

**What:** Shift the tool from point-in-time assessment to continuous compliance
maintenance. Generate recurring compliance tasks (weekly / monthly / quarterly)
that require human action — periodic asset reviews, audit-log spot-checks,
access recertification, etc.

**Two sources of recurring tasks:**

1. **Control-derived:** recurring activities mandated by the framework. Anchor on
   the existing `satisfaction_type = scheduled_operation` objectives and their
   `cadence` / `cadence_responsibility` fields (already in the schema) — this
   feature builds directly on that foundation.

2. **Document-derived (AI-assisted):** ingest the company's own policies,
   procedures, and plans; extract the recurring commitments they make ("we review
   firewall rules quarterly"); and generate tasks to hold them accountable to what
   they said they'd do. Directly attacks the most common CMMC finding: "policy
   says X, no evidence X was done." AI proposes the schedule; a human confirms —
   never silently invents obligations.

**Engine needs:**
- `cadence`, `last_completed`, `next_due`, and overdue / at-risk flagging.
  Reuse the `evidence.expires_at` staleness concept applied to activities.
- An overdue task is a live audit risk surfaced before an assessor finds it.
- Completion produces evidence-of-activity that feeds back into the assessment.

**Delivery:** push tasks to the PSA (Autotask) as tickets via the same connector
layer as automated evidence collection (feature D); completion flows back
automatically. This turns WinGRC from an assessment tool into a continuous
compliance platform.

**Sequencing:** build after D (connector layer) and after the evidence-task
system is stable. The `scheduled_operation` schema fields are already present —
no data-model prerequisite for the control-derived source.

---

## H — Append-only system audit log

**What:** A central audit mechanism every mutating operation flows through.
Records timestamp, actor, action type, entity (table + ID), before/after values,
and freeform context/metadata. Append-only — rows are never updated or deleted.
Exportable for assessors and assessor evidence packages.

**Why this now, not later:** the existing `control_state_history` table already
implements this pattern for one entity type. Generalising it to a single audit
log before more features land is far cheaper than retrofitting it into a dozen
existing endpoints after the fact. Every new mutating operation — deactivation,
evidence attach/detach, statement edits, archive actions — should flow through
the audit log from day one.

**What it captures (non-exhaustive):**
- Control state changes (mark-met, mark-partial, mark-not_met, needs_review)
- Evidence attached, detached, archived
- Product activated, deactivated, decommissioned
- Evidence task status changes (open → collected → na, archived)
- Implementation statement created, updated
- Assessment started, submitted, closed
- Scope entity added, updated, removed

**Schema sketch:**
```
audit_log
  id            UUID PK
  org_id        UUID FK → organization   (null for platform-level events)
  actor         text    NOT NULL         ("system" until auth exists; then user ID)
  actor_type    text    NOT NULL         ("system" | "user" | "api_key")
  action        text    NOT NULL         (e.g. "control_state.update", "evidence.attach")
  entity_type   text    NOT NULL         (table name: "control_state", "evidence_task", …)
  entity_id     UUID    NOT NULL
  before_value  JSONB                    (prior state snapshot; null on create)
  after_value   JSONB                    (new state snapshot; null on delete)
  context       JSONB                    (arbitrary metadata: assessment_id, product_name, …)
  created_at    timestamptz NOT NULL DEFAULT now()
```

No UPDATE or DELETE privileges on this table for the application role.

**Actor field:** wire `actor = "system"` and `actor_type = "system"` from day
one. When auth lands (item I below), the real user identity drops in with no
schema change.

**Implementation pattern:**
- A thin `audit.log_event(session, ...)` helper called from the engine and
  router layer — not a middleware that auto-captures everything blindly.
  Explicit logging at each mutating site is more precise and readable.
- `control_state_history` remains as-is for now (it carries domain-specific
  fields like `change_reason`). New operations flow to `audit_log`. A future
  consolidation pass may merge them.

**Tamper-evidence note (hardening, not now):** for deployments where the audit
log must be tamper-evident to an external assessor, add hash-chaining:
each row hashes its own content plus the previous row's hash (a la certificate
transparency). This turns any post-facto modification into a detectable break.
Implement this as a hardening pass after the basic log is stable and proven —
not on the initial build, where the overhead would slow development without yet
having the underlying log to protect.

**Export:** a `GET /orgs/{org_id}/audit-log` endpoint returning paginated log
rows, filterable by entity type, action, and time range. Included in the
assessment bundle export as an assessor-facing evidence artifact.

**Sequencing:** build into each new mutating feature from here forward. Retrofit
the existing control-state and evidence endpoints in the same sprint that adds
the `audit_log` table. The table is cheap to add; the discipline of calling
`log_event()` is the lasting investment.

---

## I — Authentication, users, and RBAC

**Status: major dedicated slice — do NOT rush.**

This is security-critical for a compliance tool holding CUI-adjacent data. It
deserves a focused design sprint, independent of feature velocity.

**Roles:**
- **MSP User** — platform operator; manages multiple tenant orgs; can activate
  products, run the magic loop, and manage evidence across all their client orgs.
- **Org User** — scoped to a single tenant; can view and update controls, attach
  evidence, and write implementation statements for their own org only.
- **Assessor** — read-only access to one or more assessments; can view all data
  for an assessment but cannot mutate state; audit log export available.

**Constraints:**
- Multi-tenant user scoping: a user's effective permissions are always evaluated
  against the org context of the request. A user with MSP-level access to org A
  has no access to org B's data unless explicitly granted.
- CUI handling: credential storage and session management must comply with the
  sensitivity level of the data. No plain-text token storage; rotate-able
  credentials; session expiry appropriate to the deployment environment.
- Vetted library over hand-rolling: do not build JWT validation, password
  hashing, or session management from scratch. Evaluate `fastapi-users` or a
  comparable library with an active maintenance record.
- GCC High / air-gapped: the auth layer must work in deployments without
  commercial identity providers. Local account support is required alongside
  any SSO / OAuth integration.

**What lands when auth ships:**
- The `audit_log.actor` field carries real user identity (no schema change needed
  if the actor field was wired as "system" placeholders).
- `org_id` scoping in every endpoint is enforced via the session's user context,
  not just a path parameter (the path parameter becomes a claim check).
- RBAC guards on the router layer (FastAPI dependency injection).

**Sequencing:** after the core assessment engine, evidence system, and audit log
are stable. Retrofitting auth into an already-working system is manageable;
retrofitting it into an actively shifting schema is painful. Ship the audit log
(H) first so real user identities drop into an already-wired actor field.
