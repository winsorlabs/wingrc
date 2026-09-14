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
- **Authentication** — session-based login (opaque tokens, HttpOnly+Secure cookie), local password (PBKDF2-HMAC-SHA256, FIPS-140 rationale) + TOTP MFA + backup codes, Microsoft Entra ID SSO, API tokens for machine access — migration 0015. Four roles shipped (`msp_admin`/`msp_engineer`/`customer_poc`/`c3pao_assessor`); see Deferred for role-guard coverage. **Known defect (2026-08-07), fixed 2026-08-11–13, verified 2026-08-17:** `require_org_access()`'s single-org gate meant MSP staff couldn't open any org but their own — full writeup moved to Done below (multi-org access entry), closed out of Known defects. **Five roles as of 2026-09-11** — `consultant_admin` added (migration 0034); see that date's Done entry below, not a correction to this one.
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
- **CRM (Customer Responsibility Matrix)** (2026-09-09, G.7 Part 3) —
  render from `raci_assignment` + `contact`, closing out the Deferred
  entry that used to sit below (verified 2026-09-07 as genuinely
  unstarted; the roadmap's own "M" dependency note for it was stale —
  RACI existing at all, not the multi-org "M" work, was the actual
  blocker, and G.7 already removed it). New `CrmRowSnap` dataclass in
  `bundle_service.py`, populated in `snapshot_bundle()` by reshaping the
  already-built `controls`/`objectives` tree (no new query — the same
  `ObjectiveSnap.raci` rows `_implementation_body` already renders
  inline), a new `_crm_body`/`_render_crm` pair following the exact
  established pattern (new snapshot field, HTML page, artifact_log entry,
  ZIP write, `_render_index` entry, consolidated-PDF section via the
  shared body-helper convention). Ships as
  `ssp/05_customer_responsibility_matrix.html` plus a PDF section;
  control/objective down the side, R/A/C/I across, each cell naming the
  assignee and their MSP-vs-customer affiliation. In-app matrix view:
  evaluated `RolesPanel.tsx` (already lists every objective's assignments)
  rather than building a second view — the one gap (affiliation wasn't
  shown in the read-only chip display, only in the picker) closed by
  adding `contact_affiliation` to `RaciAssignmentOut`/`RaciAssignmentRow`
  instead of a new component. Covered by `test_bundle.py`'s CRM section
  (contains-assignment, unassigned-shows-dash, empty-assessment message,
  artifact-log entry, second-order-hash-survives, PDF TOC size bump).
- **Assignment-load visualization** (2026-09-09, G.7 Part 4) — two new org
  dashboard widgets answering "how much is the MSP taking on." Two design
  decisions made and recorded rather than shipped silently:
  - **What counts as "load": R (Responsible) assignments only**, not a
    raw RACI-letter count and not R+A. An `I` (Informed) or `C`
    (Consulted) assignee isn't doing the work an `R` is, so counting them
    equally would misrepresent effort; `A` (Accountable) is sign-off, a
    different kind of load again, with no basis yet to weight it against
    `R`. New `RaciLoadWidget` (`routers/dashboard.py`) — `msp_count`/
    `customer_count`/`other_count` plus `by_contact` (pre-sorted desc,
    same "server does the rollup" convention every other dashboard widget
    already follows) — filters to `raci_letter == 'R'` throughout.
  - **Where it lives: the org dashboard**, not `RolesPanel.tsx`/CRM.
    Chosen over "both": the dashboard already has the "roll-up
    visualization" widget pattern (G.3) these charts fit naturally into,
    while `RolesPanel` is a working/editing surface where a chart would
    compete with the bulk-assign UI rather than add to it.
  - Two widgets, per the task's own form guidance: **MSP vs Customer
    Load** is a donut (2-3 slices, one headline number) via hand-rolled
    SVG (`lib/raciLoad.ts:donutSlices`, unit-tested — same
    no-charting-library precedent `lib/radarChart.ts` set, C.2). **Per-
    contact load** is horizontal bars — asked and confirmed before
    building (a pie with a dozen contacts can't be ranked by eye) —
    reusing `FamilyHeatmapCard`'s existing `.tier-bar` markup/CSS rather
    than inventing a second bar-chart implementation, with only the label
    column widened for a contact's full name instead of a 2-letter family
    code.
  - `docs/PLAN-gui-restructure.md`'s G.7 section carries the short
    pointer to this entry rather than duplicating it.
- **Integrations screen — credential entry + test-connection** (2026-09-09,
  root `ROADMAP.md` **D.1**). New top-level Integrations nav category
  (msp_admin only), Liongard first. Scope was deliberately narrow: the
  screen, credential storage, and test-connection — the actual device/user
  pull is **D.2**, the approval workflow is **D.3**, neither built here.
  - **Resolved D.1's open architectural question** (see that section's own
    "reconcile before building" note): item D's "the platform never holds
    third-party API keys" was written imagining a hosted multi-tenant
    WinGRC where the vendor differs from the customer. WinGRC is
    self-hosted — the MSP runs its own Postgres/MinIO/containers — so "the
    platform" is the MSP's own infrastructure, and an encrypted Liongard
    key there is the MSP holding its own credential, not WinGRC-the-vendor
    holding a customer's. Root `ROADMAP.md`'s item D and D.1 sections are
    reworded to the actual constraint this satisfies:
    **WinGRC-the-vendor never sees customer credentials.** See **D.4**
    (new, not started) for why this answer doesn't carry over unchanged to
    a future hosted WinGRC.
  - **Resolved D.1's other open question** (org-scoped vs. MSP-wide
    connections) by reading Liongard's own docs first, before designing
    anything: Access Key ID/Secret are generated per Liongard *user
    account*, scoped to the whole MSP instance
    (`https://{instance}.app.liongard.com`) — not per client. Environments
    (per-client tenants) live underneath that one account. So
    `integration_connection` (`models.py`) is deployment-wide, one row per
    connector *type*, matching `product`/`framework`'s existing
    non-org-scoped tier — not one row per org. Mapping a WinGRC org to a
    Liongard Environment id is D.2's concern, a separate org-scoped table,
    not a column here.
  - **Credential storage** (`backend/app/crypto.py`): Fernet
    (`cryptography`, already a transitive dependency via `msal` — now also
    a direct one), key from `WINGRC_CREDENTIAL_ENCRYPTION_KEYS`
    (deploy-time config, never persisted in the DB), fail-closed — a
    missing/malformed key refuses to store or read a credential rather
    than falling back to plaintext, same posture as the reset-dev
    production guard. Key-version-labeled ciphertext
    (`credential_key_version`) so rotation (prepend a new primary label,
    keep old ones for decrypt via `MultiFernet`) is a config change, not a
    data migration. The credential is write-only over the API — `PUT
    /integrations/{key}/credential` accepts it, nothing ever returns it;
    `IntegrationOut` carries at most a 4-char `credential_hint`.
  - **RBAC**: `routers/integrations.py` is `require_role("msp_admin")`
    router-wide — even viewing connector status, since there's no org_id
    to scope by and this is deployment-level config, not tenant data.
  - Test-connection calls Liongard's own documented key-validation
    endpoint (`GET /api/v1/environments/count/`), surfaces the real error
    (401/403 distinguished from a network failure) rather than a generic
    failure message, and never echoes the credential in the response or
    the audit log (`integration_connection.credential_set/.credential_delete/.test`
    log connector_key + masked hint + ok/fail only).
  - Connector abstraction (`backend/app/connectors/`) is a small registry
    keyed by `connector_key`, so a second connector (Datto RMM next, per
    item D's priority order) is a new module + one registry entry — not a
    rework of the router or the screen.
  - **Amended 2026-09-11:** the frontend placement described above ("New
    top-level Integrations nav category" under the per-org side nav) was
    wrong from the start and has been corrected — see the Done entry
    below ("Move Integrations to deployment-tier Administration"). The
    backend facts in this entry (deployment-wide `integration_connection`,
    RBAC) were always correct and are unchanged; only where the frontend
    mounted the screen was the bug.
- **Objective guidance: official + practitioner notes, split** (2026-09-09,
  substantially delivers root `ROADMAP.md` **item E**, "Objective tips").
  Fixes the sparsity bug where `AssessmentObjective.guidance` was populated
  for only 111/316 objectives (so "Show guidance" appeared on some
  objectives and not others) by replacing it with two properly-sourced,
  never-blended fields — CLAUDE.md's compliance-content discipline applied
  to guidance text itself, not just control state.
  - **`official_guidance` / `official_guidance_source`** — government-
    sourced, mechanically extracted from the real CMMC Assessment Guide
    Level 2 PDF (Version 2.13, September 2024), not paraphrased or
    recalled from memory (same standard as item B's POA&M rule). Pipeline:
    `scripts/cmmc_guidance/extract_pdf.py` (pypdf text extraction +
    structural parsing, asserts exactly 110 practice headers found) →
    `compose_guidance_yaml.py` (mechanical composition into
    `backend/app/seeds/cmmc_official_guidance.yaml`). Checked the guide's
    actual grain rather than assuming one: "Potential Assessment Methods
    and Objects" (Examine/Interview/Test) is practice-level (110/110
    practices), while "Potential Assessment Considerations" bullets ARE
    tagged per objective letter in the source (109/110 practices, 232
    bullets, 240/316 objectives with genuinely objective-specific text) —
    the composed text uses the objective-specific bullet when one exists
    and falls back to the practice-wide methods, explicitly labeled as
    such, when it doesn't. Every one of the 316 objectives ends up with
    non-empty official guidance, closing the original sparsity bug
    completely. A dictionary-based sweep (pyspellchecker) caught and fixed
    18 PDF-kerning text artifacts (e.g. "L ayer" → "Layer") with zero false
    positives — see `scripts/cmmc_guidance/README.md` for the full
    verification writeup, including PDF SHA-256 and spot-checks against
    the source document.
  - **Found and flagged 2026-09-09, fixed 2026-09-10** (see that date's
    Done entry below for the full writeup): cross-checking objective-key
    sets between `cmmc_l2.yaml` and the real PDF surfaced 4 practices
    (`AC.L2-3.1.22`, `IA.L2-3.5.8`, `RA.L2-3.11.1`, `SC.L2-3.13.8`) each
    missing one real NIST SP 800-171A objective letter from our catalog
    (316 objectives seeded vs. 320 that actually exist).
  - **`practitioner_notes` / `practitioner_notes_is_draft` /
    `_generated_at` / `_model`** — **superseded 2026-09-10, see that date's
    "Practitioner notes: edit model revision" entry below; `_is_draft` no
    longer exists.** AI-drafted (Claude Sonnet 5, authored
    2026-09-09), advisory only, all 316/316 objectives covered
    (`backend/app/seeds/cmmc_practitioner_notes.yaml`). Framing rules
    enforced while writing and self-audited afterward (grepped for
    verdict/vendor language before committing): never a verdict ("if you
    have X you meet this" is never written — matches "candidates, never
    auto-met"), no vendor product names (capability categories only),
    common pitfalls rather than how-to instructions. Mirrors
    `AssessmentObjective.is_draft`'s existing draft-until-reviewed
    pattern: every seeded row starts `practitioner_notes_is_draft=true`,
    and `seeds/catalog.py`'s `_should_write_practitioner_notes()` means a
    reseed **never overwrites a row a human has since reviewed** — chosen
    upsert semantics, checked against the pre-existing (and, it turned
    out, always-clobbering) behavior of the old `guidance` field before
    deciding: official_guidance always tracks the source YAML (verbatim
    government text, no legitimate "human improved on this" case), while
    practitioner_notes protects a review the same way an editor's sign-off
    on any other draft content would need to be protected.
  - **UI** (`ControlDrawer.tsx`): "Show guidance" is now unconditional
    (matches the task screenshot's ask — AC.L2-3.1.1[a], [b], and [c] all
    show guidance now, not just [a]). The panel renders two visually
    distinct, separately-labeled sections — "Official CMMC Assessment
    Guide Text" (cited by `official_guidance_source`) and "MSP
    Practitioner Notes" — never blended. The practitioner-notes section
    carries a non-dismissible AI-authorship warning callout that renders
    every time the section renders (not a one-time banner — someone
    landing on an arbitrary objective months later has no memory of
    having dismissed anything), plus (**superseded 2026-09-10** — see
    below; was a draft/reviewed status badge) generation date + model.
    **Not currently exported anywhere** —
    `bundle_service.py` has no reference to guidance at all today; the
    export path (SSP bundle, PDF) doesn't exist yet for this content, so
    the caveat obligation is a documented constraint on whoever adds it,
    not something built here.
  - **Coverage** (verified against the live catalog, not assumed):
    316/316 seeded objectives have `official_guidance`; 316/316 have
    `practitioner_notes`. The gap to 320 is the pre-existing 4-objective
    catalog shortfall noted above, not a guidance-pipeline miss — closed
    2026-09-10, see that date's Done entry.
- **Catalog reconciliation: 4 missing objectives, full re-derivation of the
  drift guard** (2026-09-10). Follow-up to 2026-09-09's guidance work,
  which flagged but didn't fix a 4-objective catalog gap. This is a
  scoring-correctness issue, not a content one: SPRS rolls a control up as
  met only when every objective is met, so a practice silently missing a
  determination statement can report fully-satisfied while a real
  objective was never evaluated; an exported bundle would be missing that
  statement outright too.
  - **Root cause, determined with reasonable confidence but not certainty**
    (the original script no longer exists to inspect directly): git
    history shows an earlier session (`853039ae8`, 2026-07-04, Sonnet 4.6)
    already replaced memory-derived objective text with a `pdftotext`
    extraction against this same guide, explicitly flagging in its own
    commit message that 6 *other* controls needed manual fixing because
    "pdftotext concatenated the ASSESSMENT OBJECTIVES header with
    requirement text on same line." Re-inspecting the raw PDF text at the
    4 gap locations shows the same structural hazard that pass already
    named — [a]'s text wrapping across a line break, or (for
    `IA.L2-3.5.8` specifically) "[a] ... and \n[b] ..." phrased with "and"
    rather than the usual "; [b]" separator the parser evidently keyed on
    — strongly suggesting the same known extraction fragility struck 4
    more practices that pass's manual review didn't happen to catch,
    rather than a new or different bug.
  - **Full reconciliation, not just the 4 known rows** — extended
    `scripts/cmmc_guidance/extract_pdf.py` to also parse the "ASSESSMENT
    OBJECTIVES [NIST SP 800-171A] / Determine if:" block per practice
    (previously only Methods + Considerations were extracted), then wrote
    `reconcile_catalog.py`: a full practice-id / objective-key / objective-
    text diff between `cmmc_l2.yaml` and the real PDF, normalizing case,
    trailing connector words, and whitespace before comparing text
    (the catalog deliberately rewrites the PDF's semicolon-joined list
    style into standalone capitalized sentences — comparing meaning, not
    verbatim wording). Result: **110/110 practices matched, 0 extra or
    mis-keyed objectives, exactly the 4 known missing objectives, and one
    text defect directly caused by the same root cause** —
    `IA.L2-3.5.8`'s existing `[a]` row had literally concatenated both
    real objectives' text together with a stray "and [b]" fragment
    embedded mid-string, rather than a genuine second row ever existing.
    Also caught (and fixed, generalizing the existing kerning-fix
    mechanism) 9 more PDF-extraction artifacts the new objectives-text
    parsing exposed: "se curity" — a second, independent kerning split of
    "security" distinct from the already-known "secu rity" (font kerning
    is position-dependent, not a typo) — plus 8 more found only after
    noticing and removing the sweep's own `len(word) < 2` exclusion, which
    had been silently skipping every split whose second half is a single
    letter ("securit y", "acces s", "w ith", etc.). A "reasonable-looking"
    filter hiding real misses until something forced a second look, not
    a one-off. Confirmed nothing beyond this — the fix is "4 rows plus one
    text correction," not a catalog needing re-derivation from scratch.
  - **Fix**: added the 4 missing objectives to `cmmc_l2.yaml` (satisfaction
    types chosen by matching each to the closest existing sibling pattern
    in its own control — e.g. "X are identified" → `document_list`,
    matching `AC.L2-3.1.1[a]`/`[c]`; all still seeded `is_draft=True`, the
    same as every other objective, pending real C3PAO review) and split
    `IA.L2-3.5.8`'s malformed `[a]` into a correct `[a]`/`[b]` pair.
    Regenerated `cmmc_official_guidance.yaml` and added
    `cmmc_practitioner_notes.yaml` entries for all 4 new/corrected keys
    (self-audited against the same no-verdict/no-vendor framing rules as
    the rest of that file) so they aren't the only objectives without
    guidance. Catalog is now 320/320 against the authoritative extraction,
    confirmed by `reconcile_catalog.py` reporting zero issues.
  - **No Alembic migration** — deliberately. Nothing about the schema
    changed (migration 0031 already added every column this data needs);
    this is a data fix following the catalog's own existing update
    mechanism (`cmmc_l2.yaml` + `wingrc seed-catalog`), the same path
    every prior catalog correction (including the July rewrite that
    caused this) has always used.
  - **Backfill for already-running assessments**
    (`engine.py:backfill_missing_control_states`, CLI:
    `wingrc backfill-missing-control-states`): for every existing
    assessment, adds a `control_state` row for any objective missing one
    — `not_met`/`customer_owns`, the exact same default
    `_seed_control_states()` uses for a brand-new assessment, checked
    rather than assumed. Never defaults to `met`: that would assert an
    evaluation that never happened. Deliberately does **not** re-run the
    magic loop for already-active products against the new rows — checked
    every file under `baselines/` first rather than assuming: the only
    match across all 4 objectives (RocketCyber's IA-family entry) is
    `classification: customer_owns`, which the magic loop's own query
    already excludes, so as of today running it would be a no-op anyway;
    a future baseline that does cover one of these objectives picks it up
    normally the next time that product is (re)activated, through the
    existing path.
  - **Dry-run by default** (`--apply` to commit), matching this codebase's
    established safety posture for anything that mutates broadly (the
    workbook importer's dry-run/apply, reset-dev's preview). Each affected
    assessment gets exactly **one** `audit_log` entry
    (`control_state.backfill`, actor `system`) recording the before/after
    SPRS score and which objective keys were added, with an operator-
    supplied `--reason` in context — so a score drop is explainable from
    the audit log, not mysterious. Uses `recompute_sprs()` (the one write
    path for `assessment.sprs_score`) rather than hand-computing the
    score, which means it also writes a fresh `sprs_snapshot` row per
    affected assessment — **existing historical snapshot rows are never
    touched**, by construction (this function only ever inserts).
  - **Drift guard** (`tests/test_catalog_seed.py::
    test_catalog_matches_authoritative_reference`): a silent 4-row
    omission survived from July until this unrelated guidance task
    happened to cross-check it. Added a committed, PDF-derived reference
    (`backend/app/seeds/cmmc_catalog_reference.yaml`, generated by
    `scripts/cmmc_guidance/gen_catalog_reference.py`, independent of
    `cmmc_l2.yaml` itself — checking a file against a reference derived
    from that same file would never catch drift) and a test comparing
    every practice's objective-key set against it. Deliberately left
    **unmarked** (no `@pytest.mark.integration`, no DB) so it runs in CI's
    fast `backend` job on every PR touching the catalog, not only in
    `integration` — the next silent gap fails CI instead of waiting for
    someone to notice by hand. Verified the guard actually catches drift
    (not just that it passes) by injecting a fake mismatch and confirming
    the assertion fires, before restoring the file.
  - **Not yet run against wl-util-1's live assessments** — this task's
    "land it" scope was commit+push to `main`, verified on the isolated
    bench stack; deploying and running
    `wingrc backfill-missing-control-states --apply` against real data is
    a deliberate follow-up step for Jarrod, after reviewing the dry-run
    report against whichever real assessments exist there.
- **Practitioner notes: edit model revision** (2026-09-10, migration 0032).
  Revises a decision made in 2026-09-09's guidance-split work above, same
  day the catalog-reconciliation entry shipped. Jarrod's call: the
  draft/reviewed status this shipped with implied a note could "graduate"
  to authoritative once a human signed off on it — but a human-edited
  practitioner note is still one practitioner's opinion, never official
  CMMC guidance, so marking it "reviewed" would launder it into something
  it isn't. Removed the concept entirely rather than layering a fix on
  top of it.
  - **Provenance replaces status.** `practitioner_notes_is_draft` is gone
    (column dropped). New columns: `practitioner_notes_original` (the
    AI-generated text, frozen at seed time, for revert),
    `practitioner_notes_edited_by` (FK `user.id`, `ON DELETE SET NULL`),
    `practitioner_notes_edited_at` (the actual "has this been edited"
    signal everywhere — reseed protection, UI display — chosen over
    `_edited_by` specifically because a cascade can null the latter but
    never the former). An untouched note reads "AI-generated — not
    official CMMC guidance," plus generation date/model; an edited one
    reads "AI-generated, edited by \<name\> on \<date\> — not official CMMC
    guidance." The AI-origin statement never disappears, even for a note a
    human has fully rewritten — it says what the note *is* (AI-original,
    possibly human-touched), never *where it sits in a review workflow*.
    The permanent AI-authorship caveat callout in `ControlDrawer.tsx`
    (may-contain-errors, verify against the Guide and your C3PAO) is
    unchanged — it already rendered unconditionally and still does,
    regardless of edit state.
  - **Editing**: `PATCH /objectives/{id}/practitioner-notes` (new
    deployment-wide router, `backend/app/routers/objectives.py`),
    `msp_admin` only. Considered and rejected `msp_engineer`:
    `assessment_objective` has no `org_id`, so there's no membership
    boundary to scope an engineer's write to — editing here changes
    catalog content every org on the deployment sees, the same
    "deployment-wide, not per-org" shape D.1 Integrations already draws
    an admin-only line around. Considered and explicitly rejected a
    `c3pao_assessor` write exception even though Jarrod's original ask was
    "admin or C3PAO" — that role is deliberately, permanently read-only
    (`require_write()`), and carving an exception here would weaken that
    security property as a side effect of this task rather than a
    decision made on its own terms. **Not built, sketched for Jarrod to
    decide separately**: a C3PAO "suggest an edit" surface that lands as a
    proposal an `msp_admin` reviews and applies, never a direct write —
    would need its own table (proposed text, proposing assessor, target
    objective, status) and review UI; out of scope here.
  - **Revert**: `POST /objectives/{id}/practitioner-notes/revert` restores
    `practitioner_notes` from the frozen `practitioner_notes_original` and
    clears `_edited_by`/`_edited_at` back to `NULL` — which also makes the
    row reseed-eligible again, same as one that was never touched. 409s if
    no original is on record (shouldn't happen: seeding always sets
    `_original` alongside `practitioner_notes`); 400s if the note hasn't
    been edited (nothing to revert to).
  - **Reseed protection carried forward**: `seeds/catalog.py`'s
    `_should_write_practitioner_notes()` now checks
    `practitioner_notes_edited_at IS NULL` instead of the old draft flag —
    the same never-clobber-a-human-edit guarantee, keyed on a different
    signal.
  - **Audit**: both endpoints write `practitioner_notes.edit` /
    `practitioner_notes.revert` via the existing `log_event()` path, actor
    resolved from the request's authenticated identity (no explicit
    `actor=` needed — the same `_current_actor` ContextVar every other
    router relies on). `before_value`/`after_value` carry the full
    old/new note text (added `"practitioner_notes"` to `audit.py`'s
    `_TEXT_KEYS` so it gets the same 4000-char truncation as other long
    fields) — chosen over storing only a diff or nothing at all because
    the existing `log_event(before_value=..., after_value=...)` pattern
    already does this for every other edited-text field in the codebase
    (e.g. `control_state.update`), and a full before/after is what makes
    "who changed this note and to what" answerable straight from the
    audit log without needing the (deployment-local, never exported)
    edit to still be live.
  - **Migration** (`0032_practitioner_notes_edit`): backfills
    `practitioner_notes_original` from the current `practitioner_notes`
    for every populated row, so revert works immediately for
    already-seeded content — no note's *text* is lost. The one thing
    deliberately lost: any row already marked reviewed
    (`practitioner_notes_is_draft = false`) loses that status, with no
    replacement value, because the concept itself is what's being
    removed. Checked before writing the migration: no row on any known
    deployment (bench or wl-util-1) had actually been marked reviewed yet
    — the feature this undoes shipped the day before with no review UI
    ever built for it — so in practice this discarded no real signal, but
    the migration does the same thing regardless.
  - **Frontend**: `ControlDrawer.tsx`'s practitioner-notes panel replaces
    the Draft/Reviewed `status-badge` with the provenance text above, and
    adds inline Edit (textarea + Save/Cancel) and Revert (confirm-inline,
    matching `ApiTokensPanel.tsx`'s revoke-confirm pattern) affordances,
    both gated by a new `canEditPractitionerNotes(role)` in `lib/roles.ts`
    (`msp_admin` only, mirroring the backend gate) rather than the
    existing general-purpose `canWrite`, since ordinary `msp_engineer`
    assessment write access does not extend to editing shared catalog
    content.
  - **Verified**: backend unit + integration tests (edit round-trip,
    reseed-doesn't-clobber-an-edited-note, revert restores the original
    and clears the edit markers, `msp_engineer`/`c3pao_assessor` both
    403, audit entries for both actions), `ruff check` clean, full local
    `pytest -m "not integration"` suite green. Bench-stack verification
    (migration up/down/up, full integration suite, `tsc`, `vitest`,
    `vite build`, real browser pass with direct Postgres checks) tracked
    separately per this entry's own "land it" scope — see the commit this
    entry ships with for whether that had completed by the time of
    landing.
- **`consultant_admin`: a restricted platform role for an external
  consultant** (2026-09-11, migration 0034). A fifth platform role — full
  access to compliance data, no access to identity/security
  administration — for the case CLAUDE.md's five-layer model didn't
  originally distinguish: an MSP hiring an outside party (e.g. a C3PAO
  brought on to *help* rather than assess) to work inside a client's
  assessment without handing them the keys to user/token/audit
  administration.
  - **Naming, confirmed before building.** Jarrod's original suggestion
    was `mssp_admin`. Flagged and rejected before any code was written:
    an MSSP is the same category of company as the MSP operating the
    tenant, so the name doesn't convey "external, restricted" — and
    `ContactDocumentationRole`'s CHECK constraint already uses `mssp` for
    a *documentation* role (who filled out a form), a different concept
    entirely. Two `mssp` meanings in the same app is the exact
    naming-drift problem this codebase keeps having to fix (see the
    RocketCyber vendor-CRM lesson and CLAUDE.md's "verify reference
    data" discipline). Jarrod confirmed `consultant_admin`.
  - **The classification IS the role definition.** Every existing
    `require_role(...)` / `require_org_access("msp_admin", ...)` call
    site was audited and classified explicitly (not inferred per-route
    as the work went) — full per-route reasoning lives in each router's
    own module docstring, not just here:
    - **Compliance data — extended:** `routers/scope.py`,
      `routers/assessments.py`, `routers/evidence.py`,
      `routers/contacts.py`, `routers/raci.py`, `routers/bundle.py`,
      `routers/dashboard.py` needed no change at all — none of them
      carry a role-specific allowlist beyond `require_org_access()` +
      `require_write()`, so `consultant_admin` inherits full read/write
      access to scope, assets, system description, contacts,
      assessments, control states, evidence, RACI, and bundle export
      simply by being a non-read-only role with an `org_membership` row.
      `routers/integrations.py`'s router-wide gate was extended to
      `require_role("msp_admin", "consultant_admin")` — the one place
      an explicit code change was needed to grant a "Can" item.
    - **Identity/security administration — NOT extended:**
      `routers/users.py`'s mutating routes (invite, patch, delete,
      reset-mfa, unlock, reset-password, create API user) and its
      `api-tokens` routes (create/list/revoke, previously
      `msp_admin`+`msp_engineer`) stay exactly as gated. One exception
      found on the bench stack, not assumed: `GET .../users` (list) has
      no role restriction at all — pre-existing, unrelated to this task,
      already open to every org member including `customer_poc`/
      `c3pao_assessor`, so `consultant_admin` inherits that same
      visibility rather than being newly granted anything. `routers/orgs.py`'s
      `create_org` (`msp_admin`+`msp_engineer`) also stays unextended —
      not for security reasons but because it's an MSP-business decision
      (onboarding a new client engine-wide) that would leave a
      `consultant_admin` caller owning an org it has no membership in
      and can never reach again, since `org_membership.py`'s
      `_AUTO_PROVISION_ROLES` (which grants every `msp_admin`/
      `msp_engineer` membership in every newly created org) was
      deliberately NOT extended to include it — that auto-fan-out is
      exactly what "restricted, per-engagement" rules out.
    - **Audit log — decided and reported, not left accidental.**
      `routers/audit_log.py`'s `require_org_access("msp_admin")` was
      NOT extended. The log is one undifferentiated stream: alongside
      compliance-relevant entries (`control_state.update`, evidence,
      RACI, `practitioner_notes.edit`/`.revert`, `bundle.export`) it also
      carries every identity/security-administration event for the org
      — `user.invite`, `user.role_change`, `user.mfa_reset`,
      `user.anonymize`/`.delete`, `api_token.create`/`.revoke`,
      `integration_connection.credential_set`/`.delete` — plus IP
      addresses. A consultant has a real, legitimate interest in the
      first category; granting the whole log discloses the second too.
      Read-only access doesn't change that analysis — it's a
      disclosure question, not a write-permission one. Left
      `msp_admin`-only rather than half-built a filtered view; that's a
      new capability (a compliance-category-only audit view), not a
      role-gate tweak, and isn't built here.
    - **Practitioner notes — NOT extended, unlike Integrations, on the
      identical deployment-wide-scope property.** `routers/objectives.py`
      (migration 0032's practitioner-notes edit/revert) stays
      `msp_admin`-only. `AssessmentObjective` has no `org_id` at all —
      editing a note changes catalog content every org on the deployment
      reads, not just the one client a consultant was engaged for.
      Integrations shares that exact deployment-wide-scope property (one
      Liongard credential per MSP instance) and WAS extended, because
      this task's own "Can" list named "integrations config" explicitly
      and objectives editing was never named — the omission is read as
      deliberate rather than inferred, but the tension between the two
      routers' outcomes on the same underlying property is real and
      flagged in both docstrings, not silently accepted. Worth Jarrod
      revisiting if a multi-client MSP actually hires a per-client
      consultant for the Integrations screen specifically.
  - **Rank ladder renumbered**, `auth.py`'s `_ROLE_RANK`: `msp_admin: 5,
    consultant_admin: 4, msp_engineer: 3, customer_poc: 2,
    c3pao_assessor: 1` (previously `msp_admin: 4` down to
    `c3pao_assessor: 1`). Verified before renumbering that the map is
    consumed only by `min()`/comparison expressions (API token rank
    clamp, `_resolve_api_token`'s demotion clamp) — no persisted integer
    depends on the old numbering, so this was a pure code-level change.
    Rank does not imply route access on its own — every route-gate
    decision above is an explicit allowlist, never inferred from where a
    role sits in this ladder.
  - **Not read-only.** `consultant_admin` is not in `auth.py`'s
    `_READ_ONLY_ROLES` (`c3pao_assessor` stays the sole permanently
    read-only member, unchanged).
  - **Migration 0034**: extends the role `CHECK` constraint on every
    table that has one — `ck_user_role`, `ck_api_token_role`,
    `ck_org_membership_role`. `ContactDocumentationRole`'s own `mssp`
    documentation-role value is untouched (see the naming rationale
    above for why the two are deliberately kept separate).
  - **Frontend** (`lib/roles.ts`): `ROLE_RANK`/`ROLE_LABELS` updated
    (`ALL_ROLES` derives from `ROLE_RANK`'s keys, so both role pickers in
    `UsersPanel.tsx` and `ApiTokensPanel.tsx` — already driven entirely
    by `ALL_ROLES`/`ROLE_LABELS`/`ROLE_RANK` — picked up the new role
    with no component changes needed). New `canSeeIntegrations` allowlist
    (`INTEGRATIONS_ROLES = {"msp_admin", "consultant_admin"}`);
    `canSeeUsers`/`canSeeApiTokens`/`canSeeAuditLog`/`canSeeSecurity`/
    `canEditPractitionerNotes` all left unchanged (still evaluate false
    for `consultant_admin`) — Integrations is its own top-level `SideNav`
    entry, not part of the Security category, so seeing it doesn't imply
    seeing Security's three sub-items.
  - **Tests**: `test_consultant_admin_role.py` (new) — reuses
    `test_assessor_readonly.py`'s own scenario/case builders so the
    "CAN write compliance data" / "CANNOT reach admin-gated cases" matrix
    runs against the identical endpoint surface that file already proves
    `c3pao_assessor` is blocked from, plus standalone checks for scope,
    RACI, bundle export, integrations config, audit log, `create_org`,
    and practitioner-notes editing; a rank-ladder assertion; and a
    `_READ_ONLY_ROLES` regression guard confirming `c3pao_assessor`'s
    write-block survived the renumber. `test_api_tokens.py` gained the
    literal scenario the rank slot exists to prevent — an `msp_admin`
    minting a token *for* a `consultant_admin` target cannot mint it at
    `msp_admin` role — plus the demotion/promotion clamp cases
    (`_resolve_api_token`'s `effective_role = min(...)`) at the new rank
    slot specifically. `permissions.test.ts` extended with
    `consultant_admin` expectations on every existing "covers every known
    role" axis plus a new `canSeeIntegrations` describe block.
  - **Docs**: this entry; `CLAUDE.md`'s role list;
    `docs/PLAN-auth-rbac-completion.md`'s status header (five roles now,
    not four — flagged as a follow-up note, the four-role slices
    I.1–I.9 themselves are left as the historical record they are); root
    `ROADMAP.md` item I's "three-role sketch... superseded" note updated
    to "four-plus-one."
  - **Verification**: tracked against this entry's own "land it" scope —
    see the commit this entry ships with for whether the full bench-stack
    pass (migration up/down/up, full integration suite, `ruff`, `tsc`,
    `vitest`, `vite build`, real browser walkthrough logged in as a
    `consultant_admin` user with direct Postgres role verification) had
    completed by the time of landing.
- **Credential encryption key rotation** (2026-09-11) — built the
  re-encryption command D.1's design left for later: `crypto.py` already
  supported decrypt-with-any-configured-key / encrypt-with-primary
  (`MultiFernet`), but nothing actually walked existing rows onto a new
  primary, so "drop a retired key from
  `WINGRC_CREDENTIAL_ENCRYPTION_KEYS`" was never actually safe to do —
  the ciphertext would just silently stop decrypting for any row still
  under it. Triggered by a concrete need: the production key had passed
  through a chat transcript and needed retiring, and rotating while
  exactly one credential existed was the cheap moment to prove the path
  before it's needed under pressure.
  - **New**: `backend/app/credential_rotation.py`'s
    `rotate_credential_keys(session, dry_run=True)` — walks every
    `IntegrationConnection` row with a stored credential, decrypts
    (`crypto.decrypt_credential`, already tries every configured key —
    no per-row targeted-by-label decrypt exists or is needed, since
    MultiFernet's blind trial produces the identical plaintext as long as
    the row's original key is still configured), and for any row not
    already labeled with the current primary
    (`crypto.current_primary_label()`, new — exposes just the label, no
    key material), re-encrypts under the primary and updates
    `credential_key_version`.
  - **Fail-closed, whole-run "change nothing"**: every row needing
    rotation is decrypt-checked *before* any row is mutated. One
    undecryptable row (e.g. its key was already dropped from config)
    refuses the entire run — not just that row — since a half-rotated
    table (some rows on the old key, some on the new) is exactly the
    state a `pg_dump` restore exists to make recoverable from and painful
    without one.
  - **CLI**: `wingrc rotate-credential-keys` (`cli.py`), `--apply` to
    commit (default dry-run), reusing `reset-dev`'s own
    `_preflight_backup` helper — taken once, right before the real write,
    skipped when there's nothing to rotate (no write, no need). Dry-run
    always runs first regardless of `--apply` and reports the same
    decrypt-failure detail the real run would, so a bad key config is
    caught before anyone commits to it.
  - **Audit**: one `integration_connection.key_rotated` entry per rotated
    row (`actor="system"`, `context={"via": "cli"}`, matching
    `backfill-missing-control-states`'s own CLI-actor convention);
    before/after carry the old/new key *labels* only, never key material
    or the credential itself.
  - **Tests**: `test_crypto.py` (+`current_primary_label`),
    `test_credential_rotation.py` (round-trip under the new key alone
    after the old one is fully dropped from config; idempotent re-run;
    fail-closed across multiple rows — one bad row blocks a co-existing
    good one too; audit entry content), `test_cli_rotate_credential_keys.py`
    (backup-failure aborts without rotating, backup-success precedes the
    real write — order tracked explicitly, not inferred; dry-run never
    touches `pg_dump`; nothing-to-rotate skips the backup; fail-closed
    reports and exits non-zero without attempting a backup at all).
  - **Live rotation on wl-util-1** (2026-09-11): before rotating,
    checked `integration_connection` directly rather than trusting the
    task's own premise — found **zero rows**, not the one test credential
    expected. The Liongard credential configured during D.1's live
    verification was never left in the production database (that
    verification ran against a bench stack, since torn down per this
    session's own established cleanup convention). Flagged this
    discrepancy rather than fabricating a throwaway live credential to
    force a real data-migration exercise — the full 6-step sequence
    (including step 6: decrypt-with-only-the-new-key) was already proven
    end-to-end against a real throwaway credential on an isolated bench
    stack first, which is what "verify on the bench stack before
    touching wl-util-1" was for.
    - The concrete goal — retiring `prod-wl-util-1-2026-09-09` (generated
      during the D.1 deploy and reported through a chat transcript, the
      thing this task exists to fix) — doesn't depend on live credential
      rows existing, so proceeded: added the new key
      (`prod-wl-util-1-2026-09-11`) alongside the old one as primary,
      restarted, confirmed healthy; ran `rotate-credential-keys` (dry-run
      then `--apply`) against the live database, correctly reporting
      "Nothing to rotate" both times (zero rows, zero audit entries,
      `_preflight_backup` correctly never invoked — matches the
      nothing-to-rotate-skips-backup behavior proven in tests); removed
      `prod-wl-util-1-2026-09-09` from `.env`, restarted, confirmed
      healthy and the crypto config still resolves correctly with the
      new key alone.
    - `prod-wl-util-1-2026-09-09` is now removed from wl-util-1's
      environment. The only place its key material existed on disk
      outside `.env` itself was a transient `.env.bak-pre-rotation`
      taken as a safety net during the key swap — deleted once the swap
      was confirmed working, so nothing on wl-util-1 still holds it.
      `prod-wl-util-1-2026-09-11` is now the sole configured key — see
      this session's own chat transcript for the explicit reminder to
      store it durably (password manager / wherever the Postgres/MinIO
      secrets live) precisely so this doesn't repeat the original
      mistake by putting it in a doc instead.
- **RACI copy-forward on new assessment creation** (2026-09-10) — closes
  the decision recorded in `docs/PLAN-gui-restructure.md`'s G.7 section
  ("decided: copy forward from the most recent prior assessment, editable
  from there... not implemented in this pass"). Before this,
  `start_assessment` seeded no RACI rows at all, so an org's second
  assessment always opened with all 320 objectives unassigned even when
  the first assessment had been fully staffed.
  - `engine.py:copy_forward_raci` is a separate function the router calls
    right after `start_assessment()`, in the same transaction — not a step
    inside `start_assessment` itself, which has 40+ existing call sites
    (mostly test fixtures) with no expectation of a RACI side effect;
    changing its return shape would have touched all of them for no
    reason.
  - **The join is by objective, not by row id.** Every new assessment gets
    entirely new `control_state` rows, so copy-forward resolves each
    source assignment's `AssessmentObjective` (a stable, deployment-wide
    catalog identity) and finds the matching `control_state` row in the
    new assessment by `(assessment_id, objective_id)`. All letters and all
    contacts on an objective carry, not just R.
  - **Which prior assessment — decided by checking reachable states, not
    guessing:** most recent by `started_at`, status ignored, scoped to the
    same `framework_id`. The obvious-seeming safer rule — prefer the most
    recent *completed* assessment — turned out to be a dead end:
    `Assessment.status` never transitions past `"in_progress"` anywhere in
    this codebase today, so filtering to submitted/closed would make
    copy-forward permanently unreachable. Framework-scoping prevents a
    more-recent-but-incompatible-framework assessment from silently
    shadowing an older, compatible one.
  - **Framework/catalog drift:** an objective present in the source
    assessment but absent from the new one (a catalog change) is skipped
    and counted (`skipped_no_match`), not treated as an error — every
    other matching objective still carries.
  - **Departed contacts — checked the schema before assuming a
    deactivation flag existed:** `Contact` has no `deleted_at`/active
    column; deletion is a hard delete with
    `RaciAssignment.contact_id ON DELETE CASCADE`, so a departed contact's
    assignment is already gone by the time copy-forward runs — nothing to
    skip in the realistic case. Kept a defensive
    `skipped_inactive_contact` counter anyway (cheap, forward-compatible
    if soft-deactivation is added later); a test bypasses the FK via
    `DISABLE/ENABLE TRIGGER` to exercise the otherwise-unreachable branch
    directly.
  - **Made visible, not silent:** the summary (source assessment,
    carried/skipped counts, objectives still unassigned) rides on the
    assessment-creation response (`AssessmentOut.raci_copy_forward`) rather
    than also becoming a Roles-view banner — the `raci.copy_forward` audit
    log entry is the durable record for anyone who misses the one-time
    response, so a second delivery mechanism wasn't worth the added
    surface. `OrgPicker.tsx` interrupts the normal auto-navigate-to-board
    flow with a one-screen confirmation whenever something was actually
    carried, so inheriting last cycle's assignments can't be missed by not
    reading a toast.
  - **Not retroactive** — existing assessments are not backfilled; this
    only applies to assessments created after it shipped.
  - Tests: objective-based (not row-id-based) mapping, multiple
    letters/contacts on one objective, an unmatched objective skipped
    without error, the defensive inactive-contact branch, an org's first
    assessment creating cleanly with zero assignments and no error. Full
    regression suite, ruff, tsc, vitest, and build all clean.
  - **Verified live on an isolated bench stack, not just the test suite:**
    seeded Acme MSP, bulk-assigned Jane Smith as R across the PS family
    plus an override adding Bob Jones as A on one objective (multi-letter/
    multi-contact case), started a second assessment — the frontend
    confirmation screen reported "Carried 5 RACI assignment(s)," and a
    direct Postgres query confirmed all 5 landed on the new assessment's
    `control_state` rows against the correct objectives. Hard-deleted Bob
    Jones (cascade removed his assignment immediately, before a third
    assessment ever existed), started a third assessment, and confirmed in
    Postgres that only Jane's 4 assignments carried
    (`skipped_inactive_contact: 0` — correct, since the cascade already
    took care of it) with a matching `raci.copy_forward` audit log entry.
- **D.2 — Liongard device/user pull into scope_entity** (2026-09-11) —
  closes root `ROADMAP.md`'s D.2 item. `org_liongard_environment`
  (migration 0035) maps a WinGRC org to a Liongard Environment id;
  `connectors/liongard.py` pulls devices and identities;
  `importers/liongard.py` maps them onto the canonical
  `scope_entity.attributes` vocabulary as the third writer alongside the
  manual Add Asset UI and the workbook importer. Routes through the
  existing `reconcile()` → dry-run → apply flow — zero parallel ingest
  path, per the task's explicit constraint.
  - **Liongard's real API differs from the roadmap's assumptions — found
    by pulling the actual schema, not assumed:** the published reference
    docs (docs.liongard.com) don't show response bodies, so the real
    request/response shapes came from Liongard's own Postman collection.
    The inventory pull lives under `/api/v2/`, a POST "query" endpoint
    with a request body (`{Environment, Filters, Pagination}`), not the
    `/api/v1/` GET-list shape the existing D.1 test-connection call uses.
    Environment ids are small integers, not UUIDs. Every record carries
    `InventoryState` (`Discovery`/`Inventory`/`Archive`) — this connector
    pulls and keeps only `"Inventory"` (Liongard-confirmed), filtered
    client-side after fetching since the API's own `Filters` operator
    syntax isn't documented anywhere reachable and guessing at it risks
    silently returning zero rows instead of an honest error.
  - **Apply reuses `POST /imports/workbook/apply` unmodified** — its body
    never actually depended on the source being a workbook. The one real
    fix needed: the audit log's `context.source` was hardcoded to
    `"workbook"`; now reads the change's own `incoming.source`, so a
    connector-applied row is correctly labeled `"liongard"` in the audit
    trail instead of silently mislabeled.
  - **Natural-key convergence with the workbook path, deliberately
    mirrored:** devices key on `SerialNumber` (falling back to
    `Hostname`), matching `importers/workbook.py`'s
    Serial-or-Asset-Tag-then-Name precedent, so the same physical asset
    scoped through both paths converges on one `scope_entity` row rather
    than duplicating. Identities key on `Email` (Liongard's own
    account-grouping key per its docs), falling back to `Username` then
    `DisplayName`.
  - **`responsible_contact_id` deliberately never set for Liongard
    devices** — unlike the workbook's "Owner / Primary User" column,
    Liongard's device-profile schema has no authoritative owner field;
    `LastLoginUser` is telemetry (who last logged in), not an ownership
    assignment, and mapping it would silently misattribute ownership.
  - **No canonical PERSON attribute vocabulary exists anywhere in this
    codebase** (only DEVICE/SOFTWARE has one) — identity records write
    Liongard's own field names straight through, same as workbook-imported
    users keep their raw column names; inventing a canonical PERSON schema
    was out of scope for this task.
  - Pagination loops on `Data.Pagination.HasMoreRows` with a 200-page
    safety cap, not a fixed page count — a pull that silently stopped
    partway through and presented itself as complete would mark real
    assets MISSING in the reconcile diff, exactly the wrong failure mode.
  - Errors are specific, not generic: wrong Environment id, expired key,
    and HTTP 429 rate-limiting each surface their own message end to end
    (connector → router → frontend), matching D.1's Test-connection
    discipline.
  - Two real bugs found and fixed via the integration test suite on the
    bench stack, not assumed: (1) `session.refresh()` after commit on the
    new RLS-protected `org_liongard_environment` table raised
    `InvalidRequestError` — `app.current_org` is already reset by the time
    commit() returns, so the refresh's SELECT matches zero rows under RLS;
    fixed by dropping the refresh, matching `create_scope_entity`'s
    existing no-refresh pattern. (2) Reading `row.updated_at` after
    committing an UPDATE (re-mapping an org to a different Environment)
    hit the column's server-side `onupdate=func.now()` expiry path — the
    ORM marks that column expired after an UPDATE flush rather than
    populating it via `RETURNING`, so the attribute access lazy-reloaded
    under the same already-cleared `app.current_org`. Both are real
    production failure modes (a real `COMMIT` clears `SET LOCAL` the same
    way), not test-harness artifacts — fixed by setting `updated_at`
    explicitly in Python instead of depending on the DB round-trip.
  - Frontend: `LiongardSyncWizard` (environment picker → Sync Now →
    review → apply) reuses `ScopeChangeDiffTable`, extracted from
    `AssetImportWizard` — which now also surfaces the per-row and
    pull-level dry-run warnings the backend always computed but the
    frontend had never actually rendered (a real pre-existing gap, not new
    scope, found while building the shared component).
  - Tests: canonical-attribute mapping and natural-key fallback (unit),
    connector pagination/`InventoryState` filtering/error surfacing with a
    mocked `urlopen` (unit), and the full mapping/dry-run/apply HTTP flow
    including the MISSING/NEW/CHANGED classification (integration). 851
    backend tests, ruff, `tsc -b`, `vitest run` (62/62, `LiongardSyncWizard`
    5/5), and `vite build` all clean on the bench stack.
  - **No real Liongard key was available for this task — said so plainly
    rather than presenting a mock-only run as end-to-end proof.** Verified
    instead against a small real HTTP server
    (`liongard_mock_server.py`, stdlib `http.server`) returning example
    payloads captured from Liongard's own Postman collection, reached over
    genuine network calls from `connectors/liongard.py` inside the bench
    stack — not monkeypatched out — including real pagination (two actual
    HTTP round-trips for a 2-page device pull), a real `X-ROAR-API-KEY`
    auth-header check, and a Discovery-state row the connector had to
    filter out over the wire, not just in a canned test fixture. Full flow
    driven over real HTTP (login, TOTP MFA enrollment, environment listing,
    mapping, dry-run, apply) against the running app: dry-run #1 reported
    3 NEW (2 devices + 1 person, the Discovery-state device correctly
    absent), apply reported 3 applied, and a direct Postgres query
    confirmed all 3 `scope_entity` rows carry the canonical keys
    (`make_oem`, `model`, `version`, `device_subtype`, `asset_tag`,
    `mac_addresses`), `source="liongard"`, and a `source_ref` identifying
    the environment + pull timestamp — plus `scope_entity.import_apply`
    audit entries correctly labeled `source="liongard"` (the fix above).
    Re-ran the identical dry-run afterward and confirmed
    `summary={"new": 0, "changed": 0, "unchanged": 3}` — no spurious churn
    on a re-sync.
  - D.3 (approval workflow, scheduling) remains not built, per the task's
    explicit scope boundary.
- **Move Integrations to deployment-tier Administration, out of org nav**
  (2026-09-11) — closes a real bug D.1's own frontend introduced:
  `routers/integrations.py` carries no `org_id` on any route and
  `IntegrationConnection`'s docstring says it's deliberately not
  org-scoped (one credential per MSP instance), but `SideNav.tsx` rendered
  it inside the per-org side nav anyway. An admin working in one client's
  org could clear or replace a credential every other client on the
  deployment depends on, with nothing on screen indicating the blast
  radius. Frontend-only fix — the backend already had the right shape.
  - **The seam, held exactly where it was specified:** the connector list,
    credential set/replace/clear, and test-connection all moved up
    (everything `IntegrationsPanel.tsx` renders). `org_liongard_environment`
    (migration 0035, D.2), `LiongardSyncWizard` (mounted from
    `AssetsPanel.tsx`), and the `.../liongard/environment[s]` routes all
    stayed exactly where they were — genuinely org-scoped data, not part
    of this bug. Getting this backwards (dragging the mapping upward, or
    leaving the credential downstairs) was the explicit failure mode to
    avoid; verified live that neither happened, not just read from the
    diff.
  - **New `App.tsx` screen state, not a route** — this codebase has no
    router (`App.tsx` is a screen state machine, same pattern
    `InviteAcceptPage`'s pre-auth split already uses). Added `"admin"` to
    `Screen`, reached via a header button on the `"orgs"` screen
    (`OrgPicker`, the pre-org tier) gated by the unchanged
    `canSeeIntegrations`, and only offered from `"orgs"` — not
    universally — so the affordance doesn't blur the exact org/deployment
    separation this move exists to enforce. The breadcrumb's
    "Administration" text is the way back, mirroring the existing
    org-name-breadcrumb-as-back-link pattern already used for `"nav"` and
    `"onboarding"`.
  - **New `AdminArea.tsx` is a shell, not a single hardcoded panel** —
    Integrations is its only section today, but it carries its own small
    internal section nav so `docs/PLAN-gui-restructure.md`'s G.9
    (baseline-library import) and G.11 (pre-org access-grant screen) —
    both already documented elsewhere as belonging to this same
    deployment tier, before this task existed — land as new sections here
    instead of each inventing a new top-level screen. G.11's own entry is
    updated to note the host shell now exists (see that doc). No
    placeholder nav entries added for either, per the task's explicit
    instruction — `SideNav.tsx`'s Library category is already a standing
    example of how those age.
  - **Cross-references updated, not just moved:** `routers/scope.py`'s
    `_get_liongard_credential` error text now says "add a credential in
    Administration → Integrations first" instead of the stale
    "Integrations page"; `IntegrationsPanel.tsx` gained a line stating
    plainly that configuring a credential there doesn't sync anything by
    itself, since the two screens are no longer adjacent in the nav.
  - **The role-gate tension is surfaced, not resolved** — per the task's
    explicit instruction, `require_role("msp_admin", "consultant_admin")`
    and `canSeeIntegrations` are byte-for-byte unchanged.
    `routers/integrations.py`'s own docstring already flagged that
    `consultant_admin` reaching a deployment-wide screen was a tension
    worth Jarrod revisiting; moving the screen to the deployment tier
    makes that tension *visible* (a consultant_admin now sees a
    top-level "Administration" area, not a nav entry buried in one org's
    side nav) rather than creating a new one. Read: this move doesn't by
    itself argue for removing consultant_admin's access — the underlying
    justification in that docstring (integrations config is a compliance-
    data concern grouped with scope/assessments/evidence, not an
    identity-administration one) doesn't change just because the screen's
    address changed — but it does make the shape of what a consultant
    sees more legible to anyone auditing role assignments, which is worth
    weighing against the "hired for one client, sees a deployment-wide
    admin area" discomfort the original docstring named. Still Jarrod's
    call.
  - Tests: three new `App.test.tsx` cases (button visible for msp_admin
    and opens `AdminArea`/`IntegrationsPanel`; absent entirely for
    customer_poc; breadcrumb returns to the picker) plus the unmodified
    `canSeeIntegrations` coverage in `permissions.test.ts`. 851 backend
    tests, ruff, `tsc -b`, `vitest run` (65/65), and `vite build` all
    clean on the bench stack.
  - **Verified live on an isolated bench stack, not just the test suite**
    (fixture Liongard mock server reused from D.2's verification, same
    honesty discipline — no real Liongard key was needed for this slice
    since it's a nav move, not a connector change): logged in as a seeded
    `msp_admin`, confirmed the 🛠 Administration button appears only on
    the org-picker screen and opens `AdminArea` showing exactly the
    "doesn't sync anything by itself" banner and the Liongard card;
    configured the credential there against the mock server, mapped
    Acme MSP's `Assets → Sync from Liongard` to the mock Environment,
    ran a real dry-run (2 devices + 1 person, Discovery-state device
    correctly excluded) and applied it, confirmed in Postgres the 3 rows
    landed with `source="liongard"` and the environment-identifying
    `source_ref`; removed the credential and confirmed the org-side
    wizard's error read "Liongard isn't configured yet — add a credential
    in Administration → Integrations first," not a generic failure;
    logged in as a seeded `customer_poc` and confirmed the header shows
    only the account/logout icons — no Administration button — while the
    org's own Security category remained correctly absent for that role
    too (unrelated to this change, confirmed unaffected).
  - M.7/M.8/G.11 themselves were not started here, per the task's explicit
    instruction — only their host shell exists now.

- **Deployment-tier baseline library management (G.9)** — shipped
  2026-09-11 (`b0feb90`, `81d63d9`, `ae7bf7d`, `2210fb7`; see
  `docs/PLAN-gui-restructure.md`'s G.9 section for the full design
  writeup, including the mid-slice scope revision that dropped tenant
  assignment from this screen entirely). AdminArea's Tools section:
  library list, read-only tool detail (baseline mapping grouped by
  control, `platform_only` visually distinct), cross-org deployment
  footprint via a new SECURITY DEFINER function
  (`auth.product_deployment_footprint`, migration 0037), documentation
  attachments (new `product_document` table, migration 0036, reusing
  `storage.py`/`evidence.py`'s existing upload pipeline), baseline YAML
  import (dry-run diff + affected-org-count warning, then a re-validated
  apply), and publish/unpublish. `Product.is_published` — schema since
  migration 0002, read nowhere until this slice — is now enforced at both
  real read paths (`list_products_for_assessment`,
  `engine.py:activate_org_product`), landed as its own commit
  (`81d63d9`) separate from the screen itself so it can be reverted
  independently. Migration 0039 backfills every pre-existing product to
  published.
  - **Verified on an isolated bench stack** (`docker compose -p
    wingrc_g9`, fresh clone, own network/volumes, torn down afterward —
    not the shared instance): full backend suite 867/867 (`pytest`, no
    `-m` filter — unit + integration together), `ruff check .` clean,
    `npx tsc -b` clean, `vitest run` 72/72 (5 new: `AdminArea.test.tsx`,
    `ToolsLibraryPanel.test.tsx`, `ToolImportWizard.test.tsx`), `vite
    build` clean. Two test bugs surfaced only by running the full suite
    (not caught locally beforehand) and fixed on the bench branch before
    merge: `test_import_apply_invalid_yaml_writes_nothing` hadn't seeded
    a `Framework` row, so the router's own 409 fired before validation
    ever ran; `test_artifact_dedup_same_key_creates_one_task` used
    `test_evidence_tasks.py`'s `seeded` fixture directly (not `scenario`),
    which hadn't been publishing its product.
  - **The migration-backfill scenario was verified live, not just by
    reading the SQL**: downgraded the bench app database to
    `0038_product_source_docs`, seeded the catalog and RocketCyber
    baseline (landing `is_published=False`, the real default), created a
    second org and directly inserted an `OrgProduct(status="active")` row
    for it — simulating a tenant that activated RocketCyber before this
    slice existed, the way a real deployment's data would look — then ran
    `alembic upgrade head`. Confirmed by direct query: `is_published`
    flipped to `true`, the `OrgProduct` row was byte-for-byte unchanged
    (status, `activated_at`, `product_id`), and calling the real
    `list_products_for_assessment` function in-process for that org
    returned RocketCyber as active. This is the specific case flagged in
    G.9's own exit criteria as the most likely place to do silent damage —
    an **active**, not merely candidate, pre-existing tenant — and it
    passed.
  - **Live visual/keyboard nav-parity check (side nav matching the
    tenant's) was not completed** — Chrome browser automation could not
    reach the bench frontend on wl-util-1's LAN address from this
    session's environment (reachable by `curl` from wl-util-1 itself;
    every `navigate`/screenshot attempt from the browser tool returned an
    error page, on two different ports and two tabs). Jarrod chose to
    merge on the strength of the automated verification above rather than
    block on this; a manual spot-check of the nav is still worth doing
    when convenient. Flagged here rather than silently treated as done.
  - **On `consultant_admin`** (asked for explicitly in the task, not
    resolved): this role reaching this screen is a stronger version of
    the tension `routers/integrations.py`'s own docstring already flags —
    a consultant engaged for one client's assessment could import and
    publish a baseline change that alters compliance conclusions for
    every other client on the deployment, not just the one they're
    engaged on. The gate (`require_role("msp_admin", "consultant_admin")`,
    mirrored in `lib/roles.ts` as its own `TOOLS_LIBRARY_ROLES` constant,
    not a reuse of `INTEGRATIONS_ROLES`) is unchanged — Jarrod's call, not
    made here.
  - **Baseline versioning (§4 of the task) was explicitly flagged, not
    fixed** — see Planned item P below. What shipped is visibility (the
    import dry-run's affected-org-count warning), not a solution.
  - **Deployed to wl-util-1 (dev.wingrc.us) 2026-09-12.** Backed up first
    (`pg_dump --format=custom`, verified restorable via `pg_restore
    --list` — 342 TOC entries — before touching anything; the box's own
    "just pulled `git pull`, ran migrations, restarted" habit had no
    written backup step, so this deploy wrote one:
    `docs/deployment.md`'s new §7). Migrations `0036_product_document` →
    `0037_product_footprint` → `0038_product_source_docs` →
    `0039_publish_existing_products` ran cleanly. Backfill verified by
    diffing before/after query output, not eyeballing: `product.is_published`
    flipped `false→true` for the one seeded product (RocketCyber), and the
    `org_product` table's diff was empty in both directions (0 rows before,
    0 rows after — this deployment has no tenant with any product activated
    yet, so the specific "does the active tenant keep seeing it" scenario
    the deploy prompt was written to guard against does not currently
    exist on this box; verified generically instead via the library/detail/
    footprint endpoints called in-process against real data, and
    `control_state`/`evidence_task` counts confirmed unchanged, 320/0
    respectively). No Liongard credential is configured on this box either
    (`integration_connection` has zero rows), so the Liongard end-to-end
    dry-run check was not performable — reported as not-done rather than
    mocked. The login-based UI walkthrough (Administration → Integrations/
    Tools rendering correctly) was left to Jarrod to do with his own
    credentials rather than creating or resetting an account on a real
    deployment to get past it.

- **Deployment-wide user directory + org-access grant/revoke (M.7/M.8,
  G.11)** — shipped and verified 2026-09-12 (`d497c96`, `305828e`,
  `c8468d3`, `99740e5`; see `docs/adr/0009-multi-org-user-access.md`'s
  new M.7/M.8 section and `docs/PLAN-gui-restructure.md`'s M.7/M.8/G.11
  sections for the full design writeup). `auth.all_users_directory()`
  (migration 0040) backs a new `GET /admin/users` — deliberately does not
  return `User.role` (no longer authoritative for access as of M.4;
  showing it next to real per-org membership roles would mislead).
  `GET /admin/users/msp-org` resolves `deployment_settings.msp_org_id`
  (ADR 0009 M.1, already existed — not a new decision) for the "invite a
  new MSP user into our MSP" action, returning `null` on a fresh
  deployment rather than erroring. Grant/revoke landed in the existing
  `users.py` as `POST`/`DELETE /orgs/{org_id}/memberships`: grant reuses
  `org_membership.py`'s existing `_grant()` directly; revoke refuses
  self-revoke and refuses revoking the last `msp_admin` from an org, both
  unconditionally. New `UserDirectoryPanel.tsx` under Administration →
  Users, gated `msp_admin` only (not `consultant_admin`, unlike
  Integrations/Tools).
  - **A standing docstring bug was found and fixed first, as its own
    commit, ahead of the feature work**: `models.py`'s `OrgMembership`,
    `org_membership.py`'s module docstring, and `test_org_membership.py`'s
    module docstring all still asserted "`require_org_access()` doesn't
    consult this table yet, that's M.4" — true when M.2 landed, false
    since M.4 shipped 2026-08-17, and never corrected. Re-verified against
    the actual `auth.py` implementation before fixing, not assumed from
    the bug report.
  - **Verified on an isolated bench stack** (`docker compose -p
    wingrc_b`, fresh clone, own network/volumes, torn down after): full
    backend suite 883/883 (up from 867 pre-slice), `ruff check .` clean,
    `npx tsc -b` clean, `vitest run` 78/78 (5 new:
    `UserDirectoryPanel.test.tsx`; `AdminArea.test.tsx` extended to 3),
    `vite build` clean.
  - **Two rounds of test bugs were found only by running the full suite**,
    not caught locally beforehand, and fixed on the bench branch before
    merge: first, all 9 new grant/revoke tests 403'd because the test
    only granted the caller (`fake_msp_admin`) membership on their own
    home org, never on the *target* org being granted into — fixed by
    granting the caller `msp_admin` on the target org too, matching M.8's
    actual design ("the target org's own admin grants access into it").
    That fix in turn exposed a real, worth-documenting finding: because
    the revoke endpoint requires the caller to already hold `msp_admin`
    on the target org, the "last `msp_admin`" guard can only ever fire,
    through the HTTP endpoint, in the exact case the separate self-revoke
    guard already blocks — a caller revoking someone *else's* `msp_admin`
    membership is always, by construction, a second admin still standing.
    Documented in `revoke_membership`'s own docstring rather than deleted
    as dead code (it stops being redundant the moment any future caller
    of this logic isn't gated the same way), and tested by calling the
    function directly, bypassing `require_org_access`, since that's the
    only way to construct "last admin, target differs from caller" at all.
  - **The literal end-to-end proof — this ADR 0009 sequence's whole point
    since M.4's regression test — was run live**, not just in pytest:
    bootstrapped an `msp_admin` on a fresh bench Postgres, created two
    more orgs (three total), seeded a `customer_poc` homed in one of
    them. Confirmed via the real `GET /admin/users` directory call that
    the admin could see this user despite their home org differing from
    the admin's own. Confirmed via a real `TestClient` request (dependency
    overrides on `get_session`/`get_current_user`, going through the
    actual `require_org_access` chain — not a bare function call) that
    the `customer_poc` got a real 403 against the third org before any
    grant. Granted access through the real `POST .../memberships`
    endpoint, then confirmed the same request now returned 200. Revoked
    through the real `DELETE .../memberships/{user_id}` endpoint,
    confirmed 403 again. Self-revoke was separately confirmed refused
    (400) the same way. Audit log rows for both the grant and the revoke
    were confirmed present in Postgres with the correct `org_id` (the
    target org, not the actor's home org) and role values.
  - **A verification mistake, caught and corrected in the same session,
    not silently discarded**: the first attempt at the "before grant,
    access is denied" check called `list_users(...)` directly as a plain
    Python function rather than through `TestClient`, which meant
    `require_org_access` — a FastAPI dependency, not code inside the
    function body — never actually ran, so the "check" trivially returned
    200 regardless of membership state. Caught immediately (the result
    contradicted every other signal), and the entire live-verification
    pass was redone through `TestClient` with real dependency overrides,
    which is what the numbers above reflect. Recorded here because a
    verification method that silently doesn't verify what it claims to is
    exactly the kind of mistake worth naming, not quietly fixing and
    moving on as if it hadn't happened.
  - Liongard/API-token scope questions and any deployment-wide audit-log
    view remain explicitly out of scope, per the task's own instruction —
    not started, not attempted.
  - **Deployed to wl-util-1 (dev.wingrc.us) 2026-09-12.** Backed up first
    (`pg_dump --format=custom`, verified via `pg_restore --list` — 350
    TOC entries — before touching anything, per `docs/deployment.md`'s
    §7a). Migration `0039_publish_existing_products` →
    `0040_admin_users_secdef` ran cleanly; no data-modifying migration
    this time, so there was no before/after backfill diff to run.
    `deployment_settings.msp_org_id` was checked before deploying (per
    the task's explicit instruction, not assumed) and found already set
    to Acme MSP, this box's only org — the earlier `0023` migration's own
    backfill had anchored it correctly since Acme MSP already existed
    with an `msp_admin` at that migration's time. `GET /admin/users`
    (called in-process) correctly returned all 5 real users, including
    two ADR-0006-anonymized rows, with no error. **The cross-org read was
    not exercised** — every user on this deployment is homed in the same
    single org, so there is nothing to cross; reported as the degenerate
    case, not a verified cross-org result, matching `0039`'s own
    `org_product` finding. Grant then revoke were exercised against a
    clearly-named throwaway user (`wingrc-verification-throwaway@
    wingrc.invalid`, `is_active=False`) attributed to Jarrod's real admin
    identity for accurate audit trail, confirmed via direct query
    (`org_membership` row created then removed) and via the two resulting
    `audit_log` rows (`org_membership.grant`/`.revoke`, correct `org_id`),
    then the throwaway user was hard-deleted — appropriate for a record
    with no real history, not the ADR 0006 anonymize case. This check
    called the router functions directly rather than through the full
    HTTP layer (a real admin login wasn't available); `docs/deployment.md`
    §7d now names this distinction explicitly, after getting it wrong
    once during Slice B's own bench verification. No login-based UI
    walkthrough was performed, for the same credential reason as the
    prior deploy.

- **Outbound email (D.3 prerequisite #1 of 2 — job scheduler is #2, not
  built here)** — generic SMTP registered as a connector
  (`backend/app/connectors/smtp.py`) through the same `ConnectorSpec`/
  `IntegrationConnection` machinery D.1 built for Liongard, extended with
  two small, backward-compatible additions: `ConnectorSpec.kind` (a UI
  grouping discriminator — `"data_source"` vs `"notification"`, since SMTP
  reaches the API through the identical `/integrations/*` endpoints as
  Liongard but isn't a scope data source and reads oddly grouped with
  one) and `ConnectorSpec.optional_fields` (SMTP's username/password are
  legitimately blank for an unauthenticated internal relay — the first
  connector where "missing" isn't always invalid). Encryption mode is a
  three-way string (`starttls`/`tls`/`none`), not a boolean; port is
  free-form text, not a 587/465 dropdown, since SMTP2GO (the provider in
  use) publishes 2525/8025 fallbacks; certificate verification defaults
  on, with an explicit, honestly-named `verify_cert` opt-out for a
  self-signed internal relay. Test-connection genuinely exercises
  connect/EHLO/TLS-upgrade/AUTH and reports which step failed. **As of
  2026-09-14 it can also send one real, optional test message** (see
  this file's own "Outbound email — verified against a real provider"
  entry below) — the claim in this paragraph that it "does not send a
  real message" describes this slice as originally shipped, not the
  current behavior; left here rather than rewritten so the sequence of
  what changed and why stays legible. Sending itself is one boundary,
  `backend/app/email_service.py`, called
  synchronously from the two existing token-issuing call sites
  (`routers/users.py`'s `invite_user`/`reset_user_password`) — deliberately
  not fire-and-forget; see that module's docstring for the honest
  reasoning (a queue is the real fix, and that's this list's second open
  item below).
  - **Content rule, enforced by construction at the only two call
    sites that exist:** these emails carry zero compliance content — no
    control ids, findings, evidence, asset names, scores — in body or
    subject. The pattern is "something needs your attention, sign in to
    WinGRC" plus a link (`?invite_token=...`, picked up once by `App.tsx`'s
    lazy initial state and stripped from the visible URL via
    `history.replaceState` since it's a credential, then prefilled — not
    auto-submitted — into `InviteAcceptPage`'s existing manual token
    field). A new `WINGRC_PUBLIC_URL` setting supplies the browser-facing
    hostname the backend has no other way to know; unset, both call sites
    fall back cleanly to the pre-existing manual-delivery path.
  - **Fallback contract, not a nice-to-have:** manual delivery (the raw
    token in the response body) is never removed and never depends on
    email succeeding — `email_sent`/`email_error` on the invite/reset
    response tell the admin which happened, whether the cause is
    `WINGRC_PUBLIC_URL` unset, SMTP unconfigured, or a real send failure.
    Covered by `tests/test_user_email_wiring.py`.
  - Audit logging (`email.send`, recipient + template + outcome) and log
    lines never carry the raw invite/reset token or the SMTP credential —
    asserted directly in tests, not assumed
    (`tests/test_user_email_wiring.py`'s log/audit-row tests,
    `tests/test_email_service.py::test_credential_and_body_never_logged`).
    Single-use redemption of the token itself was already covered by the
    I.5 password-lifecycle tests (`test_reset_token_single_use`) before
    this slice — not re-derived here, just confirmed still true.
  - **Deliberately left open, so the gap is recorded rather than assumed
    solved:** no retry, no delivery record, no bounce handling. A failed
    send today is visible once, synchronously, in the API response that
    triggered it — there is no queue, no "retry this later," and no
    stored record of who was ever emailed what and whether it actually
    landed. `email_service.send()` is written so a future job-scheduler
    slice's queue worker can call it unchanged (plain function, session +
    args in, result out, nothing request-specific baked in), but that
    queue is D.3's second prerequisite and is not built here. Until it
    exists, "the admin saw `email_sent: false` and delivered the token by
    hand" is the only recovery path for a failed send — by design, not by
    oversight, but a real gap for anyone relying on this for anything
    higher-volume than the two low-frequency admin actions it serves
    today.
  - **Verification status:** unit tests (`tests/test_smtp_connector.py`,
    `tests/test_email_service.py`, `tests/test_user_email_wiring.py`)
    exercise a real local fake SMTP server (`aiosmtpd`, a new dev-only
    dependency — stdlib `smtpd` was removed in Python 3.12/PEP 594) across
    STARTTLS, implicit TLS, no encryption, auth success/failure, no-auth,
    cert-verification on/off, and connect timeout/refusal — never a
    monkeypatched `smtplib` call. **No real SMTP provider was exercised in
    this session** (no test credentials were supplied) — this is a
    fake-server-only result, not end-to-end proof against SMTP2GO or any
    other real provider, same caveat D.1's own Liongard check named.
    **Superseded 2026-09-13 — this caveat is now out of date; recorded
    here rather than deleted so what was and wasn't verified, and when,
    stays legible.** Jarrod configured SMTP2GO against the real service on
    wl-util-1 and it now works — see this file's own "Outbound email —
    verified against a real provider" Done entry below for the full
    writeup, including the encryption-mode/port bug that config attempt
    found and the field-descriptor/test-send fix it prompted.
    **Verified 2026-09-12 on wl-util-1, live, this run:** an isolated
    `docker compose -p wingrc_outbound_email` project (fresh clone under
    `~/bench/outbound-email`, its own network/volumes, no host ports
    published — the live `wingrc` project on that box was never touched)
    with real Postgres and MinIO — **908/908 backend tests** (`pytest -q`,
    every integration test included, not just the new ones), `ruff check .`
    clean, and a throwaway `node:24-alpine` container running the
    frontend's own scripts unmodified — `npm test` (**78/78** vitest,
    12 files) and `npm run build` (`tsc -b && vite build`) both clean. Two
    bugs surfaced only at this stage, not locally, and were fixed and
    re-verified in place: a naive test assertion that didn't account for
    `EmailMessage`'s quoted-printable line-wrapping of the long invite/
    reset link (`test_user_email_wiring.py`), and a pre-existing test
    fixture (`UserDirectoryPanel.test.tsx`) not updated for the new
    `email_sent`/`email_error` fields on `InvitedUser`, caught by `tsc -b`.
    No browser walkthrough was performed — this is a backend/API slice
    with a thin, already-shared (`IntegrationsPanel`) UI surface, not new
    interactive UI, unlike G.7's own live-browser check.

- **Job scheduler (D.3 prerequisite #2 of 2 — outbound email above is #1)**
  — `backend/app/scheduler.py`: a Postgres-backed job registry and runner,
  no broker, driven by session-level advisory locks
  (`pg_try_advisory_lock`). Recommended and built as a **separate `worker`
  container** (docker-compose.yml) running the same image as `backend`
  with a different command (`wingrc worker`) — not in-process
  (APScheduler was considered and rejected outright: it reintroduces the
  exact event-loop-starvation incident this deployment already suffered
  once, at concurrency 50, requiring a manual restart) and not a
  broker-based queue (Celery/RQ/Arq — a real new-infrastructure cost for
  an MSP self-hosting via plain Docker Compose that a Postgres-only design
  avoids). `run_due_jobs()` is the one scheduling decision both `wingrc
  worker`'s loop and a new one-shot `wingrc jobs-run-due` call — the
  latter exists specifically so an operator can point host cron at it
  instead of running the extra container, with zero behavioral difference
  from the two paths. Omitting the worker (or not running cron) leaves
  the API fully functional and scheduled jobs simply not happening, shown
  honestly (not silently) on the new Administration → Scheduled Jobs
  panel.
  - **`job_run` is a compliance record, not just plumbing** — one
    append-only row per execution attempt (job name, scheduled/started/
    finished times, outcome, error on failure, a small JSON result
    summary), following the same never-rewritten discipline as
    `audit_log`/`sprs_snapshot` elsewhere in this codebase. Deliberately a
    separate table from `audit_log`, not a new action type in it:
    `audit_log` records actor-initiated compliance mutations, and a cron
    tick has no actor; a future job that changes something audit-worthy
    still writes its own ordinary `audit_log` row for that change.
  - **Correctness properties, each with an explicit decision (see
    scheduler.py's own module docstring for the full reasoning):**
    no-double-runs via a dedicated `NullPool` connection holding the
    advisory lock (two workers/a mid-job restart can never both
    succeed); crash recovery via Postgres's own release-lock-on-
    disconnect plus a lazy per-job orphan reconciliation the moment the
    *next* run acquires that job's lock (no heartbeat, no timestamp-
    expiry guess); overlap policy is skip-don't-queue (a still-running
    job's next tick just skips); schedules are fixed UTC `timedelta`
    intervals, deliberately no "3am local" cron-expression support yet
    (no job needs one — D.3's daily sync will, and should add that
    against a real requirement, not speculatively here).
  - **RLS**: `job_run` itself carries no `org_id` (deployment-wide, like
    `integration_connection`). The one registered job
    (`expire_stale_invites` — sweeps `invite_token_hash`/
    `invite_expires_at` for already-expired tokens; hygiene, not a
    security fix, since `auth.find_user_for_invite()` already rejects an
    expired token at redemption time) is a genuinely cross-org operation,
    so it goes through a new `SECURITY DEFINER` function
    (`auth.expire_stale_invites()`, migration 0042) matching
    `auth.msp_role_users()`/`auth.all_users_directory()`'s exact existing
    precedent — never a blanket bypass. A future *per-org* job (D.3's
    Liongard sync, most likely) must instead loop over orgs and `SET
    LOCAL app.current_org` per iteration, exactly like a request handler;
    documented as the decision, not built, since nothing needs it yet.
  - **Verified 2026-09-12 on wl-util-1, live, this run:** an isolated
    `docker compose -p wingrc_job_scheduler` project (fresh clone,
    separate network/volumes, the live `wingrc` project on that box never
    touched) — **924/924 backend tests** (up from the outbound-email
    slice's 908; the 16 new tests cover concurrent-attempt locking via
    two real racing connections, a simulated killed-mid-job orphan
    reconciled on next acquisition, overlap-skip via `run_due_jobs()`
    itself, and `auth.expire_stale_invites()` working correctly under
    `wingrc_app` with no broader grant), `ruff check .` clean, and
    frontend `npm test` (**85/85** vitest, 13 files) plus `tsc -b`/
    `vite build` clean. One real bug surfaced only at this stage: the
    `worker` service inherited `backend`'s image-baked HTTP healthcheck
    (`:8000/health`), which `wingrc worker` doesn't serve, so Compose
    reported it permanently "unhealthy" regardless of whether it was
    actually working — fixed with `healthcheck: disable: true` and
    re-verified. **The starvation regression itself was measured live,
    not assumed**: with `db`/`minio`/`backend`/`worker` all running for
    real, a concurrency-50 load test against `backend`'s own `/health`
    (matching the original incident's own reproduction concurrency)
    measured p99=80ms/zero errors across 8450 requests with the worker
    idle, then p99=53ms/zero errors across 8650 requests while the worker
    was made to block for 15 seconds straight (a plain `time.sleep`, not
    even a real job) — no measurable difference, confirming the
    separate-container design actually delivers the property it was
    chosen for, rather than merely arguing for it.
  - **Deliberately left open, so the gap is recorded rather than assumed
    solved:** no manual "run now," no enable/disable — both are their own
    slice with their own authorization questions, per this slice's own
    explicit scope boundary. No local-time/cron-expression scheduling.
    Only one job exists; the scheduler mechanism itself is otherwise
    unexercised by anything beyond that one low-risk hygiene sweep until
    D.3 actually adds a second job.

- **SPRS submission record-keeping + assessment completion** (2026-09-13)
  — two coupled features built together because the annual-reminder
  clock in the second depends on the record kept by the first.
  `sprs_submission` (migration 0043) is the record of what was actually
  **filed with SPRS** — a human action in a DoD system this app cannot
  observe — kept strictly separate from two other, easily-conflated
  facts: `sprs_snapshot` (what WinGRC **computed**) and
  `Assessment.status` (whether the WinGRC assessment cycle is
  **complete**). Org-scoped, not assessment-scoped (`assessment_id`
  nullable — the first submission is typically captured at onboarding,
  before any assessment exists); append-only like `sprs_snapshot`
  (`voided_at`/`voided_reason` are a one-way annotation, never an edit
  to what a row claims was filed — a correction is a new row); the score
  is stored as a value, never a live reference, so a later recompute can
  never retroactively change what was claimed submitted.
  - **Completing an assessment creates no submission row, ever.**
    `engine.py:complete_assessment` (the `in_progress` → `submitted`
    transition `Assessment.status`'s CHECK constraint declared but
    nothing wrote — verified with a full grep before building, per the
    task's explicit instruction, and confirmed clean) only stamps
    `submitted_at` and prompts the frontend to offer recording a
    submission — clearly framed as "when you file this in SPRS, come
    back and record it," never as something already done, and
    dismissible. `closed`/`closed_at` stay unimplemented: nothing in
    this codebase defines what "closed" means distinct from
    "submitted," and implementing a state nobody needs just because the
    CHECK constraint names it would be guessing, not building. Asserted
    explicitly in tests, not just implied — this is the central
    invariant the whole design rests on.
  - **Completion gates nothing** — bundle export, evidence upload, and
    control-state edits all keep working identically before and after,
    per Jarrod's explicit instruction that people may want to test-export
    or partially work a bundle before completion. `locked_at` is left
    unwritten on purpose; auto-locking on completion would violate that
    constraint. **Still unimplemented, tracked here so the next reader
    doesn't assume completion locks:** locking is a separate, deliberate
    action with its own design, not built in this slice.
  - **Contact deletion does not destroy the submission record** —
    checked against the contact-lifecycle precedent this file's own RACI
    copy-forward entry already worked out (`RaciAssignment.contact_id`
    is `ON DELETE CASCADE`, correct there since RACI is a live
    "who's responsible now" fact) before reusing its shape, and
    deliberately NOT reusing it: `submitted_by_contact_id` is
    `ON DELETE SET NULL` instead (matching `User.contact_id`'s existing
    precedent), with the submitter's name/email denormalized into the
    row at write time so "who filed this" survives the contact being
    hard-deleted.
  - **Copy-forward's "which prior assessment" query is updated** now
    that completion exists (see this file's own RACI copy-forward
    entry, which explicitly flagged this as worth revisiting once a real
    completion concept shipped): prefers the most recently *completed*
    assessment on the same framework, falling back to most-recent-by-
    started_at only when none is completed yet, since nothing here
    blocks parallel/experimental assessments and a more-recent
    in_progress one could as easily be a throwaway as the real prior
    cycle.
  - **`sprs_annual_reminder`** (scheduler.py) is the scheduler's first
    job with actual product meaning. Clock starts at the **attested
    submission date only** — enforced by construction, since the
    SECURITY DEFINER due-check query only ever considers orgs with a
    non-voided submission on file at all; an org with none has no clock,
    full stop. Single 12-month reminder (CMMC's actual annual SPRS
    re-submission cadence), not a 60/30/7-day ramp — erring toward
    fewer notifications. Idempotent, keyed to the submission (a new
    submission resets the clock automatically), and content-rule-clean:
    no org name, no score, in neither subject nor body, and exactly one
    email per recipient per tick regardless of how many orgs are due.
  - **Recipient decision, flagged rather than picked broadly, per the
    task's explicit instruction:** reminders default to MSP staff only
    (every active `msp_admin`/`msp_engineer`, deployment-wide, via a new
    `auth.msp_staff_emails()`). **Open question for Jarrod:** whether an
    annual-submission reminder should ever reach a customer contact at
    the client org — emailing a client's own POC from the MSP's domain
    about their own compliance deadline is a different product decision
    than notifying MSP staff, and hasn't been made. Do not widen this
    recipient set without that decision.
  - **Surfaced in three places:** a new Scope → SPRS Submissions tab
    (record/void, current + full history — also reused unmodified as
    `OnboardingWizard`'s new optional 4th step, and deliberately **not**
    counted toward onboarding completeness, matching the task's own
    lean — "we have never filed" is a legitimate state for a first-time
    assessment, not an incomplete one); a dashboard widget (current
    score/date/submitter, fetched via its own call since this is
    org-scoped data on an otherwise assessment-scoped dashboard
    endpoint, same reasoning the existing Recent-Activity widget already
    established); and the assessor bundle export (captured into
    `BundleSnapshot` at export time like everything else — a bundle is a
    point-in-time snapshot, so a later correction/void must never
    retroactively change what an already-generated bundle claims the
    filing history was).
  - **Verified 2026-09-13 on wl-util-1, live, this run:** an isolated
    `docker compose -p wingrc_sprs_completion` project (fresh clone,
    separate network/volumes, the live `wingrc` project on that box
    never touched) — **964/964 backend tests**, `ruff check .` clean,
    frontend `npm test` (**91/91** vitest, 14 files) plus `tsc -b`/
    `vite build` clean. Three real bugs surfaced only at this stage, not
    locally, fixed and re-verified in place: a duplicate-email fixture
    collision in a new RBAC test (`_client_as` reused
    `_make_fake_user()`'s default email inside an org that already had
    a user at that address), a datetime-string-format mismatch in a
    reopen-audit-entry assertion (Pydantic's `...Z` vs. Python's
    `isoformat()`'s `...+00:00` for the same instant), and a frontend
    test fixture missing `Contact.created_at`.
  - **Deliberately left open, so the gap is recorded rather than assumed
    solved:** no UI for voiding-with-full-workflow beyond the basic
    reason-and-confirm dialog built here; `locked_at`/assessment locking
    (noted above); the MSP-vs-customer-contact reminder-recipient
    question (noted above); no local-time/cron scheduling for the
    reminder (a daily due-check against a 12-month SQL condition is a
    fine substitute for an annual reminder, per scheduler.py's own
    module docstring, but would not be for anything needing an exact
    calendar date).

- **Periodic review & attestation workflow (D.3's first half — the daily
  Liongard sync is the second half, still open, see its own Deferred
  entry for why it was sequenced second)** (2026-09-13) — WinGRC asks the
  MSP and the client to review the users/devices in scope and records
  their sign-off as evidence. Not a notification feature: the client's
  acknowledgement is the artifact itself, an assessor-showable record
  replacing meetings/minutes Jarrod previously ran by hand.
  - **Control mapping, derived (not recalled) from the seeded catalog**
    (`app/seeds/cmmc_l2.yaml`) and cross-checked against `catalog.py`'s
    pre-existing `AUTHORIZED_USERS`/`AUTHORIZED_DEVICES` `ListView`s (both
    already `control_ids=("AC.L2-3.1.1",)`, predating this slice):
    **AC.L2-3.1.1[a]** "Authorized users are identified" and **[c]**
    "Devices... authorized to connect... are identified" (both
    `satisfaction_type=document_list`). `[b]` (processes — a third
    subject type, deliberately out of scope) and `[d]/[e]/[f]`
    (`type=product` — real-time enforcement, not identification) are not
    mapped; `CM.L2-3.4.1` was considered and rejected as a broader
    inventory concept, not specifically "authorized users/devices."
  - **Auth finding that corrects the originating task's own premise:**
    `customer_poc` is **not** in `auth.py`'s `_READ_ONLY_ROLES`
    (`{"c3pao_assessor"}` only) — verified directly, then cross-checked
    against `docs/PLAN-auth-rbac-completion.md`'s I.2 section, which
    explicitly preserved `customer_poc` write access when making
    `c3pao_assessor` read-only. The actual carve-out this feature needed
    ran the *other* direction: without an extra restriction, the router's
    standard `require_org_access()` + `require_write()` gate would let a
    `customer_poc` open cycles and resolve MSP-follow-up flags too.
    `routers/review_cycles.py` layers a per-route
    `require_org_access("msp_admin", "msp_engineer", "consultant_admin")`
    on open/resolve-flag only, mirroring `users.py`'s existing
    grant/revoke-membership precedent — a narrow carve-out, not a role
    rethink, so this did not hit the task's stop-and-ask trigger.
  - **One cycle covers both users and devices**, not two — same control
    mapping, and matches the real-world "one review meeting" workflow.
    `ReviewCycleItem` snapshots `scope_entity` (natural_key/category/
    attributes) at open time, same immutable-snapshot discipline as
    `BundleSnapshot` — verified directly that a cycle's snapshot does not
    change when the underlying `scope_entity` rows are later renamed,
    decommissioned, or added to.
  - **Non-response is evidence, not silence:** `ReviewCycleReviewer.status`
    (`requested`/`viewed`/`attested`/`no_response`) plus
    `ReviewCycleReminderLog` (append-only, `UniqueConstraint(reviewer_id,
    reminder_number)`) keep the full attempt history. A cycle
    auto-closes to `completed` the moment every reviewer has attested
    (inside `attest()`, not waiting for the next scheduler tick); the
    `review_cycle_sweep` job force-closes to `closed_unattested` at
    `due_at` (`RESPONSE_WINDOW_DAYS = 21`) regardless, stamping every
    still-open reviewer `no_response` — never left ambiguous.
  - **Cadence is per-org, not deployment-wide** — `Organization.
    review_cadence_months` (default 6, validated 1–60), deliberately
    contrasted with `config.py`'s `session_idle_minutes` deployment-wide
    precedent for 3.1.11: review cadence is a contractual fact that
    varies client to client, not a uniform technical policy. Editable
    through the existing `PATCH /orgs/{org_id}/profile` endpoint (its
    generic field-set loop picked it up with no handler change) and
    snapshotted onto `ReviewCycle.cadence_months` at open time so a later
    cadence edit never retroactively changes what an already-open cycle's
    own due date meant. `RESPONSE_WINDOW_DAYS`/`REMINDER_DAYS = (7, 14)`
    stay fixed operational constants, not per-org.
  - **Flagging never mutates scope** — a reviewer's "this doesn't look
    right" creates a `ReviewCycleFlag` for MSP follow-up only;
    `scope_entity` is asserted byte-for-byte unchanged by a flag in
    tests. Actually changing scope still goes through the existing
    dry-run → review → apply path, untouched by this feature.
  - **New `Evidence.artifact_type = 'attestation'`** (migration 0045) —
    a genuinely new kind, not squeezed into `document`: system-generated
    from a structured sign-off event, not a human-uploaded file. Bundle
    export needed **zero `bundle_service.py` changes** — the closed
    cycle's evidence links to AC.L2-3.1.1[a]/[c]'s `control_state` rows
    via the existing `EvidenceStateLink` mechanism, and the bundle's
    evidence-embedding logic reads `artifact_type` generically with no
    hardcoded allowlist (confirmed via grep before assuming it worked).
  - **Reuses `run_due_jobs()`** — two new jobs, `review_cycle_open` and
    `review_cycle_sweep`, each backed by a SECURITY DEFINER function
    (migration 0046, same precedent as `auth.msp_role_users()`/
    `auth.expire_stale_invites()`). Recipients are deliberately **both**
    MSP staff and client reviewers (`auth.org_reviewer_candidates()`),
    unlike `sprs_annual_reminder`'s MSP-only set — a review cycle is
    inherently a two-sided conversation. `consultant_admin` **is**
    included as an MSP-side reviewer here (unlike the SPRS reminder's
    exclusion): review cycles are per-org/per-engagement, so a
    consultant_admin actually granted membership in *this* org is a
    legitimate participant, unlike a deployment-wide notification. No
    org/user/device/control detail in any email subject or body, checked
    directly in tests.
  - **Two real bugs found only via a live, real-HTTP bench walkthrough**
    (`§9`'s explicit ask — two orgs, a real `customer_poc` login, TOTP
    MFA and all, over an actual running uvicorn process, not `TestClient`
    or calling route functions directly) — neither surfaced by the full
    automated suite beforehand:
    1. `get_cycle`'s `db.commit()` ran immediately after `record_view()`,
       *before* reading items/reviewers/flags. `app.current_org` is set
       via `set_config(..., true)` (transaction-local, per
       `require_org_access()`'s own docstring) — a mid-handler commit
       ends that transaction and Postgres discards the GUC with it, so
       every RLS-scoped read after it silently saw `org_id IS NULL` and
       matched nothing. Confirmed live: the `review_cycle_item` row
       existed (raw owner-role query) while the API returned `items: []`.
       Fixed by moving the commit to the end of the handler, after every
       read — exactly the bug class `tests/conftest.py`'s `_app_session`
       wrapper (`RESET app.current_org` after commit) exists to catch,
       and a namesake of the `recompute_sprs`/`patch_control_state`
       autoflush incident this file already records.
    2. The manual MSP-triggered open (`POST .../review-cycles`) had no
       guard against a second concurrent open cycle for the same org —
       only the scheduler's own due-check excluded orgs with one already
       open. Fixed in `review_cycles.open_cycle()` itself (409, not a
       fresh cycle) so it holds regardless of which caller opens it.
  - **Verified 2026-09-13 on wl-util-1, live, this run:** an isolated
    `docker compose -p wingrc_review_cycles` project (fresh clone,
    separate network/volumes, the live `wingrc` project on that box never
    touched) — **986/986 backend tests**, `ruff check .` clean, frontend
    **97/97** vitest (15 files) plus `tsc -b`/`vite build` clean, and the
    live two-org/real-`customer_poc` HTTP walkthrough itself (login → TOTP
    enroll-free verify via a pre-seeded secret → open → cross-org 403s →
    attest → auto-close → re-verified negative-permission checks) —
    re-run end to end after both fixes above to confirm.
  - **Deliberately left open:** the daily Liongard sync (D.3's second
    half, see its own Deferred entry); a third review subject type
    (processes) — the schema (`subject_type` CHECK) leaves room to add
    one later without a redesign, but none is added here per the task's
    own scope; no UI for re-opening a `closed_unattested` cycle early.

- **Fix: a review cycle could record `no_response` for a reviewer who was
  never notified** (2026-09-14) — found by **a live deployment, not a
  test**: wl-util-1 opened a real cycle for Acme MSP with no SMTP
  credential and no `WINGRC_PUBLIC_URL` configured, and the fully
  automated suite (986 tests, including the live two-org HTTP walkthrough
  above) never caught that its two reviewers would close as
  `no_response` at the due date — asserting two named people failed to
  respond to a request neither could have received. Same class of
  violation this file already holds the line on elsewhere (assessment
  completion creating no `sprs_submission` row): the system must never
  assert a human action occurred. Worth recording as its own data point —
  this is now the **second** review-cycle correctness bug this feature
  needed a live box to surface, after the `get_cycle` mid-handler-commit
  bug above; the automated suite, however thorough, keeps missing the
  same category (real infrastructure absence/failure) that only a real
  deployment exercises.
  - **Root cause**: `email_service.send()`'s result was discarded
    entirely at cycle-open time and consulted only for reminder-log
    idempotency at sweep time — never persisted anywhere a reviewer's
    delivery outcome could be read back. `close_cycle` had no way to
    distinguish "asked, didn't answer" from "never asked."
  - **Fix**: `ReviewCycleReviewer` gains `notified_at`/`notification_error`
    (migration 0047, additive only — NULL is already the correct value
    for every pre-existing row, no backfill needed or performed).
    `close_cycle` now stamps a still-open reviewer `no_response` only if
    `notified_at` is set, else the new `not_notified` terminal status;
    if **no** reviewer on the cycle was ever notified, the cycle's own
    close status is upgraded from `closed_unattested` to a new
    `closed_undeliverable` — that wording no longer claims a review was
    attempted when none could be. A cycle with a genuine mix (one
    reviewer reached, one not) stays `closed_unattested`, since a review
    plainly was attempted; only the fully-unreachable case gets the new
    status. Two independent failure modes existed and both had to be
    fixed together, per the task's own instruction not to fix one and
    leave the other producing the same false record: a missing
    `WINGRC_PUBLIC_URL` (previously silently substituted a placeholder
    link like "the WinGRC application" and still counted as sent) now
    gates exactly like `routers/users.py`'s pre-existing invite-email
    precedent, and an `email_service.send()` failure — both unified
    behind one new `scheduler._notify_reviewer` path.
  - **The live-box gap explicitly closed**: `review_cycle_sweep` now
    retries the *initial* notification for any never-yet-reached reviewer
    on **every** tick, on any open cycle regardless of when it was
    opened — not just cycles opened in that same tick. This means the
    exact cycle sitting open on wl-util-1 right now needs no special
    handling: once SMTP/`WINGRC_PUBLIC_URL` are configured, the very next
    daily sweep notifies its two reviewers automatically. Reminder timing
    (`due_reminder_number`) now runs from `notified_at` instead of
    `requested_at`, since timing a reminder from a request that was never
    delivered doesn't make sense — a real behavior change, caught by a
    bug of its own (below).
  - **A second bug, caught on bench mid-fix**: the first version of
    `record_notification_result` set `notified_at` on *every* successful
    send, not just the first. A successful reminder send therefore pushed
    the day-7/day-14 window forward to "now," which
    `test_second_reminder_sent_at_day_14` (already in the suite,
    unmodified) immediately caught: only 1 of 2 expected reminders fired
    in one tick, because the loop's second `due_reminder_number` call saw
    a freshly-reset clock. Fixed by only setting `notified_at` when it
    was previously `None`. A smaller test-fixture bug (`sent_emails`
    needed a `.results` queue attribute a plain `list` can't carry — no
    `__dict__`) was also caught the same way, before ever reaching bench
    pytest.
  - **The revision id itself was too long** — `0047_review_cycle_
    notification_tracking` (40 chars) exceeds `alembic_version.
    version_num`'s 32-char column width, caught by the existing
    `test_migrations.py::test_revision_ids_fit_alembic_version_column`
    locally before ever reaching bench. Renamed to
    `0047_review_cycle_notify`.
  - **The live cycle on wl-util-1 (`b327e64c`, Acme MSP) was
    deliberately left untouched** — not deleted (a legitimate record of a
    correctly-opened cycle) and not data-migrated (its reviewers'
    `notified_at`/`notification_error` are `NULL` under the new columns,
    which is already the true state: neither was ever successfully
    notified). Recommendation, not yet acted on: if SMTP is configured
    before its 2026-10-04 due date, the next daily sweep will notify both
    reviewers automatically per the fix above, and the cycle becomes an
    ordinary one; if the due date passes first, it will now correctly
    close `closed_undeliverable` with both reviewers `not_notified`,
    instead of the false `no_response` it would have recorded before this
    fix. This deploy decision is Jarrod's, not made here.
  - **Verified 2026-09-14 on wl-util-1, bench:** an isolated
    `docker compose -p wingrc_review_cycle_notify` project (fresh clone,
    separate network/volumes, the live `wingrc` project on that box never
    touched, and the live cycle above never read or written by this
    verification) — **990/990 backend tests** (4 new: undeliverable
    close, mixed-outcome close, distinguishable error messages, and the
    later-notified-once-reachable scenario matching the live box), `ruff
    check .` clean, frontend **99/99** vitest (15 files, 2 new tests) plus
    `tsc -b`/`vite build` clean. Migration 0047 confirmed applying
    cleanly from `0046` and the widened CHECK constraints/new columns
    confirmed directly in Postgres, not just inferred from migration
    source.

- **Outbound email — verified against a real provider (SMTP2GO,
  2026-09-13), plus config fields made self-describing and
  test-connection gained an optional real send** (2026-09-14) — the
  outbound-email slice's own caveat ("no real SMTP provider was
  exercised") is now out of date; see that Done entry's own superseded
  note above rather than duplicating it here. What this closes: Jarrod
  configured SMTP2GO for real, hit a genuine bug, and it's fixed now, not
  just documented as a known gap.
  - **What actually happened:** `encryption_mode: tls` against SMTP2GO's
    port 2525 failed immediately with `[SSL: WRONG_VERSION_NUMBER] wrong
    version number` — port 2525 is SMTP2GO's STARTTLS alternate
    (plaintext-then-upgrade), not implicit TLS. Switching to
    `encryption_mode: starttls` fixed it immediately. The error message
    itself was diagnosable in one line (this codebase's connect/TLS/AUTH
    error discipline, tested in `test_smtp_connector.py`, worked exactly
    as designed) — the **form** was the defect: `encryption_mode` was a
    bare text input, so nothing explained the three modes at the moment
    the choice was made, even though `help_text` already did, in a
    paragraph above the fields nobody was reading when they typed the
    value in.
  - **Fix, Part 1 — `ConnectorSpec.config_fields` generalized** from
    `tuple[str, ...]` (names only) to `tuple[ConfigField, ...]`
    (`connectors/__init__.py`): a label, a type
    (text/number/select/boolean), options with human labels for selects,
    optional help text, and required-ness. Deliberately a small,
    closed generalization, not a form framework — no validation rules,
    no conditional visibility. `encryption_mode` is now a labeled select
    naming what each mode does and its typical port(s) ("STARTTLS —
    upgrade after connecting (ports 587, 2525, 8025)", etc.);
    `verify_cert` is now a checkbox, not a text field someone could type
    `"false"` into. Port stays free-form text on purpose — SMTP2GO's own
    alternate ports are exactly why a port dropdown would recreate this
    same bug in a different field. Picking an encryption mode
    **suggests** the matching standard port via
    `ConfigFieldOption.suggests` (a field-name → value map, applied only
    when the target field is still blank) — never overwrites an existing
    value, so someone deliberately on a nonstandard port keeps it.
    Liongard migrated to the same descriptor shape with no behavior
    change (one required text field renders identically to the old
    name-only tuple) — the registry contract ("add a module + one
    registry entry," router/screen untouched) holds for both connectors.
  - **Fix, Part 2 — test-connection can send a real message.**
    `TestConnectionFn` gained one optional, keyword-defaulted
    `test_input: str | None` parameter (SMTP: a recipient address;
    Liongard: unused, ignored, its test still takes no input) — a small,
    general extension, not an SMTP-shaped one. With no recipient, the
    connect-only behavior is byte-for-byte unchanged (existing tests
    caught this immediately if it wasn't). With one, `connectors/smtp.py`
    sends a trivially simple message (no compliance content, no tokens,
    no functional links) and reports outcomes deliberately distinctly: a
    `MAIL FROM`/`RCPT TO`/DATA rejection (`SmtpSendError`) is reported as
    a different operator problem from a connection or auth failure — most
    often an unverified sending domain (SPF/DKIM), which the task called
    "the most valuable thing this feature can surface," and which
    `docs/email-setup.md` covers in depth. Success is reported as
    **"accepted by the provider — check the inbox to confirm delivery,"**
    never "sent successfully" — a `250` is proof of acceptance, not
    delivery. The recipient is never defaulted, prefilled (not even from
    the configured From Address), or remembered between test runs — a
    test click must never send mail by accident — and every test send
    (recipient non-blank) is audit-logged (`integration_connection.test`,
    `after_value.test_recipient` added only when a send was attempted;
    actor stamped automatically like every other event in this router).
  - **`docs/email-setup.md`** (new) — encryption-mode/port guidance
    leading with the `WRONG_VERSION_NUMBER` error text verbatim so it's
    findable by search, a generic-domain SMTP2GO worked example (never
    Jarrod's real sending address), `WINGRC_PUBLIC_URL` documented
    immediately next to the SMTP settings (wl-util-1 hit this
    independently, before SMTP was even configured — see the periodic-
    review-workflow entries above for that finding), credential-storage
    cross-reference to `.env.example`'s key-custody guidance, and —
    flagged as the part most likely to be missed — that a passing
    connection test never proves deliverability; SPF/DKIM domain
    verification is a separate, provider-side step, and the only real
    proof is a test message landing in an inbox you control. Added to
    item O's (docs.wingrc.us) planned-content list as item 4, alongside
    the credential-encryption-key-custody content already queued there.
  - **Verified 2026-09-14, bench (wl-util-1):** an isolated
    `docker compose -p wingrc_integrations_email` project (fresh clone,
    separate network/volumes, the live `wingrc` project — including
    Jarrod's real, working SMTP2GO credential — never touched or
    re-entered) — **1002/1002 backend tests** (11 new: six on
    `connectors/smtp.py`'s send-message path directly — no test_input
    unchanged, a real send accepted with the honest "check the inbox"
    wording, sender/recipient rejection reported distinctly from a
    connection failure — plus five at the router level covering
    recipient passthrough, the pre-existing no-body call shape staying
    intact, and audit-log content), `ruff check .` clean, frontend
    **108/108** vitest (16 files, 9 new: control-type rendering for both
    connectors, the port-suggestion applying only when blank and never
    overwriting an existing value, the recipient field present for SMTP
    and absent for Liongard, never prefilled, and cleared after every
    test run regardless of outcome) plus `tsc -b`/`vite build` clean. The
    old-shape-config compatibility claim above was checked directly, not
    assumed: a row constructed exactly as one written before this change
    would have been (plain `{field_name: value}` JSON, credential
    encrypted the same way) loads and displays correctly through the new
    `_out()`/`ConfigFieldOut` path with no re-entry and no migration.

- **Azure Government hosting feasibility research** (2026-09-14) —
  `docs/azure-government-hosting-feasibility.md`, prompted by production
  needing to split from wl-util-1 (the periodic-review workflow means
  real client contacts will soon create real evidence records, and that
  can't live on a box that deploys unreleased code and carries
  `reset-dev`) and Jarrod wanting Azure Government specifically for that
  production instance. Research and documentation only — no Azure
  resources, credentials, or code. Explicitly **not** a compliance
  requirement — [ADR 0005](adr/0005-deployment-topology-per-msp-not-shared-saas.md)
  and `cloud-hosting-options.md` both already concluded WinGRC's own
  data doesn't formally require it; this is a business choice for
  defense-sector client expectations, stated plainly in the doc so it
  doesn't get misread later as a compliance claim.
  - **The two potential blockers, resolved.** PostgreSQL: Flexible
    Server is fully authorized in Azure Government through DoD IL5WI per
    Microsoft's own compliance-scope table (confirmed 2026-08-18) and
    the Gov GA roadmap (confirmed 2026-09-03) — not a blocker. A direct
    grep of the schema/migrations found nothing requiring PostgreSQL 18
    specifically (`gen_random_uuid()`, the only version-sensitive call
    in use, only needs 13+; `pgvector` is mentioned in `CLAUDE.md`'s
    stack summary but has zero actual uses in the codebase), so even a
    Gov version lag costs nothing. **Container Apps, by contrast, is a
    real, current gap**, not a stale caveat: absent entirely from the
    2026-09-03 Gov GA roadmap, authorized only through DoD IL2 on the
    compliance table (PostgreSQL reaches IL5WI on the same table), and
    reported still Public Preview / US Gov Virginia-only as of a
    2026-06-16 Microsoft GitHub thread with an unanswered open support
    case. This directly supersedes `azure-container-apps-deployment-
    plan.md`'s "confirmed directly in a real Azure Government
    subscription" claim — that plan doc now cross-references the new
    research and recommends plain VMs/App Service (both fully GA through
    IL5/IL6) as the Gov fallback if Container Apps' status doesn't firm
    up before a real deployment.
  - **A platform-wide finding that isn't Gov-specific but affects this
    plan regardless:** Azure retired default outbound internet access
    for newly-created VNets on 2026-03-31 (already past) — any new
    deployment, commercial or Gov, now needs explicit egress (a NAT
    Gateway or equivalent) just to reach Liongard or SMTP2GO at all. The
    existing Container Apps plan predates this and doesn't budget for
    it.
  - **Confirmed, not assumed:** Blob Storage SAS URLs differ from MinIO
    presigned URLs only in hostname (`*.usgovcloudapi.net` vs.
    commercial), not in the bearer-token security property — moving to
    Blob Storage would **not** have closed the evidence-download-
    hardening gap on its own. **Shipped 2026-09-14 — no longer a gap or
    a deferred item; see this file's own Done section entry.** Also
    confirmed at the time of this research: the Azure Blob
    Storage code path (`backend/app/storage.py`) is **not implemented**
    today — only `NullStorageClient`/`MinIOClient` exist — contradicting
    both `cloud-hosting-options.md`'s and the Container Apps plan's
    "zero code change" framing for that swap.
  - **Left as an explicit, undecided question for Jarrod, not designed
    around:** what an AI coding assistant with shell access (today's
    operating model for wl-util-1) is and isn't allowed to touch once
    real Azure Government credentials and real client evidence exist —
    Azure Government's US-person requirement and handling expectations
    make this a deliberate choice to make before any Gov resource
    exists, not an assumption to carry forward from how development
    works today.
  - **Honest gaps, stated as gaps:** no sourced Gov-vs-commercial cost
    premium found (pricing calculators render client-side; a real
    estimate still needs sizing inputs that don't exist yet, same
    conclusion `cloud-hosting-options.md` already reached); ACR's exact
    Government hostname suffix and whether Container Apps' managed-
    certificate feature works in Gov at all were not independently
    confirmed from primary sources.

- **Document-ingestion engine wired into the Tools baseline library** (2026-09-13) —
  `backend/app/importers/document.py`'s AI extraction pipeline (previously a
  complete, tested, but uncalled module — it produced `rocketcyber.yaml` via a
  throwaway script, not a real integration) now has a real caller: a new
  `POST /admin/products/import/from-documents` endpoint
  (`routers/admin_products.py`) that runs it against 1-2 uploaded vendor
  documents and feeds the result through the **existing** dry-run/apply
  review path (`baseline_import.py`) — no second ingest path was built.
  `ToolImportWizard.tsx` gained a mode toggle: upload a finished YAML (as
  before), or generate a candidate from vendor documents into an editable
  textarea, re-check via the same dry-run call, then apply exactly like a
  hand-authored file.
  - **`coverage_basis` has no home in the AI output, on purpose.** This
    `BaselineControl` field (`customer_system` | `platform_only` | `assists`)
    answers whether a vendor's coverage credits the customer's CUI systems or
    only the vendor's own platform — CLAUDE.md's hard rules treat these very
    differently (`platform_only` is excluded from the magic loop), and
    nothing in a vendor CRM/baseline doc reliably distinguishes the two
    without a human who knows the actual deployment. Resolved via explicit
    decision (Jarrod, this session): the ingestion pipeline
    (`baseline.py:ControlEntry.coverage_basis`) always leaves it `None`;
    `baseline_import.py`'s `validate()` was tightened to require it
    explicitly set for every `provider_satisfies`/`shared` entry (still
    defaulted for `customer_owns`, where it's moot — evidence-minimization
    already zeroes everything else out for those) — the same "no silent
    default" treatment `classification` already got. An AI-generated
    candidate's dry-run preview will always report this as a problem until
    a reviewer fills it in; this is the intended gate, not a bug to fix
    later.
  - **The "lands unpublished" requirement was already satisfied by reuse,
    not new code.** `baseline_import.apply_import()` always calls
    `_seed_product(..., reset_published=True)` — true for a hand-authored
    re-import already, so an AI-sourced one gets the same guarantee for
    free by going through the identical write path. No separate flag or
    "trusted source" bypass exists or was considered.
  - **Permanent AI-provenance**, mirroring the `practitioner_notes_
    generated_at`/`_model` precedent (never a dismissible status): new
    `Product.ai_generated_at` / `ai_generated_model` columns (migration
    `0048_product_ai_provenance`), set once at apply time and never cleared
    by a later re-import that omits them (`seeds/baselines.py:_seed_product`).
    Surfaced permanently in the Tools detail view (`ToolDetailPanel.tsx`) as
    a `drawer-ai-caveat` banner, styled identically to the practitioner-notes
    AI caveat in `ControlDrawer.tsx`, and in the raw YAML/`ProductDetailOut`
    API response.
  - **AI provider stayed env-var based, not moved into the connector
    registry** — explicit decision (Jarrod, this session) after scoping what
    a real registry migration would need (an explicit-key `AnthropicProvider`
    constructor instead of the SDK's implicit env read, a new paid
    test-completion action, and a decision about whether `Settings.ai_provider`
    keeps existing or is replaced by "is there an `integration_connection`
    row for key=ai"). Tracked as a separate follow-up, not built here. Fixed
    a small pre-existing honesty gap while here: `config.py`'s comment and
    `NullProvider`'s error message both referenced `azure_openai`/`local` as
    if configurable, though `get_ai_provider()`'s registry only ever
    supported `none`/`anthropic` — corrected the text, not implemented the
    providers.
  - **Cost/size guards added to the pipeline itself** (not the prompt,
    which stayed untouched per this task's own scope limit):
    `importers/document.py` gained a `DocumentIngestError` exception, a
    600k-character pre-flight input guard (roughly 150k tokens, comfortably
    under a 200k-token context window with headroom for the prompt and the
    model's own reasoning), a raised `max_tokens` (8192 → 16384) for this
    specific call since a comprehensive CRM can plausibly exceed the
    AIProvider default sized for shorter completions, and normalization of
    both a truncation-suspected `JSONDecodeError` and `ai_provider="none"`'s
    plain `RuntimeError` into that one exception type — the router catches
    it once and returns a clear 422, never a raw 500.
  - **§4's "surface the model's uncertainty" (a per-control supporting quote
    or section reference a reviewer could check without re-reading the whole
    source document) was deliberately not built.** It would require adding a
    new field to the AI JSON output schema, which requires editing
    `_SYSTEM_PROMPT` — directly in conflict with this task's own explicit
    "don't rewrite or improve the extraction prompt" exclusion (prompt
    quality is its own task with its own evaluation). Specified here as a
    well-defined follow-up rather than half-built: add a `support: string`
    field per control entry in the JSON schema (a quoted span or section
    reference from the source doc), render it in the review textarea/diff as
    a collapsible citation, not a confidence score — the framing throughout
    should help verification, never invite skipping it.
  - **Fetching vendor documentation from a URL instead of an upload was
    explicitly out of scope**, per the task's own instruction — noted here
    as a real follow-up with its own design problem (SSRF, auth-walled
    pages, content drift between fetch time and review time), not started.
  - **Distinct from the "AI implementation statements" roadmap item below**
    (per-objective draft statements) even though both consume the same BYO-AI
    provider abstraction — this item is baseline-*library* ingestion
    (vendor CRM/doc → candidate product mapping), not per-objective narrative
    generation. Keep the two separate on a future pass, same caution already
    recorded on that item's own Done-adjacent note above.
  - **Verification:** new/extended tests, not a rewrite of the existing
    suite — `test_document_ingest.py` gained coverage for `coverage_basis`'s
    round-trip (present when set, omitted when not), the AI pipeline never
    setting it, the size guard, the `max_tokens` override, and
    `DocumentIngestError` normalization for a malformed response, a
    wrong-shape response, and `ai_provider="none"`; `test_admin_products.py`
    gained coverage for the tightened `coverage_basis` validation and the
    new endpoint end-to-end (AI-provider-unconfigured clean 422, generate →
    reviewer fills in `coverage_basis` → apply → unpublished and invisible to
    a tenant via the real products-list endpoint → provenance visible on the
    real detail endpoint), plus the new role-gate and bad-upload cases.
    `ToolImportWizard.test.tsx` and `ToolsLibraryPanel.test.tsx` gained
    matching frontend coverage (generate/edit/re-check flow, document
    re-attachment after apply, the provenance banner rendering only when
    set).
  - **Bench-verified 2026-09-14**, together with the evidence-download-
    hardening slice below (`wingrc_verify_20260914`, wl-util-1, real
    Postgres + MinIO) — both slices landed on `main` without bench
    verification when they were first built (no SSH/Docker access in
    those sessions), touch overlapping files (`admin_products.py`,
    `storage.py`), and were verified together in one pass once access was
    resolved; see the evidence-download-hardening entry below for the
    full stack details and the three bugs this pass found and fixed
    (none caught by any local-only run — exactly why bench verification
    happens before merge, not after, going forward). Two gaps this task
    explicitly called out with no
    dedicated test yet were closed during this pass and are now covered:
    `test_ai_provenance_survives_a_later_hand_authored_reimport` (a
    plain hand-authored re-import of an AI-generated product does not
    clear `ai_generated_at`/`ai_generated_model`, checked against the
    real write path — `seeds/baselines.py:_seed_product` — not just the
    domain type) and
    `test_ingest_from_documents_reimport_warns_on_affected_orgs`
    (re-running `/import/from-documents` against a product with active
    tenants shows the same affected-org warning a hand-authored
    re-import does, through the identical dry-run path — no second,
    silently-more-permissive ingest path exists). `wingrc seed-catalog`
    then `wingrc seed-baselines` run for real against this bench
    Postgres: 110 controls / 320 objectives seeded, then
    `rocketcyber.yaml` seeded cleanly (1 product, 27 baseline controls,
    12 evidence specs, no missing-control warnings), and a second
    `seed-baselines` run confirmed idempotent (identical counts, no
    errors) — with the new `coverage_basis`/provenance code active, not
    the pre-slice version. Full backend suite: **1039 passed** (up from
    the 187 local-only run — see the evidence-download-hardening entry
    for the three real bugs this pass found and fixed, none caught by
    any local run). Frontend `tsc -b`/`vitest` (115/115)/`vite build` all
    clean via a throwaway `node:24-alpine` container.
  - Reproducing `rocketcyber.yaml` from its two named source documents
    (`RocketCyber_SIEM_and_SOC_Baseline.docx`, the RocketCyber Customer
    Responsibility Matrix doc) was not attempted — neither file is present
    in this repository or its `baselines/` directory, only the already-
    hand-corrected YAML output is.

- **Evidence download hardening** (2026-09-14) — moved from Deferred (see
  that section's now-struck entry). Presigned direct-to-MinIO download
  URLs are gone for every `Evidence` row; `routers/evidence.py:
  download_evidence` now streams the object's bytes through the backend
  itself, so every download re-checks the requester's session, MFA/
  lockout state, and org membership per request — a presigned URL was a
  bearer credential good until it expired, downloadable by anyone who
  obtained the link, with no per-request re-check of anything and no
  audit trail. Azure Blob's SAS URLs would have had the identical
  property (confirmed in the Azure Gov feasibility research), so this had
  to be an access-path fix, not a storage-backend one.
  - **Mechanism:** `StorageClient` gains `stream_bytes()` (chunked
    iterator, default 256 KB) alongside the existing `get_bytes()` —
    `get_bytes()` is untouched and still what `bundle_service.py` uses
    (a bounded, deliberate whole-object read, not a hot path).
    `download_evidence` passes a plain sync iterator straight into a
    Starlette `StreamingResponse`; Starlette wraps a non-async iterator in
    `iterate_in_threadpool`, dispatching each `next()` call (one chunk
    read) through anyio's worker threadpool individually rather than
    pinning one worker for the whole transfer — the endpoint function
    itself stays a normal sync `def`, matching every other route in this
    codebase, no special-casing needed. `MinIOClient.stream_bytes()` opens
    the object with one blocking `get_object()` call (same threadpool-
    dispatched request, no different from any other blocking storage call
    already in this codebase) and returns botocore's own
    `Body.iter_chunks()` — bytes are read from the socket on demand, never
    materialized as one blob.
  - **Ownership check precedes any storage call, verified directly:** the
    router-wide `require_org_access()`/`require_write()` dependencies
    (already on this router, unchanged) confirm the caller belongs to the
    `org_id` in the URL; `download_evidence`'s own `ev.org_id != org_id`
    check then catches a guessed `evidence_id` belonging to a *different*
    org than the one in the URL — org_access alone can't catch that,
    since it only knows about `org_id`, not which rows are actually that
    org's. A storage-call spy in `test_evidence_api.py` proves zero
    storage methods run when this check fails.
  - **`coverage_basis`-style explicit decision, not silently inferred:**
    `StorageClient` gains `is_configured()` (`True` by default, `False`
    only for `NullStorageClient`) so "no storage backend at all" can be
    told apart from "empty bytes/iterator for a real zero-byte object" —
    `get_bytes()`/`stream_bytes()` legitimately return empty for the
    latter, and inferring "not configured" from that would have been
    wrong. `download_evidence` 404s with "Storage not configured" via this
    check, before ever calling `stream_bytes()`.
  - **Every `presigned_url()` caller was found and decided individually
    (not just the two the task named up front),** per that method's own
    now-narrowed docstring:
    - **Evidence file downloads** (`routers/evidence.py`, 4 `EvidenceOut`
      construction sites + the download route itself) — moved, the core
      ask.
    - **System-description network/data-flow diagrams**
      (`routers/orgs.py`'s `_diagram_url`) — moved too, on the reasoning
      that these are `Evidence` rows (`kind='file'`) sharing the exact
      same storage-key convention and CUI-boundary sensitivity as any
      other evidence, just displayed via `<img src>` instead of a
      download link — treating them as a separate "other caller" to
      individually weigh would have missed that they're not actually
      different from evidence at all. `_diagram_url` no longer touches
      storage or takes a `StorageClient` argument — it just builds the
      same `evidence_download_path()` string evidence responses do; the
      bytes are fetched per-request when the `<img>` tag's browser
      request actually resolves it. Deliberately **not** audit-logged
      specially for this inline-view case — see the audit-volume
      reasoning below for why that's not a firehose concern here, unlike
      it would be for, say, a list of thumbnails.
    - **Org logo** (`routers/orgs.py`'s `_build_profile_out`/
      `upload_logo`) — kept on `presigned_url()`, deliberately. Not an
      `Evidence` row, not customer CUI, purely decorative branding
      fetched via `<img>` — `presigned_url()`'s docstring now says
      explicitly that this is the one caller it's still for, and why
      (routing a logo through the backend for consistency alone would
      just be extra API traffic for a non-sensitive asset with no
      security benefit).
    - **`ProductDocument` downloads** (`routers/admin_products.py`'s
      `download_document`, vendor baseline-library documents) —
      deliberately **left presigned, flagged as a follow-up**, not
      silently inconsistent: a code comment at the call site and this
      entry both say why (deployment-wide MSP-internal vendor
      documentation, msp_admin/consultant_admin only, not customer CUI
      evidence, and a genuinely different model/router than what this
      task's own title and gap description scoped to). Same bearer-URL
      property remains there until a dedicated follow-up closes it — the
      fix here (a `stream_bytes()`-backed route) is now a known, small
      shape to copy.
  - **Audit trail — the capability this slice was explicitly framed as
    also gaining:** every real download fires one `audit_log` row
    (`action="evidence.download"`, `after_value={title, artifact_type}`,
    never the bytes) — `audit.py`'s docstring event list updated to
    match, including why it also fires for a diagram `<img>` view (a page
    view rendering the org's *one* network diagram and *one* data-flow
    diagram is itself meaningful "who looked at the CUI boundary and
    when" signal, not noise the way re-logging every thumbnail on a
    crowded list view would be) and why it deliberately does **not** fire
    for upload/list/collect responses that merely *include* a
    `download_url` (building that string touches no storage and grants no
    access; only a real GET against it does) or for bundle export's
    evidence embedding (a different code path entirely —
    `bundle_service.py` still calls `storage.get_bytes()` directly,
    already covered by its own single `bundle.export` entry rather than
    one row per embedded file).
  - **HTTP Range support: deliberately not built.** Evidence uploads are
    capped at 50 MB (`routers/evidence.py`'s existing `_MAX_FILE_BYTES`,
    unchanged) — realistically screenshots and PDFs, not large media —
    so resumable/partial downloads aren't a real need this schema's data
    shape creates; adding Range parsing would have been scope creep
    against a problem this deployment doesn't have. Revisit only if a
    future evidence type meaningfully raises that cap.
  - **§4 performance measurement — run 2026-09-14 on the bench stack
    (`wingrc_verify_20260914`, wl-util-1, real uvicorn + Postgres 18 +
    MinIO, single worker, 4 vCPU / ~5 GiB host).**
    `scripts/one-off/bench_evidence_download_20260914.py`, concurrency
    1/10/50, 60 downloads per level, **10 MB PDF** (evidence caps at
    50 MB; this is a realistic upper-middle size for that range, not the
    cap itself), stdlib `ThreadPoolExecutor` client, a concurrent
    `/health` watcher throughout:

    | concurrency | download_evidence (10 MB) | baseline (`GET /orgs`, trivial payload) |
    |---|---|---|
    | 1 | p50 29.1ms / p95 31.5ms / p99 33.1ms | p50 4.6ms / p95 5.4ms / p99 14.4ms |
    | 10 | p50 250.5ms / p95 282.1ms / p99 291.9ms | p50 50.8ms / p95 65.7ms / p99 70.6ms |
    | 50 | p50 1287.5ms / p95 1401.3ms / p99 1410.9ms | p50 567.4ms / p95 653.4ms / p99 661.9ms |

    **Zero errors and zero `/health` failures at every level** — no
    repeat of the project's own concurrency-50 event-loop-starvation
    incident (`get_current_user`, above): the container stayed healthy
    and responsive throughout, at both baseline and download load. The
    download endpoint's p50 at concurrency 50 (1287.5ms) is roughly 2.3x
    the trivial-endpoint baseline at the same concurrency (567.4ms) —
    the *incremental* cost is streaming 10 MB rather than a small JSON
    payload, not a qualitatively different failure mode; both curves grow
    the same shape (DB-pool contention at 40 connections under 50
    concurrent requests, the same effect the `get_current_user` benchmark
    already characterized), the download simply adds real transfer time
    on top. Backend process memory stayed at 358 MiB (`docker stats`)
    after cumulative 1.8 GB transferred across all three levels combined
    (60+60+60 downloads × 10 MB) — well below what buffering even one
    concurrency-50 burst (500 MB at once) would require if
    `stream_bytes()` weren't actually streaming. **Conclusion: no
    meaningful degradation at this file size and concurrency — the
    chunk-at-a-time `iterate_in_threadpool` design holds up under
    measurement, not just the design argument.** Revisit only if evidence
    file sizes or concurrent-download volume grow materially past what
    this test exercised.
  - **Verification status otherwise:** `test_evidence_api.py` extended
    heavily — streamed-byte correctness (content, Content-Type,
    Content-Disposition, Content-Length), no presigned URL issued
    anywhere for evidence (grepped from real responses, not the UI),
    cross-org `evidence_id` unreachable even with real membership on the
    URL's own org, ownership-check-precedes-storage-call via a call spy,
    a real (non-bypassed) session's idle-timeout and a deactivated
    account's session both correctly 401/403 the download route
    specifically (mirroring `test_session_idle.py`'s own methodology,
    not the `_authed` bypass every other test here uses), storage-not-
    configured 404, a large-file streaming test whose `get_bytes()`
    raises if ever called (proving the chunked path is what's actually
    used), and audit-log presence/absence exactly where expected.
    `test_diagram_upload.py` and `test_org_access_guard.py` extended to
    match. `test_storage.py` covers the new `StorageClient` surface
    (`evidence_download_path`, `is_configured()`/`stream_bytes()`
    defaults) as plain unit tests, no DB needed. Frontend:
    `frontend/src/api.ts` gains `assetUrl()` (prepends the `/api` mount
    prefix onto a bare backend-returned path, since the backend has no
    business knowing about that nginx/Vite-dev-proxy detail — leaves an
    already-absolute presigned URL, i.e. the logo, untouched), with its
    own `api.test.ts`; `EvidenceSection.tsx`'s download link and
    `SystemDescriptionForm.tsx`'s two diagram `<img>` tags route through
    it.
  - **Bench-verified 2026-09-14 on `wingrc_verify_20260914` (wl-util-1),
    real Postgres 18 + MinIO, not just local unit runs:** full backend
    suite **1039 passed** (was 1037 at first run — see the three bugs
    below; two more slice-specific tests added and passing, see the
    document-ingestion entry's own bench-verification note), `ruff
    check .` clean in-container. Frontend via a throwaway `node:24-alpine`
    container running the repo's own scripts unmodified: `tsc -b` clean,
    **115/115 vitest** passed across 17 files (including this slice's new
    `api.test.ts`, `ToolImportWizard.test.tsx`, `ToolsLibraryPanel.test.tsx`),
    `npm run build` (`tsc -b && vite build`) clean, 444 KB JS bundle
    (120 KB gzip).
  - **Three real bugs found and fixed, none of which any local run
    caught** (this is exactly why the standing bench-verify-before-merge
    rule exists — see this file's top-of-Done note on the two slices that
    landed unverified):
    1. **`backend/Dockerfile` never installed `pyproject.toml`'s `ai`
       extras group** (`anthropic`/`pypdf`/`python-docx`) — only `pip
       install ".[dev]"`, never `".[dev,ai]"`. Since `extract_text()`
       calls `pypdf`/`python-docx` *before* the configured AI provider is
       ever consulted, the entire document-ingestion endpoint 500'd on
       every real upload in any container built from this Dockerfile —
       dev, bench, or a real deployment — regardless of
       `WINGRC_AI_PROVIDER`. Never caught locally because every local
       test patches `extract_text()` directly rather than installing
       real parsing libraries. This image had literally never run
       `ingest_document()` before this bench session — the committed
       `rocketcyber.yaml` was produced by a throwaway script outside any
       container. Fixed: `pip install ".[dev,ai]"`. Installing the
       `anthropic` SDK unconditionally is safe for air-gapped/CUI-
       sensitive deployments too — inert unless `WINGRC_AI_PROVIDER=
       anthropic` is actually set, nothing dials out at import time.
    2. **Two new `test_evidence_api.py` tests constructed `Evidence(...)`
       rows directly via the ORM without setting `collected_at`**
       (`test_download_cross_org_evidence_id_unreachable`,
       `_seed_real_user_with_evidence`) — that column has no default at
       any level (checked: no Python-side `default=`, no
       `server_default=`), only ever set by production code paths
       explicitly. Every pre-existing direct-`Evidence()` test
       construction elsewhere in the suite already sets it; these two,
       new this slice, didn't. `NotNullViolation` only surfaces against a
       real Postgres `INSERT` — invisible without a DB. Fixed: both now
       pass `collected_at=datetime.now(UTC)`.
    3. **`test_ingest_from_documents_requires_ai_provider_configured`
       never patched `extract_text()`**, on the theory that
       `ai_provider="none"` would short-circuit before any document
       parsing happened — it doesn't; extraction always runs first. With
       real `pypdf` now actually installed (bug 1's fix), the test's
       `_fake_pdf_bytes()` (not real PDF structure) failed real parsing
       instead of failing at import, changing the error shape. Fixed: now
       patches `extract_text` like every sibling ingestion test.
  - All three fixed forward on `main` (commits after this slice's
    original push), each re-verified on the bench stack before pushing:
    `1037→1039 passed` reflects two *new* slice-specific tests added
    during this verification pass (see the document-ingestion entry),
    not a regression count.

---

## Planned

### N. Document Library

Two new tables (a new migration — 0011 through 0015 are already in use by other shipped features, see Done above). Prerequisite: M (for `approved_by_contact_id` FK). **Verified 2026-09-07: this prerequisite is satisfied** — `Contact` (`models.py`) has existed since the Onboarding Wizard shipped (migrations 0011–0013, see Done above), well before this item was written. No remaining data-model blocker for N specifically; it's just unstarted.

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
3. **Credential encryption key — generation, custody and rotation.** Added 2026-09-11 (Jarrod). `.env.example` already documents *how* to generate one (`Fernet.generate_key()`), but nothing documents the operational consequences, and a new operator will not infer them. This must be part of **initial setup**, not a footnote — the key has to exist and be recorded before anyone enters their first connector credential, because after that point it is load-bearing. Cover:
   - **It is per-deployment.** Every WinGRC instance generates and holds its own key. Nothing is shared between installs; spinning up a new instance means generating and recording a new key.
   - **It never lives in the database, by design.** That separation is the security property — a stolen database dump is useless without the key. The corollary operators miss: **a database backup alone cannot restore a working system.** Backup runbooks must record where the key is kept, or a restore produces rows of undecryptable ciphertext.
   - **Loss is unrecoverable.** There is no reset, no backdoor, no vendor recovery. The only remedy is re-entering every stored credential by hand. Say this plainly rather than softening it.
   - **Where to keep it** — the operator's password manager or secrets store, alongside their Postgres/MinIO credentials, not solely in `.env` on a machine that may be rebuilt.
   - **Store the whole value including the label** (`label:key`), not just the base64 material — each encrypted row records which label encrypted it.
   - **Rotation exists and is proven**: `wingrc rotate-credential-keys` (dry-run by default, pre-flight `pg_dump`, whole-run fail-closed). Document the ordering, because that is where rotations go wrong: add the new key alongside the old → verify decryption → rotate → verify the new label everywhere → only then remove the old key. Reference the worked rehearsal from 2026-09-11.
   - **When to rotate**: on suspected exposure (the 2026-09-11 rotation was triggered by a key appearing in a chat transcript), on operator turnover, or periodically by the operator's own policy.
4. **Email setup (outbound SMTP)**, added 2026-09-14 after Jarrod's live SMTP2GO configuration against the real service (see this file's own Done entry, "Outbound email — verified against a real provider"). Encryption-mode/port guidance (STARTTLS vs. implicit TLS vs. none, and why the port must match), a SMTP2GO worked example, the `WINGRC_PUBLIC_URL` requirement documented next to the SMTP settings rather than separately, where the credential lives (cross-referencing item 3 above), and the deliverability caveat (a passing connection test does not prove mail arrives — SPF/DKIM domain verification is a separate, provider-side step).

Item 1's source content is written and validated: `docs/wl-util-1-worked-example-deployment.md` is the real, worked hardening/HTTPS session this item calls for. The hosting-cost/GovCloud-necessity research referenced above under **Hosting** is also written and validated: `docs/cloud-hosting-options.md`. Item 4's source content is also written and validated: `docs/email-setup.md`. All three are ready to seed their respective docs.wingrc.us pages whenever the Docusaurus build happens — the site itself is still unbuilt; only the source content for these planned pages exists so far.

---

### P. Baseline versioning

Added 2026-09-11, flagged by the G.9 Tools baseline-library screen
(`docs/PLAN-gui-restructure.md`'s G.9 section) but **predates that
screen** — it is `seed_baselines`'s own long-standing gap, only made more
reachable once a runtime admin action could trigger it, not something
this screen introduced.

**The problem:** `seed_baselines` upserts `Product`/`BaselineControl`/
`BaselineEvidenceSpec` rows by key. Editing `baselines/rocketcyber.yaml`
and re-seeding (via the CLI, or now via G.9's import screen) retroactively
changes the compliance claims of every tenant that already activated that
product — their `control_state` was set under the *old* mapping, and the
justification for it is silently replaced under them, with no record that
anything changed. This is the same class of problem this codebase already
refuses to allow elsewhere: `sprs_snapshot` is never retroactively
rewritten, the audit log is append-only, bundle exports are point-in-time
snapshots. Today's baseline edit *does* rewrite yesterday's record, and
nothing in the schema or the magic loop notices.

**Options, none chosen yet:**
1. **Immutable baseline versions.** Each import creates a new versioned
   `Product`/`BaselineControl` set rather than mutating the existing rows;
   `OrgProduct` pins to the version that was active at activation time.
   Correct, but every FK that currently points at `BaselineControl`
   (`ControlState.sourced_from_product_id`, the evidence-task fan-out,
   G.9's own footprint/detail queries) has to learn to reason about "which
   version," not just "which product" — a real migration, not a bolt-on.
2. **`OrgProduct` pinned to an import timestamp/hash**, with the mapping
   resolved against a point-in-time snapshot rather than the live row.
   Smaller schema footprint than (1), but "what did version N actually
   say" still needs somewhere durable to live — this is effectively
   option 1 with the versioning made implicit instead of a first-class
   table, which tends to be harder to reason about later, not easier.
3. **Do nothing beyond a warning at import time** and treat baseline
   edits as a rare, deliberate, MSP-wide operational event — the same way
   editing the control catalog itself already is — rather than something
   the product actively protects tenants from. Cheapest today; leaves the
   silent-rewrite risk exactly where it has always been.

**What already shipped (G.9), which is not a fix:** the risk is now
*visible* at the moment it's taken. The import dry-run computes how many
orgs have the product `active`/`candidate` today and shows that count and
the org names before Apply is enabled. It does not block the import and it
does not solve the underlying versioning question — it just stops the
rewrite from being silent.

**Not started.** No design has been chosen; this entry exists so the
choice gets made deliberately rather than by whichever option is easiest
to bolt on under time pressure the next time this gap causes a real
incident.

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
- ~~**Scope connector** — Liongard / Datto RMM → `scope_entity`~~ **Shipped
  2026-09-11 (D.2) — no longer deferred.** See this file's own Done
  section, "D.2 — Liongard device/user pull into scope_entity" entry.
  Datto RMM was not built (Liongard only); a Datto connector, if wanted
  later, is a fresh item, not a reopening of this one.
- **Asset & user onboarding approval workflow — daily Liongard sync (D.3's second half only; the periodic review/attestation half shipped 2026-09-13, see this file's own Done entry).** Daily Liongard sync; new devices/users land pending, notify the org's `security_officer` and `it_admin` contacts, approval page shows a baseline checklist (DUO/Evo, FenixPyre, RoboShadow, RocketCyber…) evaluated from Liongard metrics, Security Officer + IT formally accept the asset into the environment. Specified in root `ROADMAP.md` **D.3**. Added 2026-09-08 (Jarrod). Depends on D.1 + D.2 and, as of 2026-09-08, on **two things that don't exist in this codebase yet**: outbound email and any job scheduler for the daily run. **Both shipped 2026-09-12** — see this file's own Done entries ("Outbound email" and "Job scheduler") — so this half is now actionable; neither prerequisite is a blocker anymore. Still needs a `pending_approval` state on `domain.py:EntityStatus` (today only `active`/`decommissioned`), and is not built here — the job scheduler slice explicitly excluded any job that writes `scope_entity`/`control_state`; a scheduled job may produce a dry-run for review, never apply one unattended, so this sync must still route through the existing dry-run → review → apply path, not bypass it via the scheduler. Hard constraint recorded in D.3: email notifies, but approval requires an authenticated session — no one-click approve links in email. Sequenced after the review/attestation half deliberately: the Liongard connector (`connectors/liongard.py`) has only ever been verified against a mock server, never a live tenant, so it's the riskier of the two D.3 halves and was left for a dedicated slice rather than bundled in.
- ~~**Evidence download hardening** — replace presigned direct-to-MinIO
  download URLs with the backend streaming evidence bytes itself.~~
  **Shipped 2026-09-14 — no longer deferred.** See this file's own Done
  section entry for the full writeup, including what's still pending
  (the §4 load measurement, and the bench-stack verification run).
- **Frontend build determinism** — generate and commit `frontend/package-lock.json` (none is committed — one has been observed untracked on wl-util-1 from a local `npm install`, but that's not what this item is about), then switch `deploy/nginx/Dockerfile` from `npm install` to `npm ci` for reproducible builds. Low priority, not blocking anything currently in flight. **Verified 2026-09-07: still open** — `git ls-files frontend/package-lock.json` returns nothing (not committed), `deploy/nginx/Dockerfile` still runs `npm install`, not `npm ci`.
