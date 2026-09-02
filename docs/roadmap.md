# WinGRC Roadmap

Planned features in build sequence. Items marked **Done** are shipped and tested.
Items without a status are planned but not yet started.

---

## Done

- **Scope module** — `scope_entity` graph; CSV/spreadsheet import; authorized-user and device lists as views. AC.L2-3.1.1 authorized-entities slice end-to-end.
- **Assessment engine** — control catalog (800-171A objectives + SPRS weights), product baseline library, `control_state`, SPRS scoring.
- **Magic loop** — activate product → covered objectives → `pending_evidence`; evidence tasks seeded; SPRS recomputed.
- **Evidence** — file upload (MinIO), URL/path references, `evidence_state_link` (one artifact → many objectives), evidence manifest endpoint.
- **Implementation statements** — per-objective SSP narrative; draft/reviewed/approved status; AI-generation scaffolding.
- **Contacts + RACI** — `contact` table; `raci_assignment` per-objective; affiliation (msp/customer/mssp/government/other).
- **Deactivation + audit log** — provenance-based deactivation (all tool-sourced states → `needs_review`; evidence archived); reactivation restores archived evidence; append-only `audit_log`.
- **Reactivation** — re-activating a product restores archived evidence and sets `needs_review` (not `pending_evidence`) so MSP must re-confirm coverage is current.
- **`needs_review` status** — amber state for tool-sourced controls awaiting re-confirmation; deducts from SPRS like other non-met statuses.
- **Evidence tasks panel** — frontend task list grouped by collection session; status patch endpoint; archived tasks hidden with toggle.
- **Findings + POA&M models** — `finding` and `poa_m_item` tables; gap/deficiency/weakness/observation types; severity; remediation milestones.
- **Assessor Bundle Export** — downloadable ZIP (SSP + evidence + scores + status) for C3PAO handoff; `backend/app/bundle_service.py` assembly, `GET /orgs/{org_id}/assessments/{assessment_id}/bundle`, "Generate Assessor Bundle" button on the board. Verified against a real downloaded zip. **Amended 2026-08-06** (out-of-band, not a new roadmap slice): evidence folder in the export restructured to `evidence/<family>/<control>/<objective>/` so an assessor can navigate to one objective's evidence directly — see `docs/adr/0007-per-objective-evidence-folders-in-bundle-export.md`.
- **Onboarding Wizard v1** — Organization Profile (SSP header fields: CAGE/UEI/address/phone/logo), System Description (system type, CUI categories/storage/boundary/flow narrative), and Personnel Repository (contacts + documentation-role assignment) — migrations 0011/0012/0013; `GET/PATCH /orgs/{org_id}/profile`, `POST /orgs/{org_id}/logo`, `GET/PUT /orgs/{org_id}/system-description`, contacts CRUD + role endpoints (`contacts.py`). 3-step wizard on org creation, plus a persistent tabbed Settings page for later edits.
- **Authentication** — session-based login (opaque tokens, HttpOnly+Secure cookie), local password (PBKDF2-HMAC-SHA256, FIPS-140 rationale) + TOTP MFA + backup codes, Microsoft Entra ID SSO, API tokens for machine access — migration 0015. Four roles shipped (`msp_admin`/`msp_engineer`/`customer_poc`/`c3pao_assessor`); see Deferred for role-guard coverage. **Known defect (2026-08-07), fixed 2026-08-11–13, verified 2026-08-17:** `require_org_access()`'s single-org gate meant MSP staff couldn't open any org but their own — full writeup moved to Done below (multi-org access entry), closed out of Known defects.
- **Multi-org access (ADR 0009 M.1–M.6)** — many-to-many `org_membership`
  replacing the old single-org gate. Fixed the defect above: an `msp_admin`
  could previously list every org (`GET /orgs`) and create new ones
  (`POST /orgs`), but got 403 on everything else for any org beyond their
  own — including one they'd just created. Traced end to end at the time:
  `create_org()` produced an ownerless org, `invite_user()` required the
  caller to already belong to the target org as `msp_admin` (circular for a
  brand-new org), and `OnboardingWizard`'s very first API call 403'd. The
  only working onboarding path was `manage.py`'s one-time bootstrap CLI, not
  a real per-customer flow. Full model, migration path, and slice plan:
  `docs/adr/0009-multi-org-user-access.md`. **Verified 2026-08-18 on
  wl-util-1, live, this run:** full pytest suite (396/396 integration,
  530/530 total), `npx tsc -b` clean, `vitest run` **29/29** (2 files:
  `permissions.test.ts` 16, `filters.test.ts` 13), plus two browser smoke
  tests — (1) as `msp_admin`, created a second org, completed
  `OnboardingWizard` end to end, confirmed data stayed scoped between the
  two orgs; (2) logged in as both `c3pao_assessor` and `customer_poc`,
  confirmed each lands directly on their own org's assessment view with no
  picker and no create-org form visible. (An earlier "Verified 2026-08-17"
  claim written into this file by commit `f27f06f` — before this actual
  wl-util-1 run, and before `G.1` even existed — cited `vitest run` 25/25;
  that number didn't match this real run's 29/29. Replaced with the
  numbers from this live run; flagging so a future reader doesn't take a
  "Verified" note at face value without checking it was written after,
  not before, the run it claims.)
- **G.1 — persistent side nav shell** (`docs/PLAN-gui-restructure.md`,
  commit `e481a00`) — replaced `App.tsx`'s flat screen machine with a
  nav-category state shape; `OrgSettings.tsx` deleted, its tab content
  reused under the new shell. **Verified 2026-08-18 on wl-util-1, live:**
  `npx tsc -b` clean, browser smoke test confirmed Org Profile, System
  Description, Personnel & Contacts, Users, API Tokens, and Audit Log all
  still work under the persistent nav, `AccountSettings` renders correctly
  styled (the regression caught in review), and activating/deactivating a
  product from Tools auto-returns to Assessments correctly. `M.7`/`M.8`
  (deployment-wide user directory + admin-initiated grant/revoke,
  prerequisites for `G.11`'s pre-org admin screen) and `G.3`–`G.11` remain
  not started.
- **G.2 — SPRS score snapshot table** (`docs/PLAN-gui-restructure.md`,
  commits `cca09de`, `e63e80f`) — `sprs_snapshot` table, one row inserted
  every time `engine.py:recompute_sprs` writes `assessment.sprs_score`
  (the single write path — five call sites, all funneling through this
  one function: `start_assessment`, `activate_org_product`,
  `deactivate_org_product`, `bundle_service.snapshot_bundle`, and
  `routers/assessments.py:patch_control_state`; the last was undercounted
  in this entry's first version — see `docs/PLAN-gui-restructure.md`'s
  G.2 section for two 2026-08-19 corrections and the real bugs they
  surfaced: a concurrent-recompute lost-update race (fixed `2c00b9b`,
  `SELECT ... FOR UPDATE`), and — found because the race fix alone didn't
  close the reported symptom — a deterministic bug where `recompute_sprs`
  never saw its own caller's pending changes under production's
  `autoflush=False` session setting (fixed same day, `recompute_sprs`
  now flushes unconditionally first). `org_id` + RLS and a `seq` ordering
  column added beyond the plan's literal column list; see that section's
  own note for why.
  **Verified 2026-08-18 on wl-util-1, live — predates both fixes above,
  read together with G.2's own section, not alone:** `alembic upgrade
  head` applied `0028_sprs_snapshot` cleanly; `pytest tests/test_sprs_snapshot.py`
  3/3; full backend suite 533/533 (399/399 integration); existing
  `compute_sprs`/`recompute_sprs` coverage in `test_assessment_engine.py`
  unmodified and still green. **Both fixes above independently verified
  live on wl-util-1, 2026-08-19:** the lock fix (`2c00b9b`) via
  `test_recompute_sprs_locks_the_assessment_row`, a genuine two-connection
  `SELECT ... FOR UPDATE NOWAIT` test, not a same-transaction fake; the
  autoflush fix (`cbe3e57`) via
  `test_patch_control_state_score_reflects_its_own_edit_under_autoflush_false`.
  Full suite at that point: 545/545 (411/411 integration). Browser
  confirmed the Dashboard's SPRS Score finally matches the assessment
  screen's after deliberately overlapping a control-state edit with a
  product activate/deactivate.
- **G.3 — org dashboard** (`docs/PLAN-gui-restructure.md`, commit
  `33eeb32`) — new `GET /orgs/{org_id}/assessments/{assessment_id}/dashboard`
  endpoint serving 8 of 9 widgets in one payload (Recent Activity stays a
  separate call to the existing, unmodified, `msp_admin`-gated
  `/audit-log` endpoint, deliberately not folded in — see plan doc for
  why); new `OrgDashboard.tsx`, mounted as a new top-level "Dashboard"
  SideNav category. This slice's own smoke test is what surfaced both
  SPRS bugs recorded in the G.2 entry above — the dashboard gave the
  stored score a second, always-fresh comparison point for the first
  time. **Verified live on wl-util-1, 2026-08-19:** `pytest
  tests/test_dashboard.py` 9/9; full suite 545/545 (411/411 integration);
  `npx tsc -b` clean; `vitest run` 37/37; browser smoke test against a
  real org with non-trivial data confirmed all nine widgets, including
  the blocked-objectives anti-join and the RACI "Unassigned" bucket
  fallback (G.7 doesn't exist yet).
- **Family radar chart** (small follow-on to G.3, same 2026-08-19 batch,
  not in the original plan) — per-family completion % for all 14 CMMC
  domains plotted as radar spokes on the dashboard, alongside (not
  replacing) the existing Family Completion bar list. Hand-rolled SVG, no
  new frontend dependency added. New `lib/radarChart.ts` (pure, testable
  coordinate mapping) and `lib/families.ts` (single shared source of
  truth for the 14-family order, previously duplicated as a local const
  in `AssessmentBoard.tsx`). **Verified live on wl-util-1, 2026-08-19:**
  `radarChart.test.ts` 8/8 (part of the 37/37 `vitest` total above);
  browser smoke test confirmed all 14 spokes render correctly against
  real data.
- **G.4 — default-to-most-recent-assessment + switcher**
  (`docs/PLAN-gui-restructure.md`) — `GET /orgs/{org_id}/assessments`
  gains a derived `last_activity_at` (no new column — `MAX()` over
  `control_state`/`implementation_statement` timestamps at read time);
  `OrgDashboard.tsx` gets an assessment switcher, defaulting to the most
  recently active assessment only when nothing was already selected,
  never overriding an explicit choice. First component-level tests in
  this codebase (`jsdom` + `@testing-library/react`, scoped to the new
  test files only). **Verified live on wl-util-1, 2026-08-20:** full
  backend suite 549/549, `test_assessments_list.py` 4/4, `npx tsc -b`
  clean, `vitest run` 44/44 (5 files). The frontend test's first live run
  showed 3/4 `OrgDashboard.test.tsx` cases failing on missing test
  isolation (stale mocks/DOM leaking across tests in the same file, not a
  component bug — a static trace of the component held up once isolation
  was fixed); reran green after adding `afterEach` cleanup.
- **OrgPicker auto-resume fix** (found during G.4's browser verification,
  pre-existing since `7240bbf` — months before this GUI restructure plan
  and not caused by G.1, confirmed by diff) — the cached-assessment
  auto-resume in `OrgPicker` was bouncing every return trip to the picker
  straight back into the board, making "Start New Assessment"
  unreachable for any org with an existing assessment. Fixed via a
  `skipAutoResume` prop gating the auto-jump; fresh-login fast-resume
  preserved. Also fixed in the same commit: the multi-org branch's
  "Start New Assessment" button was missing the `canWrite` gate the
  single-org branch already had (UI-only inconsistency — the backend's
  `require_write()` already blocked the actual create for read-only
  roles). New `OrgPicker.test.tsx` (3 tests, part of the 44/44 above).
  **Verified live on wl-util-1, 2026-08-20:** browser walkthrough
  confirmed fresh-login fast-resume still works, explicit navigate-back
  now shows the real picker, and a second assessment could be created to
  exercise G.4's switcher. `G.5`–`G.11` and `M.7`/`M.8` remain not
  started.

---

## Known defects

- **`app/cli.py::_reset_dev()` would fail against the current schema —
  found 2026-08-11 while investigating a one-off wl-util-1 cleanup script,
  not fixed here.** `_reset_dev()` deletes all non-"Acme MSP" organization
  rows and their assessment-layer data in FK-safe tiers, but was written
  before the auth/audit layer (migrations 0010, 0015+) existed and was
  never updated for it. It never deletes `audit_log` rows, and
  `audit_log.org_id` has no `ON DELETE` action (no CASCADE, no SET NULL) —
  so its final `DELETE FROM organization` would raise a foreign-key
  violation the moment any test org has an audit_log row referencing it,
  which any integration test exercising an authenticated endpoint against
  the dev DB will have created. It also never explicitly handles `user`/
  `user_session`/`api_token`/`org_membership` (these do cascade correctly
  from `organization`'s own `ON DELETE CASCADE`, so that part is
  incidentally fine, just unexplained by the function's own tiered
  comments). Not fixed as part of this entry — out of scope for the
  one-off cleanup that surfaced it — but recorded so it doesn't quietly
  stay broken until someone runs `reset-dev` on a dev box with real audit
  history and gets a confusing FK error.

---

## Planned

### N. Document Library

Two new tables (a new migration — 0011 through 0015 are already in use by other shipped features, see Done above). Prerequisite: M (for `approved_by_contact_id` FK).

**Monetization boundary:** The matching engine, tagging, and publish/approve flow are core (free, open-source). The curated template content (polished ready-to-use policies) is a separately distributed seed script — not in this repo. `is_template_derived` and `template_ref` columns mark template-derived rows; no code-level paywall.

**Table `document`:** `org_id`, `doc_id` (stable human-readable ID: "AC-POL-001"; UNIQUE per org, MSP-assigned), `title`, `doc_type` (policy/procedure/plan/list/sop/form/other), `status` (draft/under_review/approved/superseded), `version`, `body` (Text; markdown/plain-text), `storage_key` (nullable; for uploaded-file documents), `is_template_derived` (bool), `template_ref` (nullable), `approved_at`, `approved_by_contact_id`.

**Table `document_objective_tag`:** `(document_id, objective_id)` UNIQUE. Tags a document to any number of framework objectives it satisfies.

**Publish action** — `POST /orgs/{org_id}/documents/{doc_id}/publish`:
1. Sets `document.status → approved`, `approved_at → now`.
2. Creates one `Evidence` record (`kind='reference'`, location = document's stable `doc_id`).
3. For each tagged objective: finds the active assessment's `control_state` → creates `EvidenceStateLink`. `control_state.status` is NOT changed — evidence is attached; engineer must review and manually mark objectives met. Same "candidates, never auto-met" discipline as tool activation.

**API:** `GET/POST /orgs/{org_id}/documents`, `GET/PATCH /orgs/{org_id}/documents/{doc_id}`, `POST/DELETE /orgs/{org_id}/documents/{doc_id}/objective-tags`, `POST /orgs/{org_id}/documents/{doc_id}/publish`.

---

### O. Public Documentation / Knowledge Base Site (docs.wingrc.us)

Independent initiative — does not block or get blocked by other roadmap items; schedule wherever makes sense.

**Tooling:** Docusaurus (React-based static site generator). Chosen for stack alignment with the existing frontend, built-in versioned-docs support, and MDX support for embedding interactive components later.

**Approach:** docs-as-code — markdown/MDX content lives in git, changes go through normal PR review, same discipline as the rest of the project.

**Repository — decided 2026-08-17 (Jarrod): separate repo**, `wingrc-docs`, rather than folding into the main app repo, so the docs deploy pipeline and contribution surface stay decoupled from the app's own CI/CD.

**Hosting:** static output on Cloudflare Pages, Netlify, or GitHub Pages — any of these give free automatic HTTPS for the docs domain itself. This is separate infrastructure from the app's own nginx/Certbot setup, which is for deployed WinGRC instances, not this docs site.

**Visual direction:** aim for the clean, minimal, sidebar-nav look of docs.fenixpyre.com. Docusaurus's default theme will need custom CSS to get there — budget this as real work, not a quick tweak.

**First planned content, in order:**
1. HTTPS/Certbot + DNSimple DNS-01 deployment runbook — write once the current HTTPS work on wl-util-1 is complete and validated. Document the real, verified process, not in advance.
2. Azure App Registration / M365 SSO setup how-to — write once SSO is implemented and validated end-to-end, not before.

Item 1's source content is written and validated: `docs/wl-util-1-worked-example-deployment.md` is the real, worked hardening/HTTPS session this item calls for. The hosting-cost/GovCloud-necessity research referenced above under **Hosting** is also written and validated: `docs/cloud-hosting-options.md`. Both are ready to seed their respective docs.wingrc.us pages whenever the Docusaurus build happens — the site itself is still unbuilt; only the source content for these two planned pages exists so far.

---

## Sequencing

```
Document library (N)
    → Personnel connector pull (Liongard / M365 → contacts)
```

---

## Deferred

- **PDF rendering** — Jinja2 + WeasyPrint over `ssp.json`. Bundle export (J) has shipped and is stable, so this dependency is satisfied; not scheduled yet regardless.
- **Document-library template content** — paid add-on seed script; depends on document library (N) mechanism being live.
- **Personnel connector** — Liongard / M365 → auto-populate contacts; depends on M.
- **AI implementation statements** — generation worker behind BYO-AI provider abstraction; scaffolding exists.
- **CRM (Customer Responsibility Matrix)** — render from `raci_assignment` + `contact`; depends on M.
- **Scope connector** — Liongard / Datto RMM → `scope_entity`; supplements manual CSV import.
- **Role-differentiated RBAC guards** — authentication shipped (see Done, migration 0015); the `require_role` guard mechanism exists and is applied to user-management endpoints (`users.py`) but not yet to the core CMMC data-surface routers (assessments, evidence, contacts, orgs, bundle, frameworks) — any authenticated user of any role currently has equal access there. The three roles named in root `ROADMAP.md` item I (MSP User / Org User / Assessor) don't map 1:1 to the four that shipped; in particular there's no enforced read-only guard for `c3pao_assessor`.
- **Evidence download hardening** — replace presigned direct-to-MinIO download URLs with the backend streaming evidence bytes itself. Presigned URLs are bearer-token style: anyone with the link can download until it expires, with no per-request re-check of session/auth state. Worth revisiting given the investment already made in session/MFA/lockout hardening (item I) — that hardening doesn't currently extend to the download path. Surfaced while proxying MinIO behind nginx for item O.
- **Frontend build determinism** — generate and commit `frontend/package-lock.json` (none exists yet), then switch `deploy/nginx/Dockerfile` from `npm install` to `npm ci` for reproducible builds. Low priority, not blocking anything currently in flight.
