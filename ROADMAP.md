# WinGRC Roadmap

Queued features. Each item notes what needs to be verified or decided
before implementation begins. **Not all of these are still unbuilt** — this
file accumulates faster than it gets reconciled against what actually
shipped; items verified shipped carry a **Status** line at the top (A,
C.2, H, I as of 2026-09-07). No status line means genuinely not started,
last verified on the date named in that item, or not verified at all.

---

## A — Board filtering / sorting by SPRS weight and CMMC Level

**Status: Shipped (`2b175463`, 2026-07-09) — verified 2026-09-07.**
`control.is_level_1` exists in `models.py`; `AssessmentBoard.tsx` has the
"L1 only" filter chip, status/responsibility filter chips, and a
`sortByWeight` toggle ("Weight ↓") plus a tier summary. This section is now
a historical design record, not open work — read the code for current
behavior rather than this sketch.

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

**Status: still open — verified 2026-09-07.** The org dashboard (`G.3`,
`docs/roadmap.md` Done) does show an SPRS Score card
(`OrgDashboard.tsx`), but it's a plain number + trend arrow
(`<div className="dashboard-big-stat">{sprs.current_score}</div>`), not a
gauge with the 88/110 threshold lines this section specifies. Not
superseded by G.3 — genuinely unbuilt if wanted as designed here.

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

**Status: Shipped — verified 2026-09-07.** Landed as a small follow-on to
`G.3` (`docs/roadmap.md` Done, "Family radar chart" entry, 2026-08-19), not
as this standalone slice, but the actual deliverable matches: hand-rolled
SVG (no charting library added), 14 axes, one per family, percentage-met
per axis. See `OrgDashboard.tsx` and `lib/radarChart.ts`. This section is
now a historical design record.

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

**Status: still open — verified 2026-09-07.** `G.3`'s org dashboard
(`docs/roadmap.md` Done) shipped a *different* set of widgets — Family
Completion (heatmap + radar), SPRS Score, Statement Authoring, Evidence
Expiring, Needs Review, Blocked Objectives, Open Tasks by Owner, POA&M
Summary, Recent Activity — that overlaps in spirit but does not implement
this section's four specific dimensions: no aggregate "N / 110 controls
met" bar, no 5pt/3pt/1pt tier breakdown *on the dashboard* (that exists on
the board itself, per item A above, not here), no evidence status broken
out by pending/in-progress/completed/waived with an artifact count, and no
controls-by-responsibility breakdown. Not superseded — genuinely unbuilt if
wanted as designed here.

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

**Status: still open — spot-checked 2026-09-07.** No dedicated polish-pass
commit found. Some `aria-label` attributes already exist scattered across
components (added incidentally as those screens were built, e.g. the
dashboard's radar chart), but that's not the deliberate sweep this section
describes — not claiming this is done.

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
- **Reworded 2026-09-09 (D.1), see that section for the full rationale:**
  credentials are encrypted at rest in the app's own Postgres (deploy-time
  key, never persisted — `backend/app/crypto.py`), which satisfies the
  constraint below because WinGRC is self-hosted: the MSP runs its own
  Postgres, so this is the MSP holding its own credential, not
  WinGRC-the-vendor holding a customer's. The original wording here
  ("platform never holds third-party API keys," "stored in the tenant's
  own vault") was written imagining a hosted multi-tenant WinGRC where the
  vendor differs from the customer — see **D.4** for why that scenario
  needs a different answer, not this one, before a hosted WinGRC ships.

**Constraints:**
- BYO-credentials: MSP supplies their own API keys. **WinGRC-the-vendor
  never sees customer credentials** — self-hosting satisfies this by
  construction, since "the platform" is the MSP's own infrastructure. (See
  the Architecture note above — reworded 2026-09-09 from the older, more
  literal "the platform operator has no access" phrasing, which read as a
  constraint on the *code* rather than on WinGRC-the-vendor specifically,
  and didn't survive contact with an actual credential-entry screen.)
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

### D.1 — Integrations screen (connection management UI)

**Shipped 2026-09-09 — credential entry + test-connection only.** Full
writeup in `docs/roadmap.md`'s Done section. The device/user pull and the
approval workflow below (D.2, D.3) are still not built — this section's
scope was deliberately just the screen, credential storage, and
test-connection.

**Added 2026-09-06 (Jarrod).** Item D above specifies the connector *backend*
(the `collect()` interface, BYO-credential handling, evidence writes) but no
UI surface for an MSP admin to actually set one up. This is that surface.

**What:** A new top-level **Integrations** section in the side nav (peer of
Scope / Assessments / Tools / Library / Security), listing available
connectors and their per-org connection state. Liongard first, matching D's
priority order.

**Per-connector screen needs:**
- Credential entry (BYO API key/secret per D's constraints — the platform
  operator never sees these) with a **Test connection** action that proves
  the credentials work before saving, rather than failing silently at first
  sync.
- Connection status: connected / never connected / last attempt failed, with
  the actual error surfaced, not a generic failure.
- Sync history — what ran, when, how many rows came back, what changed.
  Without this an MSP can't answer "why did my inventory change" to an
  assessor, which is the whole point of the audit posture elsewhere in this app.
- Manual **Sync now**, plus whatever scheduling model we land on (decide:
  cron-style server-side schedule vs. manual-only for v1 — manual-only is a
  legitimate v1 and avoids building a scheduler before the connector itself
  is proven).

**Open questions — resolved 2026-09-09:**
- Credential storage. **Encrypted at rest in the app's own Postgres**
  (`backend/app/crypto.py`, Fernet, deploy-time key via
  `WINGRC_CREDENTIAL_ENCRYPTION_KEYS`, never persisted, fail-closed,
  key-version-labeled for rotation). See item D's Architecture section
  above for why this satisfies the "never holds third-party API keys"
  constraint once that constraint is understood correctly (self-hosted =
  the platform IS the MSP's infrastructure) — and see **D.4** for why a
  future *hosted* WinGRC can't reuse this answer unchanged.
- Where org-scoped vs. MSP-wide connections live. **MSP-wide, one row per
  connector per deployment** — settled by reading Liongard's own docs
  before designing anything, not by assuming: Access Key ID/Secret are
  generated per Liongard *user account*, scoped to the whole MSP instance
  (`https://{instance}.app.liongard.com/api/v1/`), not per client.
  Environments (per-client tenants) live underneath that one account.
  `integration_connection` (`models.py`) is deployment-wide, matching
  `product`/`framework`'s existing non-org-scoped tier. Mapping a WinGRC
  org to a Liongard Environment id is **D.2**'s concern — a separate,
  org-scoped table, not a column here.

### D.2 — Scope / Inventory as a connector target

**Added 2026-09-06 (Jarrod).** D's Architecture section says connector output
"writes into the existing `evidence` + `evidence_state_link` tables" with "no
new schema required." That's correct for evidence-collection connectors, but
the first and highest-priority use of Liongard is pulling **inventory /
assets** — which lands in `scope_entity`, not `evidence`. That path isn't
specified anywhere; docs/roadmap.md carries it only as a one-line deferred
item ("Scope connector — Liongard / Datto RMM → `scope_entity`").

**What:** Liongard inventory pull populating `scope_entity` (devices,
software, users), reusing the ingest machinery `scope.py` already has rather
than inventing a third path.

**Reuse, don't duplicate:** the workbook importer already implements
dry-run → review → apply against `scope_entity` with natural-key upsert and
MISSING-row flagging (apply never deletes). A connector pull is the same
shape — fetch rows, diff against current scope, present for review, apply.
Route it through that existing flow; do not write a direct connector→DB
insert path that bypasses the review step. An assessor-facing inventory that
changes without a human seeing the diff is exactly the failure mode the
existing dry-run design avoids.

**Canonical attribute keys — resolved for the two existing writers, still
applies to this one.** `scope_entity.attributes` used to hold *different key
schemas depending on entry path* — the manual Add Asset UI writes
`make_oem`/`model`/`version`/`responsible_contact_id`, while the workbook
importer wrote raw spreadsheet headers (`Make`, `Model`, `OS`,
`Owner / Primary User`). Found 2026-09-05 while wiring the component
inventory into the SSP bundle, where it rendered as an all-"N/A" table for
spreadsheet-scoped orgs. **Fixed 2026-09-06–07:**
`importers/workbook.py:resolve_canonical_device_attributes()` now maps the
known raw headers onto canonical keys at ingest, alongside (not replacing)
the raw ones, and resolves `responsible_contact_id` against real `Contact`
rows by exact name match — never a raw string in the UUID slot, never an
auto-created Contact, unresolved owners surfaced as a dry-run warning
rather than dropped silently. See `docs/roadmap.md`'s Done section for the
full writeup. A Liongard connector is still a **third** writer into this
field — it needs to follow the same normalize-at-ingest pattern (map
whatever Liongard's own field names are onto the canonical keys, resolve
device ownership against `Contact` the same way, surface what it can't
resolve) rather than adding a fourth divergent schema. Not a blocker
anymore in the sense of "nothing works until this is fixed" — the fix
already shipped for the paths that existed before this connector.

**Canonical device attributes, extended 2026-09-07:** the vocabulary above
now also includes `device_subtype` (controlled vocabulary — see
`domain.py`'s `DeviceSubtype` StrEnum — with `device_subtype_other` as a
free-text fallback so an unrecognized Liongard device class is never
dropped), `asset_tag` (physical sticker ID; duplicate values are surfaced as
a dry-run warning, not DB-constrained — see
`importers/workbook.py:resolve_canonical_device_attributes()`), and
`mac_addresses` (`list[str]`, not a single value — a device can report
several NICs; normalize each to lowercase colon-separated form via
`domain.py:normalize_mac_address()` before writing, and note that MAC
randomization on modern mobile OSes means a MAC is an attribute here, never
an identity). A Liongard connector must write all three under these same
keys, applying the same normalization, rather than inventing its own shape.
`asset_tag` is also worth evaluating as a fallback `natural_key` for
Liongard rows that arrive without a hostname (today's `natural_key` for
workbook rows is the raw "Serial # or Asset Tag" cell — see
`importers/workbook.py:_natural_key()`) — but that's a decision for when
the connector is actually built, not something this pass changes: altering
natural_key semantics retroactively would re-key every existing
`scope_entity` row keyed on the current scheme.

---

### D.3 — Asset & user onboarding approval workflow

**Added 2026-09-08 (Jarrod). Not started.** Depends on D.1 (credentials/
connection UI) and D.2 (connector writing `scope_entity`), plus two pieces
of infrastructure this codebase does not have yet — see Prerequisites.

**What:** Daily Liongard sync of devices and users. Anything new lands in a
pending state rather than silently joining the boundary; the org's Security
Officer and IT/MSP contact are notified; they open an approval page showing
a baseline checklist evaluated against observable Liongard metrics (DUO/Evo
installed, FenixPyre installed, RoboShadow installed, RocketCyber installed,
etc.) as met / not met; they formally accept or reject the asset into the
environment.

**Why it's worth building:** this produces a *formal acceptance record* —
who accepted which asset into the CUI environment, when, and what the
security posture looked like at that moment. That's the artifact an assessor
actually wants for CM-family change/configuration control and for inventory
accuracy, and it's stronger evidence than a spreadsheet asserting the
inventory is complete. It also gives an MSP a defensible answer to "how did
this laptop get in scope."

**Prerequisites that don't exist yet — scope these honestly:**
- **Email delivery: none exists.** Verified 2026-09-08 — no `smtplib`, no
  SMTP settings, no mailer module anywhere in `backend/`. This feature needs
  outbound email from scratch: SMTP/provider config per deployment
  (self-hosted MSPs won't share one), templating, delivery-failure handling
  (a silently-bounced approval request is worse than none), and per-org
  routing. Treat this as its own sub-slice; it is bigger than it sounds and
  it is on the critical path.
- **Scheduling: none exists.** "Daily sync" needs a scheduler/worker. D.1
  already notes manual-only sync is a legitimate v1 — the same reasoning
  applies here: a manual "Sync now" that queues approvals is shippable
  before any scheduler exists, and proves the workflow first.
- **`EntityStatus` has only `active` / `decommissioned`** (`domain.py`). A
  `pending_approval` state is needed, plus a decision on what pending means
  for `in_boundary` and for the SSP component inventory — see Open questions.

**Routing targets already exist:** `ContactDocumentationRole`'s CHECK
constraint (migration 0013) already includes `security_officer` and
`it_admin`. Route notifications to the org's contacts holding those roles —
no new role vocabulary needed. Handle the "no contact holds that role" case
explicitly rather than silently dropping the notification.

**Hard security constraint — no one-click approval links in email.** Email
is a notification that something needs review; approval itself must require
an authenticated session in the app. A magic-link "Approve" button is
forwardable, phishable, and survives the recipient leaving the company —
and an attestation anyone-with-the-link can produce is worthless as
evidence. Email links to the approval page; the app authenticates and
records the approver.

**Baseline checklist — three possible sources, ship them in this order:**
1. **Derived from activated Tools.** Deterministic, needs no AI, and the
   tool-activation concept already exists. If an org has activated
   FenixPyre and RocketCyber, those become checklist rows automatically.
   This is the v1.
2. **Manually curated checklist**, editable per org — the fallback for
   anything the tool list doesn't capture. Needed regardless, because tool
   activation won't cover everything a baseline requires.
3. **AI-derived from a baseline document in the Library.** Depends on the
   document library (item F / `docs/roadmap.md` N) and on the deferred
   BYO-AI provider abstraction. **The AI proposes a checklist; a human
   confirms it before it governs anything.** An LLM silently deciding what
   "compliant" means for asset acceptance in a compliance product is not an
   acceptable failure mode — the confirmed checklist is the artifact, the
   AI is just a drafting aid.

**Checklist results are candidates, never auto-met.** A green check means
"Liongard observed this agent installed," not "the control is satisfied."
Approval decisions and control_state stay separate, consistent with the
"candidates, never auto-met" rule in CLAUDE.md that already governs tool
activation and connector output.

**Snapshot the approval, don't recompute it.** The acceptance record must
store what the checklist said *at approval time* — checklist version, each
item's result, and the underlying metric values — the same point-in-time
discipline `BundleSnapshot` already uses. If the approval page re-renders
live metrics later, the record silently becomes "what's true now" instead of
"what was accepted then," which is exactly the property that makes it
useless as evidence. Changing a checklist afterwards must not retroactively
alter prior approvals.

**Open questions to settle before building:**
- **Do pending (unapproved) assets appear in the SSP component inventory?**
  A discovered-but-unapproved device is still physically on the network.
  Omitting it makes the inventory inaccurate — arguably worse than showing
  it with a pending marker. Leaning toward include-and-flag, but this is a
  scoping/compliance judgment, not a UI preference; decide deliberately.
- **Notification volume at MSP scale.** Many orgs × daily syncs × onboarding
  bursts (a 40-person client migration) = an email storm that trains people
  to ignore approvals. Needs digest batching and per-org routing from the
  start, not as a later fix.
- **Rejection semantics.** What does rejecting an asset mean — decommission
  it, mark it out of boundary, flag for removal? It exists on the network
  either way; the app can't make it disappear. Define what a rejection
  asserts.
- **Re-approval triggers.** If an approved device later loses an agent
  (RocketCyber uninstalled), does it re-enter the approval queue, raise a
  finding, or neither? This is arguably the more valuable half of the
  feature — continuous conformance vs. one-time gate — and it overlaps
  item G (Ongoing Compliance Tasks). Decide whether D.3 is onboarding-only
  or the entry point to continuous baseline monitoring before designing the
  data model.

**Audit trail:** approvals are exactly the kind of event
`audit_log` exists for, and actor attribution now resolves to the real user
(fixed 2026-09-07) — so "who approved this asset" is answerable without
additional plumbing.

---

### D.4 — Multi-tenant credential safety for a hosted WinGRC

**Added 2026-09-09 (Jarrod). Not started.** Tracked now, deliberately,
because hosted WinGRC is under active consideration — this needs an answer
before that decision is made, not scrambled together after.

**Why this is a separate item from D.1, not a footnote on it:** D.1's
encrypted-at-rest credential store (`backend/app/crypto.py`) is correct
*for self-hosted WinGRC specifically* — the MSP runs its own Postgres, so
"the platform" holding an encrypted Liongard key is the MSP holding its
own credential, in the same box that already holds its clients' CUI
scoping data. That reasoning breaks the moment WinGRC-the-vendor operates
the infrastructure instead of the MSP: at that point, one deployment-wide
encryption key protecting every tenant's `integration_connection` row
means WinGRC-the-vendor (or anyone who compromises its infrastructure)
can decrypt every customer's third-party API keys. That is exactly the
outcome item D's original "the platform never holds third-party API keys"
constraint existed to prevent — a hosted WinGRC would be reintroducing the
problem D was written against, not satisfying it.

**What a real answer needs to cover:**
- **Per-tenant key derivation or per-tenant KMS**, not one
  deployment-wide `WINGRC_CREDENTIAL_ENCRYPTION_KEYS`. A single shared key
  is the crux of the problem above — whatever replaces it must make one
  tenant's credential unreadable without that tenant's own key material,
  not just logically partitioned by `org_id`.
- **Unreadable across tenants even given DB access.** The threat model
  isn't "a stranger reads the database" — it's "WinGRC-the-vendor's own
  infrastructure, or an attacker who compromises it, has DB access by
  definition." Encryption-at-rest under one key doesn't defend against
  that; per-tenant keys held outside the app's own reach (a KMS with
  per-tenant grants, envelope encryption with tenant-held wrapping keys,
  or similar) are the kind of thing that would.
- **Key custody and rotation when the vendor operates the
  infrastructure.** Who holds the root key(s), how rotation happens
  without vendor staff ever handling a tenant's plaintext credential in
  the process, and what a tenant's exit/offboarding does to their key
  material.
- **The compliance implication.** A hosted WinGRC operator holding
  customers' third-party credentials (Liongard, RMM, SIEM keys) is a
  managed-service-provider-of-a-managed-service-provider posture with its
  own CMMC/compliance footprint — this needs to be named explicitly in
  whatever hosted-WinGRC compliance story gets written, not discovered
  after the fact.

**Explicitly not answered by D.1:** the self-hosted design shipped there
is a deliberate, scoped-to-self-hosted choice — not a claim that it's the
permanent or only answer. Don't reuse `crypto.py`'s single-key model for a
hosted deployment without redesigning it against the constraints above.

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

**Status: unstarted, and likely duplicated by `docs/roadmap.md` item
N ("Document Library") — flagged 2026-09-07, not reconciled here.**
Both describe the same feature (a tenant document library tagged to
objectives), with different table names (`template_document` /
`template_objective_link` here vs. `document` / `document_objective_tag`
in N) and different levels of detail — N additionally specifies a
publish workflow, a monetization boundary (free matching engine vs. paid
seed content), and evidence-link integration that this section doesn't.
N reads as the more current, more developed spec; this section reads like
an earlier draft that predates it. Neither has any code behind it yet
(verified: no `Document`/`template_document` model, no
`routers/documents.py`). Reconcile into one spec before building either.

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

**Status: mostly shipped — verified 2026-09-07.** `audit_log` table,
`audit.py:log_event()`, DB-level `REVOKE UPDATE, DELETE ON audit_log`
hardening (migration `0010`), and the paginated/filterable
`GET /orgs/{org_id}/audit-log` export endpoint
(`routers/audit_log.py`, `msp_admin`-only) all exist and match this
section's design closely — see `docs/roadmap.md` Done, "Deactivation +
audit log" entry. **One piece of this spec is not done:** the audit log is
not included in the assessment bundle export (`bundle_service.py` has no
audit-log section) — this section's "Export" bullet explicitly calls for
that. Hash-chaining tamper-evidence is unbuilt too, but that's expected —
this section's own text defers it explicitly ("not now").

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

**Status: Shipped — verified 2026-09-07. This entire section is now a
historical design record, not open work.** The "do NOT rush" framing below
predates the actual build; auth landed as a real, staged effort
(`docs/PLAN-auth-rbac-completion.md`, slices `I.1`–`I.9`), whose own status
header currently reads: I.1–I.8 merged/closed, I.9's automated checks
(pytest, `tsc -b`, `vitest`) green with one manual browser self-service
walkthrough still outstanding. That plan doc is the authoritative live
status — read it, don't infer from this entry.

What shipped, checked against code: session-based local login (PBKDF2-
HMAC-SHA256) + TOTP MFA + backup codes, Microsoft Entra ID SSO, API tokens,
migration `0015`; `auth.py`'s `require_write()` enforces a read-only gate
(`_READ_ONLY_ROLES = {"c3pao_assessor"}`) on every non-idempotent request,
applied at router level on `assessments.py`, `contacts.py`, `dashboard.py`,
`evidence.py`, `scope.py`, `bundle.py`, and `orgs.py`; `require_org_access()`
enforces per-org membership; `require_role()` gates role-only routes like
org creation. `docs/roadmap.md`'s Done section has the fuller writeup
(Authentication entry, plus the Auth/RBAC-completion Done entry added
2026-09-07).

**Roles — the three-role sketch below is superseded, not unresolved.** Four
roles shipped: `msp_admin`, `msp_engineer`, `customer_poc`, `c3pao_assessor`
(see `_ROLE_RANK` in `auth.py`). The mapping is: **MSP User** below split
into two tiers (`msp_admin` / `msp_engineer`, ranked, not equal); **Org
User** → `customer_poc`; **Assessor** → `c3pao_assessor`, and its
"read-only, cannot mutate state" requirement is enforced in code (see
above), not just a role label. This is a real design evolution (finer MSP
permissioning than originally sketched), not a gap that needs
reconciling — left below as the original design intent, not because it's
still open.

**Roles (original sketch, superseded by the four shipped roles above):**
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
  if the actor field was wired as "system" placeholders). **Partially true —
  verified 2026-09-07, and still genuinely open:** `audit.py`'s own module
  docstring says it plainly — `routers/users.py` (and `auth.py`) events carry
  the real authenticated actor now, but `assessments.py`/`evidence.py`/
  `contacts.py`/`orgs.py`/`bundle.py` "have not been retrofitted yet and
  still default to `actor='system'`... that retrofit is not part of this
  slice." So the core CMMC data-mutation audit trail — control-state
  changes, evidence attach/detach, statement edits — still logs `"system"`
  as the actor even though the real user is known via auth. This is a real
  remaining gap, not resolved by auth shipping alone.
- `org_id` scoping in every endpoint is enforced via the session's user context,
  not just a path parameter (the path parameter becomes a claim check).
- RBAC guards on the router layer (FastAPI dependency injection).

**Sequencing:** after the core assessment engine, evidence system, and audit log
are stable. Retrofitting auth into an already-working system is manageable;
retrofitting it into an actively shifting schema is painful. Ship the audit log
(H) first so real user identities drop into an already-wired actor field.
