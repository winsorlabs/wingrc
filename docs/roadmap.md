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
  started. **Correction (2026-09-07):** `G.5` has since shipped — see
  below. `G.6`–`G.11` and `M.7`/`M.8` remain not started.
- **Auth/RBAC completion (items I.1–I.9)** — status verified against code
  2026-09-07, reading `docs/PLAN-auth-rbac-completion.md`'s own status
  header rather than inferring from this file: I.1–I.8 merged/closed, I.9's
  automated checks (pytest, `tsc -b`, `vitest`) are green with one manual
  browser self-service walkthrough still outstanding (see that doc's `I.9`
  section). `auth.py`'s `require_write()` enforces the `c3pao_assessor`
  read-only gate (`_READ_ONLY_ROLES`) on every non-idempotent request,
  applied at router level — confirmed present on `assessments.py`,
  `contacts.py`, `dashboard.py`, `evidence.py`, `scope.py`, and
  `bundle.py`'s `dependencies=[...]`. `orgs.py` carries `require_write()`
  at router level plus `require_org_access()` per-route and
  `require_role("msp_admin", "msp_engineer")` on org creation.
  `frameworks.py` correctly carries only `get_current_user` — it's a single
  GET over the global seeded framework catalog, nothing org-scoped or
  mutating to gate. This closes out the "Role-differentiated RBAC guards"
  entry that used to live in Deferred below, which was stale — see the
  root `ROADMAP.md` item **I** note for the fuller role-mapping writeup.
- **Audit log actor retrofit for core CMMC routers** (`audit.py`, `auth.py`,
  `routers/users.py`, `tests/conftest.py` — 5 commits, 2026-09-07) — closes
  the gap the same reconciliation that found the RBAC-guards entry above
  also surfaced: `bundle.py`/`contacts.py`/`evidence.py`/`orgs.py`/
  `scope.py`, and every `engine.py` assessment-lifecycle function reached
  through them, defaulted `audit_log.actor` to `"system"` even though the
  authenticated identity was available. `log_event()`'s `actor`/
  `actor_type` now default to a ContextVar (`_current_actor`) stamped once
  per request by `auth.py`'s `get_current_user()` — same mechanism as the
  existing `_current_ip`, and for the identical reason: `engine.py`'s
  functions and several router internals have no `CurrentUser` in scope,
  and threading one through every call site would be a far larger change.
  `routers/users.py`/`routers/auth.py` keep passing actor explicitly
  (unchanged) since `current_user` was already a local variable there;
  explicit always wins over the ContextVar default. A real bug surfaced
  building this, not just a design gap: the ContextVar mechanism silently
  didn't work at first — `get_current_user()` was a sync `def`, which
  FastAPI dispatches via anyio's threadpool, and each such dispatch gets
  its own copy of the ambient context, so a `.set()` inside one dispatch
  never reached the endpoint's own, separately-dispatched thread. Every
  new test failed with `actor == "system"` despite the plumbing looking
  correct on paper. `_current_ip` avoids this because it's set in
  `main.py`'s `async` middleware, which runs directly in the request's own
  task before any threadpool dispatch happens. Fixed by making
  `get_current_user()` (and the test suite's `_authed()` override, which
  bypasses it and has to reimplement the same side effect) `async def`
  instead of sync — FastAPI then awaits it directly in the request's task,
  so the mutation lands in the context every later dependency and the
  endpoint itself actually copies from. Verified beyond the test suite: a
  real Bearer-token request against the live wl-util-1 stack's actual
  running uvicorn process (not TestClient), followed by a direct Postgres
  query confirming the resulting `audit_log` row's `actor` matched the
  real user's UUID and `actor_type="api"`. Append-only discipline
  preserved — this is forward-only; no existing `"system"` rows were
  touched or backfilled.
  **Follow-up, measured and fixed 2026-09-09:** making `get_current_user()`
  `async def` was correct for the ContextVar propagation reason above, but
  it had a real side effect worth quantifying, not assuming — every other
  dependency/endpoint in this codebase is sync `def`, threadpool-dispatched
  specifically so blocking I/O doesn't stall the event loop, and
  `get_current_user()` does real blocking Postgres work per request
  (session/API-token resolution, an activity-heartbeat `UPDATE` + `commit`,
  a `User` lookup, an `org_membership` role lookup — 7 round trips on the
  session-cookie path, confirmed by reading `_resolve_session`/
  `_resolve_api_token`, more than the "session resolution, API-token
  resolution, `_role_for_membership`, plus the heartbeat commit" the concern
  was originally framed as). As `async def` with that work inline, all of
  it ran on the event loop instead. Benchmarked against the real running
  stack on wl-util-1 (uvicorn, single worker, real Postgres — not
  TestClient, which can't produce concurrent-request contention; load
  driven by a stdlib `ThreadPoolExecutor` client hitting `GET /orgs` with a
  real Bearer token, since socket I/O releases the GIL, at concurrency
  1/10/50, 100-300 requests per level):
  | concurrency | `async def` (inline) | plain sync `def` (threadpooled) |
  |---|---|---|
  | 1 | p50 5.0ms / p95 5.6ms | p50 5.0ms / p95 5.5ms |
  | 10 | p50 52.0ms / p95 110.0ms | p50 48.2ms / p95 70.8ms |
  | 50 | **event loop starved — even the unauthenticated `/health` check stopped responding; the container's own Docker healthcheck failed; required a manual `docker compose restart` to recover** | p50 210.7ms / p95 455.2ms / p99 537.4ms, zero failures, container stayed healthy |
  At concurrency 10 the two are close — the tiny default SQLAlchemy
  connection pool (`pool_size=5, max_overflow=10` in `db.py`, unmodified by
  this fix) is likely the dominant cost there, not the event-loop-blocking
  itself. At concurrency 50 the difference stopped being a latency question
  and became an availability one: a burst of ~50 concurrent authenticated
  requests — plausible for even a modest number of MSP engineers with a
  dashboard firing several parallel API calls — could take the whole
  WinGRC instance down for every user until someone restarted it. That's
  material, not negligible, so the fix landed: `get_current_user()` now
  splits the blocking resolution into `_get_current_user_sync()` and runs
  it via `run_in_threadpool`, while `set_current_actor()` stays directly in
  the `async def` wrapper's own body (still executed in the request's own
  task, not thread-dispatched) — keeping both properties instead of
  trading one for the other. `_authed()` in `tests/conftest.py` needed no
  change: it's a full dependency override, not a call-through to the real
  function, and it already stamped the ContextVar directly in its own
  `async def` body. Regression-verified: the full audit-actor test suite
  (`test_audit_actor.py`, including the real-Bearer-token-over-TestClient
  case) still passes unchanged. **Confirmed the fix actually closes the
  gap, not just green tests:** re-ran the identical concurrency-50
  benchmark against the fixed build — p50 239.6ms / p95 572.1ms / p99
  636.2ms, zero failures, container stayed healthy throughout. Worse
  latency than concurrency-1 (expected — 50 requests genuinely contending
  for a 15-connection Postgres pool), but the graceful-degradation shape,
  not the event-loop-starvation-and-restart shape.
  **Follow-up, measured and fixed 2026-09-08:** the concurrency-50 numbers
  above still named two throughput constraints that outlived the
  threadpool fix, both fixed this session. (1) `_resolve_api_token`'s
  `last_used_at` `UPDATE` fired on every Bearer-token request, including
  plain GETs — write amplification on every page view. Throttled the
  same way `_resolve_session`'s `last_activity_at` heartbeat already was
  (60s window, including the initial `NULL` case); no idle-timeout logic
  reads this column so there's no accuracy tradeoff, only fewer writes.
  (2) `db.py`'s SQLAlchemy pool was still at SQLAlchemy's own defaults
  (`pool_size=5, max_overflow=10` = 15 total) — a default nobody had
  chosen, sized well under what one uvicorn worker can actually drive.
  Set deliberately to `pool_size=20, max_overflow=20` = 40, matching
  anyio's default 40-thread threadpool cap (nearly every dependency/
  endpoint is dispatched onto it) against Postgres 18's default
  `max_connections=100` and this deployment's single uvicorn worker —
  reasoning recorded on `Settings.db_pool_size`.
  Benchmarked on a throwaway isolated stack (`docker compose -p
  wingrc_bench`, fresh clone, own network/volumes, torn down afterward)
  rather than the shared wl-util-1 instance — the prior round's
  concurrency-50 run had locked that shared backend up and needed a
  manual restart while real traffic was hitting it, and this stack's
  bind-mounted `--reload` made switching between commits mid-session
  cheap. Same methodology as the benchmark above (stdlib
  `ThreadPoolExecutor` client, real uvicorn, real Bearer token, `GET
  /orgs`, concurrency 1/10/50, with a concurrent `/health` watcher —
  zero health-check failures in every run below), isolated per change so
  the improvement can be attributed correctly:
  | concurrency | before (`7708c91`) | heartbeat throttle only | both fixes |
  |---|---|---|---|
  | 1 | p50 4.9–5.2ms / p95 6.7–7.9ms | p50 4.3ms / p95 5.9ms | p50 4.2–4.4ms / p95 5.0–5.3ms |
  | 10 | p50 45.5–49.2ms / p95 63.4–70.2ms | p50 43.7ms / p95 55.3ms | p50 43.6–47.3ms / p95 54.3–89.7ms |
  | 50 | p50 215.0–224.0ms / p95 411.2–464.4ms / p99 600.4–691.2ms | p50 210.2ms / p95 405.8ms / p99 632.8ms | p50 223.2–225.5ms / p95 278.5–356.4ms / p99 310.2–391.5ms |
  (Before/both ranges are 2–3 repeated runs; heartbeat-only is a single
  run — noise band is real, not a transcription gap.) At concurrency 50,
  pool sizing is the dominant contributor: p95 drops roughly 411–464ms →
  279–356ms (~25–35%) and p99 roughly 600–691ms → 310–392ms (~40–50%)
  once the pool widens from 15 to 40, consistent with the pool being the
  bottleneck the original benchmark flagged but didn't yet fix. The
  heartbeat throttle alone moves concurrency-50 p95/p99 only modestly in
  this synthetic benchmark, because a tight loop against one hot token
  hits the 60s throttle-skip after its first request and never re-pays
  the write for the rest of the run — its real payoff is write
  amplification under realistic multi-minute traffic, not raw latency in
  a several-second burst test. p50 stays flat (~210–225ms) across every
  variant at concurrency 50 in every run — expected, since the median
  request isn't queuing on the pool; it's the tail this fixes.
  Regression-verified on the isolated stack: full integration suite
  (`pytest -m integration`, 499 passed, including
  `test_last_used_at_throttled_within_60s` — new this session, mirrors
  `test_session_idle.py`'s throttle coverage — and the existing
  `test_session_idle.py` suite unchanged) and `ruff check .` clean.
- **Consolidated SSP PDF export** (`bundle_service.py:_render_ssp_pdf`,
  merged `e4e307eb`, 2026-09-03) — WeasyPrint rendering over the same
  shared `_sys_desc_body`/`_implementation_body`/`_personnel_body` helpers
  that back the three separate HTML pages, not a `ssp.json` intermediate
  (the design this was originally sketched against — see the "PDF
  rendering" entry that used to live in Deferred below). `weasyprint` is a
  hard dependency in `backend/pyproject.toml`; covered by `test_bundle.py`
  (PDF-validity, artifact-log-entry, and second-order-hash-survives-PDF
  tests). Same commit fixed two real bugs found while shipping it: a TOC
  page-number rendering bug (`target-counter()` was on the `::after` of an
  empty flex-sibling span rather than the link's own `::after` — moved
  onto the link, using `leader(".")` for the dotted rule) and a CI bug
  where the `integration` job set `WINGRC_DATABASE_URL` but `conftest.py`
  only reads `WINGRC_TEST_DATABASE_URL`, so every `@pytest.mark.integration`
  test had been silently skipping in CI and reporting green while testing
  nothing — fixed `afdea252`, 2026-09-03.
- **Scope → Assets screen (G.5)** (`381dd3f5`, 2026-09-01; merged
  `c81346e4`, 2026-09-04) — manual scope-entity CRUD
  (`POST/PATCH/DELETE /orgs/{org_id}/scope`) in `routers/scope.py`
  alongside the existing workbook dry-run/apply flow; frontend
  `AssetsPanel`/`AssetDrawer`/`AssetImportWizard`. Same PR fixed two real
  bugs: an RLS-unsafe `session.commit(); session.refresh(sd)` pattern for
  this table, and `source_ref` provenance not surviving a workbook-import
  round-trip (`6eb0b46e`).
- **Network Diagram / Data Flow Diagram slots** (merged `d2ebea5d`,
  2026-09-04) — two dedicated attachment slots on System Description (not
  generic anonymous evidence), per `docs/pdf_ssp_template_spec.md`'s
  Addendum. SVG sanitized on ingest via an allowlist parser
  (`svg_sanitize.py`, `defusedxml`) that rejects `<!DOCTYPE>`/external
  entities outright rather than attempting to sanitize them; PNG accepted
  as fallback. Embedded in both the SSP PDF and the HTML system-description
  page.
- **Component/Asset Inventory in the SSP bundle** (`1f3f1433`, 2026-09-05,
  through `38054fdf`, 2026-09-06) — new `ssp/04_component_inventory.html`
  page plus a matching PDF section, listing every device/software
  `scope_entity` row for the org (make/OEM, model, version, category,
  status, boundary, responsible party) per the NIST CUI SSP template's
  section 2.1/2.2 (`docs/pdf_ssp_template_spec.md`'s Addendum 2).
  Out-of-boundary and decommissioned assets are flagged via Status/
  Boundary badges, never dropped from the listing. A real PDF page-width
  overflow bug (WeasyPrint's default table layout silently clipped the
  last column of an 8-column table) was found and fixed by visually
  inspecting a real rendered PDF, not caught by any automated test.
- **Canonical `scope_entity.attributes` normalization at the workbook-import
  boundary** (`5f828a11`, 2026-09-06; unresolved-owner warning surfaced
  `4dd2fae2`/`3f3ea243`, 2026-09-06–07) — closes the gap the Component/
  Asset Inventory work above surfaced: the manual Add Asset UI wrote
  `make_oem`/`model`/`version`/`responsible_contact_id`, while the
  workbook importer wrote raw spreadsheet headers (`Make`, `Model`, `OS`,
  `Owner / Primary User`), so spreadsheet-scoped orgs rendered an
  all-"N/A" inventory table.
  `importers/workbook.py:resolve_canonical_device_attributes()` now maps
  the known raw headers onto canonical keys at ingest time, alongside
  (never replacing) the raw headers — `catalog.AUTHORIZED_DEVICES`'s list
  rendering still depends on those raw keys for a faithful round-trip.
  `responsible_contact_id` resolves against real `Contact` rows by exact,
  case/whitespace-insensitive name match only — never a raw spreadsheet
  string written into the UUID slot, and never an auto-created Contact.
  An unresolved owner is surfaced as a warning in the dry-run HTTP response
  (`ScopeChangeOut.warnings`) and in `cli.py seed`'s output, rather than
  silently dropped. Verified against the real seeded org (Acme MSP,
  imported from `samples/authorized-entities.example.xlsx`): re-ran the
  import to backfill its 3 existing device rows; a direct DB query
  confirmed Acme MSP was the only org anywhere on this environment with
  workbook-imported `scope_entity` rows, so no data migration was needed.
  This is the pattern any future connector (root `ROADMAP.md` **D.2**)
  needs to follow rather than becoming a third divergent writer — see that
  item's updated "Hard dependency" note.

- **`app/cli.py::_reset_dev()` fixed against the current schema — found
  2026-08-11, fixed 2026-09-09.** The defect this entry used to document
  (see git history for the original text): `_reset_dev()` never deleted
  `audit_log` rows, and `audit_log.org_id` has no `ON DELETE` action, so
  its final `DELETE FROM organization` raised a foreign-key violation the
  moment any test org had an audit_log row — which any authenticated
  request against the dev DB creates. Confirmed live: this is exactly what
  left the "Device Field Verify Org" / `verify-device-fields@example.com`
  throwaway account stuck on wl-util-1 after a browser verification pass.
  **Fixed in the utility, not the schema** — `audit_log.org_id` still has
  no CASCADE/SET NULL, deliberately: an append-only audit log must never
  silently lose rows as a side effect of deleting the org it references in
  production, and `_reset_dev()` is a dev-only wipe, the correct place to
  delete audit rows explicitly since wiping dev data is its entire
  purpose. Also found and fixed three more FK-ordering gaps while
  reconciling the function against the current schema, none previously
  documented: `system_description`'s pinned network/data-flow diagram
  slots (migration 0029) point at `evidence.id` with no `ON DELETE`
  action, and since `evidence` is wiped unconditionally while
  `system_description` itself is never deleted (only cascades away with
  its org, which never happens for the kept "Acme MSP" org), those
  pointers had to be nulled before the evidence delete;
  `evidence_task_state_link` was never deleted at all, and would
  foreign-key-violate against either `evidence_task` or `control_state`
  the moment a row existed; `poa_m_item` was deleted *after* `finding` despite
  `poa_m_item.finding_id` pointing at it with no cascade, which is
  backwards. `user`/`user_session`/`api_token`/`org_membership` were
  re-verified as still cascading correctly from `organization`'s own `ON
  DELETE CASCADE`, as previously noted. Added a real guard alongside the
  fix: `reset-dev` now refuses to run at all when
  `WINGRC_ENVIRONMENT=production`, checked before the `--yes`-skippable
  confirmation prompt so `--yes` can never bypass it — previously the only
  protection was the docstring's "NEVER run this against a production
  database," which is not a guard. Regression-tested against the exact
  broken case (a test org with audit_log rows, plus the three other gaps)
  and against Acme MSP surviving the reset — `tests/test_cli_reset_dev.py`.

---

## Planned

### N. Document Library

Two new tables (a new migration — 0011 through 0015 are already in use by other shipped features, see Done above). Prerequisite: M (for `approved_by_contact_id` FK). **Verified 2026-09-07: this prerequisite is satisfied** — `Contact` (`models.py:906`) has existed since the Onboarding Wizard shipped (migrations 0011–0013, see Done above), well before this item was written. No remaining data-model blocker for N specifically; it's just unstarted.

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

- **Document-library template content** — paid add-on seed script; depends on document library (N) mechanism being live. **Verified 2026-09-07: N is still fully unstarted** — no `Document`/`document_objective_tag` model, no `routers/documents.py`. (`importers/document.py` is a different, already-shipped feature — AI extraction of a *product baseline* from a vendor CRM/PDF, not the tenant-facing template library N describes. Don't confuse the two on a future pass.)
- **Personnel connector** — Liongard / M365 → auto-populate contacts; depends on M. **Note (2026-09-07):** "M" here isn't fully traceable — root `ROADMAP.md` has no item M; the only "M" in this repo is the Multi-org access work (`M.1`–`M.8`) tracked in this file's own Done section, whose core (`M.1`–`M.6`) is done and whose remainder (`M.7`/`M.8`, a deployment-wide user directory + admin grant/revoke UI) doesn't obviously relate to auto-populating contacts. Left as originally written rather than guessed at; re-derive the actual dependency before resuming this item.
- **AI implementation statements** — generation worker behind BYO-AI provider abstraction; scaffolding exists. **Verified 2026-09-07, more specifically than before:** `config.py`'s `ai_provider` setting and `backend/app/ai/` are real and already load-bearing — `importers/document.py` (the vendor-CRM/baseline extractor) is a working consumer of that same abstraction today. What's still missing is the per-objective draft-statement path itself: no `draft-statement` endpoint exists on `assessments.py`, and `ImplementationStatement` rows are still authored by hand. The provider plumbing this item needs already exists; the feature-specific generation logic does not.
- **CRM (Customer Responsibility Matrix)** — render from `raci_assignment` + `contact`; depends on M (see the same "M" ambiguity note under Personnel connector above). **Verified 2026-09-07: no CRM-rendering code exists** — "CRM" elsewhere in this codebase (`models.py`, `importers/document.py`, `routers/contacts.py`) refers to the generic *documentation-role* concept ("who appears in a CRM/SSP document"), not this specific render-from-RACI feature. Still genuinely unstarted.
- **Scope connector** — Liongard / Datto RMM → `scope_entity`; supplements manual CSV import. Specified in root `ROADMAP.md` **D.2** (route through the existing dry-run/apply review flow, not a direct connector→DB write). **Updated 2026-09-07:** the canonical-attribute-key blocker this item used to cite is resolved — see the "Canonical `scope_entity.attributes` normalization" Done entry above. No connector code exists yet (verified: no Liongard/Datto files under `backend/app`, `Source.LIONGARD`/`Source.DATTO_RMM` exist only as unused enum values in `domain.py`); D.2 needs to follow the pattern `importers/workbook.py` already established, not invent a fourth attribute schema.
- **Integrations screen** — new side-nav section for setting up connectors (Liongard first), specified in root `ROADMAP.md` **D.1**. Added 2026-09-06 (Jarrod). Carries an unsettled architectural question: item D says the platform never holds third-party API keys, which conflicts with an in-app credential-entry screen — reconcile before building. Still fully unstarted as of 2026-09-07 (no frontend Integrations route, no per-connector credential model).
- **Asset & user onboarding approval workflow** — daily Liongard sync; new devices/users land pending, notify the org's `security_officer` and `it_admin` contacts, approval page shows a baseline checklist (DUO/Evo, FenixPyre, RoboShadow, RocketCyber…) evaluated from Liongard metrics, Security Officer + IT formally accept the asset into the environment. Specified in root `ROADMAP.md` **D.3**. Added 2026-09-08 (Jarrod). Depends on D.1 + D.2 and on **two things that don't exist in this codebase yet**: outbound email (verified 2026-09-08 — no `smtplib`, SMTP config, or mailer module anywhere under `backend/`) and any job scheduler for the daily run. Also needs a `pending_approval` state on `domain.py:EntityStatus` (today only `active`/`decommissioned`). Hard constraint recorded in D.3: email notifies, but approval requires an authenticated session — no one-click approve links in email.
- **Evidence download hardening** — replace presigned direct-to-MinIO download URLs with the backend streaming evidence bytes itself. Presigned URLs are bearer-token style: anyone with the link can download until it expires, with no per-request re-check of session/auth state. Worth revisiting given the investment already made in session/MFA/lockout hardening (item I, now shipped — see Done) — that hardening doesn't currently extend to the download path. Surfaced while proxying MinIO behind nginx for item O. **Verified 2026-09-07: still open** — `storage.py` still defines `presigned_url()` on every storage backend, and `routers/evidence.py` still calls it at 4 call sites (`download_url=storage.presigned_url(...)` for both single-evidence and task-collection responses). Nothing streams bytes through the backend yet.
- **Frontend build determinism** — generate and commit `frontend/package-lock.json` (none is committed — one has been observed untracked on wl-util-1 from a local `npm install`, but that's not what this item is about), then switch `deploy/nginx/Dockerfile` from `npm install` to `npm ci` for reproducible builds. Low priority, not blocking anything currently in flight. **Verified 2026-09-07: still open** — `git ls-files frontend/package-lock.json` returns nothing (not committed), `deploy/nginx/Dockerfile` still runs `npm install`, not `npm ci`.
