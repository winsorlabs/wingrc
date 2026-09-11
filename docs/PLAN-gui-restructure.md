# Plan — GUI restructure: side nav, org dashboard, pre-org admin surface

**Status:** G.1 implemented, pushed (`e481a00`), and verified live on
wl-util-1 on 2026-08-18 (`npx tsc -b` clean, browser smoke test — see
`docs/roadmap.md`'s Done section). G.2 implemented, pushed (`cca09de`,
`e63e80f`) — its original 2026-08-18 "verified" pass (`alembic upgrade
head` clean, `pytest tests/test_sprs_snapshot.py` 3/3) predated two real
correctness bugs surfaced by G.3's smoke test: a concurrent-recompute
race (fixed `2c00b9b`) and a deterministic `autoflush=False` bug (fixed
`cbe3e57`, same day). Both fixes are now themselves verified live on
wl-util-1, 2026-08-19 — see the dated corrections in G.2's own section
below for the full detail. G.3 implemented, pushed (`33eeb32`), and now
**verified live on wl-util-1, 2026-08-19**: full backend suite 545/545,
integration subset 411/411, `tsc -b` clean, `vitest run` 37/37 (including
`lib/radarChart.test.ts`'s 8 new cases), and a browser smoke test
confirming the Dashboard's SPRS Score matches the assessment screen and
the new family-radar widget renders correctly — see G.3's own section
below and `docs/roadmap.md`'s Done entry for the full detail. A small
radar-chart addition to the SPRS widget (per-family completion %, 14
CMMC domains as spokes, hand-rolled SVG, no new dependency — commit
in the same 2026-08-19 batch) shipped and is verified alongside it.
G.4 implemented, pushed, and **verified live on wl-util-1, 2026-08-20**:
full backend suite 549/549, `test_assessments_list.py` 4/4, `npx tsc -b`
clean, `vitest run` 44/44 (5 files, including the new
`OrgDashboard.test.tsx` and `OrgPicker.test.tsx`), plus a full browser
walkthrough — assessment switcher default/override/hide-when-single all
confirmed. Adds `jsdom` + `@testing-library/react` as new devDependencies
(the first component-level tests in this codebase) — see G.4's own
section below for why.

G.4's browser verification also surfaced and fixed a real, pre-existing
bug unrelated to G.4 itself — `OrgPicker`'s auto-resume-from-cache logic
(present since the original assessment-board commit `7240bbf`, months
before G.1) was bouncing every return trip to the org picker straight
back into the board without ever rendering the picker or its "Start New
Assessment" button, making it impossible to start a second assessment
through the UI. Confirmed via diff that G.1 did not introduce or worsen
this. Fixed (`skipAutoResume` prop, gated on whether an org was already
open this session) and verified live alongside G.4 above — see G.4's own
section for full detail. A second, minor issue found and fixed in the
same commit: the multi-org branch's "Start New Assessment" button had no
`canWrite` gate (the single-org branch did), which was a UI inconsistency
only — the backend's `require_write()` already blocked the actual create
for read-only roles, so this was not a security gap, just made the
button visible-but-nonfunctional for e.g. `c3pao_assessor`.

G.5 implemented (`381dd3f5`, 2026-09-01; merged `c81346e4`, 2026-09-04) —
see `docs/roadmap.md`'s Done section for its own detail, not duplicated
here. G.6 implemented per the addendum this plan defers to
(`docs/pdf_ssp_template_spec.md`) — the upload/storage/frontend piece
shipped `381dd3f5`'s neighbor commit (Network/Data Flow Diagram slots on
`SystemDescription`, per `docs/roadmap.md`'s Done entry); this plan's own
remaining piece — the two side-nav entries that were still disabled
placeholders pointing at nothing — resolved 2026-09-08 (`7efdbe6e3`): both
now route to the System Description tab and scroll to/highlight the
relevant section rather than a separate page, matching the addendum's
explicit "no separate pages" scope. G.7 implemented and **verified live on
wl-util-1, 2026-09-08** (`7efdbe6e3`, `c81bcfa56`): there was no RACI API
surface at all before this — `backend/app/routers/raci.py` is new
(GET/POST/DELETE for individual assignments, `POST .../raci/bulk` for
family-level cascade). Full backend suite 507/507 on an isolated
throwaway stack (`docker compose -p wingrc_bench`, not the shared
instance), including `test_raci.py`'s 8 cases with the headline
bulk-assign-without-clobbering-an-override case; `ruff check .` clean;
`npx tsc -b` clean; a real browser walkthrough on that same isolated
stack confirmed bulk-assigning family AC to one contact, overriding one
objective to a second contact, re-bulk-assigning the family, and
confirming — both in the UI and with a direct `psql` query against
`raci_assignment` — that the override survived; and the G.3 dashboard's
"Open Tasks by Owner" widget (previously permanently degraded to an
"unassigned" bucket, per that widget's own code comment) was confirmed to
actually populate a named contact once evidence-task and RACI data
existed together. G.8–G.11 and M.7/M.8 remain proposed, not implemented.
**Baseline:** `e481a00` (G.1 landed and verified; supersedes the prior
`83fe49f` baseline this plan was originally written against).
**Scope:** replace the current screen-state-machine navigation with a persistent
side nav (Scope / Assessments / Tools / Library / Security), add an org
dashboard as the landing screen after org selection, and add an MSP-admin
screen for granting existing users access to additional orgs.

Slices are numbered `G.1`–`G.11`. Two slices continue the ADR 0009 multi-org
sequence under its own numbering (`M.7`, `M.8`) rather than `G.x`, because
they're backend prerequisites for `G.11` that belong to that ADR's model, not
to this plan's UI work — flagged explicitly where referenced. Each slice lands
as one or more commits directly on `main`, each ends green, each is
independently shippable, matching this codebase's established convention
(`docs/PLAN-auth-rbac-completion.md`'s own opening line).

---

## Current state — what "top-menu structure" actually means today

There is no top menu and no router. `App.tsx` is a five-state screen machine
(`"orgs" | "board" | "onboarding" | "settings" | "account"`) with two overlay
"drawers" (`OrgSettings`, `AccountSettings`) that remember a return screen.
The closest thing to a side nav that exists today is `OrgSettings.tsx` itself
— a tab rail (Org Profile / System Description / Personnel & Contacts / API
Tokens / Users / Audit Log) inside a drawer, not a persistent app-level nav.
`AssessmentBoard.tsx` (the main working view once an assessment is open) has
no internal tabs at all — `FamilySection`, `ProductsPanel`, and
`EvidenceTasksPanel` all render together on one page.

This matters for scoping every slice below: **most of "Scope" and all of
"Security" already exist as working screens** — G.1 is substantially a
*reorganization* of existing components into a persistent nav shell, not new
screens. The genuinely new frontend surfaces are Assets, Network/Data Flow
Diagrams, Roles (RACI), Library, the dashboard, and the pre-org admin screen.

**Naming collision, flagged before it causes confusion in review:** the
requested nav item "Library → Baselines" (security baseline/policy
documents) is a different concept from this codebase's existing
`Product → BaselineControl → BaselineEvidenceSpec` "baseline library" (per-
product control coverage, `baselines/*.yaml`, CLAUDE.md's "magic loop"). Both
are called "baseline" for good, independent reasons. G.10 below uses
"Security Baseline" (singular, capitalized) in code/schema to keep them
textually distinct; nothing about the existing product baseline library
changes.

---

## Sequencing rationale

`G.1` lands first because every other frontend slice needs somewhere to
mount. It touches zero backend and zero data — pure reorganization — so it
can land, get used, and absorb any layout feedback before anything else is
built on top of it.

`G.2` (SPRS snapshot table) lands before `G.3` (the dashboard) because the
dashboard's trajectory widget needs historical data to read; building the
widget first would mean shipping it against an empty table with nothing to
show until G.2 backfills forward from that point. The rest of G.3's widgets
need no new schema and could theoretically ship before G.2, but splitting the
one dashboard screen across two slices creates more churn than it saves —
G.2's schema addition is small enough to land immediately first.

`G.5`–`G.10` (Assets, Diagrams, Roles, Templates, Tools wizard, Library) have
no dependencies on each other or on the dashboard — they're independent Scope/
Assessments/Tools/Library additions and can ship in any order once G.1 exists.
Numbered here in the order the nav lists them, not because order matters.

`M.7`/`M.8` and `G.11` (pre-org screen) land last because they depend on
nothing above and nothing above depends on them — a fully separate feature
(cross-org access administration) that happens to need G.1's nav shell to
have somewhere to live, and otherwise stands alone.

---

## G.1 — Side nav shell + screen restructure

**Goal:** replace `App.tsx`'s flat screen machine with a persistent left nav
(Scope / Assessments / Tools / Library / Security) once an org is open,
remounting existing components under it. No new backend, no new data.

**Current state:** `OrgSettings.tsx`'s tab rail already covers three of the
five "Scope" sub-items (Org Profile, System Description, Personnel &
Contacts) and all of "Security" (Users, API Tokens, Audit Log — the last two
role-gated the same way they are today). `ProductsPanel.tsx` (rendered inline
inside `AssessmentBoard`) is the existing "Tools" surface — activate/
deactivate only, no import wizard (see G.9). "Assessments" today is a single
page per open assessment with no assessment-list view of its own within the
org (the list lives in `OrgPicker`, pre-dashboard).

### Design decision — nav is a shell, not a router

This codebase has no client-side router anywhere (`InviteAcceptPage`'s own
comment notes this deliberately). Introducing one is out of scope for this
slice — G.1 keeps the existing "screen state in `App.tsx`" pattern, just
restructures what "screen" means: instead of `orgs | board | onboarding |
settings | account`, an org-scoped session becomes `nav category + selected
item`, with the five categories as top-level state and each category's
sub-items as a second level, mirroring `OrgSettings`' existing `tab` pattern
one level higher. `onboarding` and `account` screens are unaffected — they're
already correctly orthogonal to nav (onboarding precedes having anything to
navigate; account is explicitly "not org-scoped," see I.9's own note on why
its mount point is top-level, not nested).

### Changes

- New `frontend/src/components/SideNav.tsx` — five categories, sub-items per
  the request's list. Role-gates sub-items the same way `OrgSettings` already
  does per-tab (`canSeeUsers`, `canSeeApiTokens`, `canSeeAuditLog` move here
  unchanged).
- `App.tsx` — replace `screen: Screen` with a nav-aware state shape once
  `org !== null`; `OrgPicker`/`onboarding`/`account` screens unchanged.
- `OrgSettings.tsx`'s tab content (not its tab rail) becomes the Scope/
  Security nav content directly — the components it already renders
  (`OrgProfileForm`, `SystemDescriptionForm`, `ContactsPanel`, `UsersPanel`,
  `ApiTokensPanel`, `AuditLogPanel`) are reused verbatim, just mounted under
  the new shell instead of `OrgSettings`' own drawer chrome. `OrgSettings.tsx`
  itself is retired once nothing mounts it (confirm via the same grep-for-
  zero-usages check M.6 used for `api.getOrg`).
- `AssessmentBoard.tsx` mounts under "Assessments," unchanged internally.
- `ProductsPanel.tsx` mounts under "Tools," unchanged (G.9 extends it later).

### Tests
- `permissions.test.ts`-style coverage for the nav's per-item role gates
  (mirrors `OrgSettings`' existing `canSeeUsers`/`canSeeApiTokens`/
  `canSeeAuditLog` logic, just relocated).
- Manual/browser smoke test: every sub-item that previously worked under
  `OrgSettings`' tabs still renders and functions identically under the new
  shell — this slice must be behavior-neutral for every screen it moves.

### Exit criteria
`tsc -b` clean, browser smoke test confirms no regression on any relocated
screen, `OrgSettings.tsx` deleted (not just unmounted) if the zero-usage
check in Changes confirms it's dead.

**Met — verified live on wl-util-1, 2026-08-18:** `npx tsc -b` clean;
`OrgSettings.tsx` deleted in `e481a00` itself; browser smoke test confirmed
Org Profile, System Description, Personnel & Contacts, Users, API Tokens,
Audit Log all working under the new shell, `AccountSettings` correctly
styled (regression caught and fixed in review before this verification),
and Tools activate/deactivate auto-returns to Assessments correctly.

---

## G.2 — SPRS score snapshot table

**Goal:** give the dashboard's SPRS trajectory widget (G.3) something to plot.

**Current state:** `assessment.sprs_score` holds only the *current* value,
overwritten on every recompute (`engine.py:recompute_sprs`, per CLAUDE.md).
No historical series exists. `ControlStateHistory` records per-*control*
status transitions, not the aggregate score — replaying it to reconstruct
historical scores would mean re-running `compute_sprs` at every historical
point in time, which is expensive and fragile (control weights, framework
membership, and the rollup logic could all change over the replay window).
Recommend a snapshot table instead of a replay.

### Changes

Migration: `sprs_snapshot(id, assessment_id FK, score, computed_at)`, no
`ondelete` needed beyond matching `assessment`'s own existing FK convention
(no CASCADE specified on `assessment`'s other org-scoped children per the
audit done for the wl-util-1 cleanup script — match that, don't diverge).
Index `(assessment_id, computed_at)`.

`engine.py:recompute_sprs` — after writing `assessment.sprs_score`, insert one
`sprs_snapshot` row. This is the *only* write path for the score today (per
CLAUDE.md: "SPRS recomputed after every product activation/deactivation" and
"always recomputed fresh before bundle export"), so one hook point covers
every case — activation, deactivation, and bundle export all already call
into this function.

**Retention:** unbounded for now — matches this table's own small row size
(one row per recompute event, recomputes are not high-frequency) and this
codebase's general pattern of not building retention/pruning until a real
volume problem shows up. Flag as a possible follow-up if recompute frequency
ever changes materially (e.g., a future "recompute on every control-state
edit" mode, which does not exist today).

### Tests
- `recompute_sprs` called twice with different states produces two distinct
  snapshot rows, correct scores, correct ordering.
- Bundle export's pre-export recompute produces a snapshot (confirms the
  hook fires on that call path too, not just activation/deactivation).

### Exit criteria
Migration applies cleanly, `pytest tests/test_sprs_snapshot.py` (new) green,
existing `compute_sprs`/`recompute_sprs` tests unmodified and still passing —
this slice adds a side effect, it must not change the score computation
itself.

**Implemented, pushed (`cca09de`, `e63e80f`), verified 2026-08-18 live on
wl-util-1.** `org_id` + RLS added beyond the plan's literal column list
(matching control_state/evidence_task/finding's convention — G.3's
dashboard endpoint will read this table directly); `seq` (BIGINT GENERATED
ALWAYS AS IDENTITY) added as the ordering key instead of `computed_at`,
same fix as migration 0020's password_history precedent, so the index
lands on `(assessment_id, seq)` rather than `(assessment_id, computed_at)`
as literally scoped above. **Verified, real run:** `alembic upgrade head`
applied `0028_sprs_snapshot` cleanly against real Postgres;
`pytest tests/test_sprs_snapshot.py -m integration -v` — 3/3 passed;
full backend suite 533/533 (up from 530 pre-G.2, the 3 new tests);
integration subset 399/399 (up from 396). `test_assessment_engine.py`'s
existing `compute_sprs`/`recompute_sprs` coverage unmodified and still
green, confirming this slice only added the snapshot side effect.

**Correction, 2026-08-19 (found via G.3's browser smoke test):** the
"activation, deactivation, and bundle export" call-site list above (and
this file's own paraphrase of CLAUDE.md's "SPRS recomputed after every
product activation/deactivation") was incomplete. `recompute_sprs()` has
five call sites in total, not the three named categories above:
`start_assessment`, `activate_org_product`, `deactivate_org_product`,
`bundle_service.snapshot_bundle`, and — the one missed here —
`routers/assessments.py:patch_control_state`, which predates this slice
entirely (added 2026-07-09, in the original "Wire live SPRS scoring"
commit) and fires on every control-state status PATCH (e.g. marking an
objective "met" from the assessment board). The omission was repeated
verbatim into `models.py`'s `SprsSnapshot` docstring, now corrected
there too. The "single write path" claim itself still holds — every
site, including this one, funnels through the same `recompute_sprs()`
function — only the enumerated list was wrong, not the design.

That said: this *is* the mechanism behind a real bug the smoke test
found (Dashboard's SPRS widget showing a different score than the
assessment board's live recomputation after marking several objectives
met). `recompute_sprs()` does an unprotected read-then-write of
`assessment.sprs_score` with no row lock — any two concurrent calls
(from any combination of the five sites above, e.g. a control-state PATCH
racing a product deactivation on the same assessment) can interleave
under READ COMMITTED such that the transaction with the *less complete*
snapshot commits last and overwrites the more complete one. This is a
pre-existing gap in `recompute_sprs()` itself, not something this slice
introduced — G.2/G.3 are just the first things to surface it visibly,
by putting the stored value next to an always-fresh live computation in
the same UI for the first time.

**Fixed, pushed (`2c00b9b`):** `recompute_sprs()` now acquires
`SELECT ... FOR UPDATE` on the assessment row first, before reading
`control_state` — locking only at the final write (its original
position) would not have fixed this, since both transactions' reads
could still happen before either committed. Verified live on
wl-util-1 — `test_recompute_sprs_locks_the_assessment_row` (a genuine
two-connection test using `SELECT ... FOR UPDATE NOWAIT`, not a
same-transaction sequential-calls test that wouldn't exercise real
concurrency) passed against real Postgres.

**Second correction, 2026-08-19 (same day, found because the bug report
above persisted after the lock fix landed):** the lock fix was correct
but incomplete — it closed the genuinely concurrent race but not a
second, different, *deterministic* bug that was producing the exact
same symptom ("Dashboard lags the assessment board by exactly one
recompute," reproducible every time, not intermittent). Root cause:
`app/db.py`'s production `SessionLocal` sets `autoflush=False`.
`patch_control_state` sets `cs.status = body.status` and calls
`recompute_sprs()` immediately, with no flush in between — under
`autoflush=False`, `recompute_sprs()`'s own `control_state` SELECT never
saw that pending change, so it computed a score missing the very edit
that triggered it, every single call. `activate_org_product`/
`deactivate_org_product`/`_run_loop` all happen to flush their own
writes before reaching `recompute_sprs()` already, so this was never
visible from those call sites — only `patch_control_state` hit it, and
it hit it deterministically, not as a race. Not caught by this table's
own test suite before now because `tests/conftest.py`'s test session
never sets `autoflush`, defaulting to `True` — silently masking exactly
this bug in every test that would otherwise have exercised it,
including the `patch_control_state` test added in the first correction
above. Fixed, pushed: `recompute_sprs()` now flushes unconditionally as
its first statement, before the lock acquisition, so it's correct
regardless of caller discipline or session `autoflush` setting. New
regression test explicitly sets `db_session.autoflush = False` before
calling the endpoint, to remove the accidental safety net the default
test session setting provided and actually exercise production's
ordering — see `test_patch_control_state_score_reflects_its_own_edit_under_autoflush_false`
in `tests/test_sprs_snapshot.py`.

---

## G.3 — Org dashboard: shell + existing-data widgets

**Goal:** the landing screen after org selection. Every widget in this slice
reads data that already exists — no new schema beyond G.2's snapshot table,
which this slice is the first consumer of.

**Current state:** no dashboard exists. `OrgPicker` currently goes straight
from org selection to the assessment list; there is no org-level summary
screen at all.

### Widget-by-widget: exists vs. new

| Widget | Data source | New backend work |
|---|---|---|
| Family completion heatmap | `Control.family` + `ControlState.status`, aggregated | New read endpoint (aggregation query); no new schema |
| SPRS score + trajectory | `assessment.sprs_score` + G.2's `sprs_snapshot` | New read endpoint over G.2's table |
| Statement authoring progress | `ImplementationStatement.status` counts | New read endpoint; no new schema |
| Evidence expiring in 30 days | `EvidenceTask.expires_at`/cadence fields (already exist per CLAUDE.md's roadmap item 9b note) | New filtered read endpoint; no new schema |
| Needs-review queue | `ControlState.status = 'needs_review'` | New read endpoint; no new schema |
| Blocked objectives | `ControlState.status = 'pending_evidence'` with zero `EvidenceStateLink` rows | New read endpoint (anti-join); no new schema |
| Open tasks by RACI-responsible contact | `EvidenceTask` → `EvidenceTaskStateLink` → `ControlState` → `RaciAssignment` (`raci_letter='R'`) → `Contact` | New read endpoint (multi-table join); no new schema. Degrades gracefully to an "unassigned" bucket until G.7 (Roles UI) exists to populate `raci_assignment` — not a hard dependency, just a quieter widget until then. |
| POA&M summary | `PoamItem.status` counts | New read endpoint; no new schema |
| Recent activity | `audit_log`, existing viewer/endpoint (`GET /orgs/{org_id}/audit-log`) already built | Reuse as-is with `limit` |

Every "new backend work" cell above is a **new read endpoint**, not new
tables — this is the one area of the whole plan where "new backend work" is
uniformly cheap. Recommend one `GET /orgs/{org_id}/dashboard` endpoint
returning all widgets' data in one response (one round trip, one place to
keep the aggregation queries) rather than nine separate endpoints — this
mirrors `OnboardingStatus`'s existing shape (`GET
/orgs/{org_id}/onboarding-status` already returns several independent
completion signals in one payload).

### Changes
- `backend/app/routers/orgs.py` (or a new `dashboard.py` router, given nine
  aggregation queries is a meaningfully different shape from `orgs.py`'s
  existing CRUD-per-resource pattern — lean toward the new router) —
  `GET /orgs/{org_id}/assessments/{assessment_id}/dashboard`.
- `frontend/src/components/OrgDashboard.tsx` (new) — nine widget components,
  mounted as the default view once G.1's nav shell resolves an org+assessment.

### Tests
Backend: one integration test per widget's query correctness (seed a known
state, assert the aggregation matches), plus the anti-join case for blocked
objectives specifically (easiest of the nine to get subtly wrong).

### Exit criteria
`pytest` green for the new dashboard endpoint, browser smoke test against a
real org with non-trivial data in every table the widgets read.

**Implemented, pushed (`33eeb32`), verified live on wl-util-1,
2026-08-19.** "Recent activity" (the plan's ninth widget) is deliberately
NOT folded into the combined payload — it stays a separate frontend call
to the existing, unmodified, msp_admin-gated `GET
/orgs/{org_id}/audit-log` endpoint, since embedding it would mean either
leaking audit data to every dashboard-viewing role or conditionally
omitting a field per role, both worse than the plan's own "reuse as-is."
Mount point resolved with the user before implementing: new top-level
"Dashboard" SideNav category, not nested under "Assessments."

**Verified, real run:** `pytest tests/test_dashboard.py` 9/9; full
backend suite 545/545 (up from 542 pre-G.2-fixes baseline — includes the
autoflush regression test); integration subset 411/411; `alembic upgrade
head` clean (no new migration for this slice, confirmed no-op); `npx tsc
-b` clean; `vitest run` 37/37 across `permissions.test.ts` (16),
`filters.test.ts` (13), and `radarChart.test.ts` (8, new). Browser smoke
test confirmed: all nine widgets render against a real org with
non-trivial data; the blocked-objectives anti-join correctly excludes
controls with evidence attached; "Open Tasks by Owner" degrades
gracefully to an "Unassigned" bucket since G.7 doesn't exist yet;
Recent Activity stays `msp_admin`-gated; and — after two real bugs were
found and fixed via this exact check (see G.2's corrections above) — the
Dashboard's SPRS Score now matches the assessment screen's exactly, even
after deliberately overlapping a control-state edit with a product
activate/deactivate to try to retrigger the original race.

**Family radar chart addition** (not in the original plan; small
follow-on request, same 2026-08-19 batch): per-family completion % for
all 14 CMMC domains as radar spokes, added alongside (not replacing) the
existing Family Completion bar list. Hand-rolled SVG — `frontend/package.json`
had no charting library, and a fixed 14-axis non-interactive chart didn't
justify adding one. New `lib/radarChart.ts` (pure coordinate-mapping
function, no DOM) and `lib/families.ts` (extracted the 14-family order
from `AssessmentBoard.tsx` into one shared source of truth, so the radar
and the bar list can't drift into two different orderings). 8 new tests
in `radarChart.test.ts` — verified live, all passing. Browser smoke test
confirmed all 14 spokes render in bar-list order with matching values.

---

## G.4 — Default-to-most-recent-assessment + switcher

**Goal:** the dashboard defaults to the assessment the user most recently
worked, with a dropdown to switch.

**Current state:** `Assessment` has `started_at`, `submitted_at`,
`closed_at`, `created_at` — nothing tracking "last touched." `ControlState`
and `ImplementationStatement` both have `updated_at`, which is the closest
existing proxy for "recently worked."

### Design decision — derive, don't add a column

Two options: (a) derive "most recent" as `MAX(control_state.updated_at)`
(and `implementation_statement.updated_at`, unioned) per assessment at read
time, or (b) add `assessment.last_activity_at`, written on every mutation
touching that assessment. Recommend **(a)**: no migration, no new write path
to keep correct as more mutation sites get added over time (every future
slice that touches a `ControlState` row already updates its `updated_at` —
free correctness, vs. (b) requiring every such call site to also remember to
bump a second column). The query cost is one `MAX()` over an indexed column
per org's assessment list, which is small (an org's assessment count is
low — one per framework per audit cycle, not a high-cardinality table).

### Changes
- `GET /orgs/{org_id}/assessments` (existing) gains a `last_activity_at`
  computed field via the `MAX()` above.
- `OrgDashboard.tsx` — assessment switcher dropdown, defaults to the entry
  with the latest `last_activity_at`.

### Exit criteria
Backend test confirms the derived ordering; browser smoke test confirms the
dropdown switch actually re-fetches the dashboard for the newly selected
assessment.

**Implemented, pushed, verified live on wl-util-1, 2026-08-20.** Two
design questions confirmed with the user
before implementing rather than picked silently:

1. **Default-vs-override:** the switcher's "defaults to the entry with the
   latest `last_activity_at`" only fires when `App.tsx` had no assessment
   already selected (the settings-gear entry path, previously a dead end
   showing "go back to org picker"). It never overrides an assessment
   already established by `OrgPicker` — an explicit click, or `OrgPicker`'s
   own separate, pre-existing "last opened in this browser" `localStorage`
   cache (`getCachedAssessmentId`/`setCachedAssessmentId` in `api.ts`,
   unrelated to this slice and left untouched). The alternative
   ("always default to freshest on mount") would have silently swapped
   away from a choice the user just made.
2. **Component-test infrastructure:** verifying "the dropdown switch
   actually re-fetches, not just relabels" for real means rendering
   `OrgDashboard` and simulating a `<select>` change — this codebase had
   zero component-rendering test infrastructure before this slice
   (`vite.config.ts` set `test.environment: "node"`, no DOM; every
   existing frontend test is a pure-function test in `lib/`). Added
   `jsdom` + `@testing-library/react` as new devDependencies, scoped to
   this one test file only via a `// @vitest-environment jsdom` comment
   (not a global `vite.config.ts` change) —
   `frontend/src/components/OrgDashboard.test.tsx` is the first
   component-level test in the codebase. Confirmed with the user first,
   same standard as the radar chart's charting-library question.

Backend: `_last_activity_by_assessment` (two `GROUP BY` queries, not a
join — `control_state`/`implementation_statement` have no relationship to
each other, only to `assessment`) in `routers/assessments.py`.
`AssessmentOut` gains a required `last_activity_at: datetime` (no default —
only two construction sites exist, so nothing is gained from a silent
fallback that could mask one that forgot to set it). Caught before
running anything: both construction sites originally used
`AssessmentOut.model_validate(a).model_copy(update={...})`, which is
wrong — `model_validate(a)` validates immediately against the ORM object's
own attributes, and `Assessment` has no `last_activity_at` attribute, so
it would raise "field required" before `.model_copy()` ever got a chance
to patch it in. Fixed by constructing `AssessmentOut(...)` directly with
every field supplied as an explicit keyword argument in both places.

Frontend: `OrgDashboard.tsx` fetches the org's assessment list itself,
auto-selects per the confirmed default-vs-override rule, and lifts the
switcher's selection back up to `App.tsx`'s own `assessment` state via a
new `onSwitchAssessment` prop — this keeps `AssessmentBoard`/
`ProductsPanel`/the breadcrumb showing the same assessment the dashboard
switched to, rather than the dashboard silently diverging from the rest
of the nav shell.

Tests: `tests/test_assessments_list.py` (backend) covers the derived
ordering — including proving it reflects `implementation_statement`
activity too, not just `control_state` — using explicit `updated_at`
values set directly on ORM objects rather than real wall-clock gaps,
since this test suite runs inside one `db_session` transaction and
Postgres's `now()` is transaction-start time, not statement-execution
time (the same root cause as migration 0020's `password_history` bug and
`sprs_snapshot`'s `seq` column — flagged in the test file's own module
docstring so it isn't independently rediscovered a third time).
`OrgDashboard.test.tsx` (frontend, new infrastructure) covers: switching
re-fetches with the new assessment id; auto-default fires only when
nothing was pre-selected; auto-default never overrides a pre-existing
selection even when a fresher assessment exists; the switcher hides
entirely when the org has only one assessment.

**Verified, real run, 2026-08-20:** `pytest tests/test_assessments_list.py`
4/4; full backend suite 549/549; `npx tsc -b` clean;
`vitest run` 44/44. `OrgDashboard.test.tsx`'s first pass on wl-util-1
showed 3 of 4 cases failing — root cause was missing test isolation
(no `afterEach(() => { cleanup(); vi.clearAllMocks(); })` in the test
file), so calls and DOM nodes from earlier tests in the same file were
leaking into later assertions; a static trace of `OrgDashboard.tsx`
itself against the failures found no actual component bug, which held
up once isolation was fixed — component code was untouched, only the
test file changed. Fixed and reverified: 44/44 green.

**Real, pre-existing bug found and fixed during this slice's browser
verification (not a G.4 bug, not introduced by G.1 — diffed against
`e481a00` to confirm):** `OrgPicker`'s cached-assessment auto-resume
(present since `7240bbf`, the original assessment-board commit, months
before this GUI restructure plan existed) unconditionally bounced any
return trip to the org picker straight back into the board whenever a
`localStorage` cache hit existed — including an explicit breadcrumb
"go back," not just a fresh login. This made "Start New Assessment"
unreachable for any org that already had one assessment open, which is
precisely why G.4's own browser verification (needing a second
assessment to test the switcher) surfaced it. Fixed via a
`skipAutoResume` prop threaded from `App.tsx` (`org !== null`, i.e. true
once an org was already opened this session) into `OrgPicker`, gating
the auto-jump — fresh-login fast-resume is preserved, explicit
navigate-back now shows the real picker. Also fixed in the same commit:
the multi-org branch's "Start New Assessment" button had no `canWrite`
gate (the single-org branch did) — UI inconsistency only, not a security
gap, since `require_write()` already blocked the actual create
server-side for read-only roles. New `OrgPicker.test.tsx` (3 tests,
included in the 44/44 above) covers both the skip-vs-resume behavior and
the `canWrite` gate. Verified live: browser walkthrough confirmed fresh
login still fast-resumes, explicit navigate-back now shows the picker,
and a second assessment could finally be created to exercise G.4's
switcher.

---

## G.5 — Assets (in-scope hardware/software inventory)

**Goal:** a real Assets screen under Scope, resolving the open question
`docs/pdf_ssp_template_spec.md` explicitly left unresolved ("build a real
Hardware/Software Inventory feature, or keep treating this as an uploaded
evidence artifact? ... decide before building either").

**Current state — more already exists than "new" suggests.**
`domain.py`'s `EntityType` already has `DEVICE` and `SOFTWARE` values,
explicitly commented "reserved for later modules; not yet imported from the
workbook." `ScopeEntity`'s `attributes` JSONB column can hold arbitrary
per-type fields with no migration. `repo.upsert()` (a real, tested,
reusable single-entity upsert) already exists and is used today only by
`cli.py`'s `seed --apply` command. **What doesn't exist: any frontend at
all for scope data, and any API write path outside the CLI.** Confirmed by
grep: zero frontend files reference `scope_entity`/`ScopeEntity` today;
`main.py`'s own docstring says apply is "deliberately a separate, explicit
step" but that principle is about dry-run-before-apply, not
CLI-vs-API — nothing blocks exposing apply over HTTP the same way dry-run
already is.

### Changes

Backend (`main.py` or a new `scope.py` router — the existing scope endpoints
live directly in `main.py`, which is already the exception to this
codebase's "one router file per resource" pattern; new work here should
probably extract a real router rather than grow `main.py` further):
- `POST /orgs/{org_id}/imports/workbook/apply` — takes the same confirmed
  diff shape the dry-run endpoint already returns, calls `reconcile()` +
  `repo.upsert()` per changed entity, same functions `cli.py` already calls.
  Preserves the "confirmed diff before mutation" principle — the frontend
  shows the dry-run diff, the user confirms, *then* this fires.
- `POST /orgs/{org_id}/scope` / `PATCH /orgs/{org_id}/scope/{id}` / `DELETE
  /orgs/{org_id}/scope/{id}` — manual single-entity CRUD for ad-hoc asset
  entry without a spreadsheet. `POST`/`PATCH` are thin wrappers around
  `repo.upsert()`; `DELETE` is new (no delete path exists in `repo.py`
  today, a plain `session.delete()`).
- Per-`EntityType` `attributes` schema convention for `DEVICE`/`SOFTWARE`
  specifically (make/OEM, model, version, responsible-contact reference),
  matching the NIST template gap doc's exact field list
  (`pdf_ssp_template_spec.md`'s "Component/asset inventory" gap section).
  Validate at the Pydantic layer, not the DB — `attributes` stays JSONB,
  no schema migration.

Frontend:
- `frontend/src/components/AssetsPanel.tsx` (new) — table filtered to
  `entity_type IN (device, software)`, add/edit form for the fields above,
  and a workbook-import wizard (dry-run diff preview → confirm → apply)
  that's genuinely new UI — no prior art to reuse, since no import UI has
  ever existed in this codebase.

### Tests
Backend: the two new CRUD-ish endpoints round-trip correctly; apply matches
what `cli.py --apply` already does bit-for-bit (regression-test the two code
paths produce identical `scope_entity` state for the same input, so the CLI
and API never silently diverge). Frontend: the import wizard's dry-run
preview correctly reflects the same diff the backend would apply.

### Exit criteria
`pytest` green, `tsc -b` clean, browser smoke test: import a workbook via the
new wizard, confirm the resulting `scope_entity` rows match what `wingrc
seed --apply` would have produced for the same file.

---

## G.6 — Network Diagram & Data Flow Diagram

**Goal:** exactly `docs/pdf_ssp_template_spec.md`'s "Addendum: Network
Diagram & Data Flow Diagram" section — built against that spec directly, not
independently, per the instruction.

**Current state:** fully specified, nothing built. Repeated here only to
confirm this plan adds no new decisions beyond what that addendum already
settled — implementers should read that section directly, not this summary.

### Changes (per the addendum, verbatim scope)
- Two dedicated attachment slots on `SystemDescription` — not generic
  evidence — "Network Diagram" and "Data Flow Diagram," pinned the same way
  the org logo has a dedicated slot today (`Organization.logo_storage_key`
  is the precedent to follow: two new nullable `storage_key` columns on
  `SystemDescription`, migration only, no new table).
- Reuse the existing evidence pipeline: MinIO storage, MIME allowlist
  extended to `image/svg+xml` and `image/png`, existing magic-byte check,
  existing SHA-256 hashing. SVG sanitization on ingest (strip `<script>`
  and external references) — new, since no prior upload path has accepted
  SVG.
- Optional linking to relevant objectives via the *existing*
  evidence-to-objective many-to-many (`EvidenceStateLink`) — no new linking
  mechanism.
- Frontend: two upload widgets in the System Description editor
  (`SystemDescriptionForm.tsx`, extended), live thumbnail preview, replace
  action that retains prior versions (existing evidence-versioning
  convention, not overwrite).
- PDF placement is scoped to the PDF SSP template work itself (same doc,
  main section), not this slice — flagged so this slice's scope stays the
  upload/storage/frontend piece, not the render piece.

### Open risk (carried over from the spec, not resolved here)
The addendum's own "Open risk" section — interchange contract with the
separate survey/diagram-generating project — is unchanged by this plan.
Test real output from that tool against this ingest path before considering
the slice done, per the spec's own words.

### Exit criteria
Matches the addendum's own implied criteria: upload → thumbnail → replace
round-trip works for both slots, SVG sanitization confirmed against a
deliberately malicious test SVG (embedded `<script>`, external `<image
href>`), MIME/magic-byte rejection confirmed for non-image uploads.

---

## G.7 — Roles (RACI / responsibility roles)

**Goal:** the RACI assignment UI named in `CLAUDE.md`'s roadmap item 4,
finally built.

**Current state:** `RaciAssignment` (model) and `Contact` CRUD both exist.
**No RACI API surface exists at all** — confirmed by grep across every
router: no route matches `raci` anywhere. The model has never been read or
written outside test fixtures. This is more backend work than "model
already exists" implies on its own — the model is a table, not a feature.

### Changes

Backend (new `backend/app/routers/raci.py`):
- `GET /orgs/{org_id}/assessments/{assessment_id}/raci` — every assignment
  for the assessment, joined to `Contact` for display.
- `POST /orgs/{org_id}/assessments/{assessment_id}/raci` — assign one
  `(control_state_id, contact_id, raci_letter)`.
- `DELETE .../raci/{id}` — remove one assignment.
- `POST .../raci/bulk` — family-level assign cascading to every child
  `control_state` in that family, per the roadmap item's own stated design
  ("family-level assign cascades to all child control states; override at
  individual objective level"). This is the one genuinely non-trivial piece
  — needs to correctly *add* the family-level assignment to every objective
  currently without an override, without clobbering objective-level
  overrides already in place.
- Magic-loop pre-suggestion (`BaselineControl.responsibility` → suggested
  MSP-vs-customer contact) — per the roadmap item's stated design, but
  scoped here as a *suggestion* the UI surfaces, not an auto-write; matches
  this codebase's "candidates, never auto-met" hard rule in spirit even
  though RACI isn't a control-state mutation — don't silently assign, offer.

Frontend:
- `frontend/src/components/RolesPanel.tsx` (new), under Scope. Family-level
  bulk-assign control, per-objective override list, contact picker.

### Tests
Backend: bulk-assign cascades correctly and doesn't clobber existing
per-objective overrides (the one case worth real test weight); CRUD
round-trip for individual assignments.

### Exit criteria
`pytest` green, browser smoke test: bulk-assign a family, confirm every
child objective shows the assignment, override one objective, confirm the
override survives a second family-level bulk-assign of the same family.

### Follow-up, 2026-09-09 — moved under Assessments; inline per-objective control added

**Moved from Scope to Assessments.** RACI is assessment-scoped data
(`RaciAssignment.control_state_id` → one specific assessment's control
states) sitting in an org-scoped menu — the "under Scope" placement above
was wrong from the start, not a later regression. `RolesPanel.tsx` now
renders as an Assessments sub-tab ("Roles", alongside the existing board,
called "Assessment Board" in the nav) instead of a Scope sub-tab; Scope's
own "Roles" entry is removed rather than left as a second pointer to the
same editor. `RolesPanel` already took `assessmentId` as an explicit prop
from `App.tsx`'s `assessment` state (never an implicit "current
assessment" lookup), so the move changes nothing about *which* assessment
it edits — confirmed before moving, not assumed.

**Inline per-objective RACI added to the assessment board's own objective
drawer** (`ControlDrawer.tsx`), directly above the Evidence section —
ownership before proof. Uses the exact same endpoints `RolesPanel.tsx`
does (`GET/POST/DELETE .../raci`), fetched once per drawer open (the whole
assessment's assignments + contacts, not once per objective) and filtered
client-side per `control_state_id` — one API, two presentations, per this
plan's own anti-duplication discipline. New `RaciSection.tsx`, styled to
match `EvidenceSection.tsx`'s section-header/count-badge/footer treatment
exactly (new `.raci-drawer-*` CSS classes, deliberately not shared with
`.ev-*` — same visual language, independent sections free to diverge
later). The MSP-vs-customer suggestion needed `ControlState.responsibility`
on `StatementOut` (`get_statements`) — a one-field addition, since that
endpoint already loads the `ControlState` row per objective for
`control_state_id`, not a new query. `suggestedAffiliation`/
`sortedForSuggestion` (previously private to `RolesPanel.tsx`) moved to
`lib/raci.ts` so both presentations share one suggestion rule.

**Product question raised and answered:** does RACI carry forward when a
new assessment starts, or begin empty? Asked Jarrod rather than picking
(nothing today addresses it, since there's only ever been one
assessment's worth of RACI data) — **decided: copy forward from the most
recent prior assessment, editable from there.** Not implemented in this
pass — `start_assessment`/`engine.py` still seeds no RACI rows, so today's
behavior is unchanged (starts empty) — this is a recorded decision for
whoever picks up the actual copy-forward implementation, not a completed
slice.

**Built 2026-09-10** — see `docs/roadmap.md`'s Done entry for the full
writeup, not duplicated here. `engine.py:copy_forward_raci` implements
this decision as stated (most recent prior assessment on the same
framework, editable from there); the two open questions this section left
unresolved (framework mismatch, departed contacts) were decided at build
time — see the roadmap entry.

### Part 3, 2026-09-09 — CRM (Customer Responsibility Matrix)

The deferred CRM roadmap item, unblocked now that RACI data exists —
see `docs/roadmap.md`'s Done entry for the full writeup, not duplicated
here. Summary: `bundle_service.py` gains a `CrmRowSnap` dataclass
(reshapes the already-built `controls`/`objectives` tree, no new query),
a `_crm_body`/`_render_crm` pair wired into all four `render_bundle()`
touch-points plus the consolidated PDF, shipping as
`ssp/05_customer_responsibility_matrix.html`. **In-app matrix view:
evaluated before building** — `RolesPanel.tsx` already lists every
objective's RACI assignments, so no second view was built; the one real
gap (affiliation wasn't visible in the read-only chip, only in the
picker) closed by adding `contact_affiliation` to
`RaciAssignmentOut`/`RaciAssignmentRow` instead.

### Part 4, 2026-09-09 — Assignment-load visualization

See `docs/roadmap.md`'s corresponding Done entry for the "what counts as
load" and "where it lives" decisions and the full writeup.

---

## G.8 — Assessment templates

**Goal:** "current assessments, assessment templates (pre-built like the
current CMMC one, or user-created)."

**Decided 2026-08-17 (Jarrod): reading (a), multi-framework support.**
Recorded here so implementation can start from this plan directly; the two
readings below are kept for the record of what was weighed.

**(a) Multi-framework support.** `Framework → Control →
AssessmentObjective` already models exactly this — the catalog is generic,
only one framework (`nist-800-171-r2`) has ever been seeded. Under this
reading, "pre-built like the current CMMC one" means *other* `Framework`
rows (e.g., a lighter self-assessment checklist, a different regulatory
catalog), and "user-created" means a UI for authoring a new `Framework` +
`Control` + `AssessmentObjective` set. `start_assessment` already takes a
`framework_id` — `OrgPicker`'s `startAssessment` just hardcodes `cmmc_l2`
today. This reading is **mostly existing model, new UI + a framework
authoring surface**.

**(b) A distinct template/checklist concept**, lighter than a full
`Framework` (no SPRS weights, no assessment-objective granularity) — e.g., a
reusable named subset of controls for a narrower internal review. This
reading needs a **new data model** (`AssessmentTemplate` or similar) with no
existing precedent to build on.

**Recommendation: (a).** It reuses the entire existing catalog model
(SPRS scoring, objective-level tracking, everything the rest of the product
already assumes an assessment has) rather than introducing a second,
parallel "lighter" assessment concept that every other feature (dashboard,
bundle export, magic loop) would need to learn to handle or explicitly
exclude. If a genuine need for (b)'s lighter checklist concept surfaces
later, it's a materially different feature from "assessment templates" as
named here and deserves its own ADR-style scoping pass, not a rider on this
slice.

### Changes (assuming (a))
- Backend: framework authoring endpoints (`POST /frameworks`,
  `POST /frameworks/{id}/controls`, `POST .../assessment-objectives`) — new,
  `frameworks.py` today is read-only (`GET /catalog/views` and framework
  listing only, confirmed by the router's current shape).
- Frontend: framework picker in the "start assessment" flow (replacing
  `OrgPicker`'s hardcoded `cmmc_l2` lookup), plus a new authoring UI under
  Assessments for user-created frameworks.

### Exit criteria
`pytest` green for the new framework-authoring endpoints, `tsc -b` clean,
browser smoke test: author a second framework end to end (controls +
assessment objectives), start an assessment against it from the picker,
confirm SPRS scoring and the magic loop work against the new framework the
same as they do against `nist-800-171-r2`.

---

## G.9 — Tools import wizard ✅ SHIPPED 2026-09-11 (pending live wl-util-1 verification — see status line at the end of this section)

**Original goal:** "tool library activate/deactivate, plus import wizard /
manual creation."

**What actually shipped is narrower than the original goal, on purpose —
Jarrod's own revision mid-slice.** The original prompt for this slice
described Tools as *assigning products to tenants* from the deployment
tier; that was explicitly dropped before any code was committed
(`OrgProduct` writes, activation, and any UI for it stay exactly where
they were — per-org, via `ProductsPanel.tsx` / `engine.py`'s magic loop,
fired from inside an org). The design-decision split this section
originally proposed ("activate/deactivate stays per-org; import wizard
moves to deployment tier") is what actually landed, but the deployment-tier
half is **library management**, not tenant assignment: it manages the
`Product`/`BaselineControl`/`BaselineEvidenceSpec` rows themselves, and
never creates an `OrgProduct` row. "Manual single-entry creation" (the
`POST /admin/products` / `.../baseline-controls` form this section
originally proposed) was dropped too — the YAML stays the sole authoring
format; this screen ingests and reviews it, it does not edit fields
in-app. If in-app field editing is wanted later, that's its own slice with
its own versioning design (see the hazard below).

### What shipped

- **Library view** (`GET /admin/products`) — every `Product` with provider,
  category, asset_type, framework, `is_published` state, and a
  control/objective count computed from `BaselineControl`.
- **Deployment footprint** (`GET /admin/products/{id}/footprint`) — which
  orgs have a given product and at what `OrgProduct.status`, read-only.
  Cross-org by nature (the whole point is one product's footprint across
  every org in one view), so it reads through a new SECURITY DEFINER
  function, `auth.product_deployment_footprint(p_product_id)` (migration
  `0037_product_footprint`), the same M.2/M.5 pattern — RLS on
  `org_product` is untouched; `EXECUTE` is restricted to `wingrc_app`.
- **Tool detail** (`GET /admin/products/{id}`) — product metadata plus its
  full baseline mapping grouped by control (classification, coverage_basis,
  objectives, provider_contribution, customer_action, scope_note, evidence
  specs). `coverage_basis = platform_only` rows render visually distinct
  (`ToolDetailPanel.tsx`'s `.coverage-basis-platform-only` styling) with an
  inline note that they're excluded from magic-loop activation — a reader
  who misses that distinction misreads the whole mapping.
- **Documentation attachments** — new `product_document` table (migration
  `0036_product_document`), FK to `product`, storage key
  `products/{product_id}/{document_id}/{document_id}{ext}` (no org prefix —
  `Product` is deployment-wide, unlike `Evidence`'s per-org key). Reuses
  `storage.py`'s existing `StorageClient` abstraction and `evidence.py`'s
  upload validation pipeline (magic bytes, extension/MIME allowlist, 50 MB
  cap) rather than inventing a second path. The YAML's `source_docs:` free
  text (now actually stored — migration `0038_product_source_docs` added
  the column; it was parsed by nothing and stored nowhere before this
  slice) is kept as-is; an upload references one of those strings via
  `source_docs_ref` rather than replacing it. This closes the provenance
  chain: vendor document → mapping claim → tenant's control_state.
- **Import** (`POST /admin/products/import/dry-run` then `.../import/apply`)
  — `backend/app/baseline_import.py`'s `validate()` collects every problem
  in the file in one pass (unknown control ids, bad
  classification/coverage_basis/candidate_state/evidence_type enum values,
  malformed objective keys, and the minimization invariant —
  `classification == customer_owns` rows may never carry evidence specs).
  Dry-run shows a diff (new product vs. changed/unchanged controls on an
  existing key) before anything is written; apply independently
  re-validates rather than trusting a token from a prior dry-run call — see
  that module's own docstring for why that's deliberate, not duplicated
  work. On apply, the product lands with `is_published = False`.
- **Publish / unpublish** (`POST .../publish`, `.../unpublish`) —
  `Product.is_published` (schema column since migration 0002, set `False`
  by the seeder, read nowhere until this slice) is now enforced at the two
  real read paths: `routers/assessments.py:list_products_for_assessment`
  (the tenant-facing list) and `engine.py:activate_org_product` (the actual
  gate against direct API activation, not just the UI). Migration
  `0039_publish_existing_products` backfills every existing product to
  `is_published = True` — **every product in the database had
  `is_published = False` before this slice**, so a naive filter would have
  silently emptied every tenant's Tools panel, including any tenant with
  RocketCyber already active. Landed as its own commit, separate from the
  nav/import/document work, so it can be reverted independently — its
  `downgrade()` is a deliberate no-op (blindly reverting would erase real
  admin publish/unpublish decisions made after the backfill, not just the
  migration's own change).
- **Nav**: `AdminArea.tsx`'s section nav is now built on the same
  `SideNavKit.tsx` presentational primitives `SideNav.tsx` uses (extracted
  from `SideNav.tsx`'s own hand-rolled class-name helpers), rather than a
  parallel hardcoded single-button nav. Top bar stays context-only (the
  existing `WinGRC › Administration` breadcrumb). Users (deployment Users
  directory, slice B / `G.11`) is intentionally not present as a disabled
  placeholder — see `SideNav.tsx`'s own Library-category comment for why
  that pattern is a standing mistake, not a precedent to repeat.
- **Access**: router-wide `require_role("msp_admin", "consultant_admin")`,
  matching `routers/integrations.py` exactly. Mirrored in `lib/roles.ts` as
  its **own** constant, `TOOLS_LIBRARY_ROLES` / `canSeeToolsLibrary` — not
  a reuse of `INTEGRATIONS_ROLES`, even though the two sets are identical
  today (see that file's own opening comment on why each axis gets its own
  name). Every import/publish/unpublish/document upload-or-delete event is
  audit-logged (`product.import`, `product.publish`, `product.unpublish`,
  `product_document.upload`, `product_document.delete` — `audit.py`'s
  docstring list was updated to match).

**On `consultant_admin`, asked for explicitly, not resolved:** this is a
materially stronger version of the tension `routers/integrations.py`'s own
docstring already flags. A consultant engaged for one client's assessment
(C3PAO hired to help, not to assess — see `consultant_admin`'s own
definition in the roadmap's Auth/RBAC section) would, with this gate, be
able to import and publish a baseline change that alters compliance
conclusions for *every other client on the deployment*, not just the one
they're engaged on. Integrations config has a comparable shape but a
narrower blast radius (a bad credential fails loudly, a test-connection
call, nothing silently rewrites another tenant's `control_state`). The gate
is unchanged here on purpose — this is Jarrod's call, not a decision to
make silently in code.

### The baseline-versioning hazard (§4 of the slice prompt) — flagged, not fixed

`seed_baselines` (and now this screen's import path, which calls the same
function) **upserts**. Editing `rocketcyber.yaml` and re-seeding —or
re-importing through this screen— retroactively changes the compliance
claims for every tenant that already activated that product: their
`control_state` was set under the old mapping, and the justification for it
is silently replaced under them with no record that anything changed. This
predates this screen; it's the same gap `seed_baselines` always had, just
now reachable at runtime instead of only via a CLI re-seed.

This is the same principle already enforced elsewhere in this codebase —
`sprs_snapshot` is never retroactively rewritten, the audit log is
append-only, bundle exports are point-in-time snapshots — but it is **not**
enforced here, and this slice deliberately does not attempt to fix it.
Real options, none chosen yet:
- **Immutable baseline versions** — each import creates a new versioned
  `Product`/`BaselineControl` set rather than mutating the existing rows;
  `OrgProduct` pins to the version active when it was activated.
  Correct but a real migration: every `BaselineControl` FK in the schema
  (`ControlState.sourced_from_product_id`, evidence task fan-out, etc.)
  would need to reason about "which version," not just "which product."
- **`OrgProduct` pinned to an import timestamp/hash**, mapping resolved
  against a point-in-time snapshot of the baseline rather than the live
  row. Smaller schema footprint, but the "what did version N actually say"
  question still needs somewhere to live — effectively immutable versions
  again, just implicit instead of a first-class table.
- **Do nothing beyond the warning already shipped** (see below) and treat
  baseline edits as a rare, deliberate, MSP-wide operational event — like
  editing the control catalog itself — rather than something the product
  needs to protect tenants from automatically. Cheapest, but leaves the
  silent-rewrite risk exactly where it is today.

What *did* ship: the risk is now visible at the moment it's taken. Dry-run
computes `affected_org_count`/`affected_org_names` — live (`active` or
`candidate`) `OrgProduct` rows for the product's key — and the wizard shows
that warning before Apply is enabled. It does not block the import; it
makes the blast radius legible to whoever is about to cause it.

### Verification status

Backend (`pytest`, `ruff check .`) and static typecheck have not yet run on
a live stack for this slice — see this section's own header. Bench-stack
verification (two orgs, one with RocketCyber active, per the slice's own
§6 checklist) is the next step before landing.

---

## G.10 — Library (Lists, Baselines, Plans, Policies, Procedures)

**Goal:** document storage supporting the assessment. Explicitly **not**
building a version-controlled editor in this pass, per the instruction —
storage + retrieval only.

**Current state:**
- **Lists** — the CMMC list views (`catalog.py`'s `ALL_VIEWS`/`VIEWS_BY_ID`,
  `render_view`, the `GET /orgs/{org_id}/exports/{view_id}` endpoint)
  already exist as a rendering/export mechanism. No frontend surface exists
  for browsing them in-app today (`wingrc render` is CLI-only for producing
  the `.xlsx` output). Library's "Lists" sub-item is mostly a frontend
  wrapper over this existing endpoint.
- **Baselines** (security baseline / policy documents — see the naming-
  collision note at the top of this plan, distinct from the product
  baseline library), **Plans**, **Policies**, **Procedures** — none of
  these have any backend model. This is CLAUDE.md's roadmap item 7,
  "Document library / SSP templates," verbatim: *"Org-level document store
  for policies, procedures, and plans... `Document` model (title, category,
  body_text or storage_key for uploaded files)."*

### Changes

Migration: `document(id, org_id FK, category, title, body_text NULL,
storage_key NULL, created_at, updated_at)` — `category IN ('security_baseline',
'plan', 'policy', 'procedure')`, matching the `CheckConstraint`-per-enum
convention every other categorical column in this schema already uses.
Either `body_text` (in-app authored, plain text/markdown — no rich editor
per the explicit "not this pass" scope) or `storage_key` (uploaded file,
reuses the existing evidence storage pipeline and MIME allowlist) is set,
never both — enforce via `CheckConstraint`, matching `Evidence.kind`'s
existing `file`-vs-`reference` exclusivity pattern.

- Backend: `backend/app/routers/documents.py` (new) — CRUD, org-scoped,
  `require_org_access()`.
- Frontend: `frontend/src/components/LibraryPanel.tsx` (new), four
  sub-tabs (Lists reusing the existing export endpoint; Baselines/Plans/
  Policies/Procedures as `Document` CRUD, one shared list+upload-or-author
  UI parameterized by category).

**Explicitly deferred, matching the instruction:** version history, rich
text/structured editing, template library (pre-built AUP/IR-plan/media-
sanitization-SOP text CLAUDE.md's roadmap item 7 also mentions) — all real,
all later phases, not this plan.

### Exit criteria
`pytest` green for `Document` CRUD, browser smoke test: upload a policy
file, author a plan as body text, confirm both round-trip and appear
correctly categorized in the Library UI; confirm the Lists sub-tab renders
the same export the CLI's `wingrc render` command already produces.

---

## M.7, M.8 — ADR 0009 continuation: cross-org access administration

**Not `G`-numbered.** These are backend prerequisites for `G.11` below, but
they extend ADR 0009's multi-org model (`org_membership`, the M.1–M.6
sequence already landed) rather than being UI-restructure work — recorded
under that ADR's own numbering for the same reason the ADR's "Flagged"
section tracks `M.5`/`M.6` there and not here. `docs/adr/0009-multi-org-
user-access.md` should get a corresponding update landing these, matching
how M.1–M.6 are documented there today.

### M.7 — Deployment-wide user directory read

**Goal:** the pre-org screen needs to show existing users to grant access
to. **No endpoint today lists users outside one org.**
`GET /orgs/{org_id}/users` (`routers/users.py:list_users`) filters by
`User.home_org_id == org_id` — deliberately org-scoped, correct for its
existing purpose (an org's own Users admin panel), wrong shape for "every
user in this deployment, regardless of home org."

**Design:** same SECURITY DEFINER pattern M.2/M.5 already established for
"read across every org in one query" — `auth.msp_role_users()` (M.2) is
almost this already (`SELECT id, role FROM user WHERE role IN
('msp_admin','msp_engineer')`) but doesn't return enough for a picker UI
(email, display_name) and doesn't include non-MSP users, which a grant
target legitimately could be (granting a `customer_poc` from one client
secondary access to a related org, or a `c3pao_assessor` a second
engagement — both explicitly named as real scenarios in ADR 0009's
Decision section). New `auth.all_users_directory()` SECURITY DEFINER
function, EXECUTE restricted to `wingrc_app` (same restriction rationale as
every M.2/M.5 function: this discloses identity information across org
boundaries, not something to leave at Postgres's PUBLIC default).

**Exit criteria:** new endpoint returns every `User` row's
`(id, email, display_name, role, home_org_id)`, `msp_admin`-only
(`require_role`, matching the access-tier this whole feature sits at).

### M.8 — Grant/revoke an existing user's org_membership

**Goal:** the actual mutation — give a user found via M.7 access to a
specific org, or remove it.

**Design:** this is `org_membership.py`'s existing `_grant()`/
`auth.grant_org_membership()` SECURITY DEFINER function (M.2), already
built, already idempotent (`ON CONFLICT DO NOTHING`), already carrying the
"no authorization check of its own, caller is responsible" contract — this
slice is the first *deliberate, admin-initiated* caller of it, as opposed
to M.2's automatic new-org/new-user provisioning. No new migration. New
endpoint only:
- `POST /orgs/{org_id}/memberships` (`{user_id, role}`) — `msp_admin`-only,
  `require_org_access("msp_admin")` (the target org's own admin grants
  access into it — matches every other org-scoped admin action's gate).
- `DELETE /orgs/{org_id}/memberships/{user_id}` — revoke. Needs a self-
  protection check mirroring `deactivate_user`'s existing pattern (an admin
  should not be able to revoke their own last remaining membership and
  lock themselves out) — flagged as a real edge case to design deliberately,
  not fixed here.

**Exit criteria:** grant/revoke round-trip correctly updates
`org_membership`; revoking the caller's own only membership is rejected
with a clear error, tested explicitly (this is the one genuinely new
correctness risk in an otherwise-thin wrapper around existing M.2 machinery).

---

## G.11 — Pre-org screen (MSP admin: grant org access)

**Goal:** the MSP-admin surface for granting existing users access to
organizations, per the request. **Depends on M.7 and M.8.**

**Current state:** no directory/grant/revoke backend or UI exists yet —
M.7/M.8 and this screen's actual content are all still unbuilt. **The host
shell this screen was always meant to land in now exists, though**
(2026-09-11, out of the Integrations-relocation task, not this one):
`App.tsx` gained a deployment-tier `"admin"` screen state — reached from
`OrgPicker` via a header button gated by role, not nested under any org's
side nav — and `frontend/src/components/AdminArea.tsx` is its shell,
holding a small internal section nav (Integrations is the only section
today). The mount-point question this section used to leave open
("likely the natural landing point is `OrgPicker` itself... gaining an
admin-only entry point") is answered: that's exactly what got built.
Whoever picks up M.7/M.8/this screen next adds a new `AdminSection` value
and a new entry in `AdminArea.tsx`'s section nav — the screen itself, its
role gate for wherever `AccessAdminPanel` lands, and the directory/grant/
revoke work below are all still to build.

### Changes
- `frontend/src/components/AccessAdminPanel.tsx` (new) — user directory
  (M.7) with search/filter, org picker, role selector, grant button; a
  per-org membership list with revoke actions (M.8).
- Mount point: **msp_admin-only, deployment-tier**, alongside G.9's baseline-
  import admin screen (both are "MSP staff administering the deployment,"
  not "an org's own settings") — not nested under any single org's side
  nav, since granting access to org B shouldn't require already being
  inside org A's UI. Lands as a new section in `AdminArea.tsx` (see
  "Current state" above), not a separate top-level screen.

### Tests
Browser smoke test: as `msp_admin`, find an existing `customer_poc` from
org A via the directory, grant them access to org B, confirm they can now
open org B (the literal end-to-end proof this whole ADR 0009 sequence has
been building toward since M.4's regression test).

### Exit criteria
`tsc -b` clean, `pytest` green for M.7/M.8, live walkthrough on wl-util-1 —
same verification bar ADR 0009's own M.4 exit criteria set, since this is
the feature that finally makes that fix *usable* by an admin rather than
only self-healing via auto-provisioning.

---

## Summary: what's genuinely new backend work vs. existing

| Nav area | Exists today | New |
|---|---|---|
| Scope → Org Profile, System Description, Personnel & Contacts | Full CRUD + UI | Nav relocation only (G.1) |
| Scope → Assets | Data model slot (`EntityType.DEVICE`/`SOFTWARE`), single-entity upsert function | API write surface, delete path, **entire frontend** (G.5) |
| Scope → Network/Data Flow Diagrams | Evidence pipeline, MIME/hash infra | Two schema columns, SVG sanitization, **entire frontend** (G.6) |
| Scope → Roles (RACI) | Table only | **Entire API surface** (zero routes exist today), **entire frontend** (G.7) |
| Assessments → current assessments | Full | Nav relocation only |
| Assessments → templates | `Framework` catalog model (single-framework only) | Decision needed; framework authoring UI + endpoints if (a) (G.8) |
| Tools → activate/deactivate | Full | None |
| Tools → baseline library management | YAML parsing logic (CLI-only) | Shipped 2026-09-11 (G.9): admin-tier endpoints + UI, `product_document` table, `is_published` enforcement + backfill migration, `product_deployment_footprint()` SECURITY DEFINER function — pending bench-stack verification |
| Library → Lists | View/export logic + endpoint | Frontend wrapper only |
| Library → Baselines/Plans/Policies/Procedures | Nothing | **New `Document` model**, full CRUD, **entire frontend** (G.10) |
| Security → Users, API Tokens, Audit Log | Full | Nav relocation only |
| Org dashboard | Nothing | One new small table (G.2), aggregation endpoint(s), **entire frontend** (G.3/G.4) |
| Pre-org admin (grant access) | `org_membership` model + grant primitive (M.2), deployment-tier host shell (`AdminArea.tsx`, 2026-09-11) | Directory read + grant/revoke endpoints (M.7/M.8), the panel itself (G.11) |
