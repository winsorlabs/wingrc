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
3. **Credential encryption key — generation, custody and rotation.** Added 2026-09-11 (Jarrod). `.env.example` already documents *how* to generate one (`Fernet.generate_key()`), but nothing documents the operational consequences, and a new operator will not infer them. This must be part of **initial setup**, not a footnote — the key has to exist and be recorded before anyone enters their first connector credential, because after that point it is load-bearing. Cover:
   - **It is per-deployment.** Every WinGRC instance generates and holds its own key. Nothing is shared between installs; spinning up a new instance means generating and recording a new key.
   - **It never lives in the database, by design.** That separation is the security property — a stolen database dump is useless without the key. The corollary operators miss: **a database backup alone cannot restore a working system.** Backup runbooks must record where the key is kept, or a restore produces rows of undecryptable ciphertext.
   - **Loss is unrecoverable.** There is no reset, no backdoor, no vendor recovery. The only remedy is re-entering every stored credential by hand. Say this plainly rather than softening it.
   - **Where to keep it** — the operator's password manager or secrets store, alongside their Postgres/MinIO credentials, not solely in `.env` on a machine that may be rebuilt.
   - **Store the whole value including the label** (`label:key`), not just the base64 material — each encrypted row records which label encrypted it.
   - **Rotation exists and is proven**: `wingrc rotate-credential-keys` (dry-run by default, pre-flight `pg_dump`, whole-run fail-closed). Document the ordering, because that is where rotations go wrong: add the new key alongside the old → verify decryption → rotate → verify the new label everywhere → only then remove the old key. Reference the worked rehearsal from 2026-09-11.
   - **When to rotate**: on suspected exposure (the 2026-09-11 rotation was triggered by a key appearing in a chat transcript), on operator turnover, or periodically by the operator's own policy.

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
- **Scope connector** — Liongard / Datto RMM → `scope_entity`; supplements manual CSV import. Specified in root `ROADMAP.md` **D.2** (route through the existing dry-run/apply review flow, not a direct connector→DB write). **Updated 2026-09-07:** the canonical-attribute-key blocker this item used to cite is resolved — see the "Canonical `scope_entity.attributes` normalization" Done entry above. No connector code exists yet (verified: no Liongard/Datto files under `backend/app`, `Source.LIONGARD`/`Source.DATTO_RMM` exist only as unused enum values in `domain.py`); D.2 needs to follow the pattern `importers/workbook.py` already established, not invent a fourth attribute schema.
- **Asset & user onboarding approval workflow** — daily Liongard sync; new devices/users land pending, notify the org's `security_officer` and `it_admin` contacts, approval page shows a baseline checklist (DUO/Evo, FenixPyre, RoboShadow, RocketCyber…) evaluated from Liongard metrics, Security Officer + IT formally accept the asset into the environment. Specified in root `ROADMAP.md` **D.3**. Added 2026-09-08 (Jarrod). Depends on D.1 + D.2 and on **two things that don't exist in this codebase yet**: outbound email (verified 2026-09-08 — no `smtplib`, SMTP config, or mailer module anywhere under `backend/`) and any job scheduler for the daily run. Also needs a `pending_approval` state on `domain.py:EntityStatus` (today only `active`/`decommissioned`). Hard constraint recorded in D.3: email notifies, but approval requires an authenticated session — no one-click approve links in email.
- **Evidence download hardening** — replace presigned direct-to-MinIO download URLs with the backend streaming evidence bytes itself. Presigned URLs are bearer-token style: anyone with the link can download until it expires, with no per-request re-check of session/auth state. Worth revisiting given the investment already made in session/MFA/lockout hardening (item I, now shipped — see Done) — that hardening doesn't currently extend to the download path. Surfaced while proxying MinIO behind nginx for item O. **Verified 2026-09-07: still open** — `storage.py` still defines `presigned_url()` on every storage backend, and `routers/evidence.py` still calls it at 4 call sites (`download_url=storage.presigned_url(...)` for both single-evidence and task-collection responses). Nothing streams bytes through the backend yet.
- **Frontend build determinism** — generate and commit `frontend/package-lock.json` (none is committed — one has been observed untracked on wl-util-1 from a local `npm install`, but that's not what this item is about), then switch `deploy/nginx/Dockerfile` from `npm install` to `npm ci` for reproducible builds. Low priority, not blocking anything currently in flight. **Verified 2026-09-07: still open** — `git ls-files frontend/package-lock.json` returns nothing (not committed), `deploy/nginx/Dockerfile` still runs `npm install`, not `npm ci`.
