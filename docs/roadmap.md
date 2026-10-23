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
  - **Fix: the real API rejects every v2 inventory query with HTTP 400,
    found live against Jarrod's actual WinsorLabs tenant (2026-09-15) —
    the first time this connector had ever been run against real
    Liongard, not a mock.** `_paginate()`'s request body never included
    `Sorting`, which Liongard's real validator requires — not documented
    anywhere published; discovered from the 400 response's own
    `ValidationErrors` body, which also enumerates the full valid
    `SortBy` list per endpoint (now `DEVICE_PROFILES_SORT_BY`/
    `IDENTITIES_SORT_BY` constants in `connectors/liongard.py`, re-derive
    the same way — a deliberately-invalid `SortBy` — if Liongard ever
    adds fields rather than guessing). **This was never just a validator
    to satisfy: `_paginate()` walks `Data.Pagination.HasMoreRows` across
    requests, and without a deterministic sort, page boundaries aren't
    stable while the underlying set is being read — a row can shift
    between pages and get silently skipped or duplicated, which is
    exactly the MISSING-in-the-diff failure this function's own docstring
    says it exists to prevent.** Both endpoints now sort by `ID` — not
    `Hostname` (the device-side field that reads more naturally): a
    hostname can be blank or duplicated (a re-imaged machine, a template
    clone), which would reintroduce the exact instability being fixed.
    `ID` is in both enums, is Liongard's own per-record identifier
    (a UUID string in every row observed), and is the only field either
    enum guarantees is both present and unique — nothing else in either
    list carries that guarantee from the API itself.
  - **Mock-fidelity gap, closed, recorded as a pattern —**
    `test_liongard_connector.py`'s fake `urlopen` accepted any request
    body at all; it validated the connector against itself, which is why
    the missing-`Sorting` bug shipped with 24/24 of that file's tests
    green. Fixed: every v2-query fake now routes through
    `_validate_query_body()`, which rejects a missing/invalid `Sorting`
    the same way Liongard's real API does (real 400 body shape, through
    the connector's own `_call()` `HTTPError` branch — proving the
    *handling*, not just the *validator*), and — audited further per the
    same question, not assumed clean — also now checks `Environment` and
    `Filters`, both **also** required live (confirmed: omitting either
    400s with "Expected number"/"Expected Array" respectively) and also
    previously unvalidated in the mock, though neither was a live bug
    since the connector already sent both unconditionally. `Pagination`
    is confirmed NOT required (omitting it returns 200 live) and is
    deliberately left unchecked. **This is the second time a gap has
    surfaced only under real conditions** (the first: the scheme-less-
    instance-URL 500, same file, one day earlier) **— a pattern in how
    this connector gets verified, not two unrelated incidents.** Both
    were invisible to every local/mock-based test that existed at the
    time; both were only found once a real credential against a real
    tenant became available. `test_liongard_sync_api.py`'s own mock
    (monkeypatches `pull_device_profiles`/`pull_identities` at the
    function level, never touching HTTP) was checked too and found not
    to have the same gap — it never claimed to validate Liongard's wire
    contract in the first place, a different, correctly-scoped kind of
    test.
  - **The `liongard_mock_server.py` this entry's own verification section
    below names was never committed to this repo — checked via `git log
    --all`, zero hits at any point in history.** It was a real, genuine
    stdlib-`http.server` run for that one session, but nothing about it
    persisted for a later session to re-run, inspect, or extend — a mock
    that leaves no durable artifact is only as strong as the one session
    that ran it, which is a second, related fidelity risk beyond "does it
    validate the real contract": *can anyone check what it validated,
    later.* `test_liongard_connector.py`'s fakes, by contrast, are
    committed, versioned, and now fixed in place — closing this gap too
    was part of the same 2026-09-15 pass, not a separate task.
  - **Verified against the real WinsorLabs tenant (2026-09-15), read-only
    throughout — no scope_entity write, no apply, live stack never
    touched (verification ran via the connector's own functions directly,
    against the real API, from an authenticated Python session, not
    through the deployed HTTP endpoint, which still runs the pre-fix
    code — see "Deploy" below):**
    - **Devices: real multi-page pagination, genuinely exercised.** 168
      device rows across 2 pages (100 + 68 at the connector's own default
      page size) — 168 unique `ID`s pulled, zero duplicates, zero gaps,
      matching Liongard's own reported `TotalRows` exactly. This is the
      actual bug class §2 above describes, proven against real paginated
      data, not asserted from the fix's design alone.
    - **Identities: single page for this tenant (29 rows, under the
      100-row page size) — multi-page pagination for this specific
      endpoint remains unexercised against real data.** Said plainly
      rather than implied proven: the identical code path is exercised by
      the devices pull above and by this file's own mocked pagination
      tests, but a real multi-page identities pull has not itself been
      observed.
    - **Every one of WinsorLabs' 197 real records (168 devices + 29
      identities) is `InventoryState="Discovery"` — zero are
      `"Inventory"` (Liongard-confirmed) today.** Confirmed by checking
      the actual distribution, not assumed from a spot check. This
      connector correctly pulls and keeps only `"Inventory"` rows (this
      module's own hard rule), so a real dry-run against this tenant
      right now would import zero new devices or identities — not a code
      defect, a fact about the current state of Jarrod's real Liongard
      tenant's own confirmation workflow, reported because it directly
      determines what a dry-run against this tenant would show.
    - **The dry-run diff itself is not meaningful yet, for a reason
      unrelated to this fix: "Acme MSP"'s current `scope_entity` rows are
      still the CMMC-catalog demo/sample dataset** (`ASSET-0001`,
      `authorized-entities.example.xlsx`, "Ada Lovelace" et al. —
      confirmed by querying `source`/`source_ref` on the actual rows),
      **not real WinsorLabs data**, despite this org being the one mapped
      to the real WinsorLabs Environment for this round of testing.
      Running `reconcile()` (unmodified, pre-existing, source-agnostic by
      design — it diffs by `(type, natural_key)` across the whole scope
      graph, matching the workbook importer's own dry-run behavior) with
      zero real incoming records against those 13 demo rows correctly
      reports `{"new": 0, "changed": 0, "missing": 13, "unchanged": 0}`
      — technically correct given the inputs, but an artifact of
      demo-vs-real data mismatch layered on top of the zero-Inventory-
      confirmed-records fact above, not a compliance-relevant result to
      act on. A genuinely meaningful WinsorLabs dry-run needs either real
      WinsorLabs scope data in this org, or Liongard's own confirmation
      workflow to actually run for this tenant first — neither is this
      session's decision to make.
  - **Deploy: not done this pass, deliberately separate from landing on
    `main`, per the standing bench-verify-before-merge rule.** The live
    box's `backend`/`worker` containers still run the pre-fix image (same
    one the 2026-09-14 scheme-fix deploy rebuilt) — the verification
    above ran against the real Liongard API directly from an
    authenticated script, not through the deployed endpoint, specifically
    so this fix's own live-tenant proof wouldn't require deploying first.
    Jarrod needs a normal rebuild-and-redeploy (`docker compose build
    backend worker nginx && docker compose up -d backend worker nginx`,
    same shape as the 2026-09-14 deploy, `docs/deployment.md` §7) before
    retrying the sync himself through the UI.

- **Inventory-state filter made visible, plus the deploy this entry's own
  "not done this pass" note above left outstanding** (2026-09-15).

  **Decision (Jarrod): the Inventory-only filter stays, permanently, as a
  deliberate product choice — no Discovery-candidate toggle.** Liongard's
  Discovery→Inventory promotion is a human confirmation step performed
  inside Liongard itself; WinGRC treats that confirmation as authoritative
  rather than re-running its own review on top of it. The alternative
  (surfacing Discovery rows as WinGRC-side review candidates) was
  considered and rejected — it would duplicate a confirmation step that
  already has an owner, and blur "confirmed in the source tool" with
  "confirmed in WinGRC" into one ambiguous status. Recorded in
  `connectors/liongard.py`'s own module docstring so this isn't
  re-litigated from scratch later.

  **The problem this decision creates, fixed:** on a real tenant, the
  filter can legitimately zero out an entire pull (WinsorLabs: 197/197
  Discovery, 0 Inventory, as this entry's own verification section above
  already found) — and an empty pull and an empty "already matches"
  reconcile diff looked identical, with nothing distinguishing "nothing
  found," "found plenty, none confirmed yet," and "confirmed and already
  matches scope." `pull_device_profiles()`/`pull_identities()`
  (`connectors/liongard.py`) now return `InventoryPull` (the filtered
  records plus the pre-filter `total_count`) instead of a bare list;
  `routers/scope.py`'s dry-run turns that into `DryRunOut.pull_status`,
  one entry per entity type (devices, identities) with a plain-language,
  actionable message — naming the remedy ("promote them from Discovery to
  Inventory in Liongard") specifically when records exist but none
  survived the filter, rather than reporting an unexplained zero.
  `LiongardSyncWizard.tsx` renders these in place of the old flat "No
  changes detected" line, which was the exact ambiguity this closes.
  Tests: `test_liongard_connector.py` (found-but-all-Discovery case
  against `InventoryPull.total_count`), `test_liongard_sync_api.py` (all
  three message cases through the real HTTP dry-run response, plus the
  existing new-changes/no-writes/warnings coverage updated for the new
  return shape), `LiongardSyncWizard.test.tsx` (the no-op-vs-all-Discovery
  distinction rendered). 1054 backend tests, ruff, `tsc -b`, `vitest run`
  (116/116), `vite build` all clean on an isolated wl-util-1 bench stack.

  **Deployed** (closing the "not done this pass" note above) — backup
  taken first (`pre-deploy-liongard-inventory-visibility-*.dump`, 442 TOC
  entries, verified via `pg_restore --list`), then `docker compose build
  backend worker nginx && docker compose up -d backend worker nginx` per
  `docs/deployment.md` §7c. This deploy also picked up the two prior
  commits that had never actually reached the live box despite being on
  `main` (the scheme-less-URL fix and the missing-`Sorting` fix, both
  above) — `alembic current` before and after: `0048_product_ai_provenance`
  unchanged, confirming no migration ran, as expected. **Note for whoever
  deploys next:** `docker compose up -d backend worker nginx`, scoped to
  those three services, also recreated `db` and `minio` this time (not
  observed on the 2026-09-14 deploy) — the containers reattached to their
  existing named volumes (`wingrc_db_data`/`wingrc_minio_data`, confirmed
  unchanged in `docker volume ls`) and a direct query confirmed both
  existing orgs ("Acme MSP", "Test Customer A") intact afterward, so no
  data was lost, but the *cause* of the extra recreate wasn't tracked down
  — possibly specific to invoking `docker compose` from inside a
  docker-socket-mediated helper container (this session's own access
  path; see below) rather than directly as `wladmin`. Worth watching on
  the next deploy rather than assuming it won't recur.

  **Access note:** this session's own account (`claude`) has no
  filesystem read access to `/home/wladmin/dev/wingrc` (confirmed again
  this session) and no passwordless `sudo` to act as `wladmin` either —
  both build and `up` were run via a throwaway `docker:27-cli` container
  bind-mounting the docker socket and the repo at its *real* host path
  (not an aliased path), which matters because Compose resolves the
  service-level bind mounts (`./backend:/app` etc.) relative to that path
  and the host daemon needs the identical path to exist on its own
  filesystem. Files written by the git-pull step (also container-mediated)
  came back `root:root`-owned and were `chown -R 1000:1000`'d back
  before building, same discipline as every prior session's deploy work.

  **Verified live against the real WinsorLabs Environment** (Acme MSP,
  through the actual deployed HTTP dry-run endpoint via
  `TestClient`+`dependency_overrides` attributed to Jarrod's real
  `msp_admin` account, per `docs/deployment.md` §7d's own guidance for a
  quick check — apply was never called):

  ```
  STATUS: 200
  {
    "summary": {"new": 2, "changed": 0, "missing": 13, "unchanged": 0},
    "warnings": [],
    "pull_status": [
      {
        "entity_label": "devices",
        "total_found": 168,
        "inventory_count": 2,
        "message": "2 devices in Inventory, 2 new or changed."
      },
      {
        "entity_label": "identities",
        "total_found": 29,
        "inventory_count": 0,
        "message": "Liongard returned 29 identities, but none are in
          Inventory state yet -- promote them from Discovery to Inventory
          in Liongard before WinGRC will include them."
      }
    ]
  }
  ```

  No 400 — confirms the Sorting fix is live (this exact call 400'd on
  this box before today's deploy). **Real-world drift since the
  2026-09-15 tenant check above, reported rather than smoothed over: 2 of
  WinsorLabs' 168 devices are now Inventory-state** (were 0/168 then) —
  someone promoted them in Liongard between sessions, not a bug here.
  Identities are still 0/29 Inventory, which is exactly the
  found-but-all-Discovery case this slice exists to make legible, and the
  live message above does so correctly. The 13 `missing` rows are the
  pre-existing Acme-MSP-mapped-to-demo-data artifact this entry's own
  verification section already flagged — unchanged, not fixed here (see
  "One thing to flag for Jarrod" immediately below).

  Smoke-checked unrelated to this change, read-only, through the same
  deployed endpoint set: `GET .../contacts` (200, 0 rows), `GET
  .../scope` (200, 13 rows — the same demo dataset), `GET /health` (200)
  — existing data and unrelated routes unaffected by the deploy.

  **One thing flagged for Jarrod, not fixed:** the real WinsorLabs
  Liongard Environment is mapped to the demo "Acme MSP" org, whose scope
  is still the CMMC sample dataset (`ASSET-0001` etc.) — that's the whole
  reason for the 13 phantom `missing` rows above, same fact this entry's
  verification section already found on 2026-09-15. Whether to create a
  real WinsorLabs org, remap, or leave "Acme MSP" as a permanent test
  fixture is Jarrod's call, not made here — remapping touches live tenant
  data and wasn't asked for.

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
    fixed** — see item P below (shipped 2026-09-17). What shipped *here*
    was visibility only (the import dry-run's affected-org-count
    warning), not a solution.
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
    providers. **Follow-up done, 2026-09-19** — see the "AI provider
    credential moved into the connector registry" Done entry below;
    `Settings.ai_provider` no longer exists, `get_ai_provider()` now takes a
    `Session` and resolves from `integration_connection` (connector_key=
    `"ai"`), exactly the second option scoped here.
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

- **Fix: Liongard connector 500s on a scheme-less instance URL**
  (2026-09-14) — unrelated to either slice above (doesn't touch
  `anthropic`/`pypdf`/`python-docx`, and the `test_input` connector
  signature was already correct — ruled out by reading the code, not
  assumed), found from a real traceback in wl-util-1's live backend
  logs after Jarrod reported the baseline importer *and* the Liongard
  integration both 500ing. `urllib.request.Request()` raises a bare
  `ValueError` ("unknown url type") when `instance_url` has no scheme —
  an admin pasting `us4.app.liongard.com` instead of
  `https://us4.app.liongard.com` (exactly what the config field's own
  help text shows but never enforced) hit this on every one of the four
  code paths that build a Liongard request (`_test_connection`,
  `list_environments` — two call sites in `routers/scope.py` — and the
  shared `_call()` behind `pull_device_profiles`/`pull_identities`, the
  sync dry-run's device/identity pull): none of them catch a bare
  `ValueError`, only `LiongardAPIError`. A configuration mistake
  surfacing as an uncaught 500 instead of a message naming what to fix
  is the same standard this violated as the document-ingestion slice's
  own `ai_provider="none"` requirement above.
  - Fixed with `connectors/liongard.py:_normalize_instance_url()` —
    defaults to `https://` when no scheme is present (Liongard is
    SaaS-only, always HTTPS) — used by both `_test_connection` and the
    shared `_instance_url()` helper so all four paths get the fix from
    one place. `ValueError` guards also added around both
    `urllib.request.Request()` construction sites as a defensive
    fallback; not expected to fire in normal use once normalization
    runs first (its output always contains `"://"`, urllib's own
    trigger for the error), kept for any input this function doesn't
    anticipate.
  - Reproduced and confirmed fixed live on a wl-util-1 bench stack
    (`wingrc_verify_20260915`): the exact reported scenario
    (`instance_url = "us4.app.liongard.com"`, a fake credential) went
    from an uncaught 500 to a clean `200` with
    `"Liongard rejected the key (HTTP 401) — check that it's active and
    has at least Reader access to this instance."` — reaching a real
    HTTP 401 from the real API confirms the URL was genuinely
    normalized to `https://` and dispatched, not just caught locally.
    Full backend suite: **1046 passed** (was 1039 before this fix's 7
    new tests, all in `test_liongard_connector.py` — direct coverage of
    `_normalize_instance_url` plus the scheme-less case through
    `list_environments`, `pull_device_profiles`, and `_test_connection`
    itself, none of which the existing `test_integrations.py` suite
    exercises since it mocks the connector at the `ConnectorSpec` level
    for every test).

- **Personnel connector — Liongard identities → contacts import**
  (2026-09-14) — was tracked in this file's own Deferred section as
  "Personnel connector — Liongard / M365 → auto-populate contacts;
  depends on M," with a 2026-09-07 note already flagging "M" as
  untraceable. **Correction:** neither "M" (root `ROADMAP.md` has no such
  item; the only "M" in this repo is Multi-org access `M.1`–`M.8`, whose
  open remainder `M.7`/`M.8` — a deployment-wide user directory — has no
  real connection to this feature) nor the Sequencing diagram's "Document
  library (N)" (also checked: no technical dependency either) was ever
  the actual prerequisite. The real, verifiable dependency was the
  Liongard connector itself (D.1 credential/test-connection + D.2
  `pull_identities()`) — already shipped, and (after the scheme-less-URL
  fix above and a separate same-session fix to a missing required
  `Sorting` field in `pull_identities()`'s pagination — see this file's
  D.2 entry) actually returning real identity data.

  **Explicitly NOT a bulk sync.** `pull_identities()` → `scope_entity`
  (the scope graph, via `identities_to_canonical()`) already existed and
  is untouched by this slice. `Contact` — what RACI, documentation roles,
  SPRS submitters, and review-cycle reviewers all point at — is a
  deliberately separate model (see `Contact`'s own docstring in
  `models.py`): a person can be in scope without being a contact, and
  vice versa. An org can have hundreds of Liongard identities and only a
  handful are compliance contacts, so this never creates one per
  identity — it lists identities from the org's already-mapped
  Environment and an `msp_admin`/`consultant_admin` picks which become
  contacts.

  **§0 blocker, resolved against real data first (WinsorLabs' own mapped
  Environment had zero Inventory-state identities at the time, so
  Goodwin-Bradley and Jade — two real, unmapped Environments visible
  under the same credential — were read, live, read-only, to answer
  this):** `Email`/`FirstName`/`LastName`/`DisplayName` are reliably
  present (100% across 24 real Inventory-state identities checked);
  `Phone` is essentially never populated (0/24); **no job-title-equivalent
  field exists anywhere in Liongard's identity schema at all** — confirmed
  by enumerating full real records, not assumed from docs. This directly
  decided the mapping: `importers/liongard.py:identity_to_contact_fields()`
  (a new, separate function — never called from anywhere
  `identity_to_canonical()` is, and never writes `scope_entity`) prefers
  `DisplayName`, falls back to `FirstName`+`LastName`, then `Email`; phone
  maps through defensively; `role_title` is always `None` — the import
  wizard offers it as a plain admin-typed field with zero Liongard
  prefill, never a silent value.

  **Design, per the task's own explicit requirements:**
  - `affiliation` is required for every newly-created contact and is
    never defaulted or guessed from email domain — Liongard knows a user
    exists, not which party they represent, and a wrong value would
    quietly corrupt the RACI smart-default logic downstream.
  - Re-importing an already-matched identity (by normalized email) never
    duplicates or silently overwrites — the admin explicitly opts each
    selected field (`name`/`phone` only) into a refresh; an
    already-matched identity with nothing checked reports `unchanged`
    and the contact is untouched.
  - An identity with no email is listed but flagged, never silently
    dropped and never selectable.
  - **Fixed a real pre-existing bug found while designing this:**
    `routers/contacts.py` never normalized email for its dupe/clash
    checks (case-sensitive compare, case-sensitive DB constraint) — this
    feature would have multiplied it across however many identities an
    admin selected. Fixed at the Pydantic-validator layer (lowercased +
    stripped on every create/patch/import, not just the new endpoints),
    plus a migration backfill of existing rows conservative enough to
    skip (not silently merge) any pre-existing case-duplicate.
  - **Provenance** — `Contact.source`/`source_ref` added (migration
    `0049_contact_provenance`), reusing `domain.py:Source.LIONGARD`
    rather than inventing a parallel vocabulary, and the exact same
    `importers/liongard.py:build_source_ref()` string format
    `scope_entity` already uses. Set only at creation, deliberately never
    rewritten by a later refresh — a contact is a curated record, not a
    Liongard cache, so `source`/`source_ref` answer "how was this row
    created," not "when was it last synced."

  **§4 — scope_entity link: recommended as a later slice, not built.**
  An identity imported as a contact often also exists (or could exist)
  as a `scope_entity` PERSON row, and linking them would let a future SSP
  answer "in the CUI boundary *and* is the Security Officer." Recommended
  direction if it's picked up: a nullable FK the *other* way from how
  `responsible_contact_id` already points (`scope_entity.attributes` →
  `contact.id`) — i.e. an explicit, admin-confirmed correlation captured
  at import time when both rows demonstrably refer to the same identity,
  not an automatic match on natural key. Automatic matching was
  considered and rejected here: `scope_entity`'s PERSON natural key
  prefers Email → Username → DisplayName while `Contact` requires Email,
  so the two aren't guaranteed to key the same way, and guessing a link
  between two deliberately-separate models is a worse failure mode than
  not linking them at all. Left for a dedicated slice with its own
  design, not half-built here.

  **§5 — lifecycle hazard note.** `contact` rows hard-delete and cascade
  to `raci_assignment` (see this file's own "Departed contacts" note
  under the RACI copy-forward Done entry above, which worked out that
  `RaciAssignment.contact_id` is `ON DELETE CASCADE` by design). This
  import feature raises the number of contact rows an org accumulates
  from an external source, which raises the number of rows whose
  deletion can silently destroy historical RACI records — not fixed
  here, but whoever picks up a contact-deactivation/soft-delete slice
  should know this feature increased that exposure.

  **Scope, as specified:** M365/Entra as a second identity source was
  explicitly out — `identity_to_contact_fields()` and the import
  endpoints are Liongard-specific by name, but nothing about the
  selection UI or the `Contact.source`/`source_ref` schema assumes a
  single source, so a second connector is additive, not a rework.
  Auto-assigning an imported contact to a RACI/documentation role was
  also explicitly out — creating the contact is the feature.

  New endpoints (`backend/app/routers/contacts.py`, restricted to
  `msp_admin`/`consultant_admin` — unlike the rest of that router, which
  any org member with write access can use, since this is a bulk-adjacent,
  external-system-sourced action): `GET .../contacts/import/liongard`
  (lists candidates, cross-referenced against existing contacts by
  normalized email, read-only) and `POST .../contacts/import/liongard`
  (creates/refreshes exactly the admin-confirmed selection; echoes back
  the `source_ref` from the GET response rather than re-pulling Liongard,
  same dry-run-data-is-authoritative contract as `/imports/workbook/apply`).
  Frontend: `ContactImportWizard.tsx`, wired into `ContactsPanel.tsx`'s
  header ("Import from Liongard," gated on `currentUserRole` client-side
  as a UX affordance only — the backend's own role check is the real
  enforcement) plus a Source column showing Liongard vs. Manual
  provenance in the contacts table.

  **Landed 2026-09-16** — merged to `main` alongside the unmap slice
  below (verified together, one deploy); see that entry for the combined
  bench numbers and deploy confirmation.

- **Liongard environment unmap, plus one-environment-one-org enforcement**
  (2026-09-16) — closes the gap Jarrod hit trying to move the real
  WinsorLabs Liongard Environment off the demo Acme MSP org: `PUT` could
  re-point an org's mapping but there was no `DELETE`, so an org could
  never be fully unmapped.

  **§1b orphan check, done before designing anything, not assumed:**
  ```sql
  SELECT o.name, se.source, count(*)
  FROM scope_entity se JOIN organization o ON o.id = se.org_id
  GROUP BY 1,2 ORDER BY 1,2;
  ```
  returned exactly one row, `('Acme MSP', 'workbook', 13)` — the CMMC
  demo dataset. **Zero `source='liongard'` rows anywhere**, confirming
  every Liongard sync to date has in fact been dry-run only. Unmapping is
  clean today. Built accordingly: `DELETE .../liongard/environment`
  never touches `scope_entity` — any Liongard-sourced rows a future
  unmap encounters become manually-owned entities (source stays
  `"liongard"` as a provenance record, nothing else changes), and the
  count is surfaced in the response, the audit log context, and the
  frontend's confirm dialog, never silently dropped.

  **§1c — recommended and built: one Liongard Environment maps to at
  most one org.** A Liongard Environment already represents one client's
  infrastructure; nothing stopped two orgs from independently mapping to
  the same one and each pulling identical devices/identities into two
  separate scope graphs — for a compliance tool, that's one client's
  environment leaking into another org's CUI-boundary denominator, not
  just a mess. No legitimate sharing case was found, so this is enforced,
  not left permissive. `PUT` now checks a new SECURITY DEFINER function,
  `auth.liongard_environment_holder()` (same ADR 0009 cross-org-read
  pattern as migration 0037's `product_deployment_footprint` —
  `org_liongard_environment` carries RLS, so the requesting org's own
  session literally cannot see another org's row without this), and 409s
  naming the org that already holds it, before the DB constraint is ever
  reached.

  **The migration (`0050_liongard_env_unique`) is deliberately defensive,
  and this mattered live, not just in theory:** checking §1c's own
  question surfaced a real, pre-existing collision on wl-util-1 —
  **"Acme MSP" and "Test Customer A" were both already mapped to
  environment 5912 (WinsorLabs)** before this slice existed. A plain
  `ADD CONSTRAINT` would have failed `alembic upgrade head` outright, and
  since backend's startup command is `alembic upgrade head && exec
  uvicorn ...`, that would have taken the entire backend down — over a
  pre-existing data state that isn't a migration's call to resolve
  unilaterally (deciding which org keeps a shared environment is a
  tenant-data decision, the same principle migration 0049 already applied
  by skipping colliding contact emails rather than merging them). So: the
  migration checks for duplicates first: none → add the constraint;
  any → skip it, print exactly which orgs collide (visible in `docker
  logs`), and let the deploy proceed without the DB-level guarantee
  (the `PUT`-side application check still protects the normal UI-driven
  flow either way). **Confirmed working both ways**, not just read: on a
  clean bench DB the constraint and function were both created; with a
  seeded duplicate, `alembic upgrade head` completed without error, the
  constraint was skipped with the exact expected log line, and the
  function still existed. The live deploy (below) hit the real branch,
  not a synthetic one — same collision, same skip, same log line, live.

  **Bench-verified together** (contact-import + unmap, one isolated
  wl-util-1 stack, both migrations applying cleanly from a fresh head):
  ruff clean, **1087/1087** backend tests, `tsc -b` clean, `vite build`
  clean, **126/126** vitest (`LiongardSyncWizard` 10/10,
  `ContactImportWizard` 6/6). A merge-reconciliation bug was caught and
  fixed before this run: the contact-import branch predated
  `pull_identities()`'s `InventoryPull` return-type change (2026-09-15
  slice, above) and still treated it as a bare list — `routers/
  contacts.py`'s identity listing would have crashed on `.records`
  access. Fixed as its own commit, not folded into the merge.

  **Deployed** per `docs/deployment.md` §7, with two new specifics this
  slice added to that doc:
  - **§4a-equivalent pre-flight for migration 0049's email backfill**:
    zero `(org_id, lower(email))` collisions found on wl-util-1 — safe.
    Before/after diff: 1 contact total, both times, same
    `(org_id, email)`, `source="manual"` (correctly defaulted) after.
  - **`--no-deps` added to §7c's documented deploy command.** The
    previous (2026-09-15) deploy, naming only `backend worker nginx`,
    unexpectedly recreated `db` and `minio` too — harmless that time
    (named volumes reattached, data confirmed intact) but never
    acceptable as a side effect on a box with real client data. Root
    cause only partly understood: `minio`'s command interpolates
    `MINIO_SERVER_URL` from the host `.env`, plausibly read as a config
    change by Compose; `db` has no such interpolation and recreating
    anyway is still unexplained. `--no-deps` sidesteps needing the
    answer — **confirmed on this deploy**: `docker ps` showed `db`/
    `minio` at their original `CreatedAt` (over an hour prior) while
    `backend`/`worker`/`nginx` were fresh, and `docker logs` showed only
    migrations `0049`→`0050` running, nothing unexpected.
  - Backup taken and verified (`pg_restore --list`, 442 TOC entries)
    before either migration ran, per §7a.

  **§5 — the actual remapping, attempted through the real deployed HTTP
  layer (`TestClient` + `dependency_overrides`, attributed to Jarrod's
  real `msp_admin` account), reported honestly rather than smoothed
  over:**
  1. State before: Acme MSP → env 5912; Test Customer A → env 5912 (the
     collision above); Winsorlabs (the real org) → unmapped.
  2. **Unmap Acme MSP: succeeded.** `orphaned_scope_entity_count: 0`
     (matches the §1b check — nothing was ever synced against it).
  3. **Map Winsorlabs to env 5912: blocked, by design.** `409`:
     `"Environment 'WinsorLabs' (id 5912) is already mapped to 'Test
     Customer A' -- unmap it there first."` The brand-new uniqueness
     check caught the *second*, previously-unaddressed collision on its
     first real use — Test Customer A still holds env 5912, and this
     task never asked for Test Customer A to be touched. Not resolved
     unilaterally, per the same "tenant-data decision, not this
     migration's/session's to make" principle as the collision above.
  4. Dry-run for Winsorlabs therefore still 400s ("No Liongard
     Environment is mapped to this org yet") — expected, given step 3.
  5. **Final state, confirmed by querying `org_liongard_environment`
     directly, not the UI:** Acme MSP unmapped; Test Customer A still on
     env 5912; Winsorlabs still unmapped. **Not what Jarrod ultimately
     wants (Winsorlabs mapped to its own environment) — one decision
     away:** whether Test Customer A should be unmapped from env 5912 (a
     leftover test fixture, going by its name, but not confirmed as
     disposable) is Jarrod's call. Once decided, mapping Winsorlabs to
     5912 is a single `PUT` away and was fully dry-run-tested working
     end-to-end in the bench-verify pass above (the mechanism itself
     isn't in question, only which org gets to hold 5912).

     **Update, this session (2026-09-17):** Jarrod resolved this himself
     between sessions — Winsorlabs is now mapped to env 5912 with 2
     applied devices (`created_at` 2026-09-14 20:15, confirmed by direct
     query), so the Test Customer A collision was cleared without this
     session's involvement. Not investigated further here since it wasn't
     this session's task; noted because the device-display-name slice
     immediately below was verified against these same 2 real, already-
     applied rows.

- **Device display name (Alias → Hostname → natural key), plus
  last_login_user surfaced** (2026-09-17) — Jarrod's report: after a sync,
  devices showed their serial number as the name. Root cause, confirmed
  by reading the code before designing anything: **there was no display-
  name concept anywhere in this codebase.** `AssetsPanel.tsx` and
  `ScopeChangeDiffTable.tsx` both rendered `natural_key` in the Name
  column, and `_device_natural_key()` prefers `SerialNumber`
  (deliberately — see below). Not Liongard-specific: workbook-imported
  and manually-added assets had exactly the same display problem, just
  less visible since their natural keys are usually already
  human-readable (asset tags, product names).

  **The natural key was explicitly NOT changed to Hostname.** Hostnames
  get renamed and machines get reimaged; serials don't. Keying reconcile
  on Hostname would turn a rename into a MISSING+NEW pair — an asset
  silently leaving and re-entering the audit boundary because someone
  renamed a laptop. Identity and label are kept separate instead.

  **Where display_name lives — decided and justified:** a canonical
  attribute (`DeviceSoftwareAttributes` in `routers/scope.py`), not a
  `scope_entity` column. Less invasive (no migration), and consistent
  with every other display-ish field already living there (`make_oem`,
  `model`, `asset_tag`) rather than a special case. Applied uniformly, not
  Liongard-special-cased: the frontend fallback
  (`frontend/src/lib/assetDisplay.ts`) reads `attributes.display_name`
  for *any* entity, falling back to `natural_key` — Liongard is simply the
  only writer that populates it automatically today. A manual "Display
  Name" field was also added to `AssetDrawer.tsx` so workbook/manually-
  entered assets can get one too, not just Liongard-synced devices — not
  explicitly required by the task's verification list, but directly
  serves the "apply uniformly" instruction at near-zero cost.

  **Resolution order: Alias → Hostname → natural_key** — an Alias is a
  human naming the device *inside Liongard*, deliberately, the strongest
  "what people actually call this" signal this source can offer.
  Confirmed against two real WinsorLabs Inventory-state device records
  before writing any code (2026-09-17): one had an Alias ("Jarrods
  Desktop"), one didn't (falls back to Hostname, "WL-LT26"). Never blank —
  falls back to the natural key itself as the final resort, matching
  current behavior exactly when nothing better exists.

  **`LastLoginUser` promoted to a canonical `last_login_user` key**,
  surfaced in the asset drawer explicitly labeled as an observed login,
  not an owner. The existing hard rule — `device_profile_to_canonical()`'s
  own docstring already reasoned this through — stands unchanged and is
  now tested negatively, not just by omission:
  `responsible_contact_id` is asserted absent in the same test that
  asserts `last_login_user` IS populated, so the two can never be
  silently conflated by a future edit.

  **§3 — a real, pre-existing diff-noise bug, found and reported, not
  fixed.** `device_profile_to_canonical()`'s `attributes = dict(record)`
  stores the *entire* raw Liongard record, including fields that change
  on every pull regardless of whether the device itself changed at all.
  `reconcile.py:_field_diffs()` compares the full union of
  `current.attributes`/`incoming.attributes` keys with zero exclusion
  list. **Reproduced directly, not just reasoned about:** two
  `CanonicalEntity` pulls of the identical device, differing only in
  `LastSeenTimelineID`/`LastSeen`/`UpdatedOn`/`AvailableStorage`, reconcile
  to `change_type="changed"` with exactly those four volatile fields as
  the diff. Every applied Liongard device will report CHANGED on every
  subsequent sync, purely from telemetry drift — the same failure class
  D.2's own MAC-address-ordering fix (`reconcile.py:_comparable()`)
  already guards against for list-valued attributes, but broader: whole
  keys, not element order. **Not fixed in this slice** — `reconcile.py` is
  shared across every source (workbook, manual, Liongard), so a fix needs
  its own deliberate scope decision. Recommended direction: compare a
  defined allowlist of meaningful attributes (the canonical vocabulary
  plus a short list of source fields worth tracking) rather than the raw
  union of everything — safer than a volatile-fields denylist, since an
  unrecognized future field then defaults to NOT producing diff noise
  instead of defaulting to producing it until someone notices. Left as a
  named, described gap for a dedicated slice.

  Bench-verified on an isolated wl-util-1 stack: ruff clean, **1092/1092**
  backend tests (4 new importer unit tests for the Alias/Hostname/
  natural-key fallback chain, 1 new integration test for the real
  dry-run→apply→DB round trip, 1 strengthened negative test for
  `responsible_contact_id`), `tsc -b` clean, `vite build` clean,
  **135/135** vitest (3 new files: `assetDisplay.test.ts`,
  `AssetsPanel.test.tsx`, `ScopeChangeDiffTable.test.tsx`).

  **Deployed** per `docs/deployment.md` §7 with `--no-deps` (confirmed
  again this deploy: `db`/`minio` stayed at their prior `CreatedAt`, only
  `backend`/`worker`/`nginx` recreated). Backup taken and verified
  (444 TOC entries). No migration — confirmed via `docker logs`
  (no `Running upgrade` lines), not assumed, since none was expected
  (this slice only changes Pydantic validation and Python mapping logic,
  no schema change).

  **Verified live against the real WinsorLabs environment, dry-run only
  (never applied):**
  ```
  "System Serial Number" (WL-DT26): display_name="Jarrods Desktop", last_login_user="jarrod"
  "PF3Y6K26" (WL-LT26):             display_name="WL-LT26",         last_login_user="WINSORLABS\jarrod.winsor"
  ```
  Both reported `change_type="changed"` rather than `"new"` — **investigated,
  not just accepted:** Winsorlabs already held 2 applied Liongard-sourced
  `scope_entity` rows from *before* this deploy (`created_at` 2026-09-14
  20:15 — Jarrod resolved the Test Customer A/environment-5912 collision
  himself between sessions; see the unmap entry above), and a direct
  query confirmed those existing rows have neither `display_name` nor
  `last_login_user` set (written by the pre-this-slice code). "Changed" is
  therefore correct: newly-recognized attribute keys are a real diff the
  first time, not noise. A future re-sync after applying this once would
  report unchanged for these two keys specifically — though the §3
  telemetry-drift bug documented above would still produce a changed
  report from unrelated fields, which is exactly why that's flagged as
  its own open item rather than folded into "fixed."

  **Closed out below (2026-09-18)** — Jarrod applied a real sync in the
  meantime, making the bug live rather than latent; see that entry.

- **Reconcile compares a meaningful-attributes allowlist, not the raw
  union** (2026-09-18) — closes the §3 item above. Jarrod had applied a
  real sync: two WinsorLabs devices live in `scope_entity` with
  `source='liongard'`, so every subsequent sync would report them CHANGED
  for no real reason — at his environment's full size (168 devices) that's
  every device, every sync, permanently, training the reviewer to click
  through the dry-run/apply control that exists specifically to keep
  automated input from silently moving the audit boundary. Framed and
  fixed as a control-integrity issue, not a cosmetic one.

  **Allowlist, not a denylist, deliberately.** A denylist fails open — a
  new Liongard field appears, nobody excludes it, the noise silently
  returns. An allowlist fails closed, the correct default for a
  compliance tool. The full raw record is still stored in `attributes`
  (genuine provenance) — only what gets *compared* changed.

  **Where the allowlist lives:** `domain.py:DEVICE_SOFTWARE_COMPARABLE_
  ATTRIBUTES`, derived from the same canonical vocabulary as
  `routers/scope.py:DeviceSoftwareAttributes` (one list, not two that
  drift — `test_domain_attribute_vocabulary.py` guards it, since Pydantic
  fields can't be generated from a plain frozenset without losing each
  field's type/validator).

  **Per entity type, recommended and justified:** only DEVICE/SOFTWARE
  have a defined canonical vocabulary today, so only they get an
  allowlist; every other entity type (PERSON included) falls back to
  comparing every attribute key, completely unchanged. Extending this to
  PERSON would mean inventing a canonical PERSON vocabulary, already
  explicitly ruled out of scope in the Liongard-contacts-import slice —
  the canonical vocabulary itself *is* the right basis for DEVICE/
  SOFTWARE (checked, not assumed: it names exactly the fields that should
  matter), so this wasn't a "stop and ask" case; it's simply that no
  equivalent vocabulary exists yet for PERSON. **PERSON therefore still
  has the identical telemetry-diff-noise problem** — a named, deliberate
  gap for a future slice, not an oversight, and not silently fixed by
  implication here.

  **`last_login_user` — the judgment call, made deliberately:** canonical
  (validated, shown in the asset drawer) but excluded from the comparison
  allowlist. It's telemetry that changes legitimately whenever a
  different person logs in; comparing it would reintroduce the same
  noise problem at a lower frequency. Consequence, stated plainly: its
  *stored* value still updates on every apply, but only when a row is
  re-applied for some OTHER, genuinely meaningful reason — a device whose
  only drift, sync over sync, is who logged in will not surface as
  CHANGED, and its stored `last_login_user` can go stale until something
  else about it changes. Judged acceptable: the field is presented (asset
  drawer) as "last observed as of the most recent sync that touched this
  record," never a live value, and nothing authoritative (ownership,
  compliance status) depends on its freshness.

  **The allowlist's cost, addressed, not left invisible:**
  `importers/liongard.py:_unrecognized_device_attributes()` warns (via
  the existing per-row `warnings` channel — no new plumbing, same
  mechanism `_resolve_device_subtype()`'s "unrecognized Type" warning
  already uses) when a pull's attribute keys fall outside both the
  allowlist and a known-field set (the canonical vocabulary +
  `connectors.liongard.DEVICE_PROFILES_SORT_BY`, Liongard's own
  confirmed field-name list, + a short supplementary set of real fields
  observed live but absent from that sortable-fields-only enum).
  Confirmed against two real WinsorLabs device records to produce zero
  warnings today (committed as a permanent regression fixture, not just a
  one-off check), and against a synthetic new field to confirm the
  warning actually fires when it should.

  Bench-verified on an isolated wl-util-1 stack: ruff clean,
  **1113/1113** backend tests (15 new in `test_reconcile.py` — the exact
  telemetry-only-drift reproduction from the prior session, now inverted
  to prove UNCHANGED, plus every meaningful attribute tested individually
  for the dangerous "allowlist too narrow" direction, plus the
  `last_login_user` exclusion tested both alone and combined with a real
  change, plus PERSON's fallback-to-everything behavior proven not just
  assumed; 2 new in `test_domain_attribute_vocabulary.py`; 3 new in
  `test_liongard_importer.py` for the unknown-field warning; 1 new
  end-to-end integration test reproducing §4's exact scenario — a
  pre-this-slice applied row meets the new code, shows CHANGED once for
  the real reason, then a second sync is clean), `tsc -b` clean,
  `vite build` clean, **135/135** vitest (frontend untouched by this
  slice — the existing per-row `warnings` rendering in
  `ScopeChangeDiffTable.tsx` already surfaces the new warning with no
  code change needed).

  **Deployed** per `docs/deployment.md` §7 with `--no-deps` (confirmed
  again: `db`/`minio` untouched). Backup taken and verified (444 TOC
  entries). No migration — confirmed via `docker logs`, none expected
  (Pydantic validation + pure Python mapping/comparison logic only).

  **Verified live against the real WinsorLabs environment, full proof
  completed:** first post-deploy dry-run against the same 2 already-live
  devices (still unapplied from the prior slice's own dry-run-only check)
  showed `changed` with **exactly one field in the diff —
  `display_name`** — where before this fix it would have included
  `LastSeenTimelineID`, `LastSeen`, `UpdatedOn`, `AvailableStorage`, etc.
  every time. Zero unrecognized-attribute warnings. Applying it was Jarrod's
  call, not assumed -- asked explicitly, approved, then applied
  (`applied: 2`, 2 new `scope_entity.import_apply` audit entries,
  direct query confirms both rows now carry real `display_name`/
  `last_login_user` values). **The second dry-run, run immediately after
  against Liongard live (genuine telemetry drift between the two real API
  calls, minutes apart) came back completely clean: `{"new": 0, "changed":
  0, "missing": 0, "unchanged": 2}`, `changes: []`, pull_status "2 devices
  compared against scope -- no changes."** This is the actual proof, live,
  not just on the bench stack: the exact reproduction from two sessions
  ago, inverted and closed.

- **AI provider credential moved into the connector registry** (2026-09-19)
  — closes the follow-up the document-ingestion Done entry above explicitly
  deferred. `ANTHROPIC_API_KEY`/`WINGRC_AI_PROVIDER` were the sole
  third-party credential in the product still living in a plaintext env
  var, teaching every reader of `.env.example` (AGPL, MSP-self-hosted) the
  opposite of every other integration credential's pattern. Registered a
  new `"ai"` connector (`backend/app/connectors/ai.py`, `kind="ai"` — a
  new display-only grouping, never an authorization input, alongside
  `data_source`/`notification`): `config_fields` = Provider (a `select`
  offering only `anthropic`, since that's all `get_ai_provider()` has ever
  constructed — azure_openai/local stay unimplemented, so not rendered as
  choices that would 500) + Model (defaults to `claude-sonnet-4-6`,
  overridable); `credential_fields` = `api_key`, write-only/encrypted via
  the existing Fernet/`IntegrationConnection` pattern, last-4 hint only.
  `test_connection` makes a minimal (10 max_tokens) real completion call;
  error categorization (auth/permission/rate-limit/not-found/timeout/
  connection/generic-status) is built from the real `anthropic` SDK's
  exception hierarchy, read from the installed package's own
  `_exceptions.py` source rather than guessed.

  **Env-var fallback: dropped, not kept.** Matches the Liongard/SMTP
  precedent (one configuration path, no env-var alternative) rather than
  risking a silent-override precedence question between a stale env var
  and whatever Administration shows. `Settings.ai_provider` (`config.py`)
  is removed entirely — `ai/__init__.py:get_ai_provider()` now takes a
  `Session` and resolves from `integration_connection`
  (`connector_key="ai"`) instead of a string setting.
  `.env.example`/`CLAUDE.md`/`backend/Dockerfile` updated to stop
  referencing the env var.

  **Three distinguishable "not configured" states**, per
  `ai/__init__.py:get_ai_provider()`'s own docstring: (1) no row, or a row
  with the credential removed → `NullProvider` → the existing
  `RuntimeError` → `DocumentIngestError` → clean `422` degradation,
  unchanged in shape, message updated to point at Administration → AI
  Provider instead of env vars; (2) a credential that fails to decrypt
  (`CredentialCipherError`, a deployment-level
  `WINGRC_CREDENTIAL_ENCRYPTION_KEYS` problem) → left uncaught by
  `get_ai_provider()`, mapped to `500` at
  `routers/admin_products.py:import_from_documents`; (3) a credential that
  decrypts but fails `connectors/ai.py:parse_settings()` (an unsupported
  provider value) → wrapped as `RuntimeError` → `422`. A fourth,
  call-time-only state (bad key/rate limit/network/model-not-found once a
  real `AnthropicProvider.complete()` call is attempted) is
  `AnthropicProvider`'s own concern, not `get_ai_provider()`'s — surfaced
  through the same `run_completion()` error categorization
  `test_connection` uses, so the two can never report a given failure
  differently.

  **No caching** — per-request resolution (one DB round-trip + Fernet
  decrypt per document-ingestion call) is immeasurable next to the
  multi-second AI completion it precedes; document ingestion is the only
  caller and is rare/human-initiated. Not the "needs a cache with a
  non-obvious invalidation story" case the task's own stop-condition
  named — no cache was needed at all, so there was nothing to design an
  invalidation story for.

  Bench-verified on an isolated wl-util-1 stack: ruff clean, **1138/1138**
  backend tests (16 new in `test_ai_connector.py` — `parse_settings`
  validation plus `run_completion`'s exception categorization for every
  named `anthropic` SDK exception type, each constructed as a *real*
  instance via `httpx2.Request`/`Response`, not a stand-in class; 13 new
  in `test_integrations.py` mirroring the existing Liongard/SMTP router
  coverage — list/RBAC/never-echoed/ciphertext-at-rest/test-connection
  ok-fail/delete), `tsc -b` clean, `vite build` clean, **135/135** vitest
  (`AdminArea.test.tsx` unaffected by the new "AI Provider" nav section).

  **Verified live, with a real (Jarrod's own) Anthropic API key** —
  entered directly by Jarrod against the isolated bench stack, never by
  the agent, per the standing rule against entering credentials on the
  user's behalf even with explicit authorization: (1) `PUT
  /integrations/ai/credential` stored it, `credential_hint` only, never
  the key, in the response, the audit log, or any other endpoint; (2) DB
  row's `encrypted_credential` confirmed as genuine Fernet ciphertext
  (`gAAAAAB...` prefix), not plaintext or base64; (3) first test-connection
  attempt (before Jarrod added billing credit) produced a real network
  round-trip to Anthropic and a specific `HTTP 400` — "credit balance too
  low" — proving both the request path and the generic `APIStatusError`
  catch-all (a status code this module doesn't name individually) work
  against the real API, not just mocks; (4) after credit was added,
  test-connection succeeded for real (`last_test_ok: true`); (5) deleting
  the credential and re-attempting ingestion produced the exact clean
  `422` — "No AI provider configured. Configure one in Administration ->
  AI Provider..."; (6) a real end-to-end ingestion (a small
  `weasyprint`-rendered PDF describing a fictitious "SentinelGuard EDR"
  product) returned `200` in ~39s with coherent AI-drafted YAML correctly
  identifying `SI.L1-3.14.2` and stamping
  `ai_generated_model: anthropic:claude-sonnet-4-6`; (7) `customer_poc`,
  `msp_engineer`, and `c3pao_assessor` all got `403` on both `GET
  /integrations` and `POST /integrations/ai/test`; (8) Liongard/SMTP
  connectors' own tests passed unchanged as part of the full regression
  suite above. The wrong-key/`401` path itself was proven with a real
  `anthropic.AuthenticationError` instance in `test_ai_connector.py`
  (constructed via genuine `httpx2` request/response objects, not a
  stand-in) rather than live, since deliberately submitting a bad key is a
  credential-entry action reserved for Jarrod the same as the real one —
  the live `400` catch-all above already proves the request/categorization
  path is real, not mocked.

  **Deployed** 2026-09-15 per `docs/deployment.md` §7 with `--no-deps`
  (backup taken and verified — 444 TOC entries — before deploying; no
  migration in this slice, so no schema diff to confirm). Hit and fixed a
  new deploy-tooling gotcha along the way: running `docker compose up`
  from inside a docker-CLI helper container mounted at an aliased path
  (`/repo`) rather than the real host path broke `backend`'s
  `./backend:/app` bind mount (the host daemon resolved it against the
  alias, not the real checkout, mounting an empty directory over `/app`
  and crash-looping `backend` on a missing `alembic.ini`) — recorded
  permanently in `docs/deployment.md` §7c so it isn't rediscovered the
  hard way again. Live post-deploy confirmation: `REGISTRY` includes `ai`
  alongside the untouched `liongard`/`smtp` (both still show
  `configured: true`, unaffected by the deploy), and the same clean `422`
  degradation confirmed against the real production database (a
  write-nothing dry-run call, safe to exercise live). Jarrod enters the
  real Anthropic API key through Administration → AI Provider on the live
  deployment (not `.env`) to activate document ingestion there — it was
  configured only on the throwaway bench stack during verification, torn
  down afterward; production itself has no key yet.

- **Document-ingestion input hardening + product-key normalization**
  (2026-09-19). Two independent fixes in the same slice, both in
  `importers/document.py`'s extraction path and `Product.key`'s write
  paths.

  **Extraction hardening.** A truncated/corrupt PDF (`pypdf.errors.
  PdfStreamError`/`EmptyFileError`), a password-protected PDF
  (`FileNotDecryptedError` — checked proactively via `reader.is_encrypted`
  rather than waiting for the exception), a legacy `.doc` file uploaded as
  `.docx` (detected by OLE2 magic bytes before python-docx ever gets a
  `zipfile.BadZipFile`), a genuinely corrupt `.docx`, and a valid zip that
  isn't a real Word package (`KeyError` on `[Content_Types].xml`) each now
  raise a specific `DocumentIngestError` naming the file, instead of a raw
  500. **Known gap, recorded not silent:** no OCR — a scanned-image PDF
  with no text layer is *detected* (`_MIN_EXTRACTED_TEXT_CHARS = 50`, a
  deliberately low floor that catches "extracted next to nothing" without
  rejecting legitimately short real documents) and reported by name
  before ever reaching the AI provider, never silently sent as an empty
  document and presented as a real mapping. Building OCR is out of scope;
  the workaround is a text-based re-export or hand-entry.

  A real bug this slice's own new router-level tests caught: the
  document-ingestion endpoint wrote each upload to `tempfile.mkstemp()`'s
  randomly-named file, so every error message (and `source_docs` in the
  returned YAML) named a meaningless `tmpXXXXXX.pdf` instead of what the
  admin actually uploaded — "say which document failed" only works if
  the name shown is the real one. Fixed by writing into a per-request
  temp directory under the real (sanitized) filename instead.

  **Product key normalization.** `Product.key` was user-entered,
  `unique=True`, and only `.strip()`ed — `DattoRMM` and `dattormm` would
  silently become two distinct products with two separate baselines.
  `seeds/baselines.py:normalize_product_key()` lowercases and validates
  against a slug pattern (`^[a-z0-9]+(-[a-z0-9]+)*$`); anything containing
  a disallowed character (a space, most commonly) is rejected with the
  expected shape rather than silently mangled. Wired into every write
  path: the document-ingestion endpoint's `product_key` form field
  (rejected before the paid AI call, not after), `baseline_import.py`'s
  `validate()` (added to the reported problems list) and `build_preview()`
  (normalized before the existing-product lookup, so a re-import spelled
  differently in case is recognized as an update, not a new product), and
  `seeds/baselines.py:_seed_product()` itself — the one real write path
  both the CLI seed and the admin YAML import funnel through, so
  normalization only needed to be correct in one place.
  `baselines/rocketcyber.yaml`'s key (`rocketcyber`) was already
  conformant; confirmed live (below) that re-seeding it resolves to the
  exact same `Product.id` as before — no orphaned row.

  Bench-verified on an isolated wl-util-1 stack: ruff clean, **1161/1161**
  backend tests (36 new — 11 extraction-hardening unit tests in
  `test_document_ingest.py` against genuinely broken real files built
  with pypdf alone, no new dependency; 12 router-level tests in
  `test_admin_products.py` proving the full HTTP path returns 422 (never
  500) for each fixture and correctly names which of two uploaded
  documents failed; product-key normalization/dedup tests for both the
  document-ingestion endpoint and the YAML import path), `tsc -b` clean,
  `vite build` clean, **135/135** vitest (frontend untouched by this
  slice). `wingrc seed-baselines` re-run twice against a fresh catalog
  confirmed idempotent and produced the identical `Product.id` both times.

  **Deployed** 2026-09-15 per `docs/deployment.md` §7 with `--no-deps`
  (backup verified — 444 TOC entries; no migration in this slice).
  Mounted the deploy helper container at the real host path this time
  (the §7c gotcha from the AI-connector deploy just above) — clean
  deploy, healthy immediately, `db`/`minio` untouched.

  **The real end-to-end run, for the first time.** Jarrod had by this
  point entered a real, funded Anthropic key through Administration on
  production (confirmed live: `configured: true`, `last_test_ok: true`).
  The task's own candidate document (a Datto RMM/Kaseya CRM) wasn't
  available on this box; used a genuine real vendor CRM that was —
  RocketCyber's own CRM PDF, already attached to the real `rocketcyber`
  product as a `ProductDocument` (585,725 bytes, ~19,500 extracted
  characters, ~4,900 estimated input tokens). Ran through the actual
  `/admin/products/import/from-documents` endpoint (not a unit-level
  call) against a throwaway key (`rocketcyber-real-ingest-test`) so the
  real, published `rocketcyber` product was never touched — confirmed
  unchanged afterward (same `Product.id`, still published) — and deleted
  the throwaway product once verification was done.

  Result: `200`, **74.5s elapsed**, 17 control entries, split 16
  `shared` / 1 `customer_owns` (the full IA family, correctly batched and
  disclaimed — "Kaseya enables SSO pass-through but does not own any of
  these controls," matching the exact IA-family pattern CLAUDE.md's
  hard-rules section names as the reason this classification exists).
  `coverage_basis` unset on all 17/17, exactly as designed — confirmed by
  `apply` itself: the first apply attempt correctly **rejected** the raw
  AI output with a 422 naming all 16 non-`customer_owns` entries, since a
  reviewer must set `coverage_basis` before anything can land. Completed
  that one reviewer step by hand (as the real UI would) for a single
  representative entry and re-applied: `201`, `is_published: False`,
  zero `OrgProduct` rows referencing it — nothing reached a tenant.

  **Plausibility review (the task's own explicit ask, not just "did it
  parse"):** two entries (`AC.L2-3.1.8`, `CM.L2-3.4.2`) were classified
  `shared` while their own AI-written note reads as a full disclaim
  ("Kaseya explicitly states it does not implement or enforce...",
  "...does not manage or enforce the customer's chosen configurations")
  — arguably `customer_owns` cases per CLAUDE.md's own "never auto-credit
  a vendor CRM" rule, not `shared`. Not treated as a bug in this slice
  (the extraction prompt is explicitly out of scope here) — surfaced
  because this is exactly the failure mode the mandatory human-review
  step before `apply`/`publish` exists to catch, and it caught it: this
  candidate was never applied under the real key, and a real reviewer
  would need to correct these two before using this output. Worth a
  future prompt-tuning pass, tracked here rather than silently accepted.

  **Cost**: not captured precisely — `AIProvider.complete()` doesn't
  expose token usage, and adding that was out of this slice's scope.
  ~4,900 input tokens (extracted text) + system prompt, plus a
  substantial 17-entry structured output; exact dollar cost is on
  Jarrod's Anthropic console usage page for this timestamp, not restated
  here as a guessed figure.

- **Fix 504 on document ingestion** (2026-09-15). Jarrod's real Datto
  RMM/Kaseya upload was 504ing through the live proxy despite the slice
  just above having verified a real end-to-end run.

  **Root cause.** `deploy/nginx/nginx.conf`'s `location /api/` set no
  `proxy_read_timeout`, so nginx used its 60s default. The prior slice's
  74.5s verified run — and this slice's own repeat of it, 71s (below) —
  both exceed that. The Anthropic call completed and billed every time;
  the result was discarded because nginx had already closed the
  connection. A retry spends the money again and is likely to time out
  again the same way.

  **The verification gap that let this ship, twice now.** The prior
  slice's "real end-to-end run" called the endpoint directly, not through
  nginx — the proxy is part of the path every real user traverses, and is
  exactly what silently broke this. This is the same shape of gap as the
  Liongard connector having only ever been verified against a mock server
  (docs/roadmap.md's D.3 entry) — worth recording as a pattern this
  project keeps needing to relearn, not a one-off incident. Verification
  claiming "end to end" must mean through the actual deployed stack a
  browser hits, not a shortcut around any of it.

  **Fix.** A route-scoped `location = /api/admin/products/import/from-documents`
  block in `deploy/nginx/nginx.conf`, not a blanket increase for all of
  `/api/` — a genuinely hung request on any other route would otherwise
  tie up a connection for as long as the raised timeout, for no reason.
  `proxy_read_timeout`/`proxy_send_timeout` set to **300s**, chosen as
  headroom above the two verified real runs (74.5s, 71s) without being
  unbounded. `client_max_body_size` set to **105m** on the same route
  (nginx's compiled-in 1m default was otherwise silent about a real risk:
  a legitimate upload over 1MB but under the app's own 50MB-per-file cap
  — `_MAX_FILE_BYTES`, `evidence.py` — would have been rejected by nginx
  with a bare, unhelpful 413 before the backend's own clear error message
  ever ran). 105m covers `_MAX_INGEST_FILES = 2` × 50MB plus multipart
  overhead — comfortably above anything the app itself would ever accept,
  so the app's own per-file message is what a reviewer actually sees for
  any oversized upload, not nginx's generic page.

  **Bench-verified** on an isolated `wingrc_verify_20260915` stack (live
  `wingrc` project confirmed running, untouched, before and after):
  **1161/1161** backend tests, ruff clean, `tsc -b` clean, **135/135**
  vitest, `vite build` clean. nginx config syntax validated with
  `nginx -t` run from the newly-built image, attached to the bench
  project's own network so the `backend` upstream actually resolves
  (mounting the real `wingrc_certs` volume read-only) — syntax ok.

  **Deployed** 2026-09-15 per `docs/deployment.md` §7c with `--no-deps`
  — this slice touches only `deploy/nginx/nginx.conf`, so only `nginx`
  was rebuilt and recreated; `backend`/`worker`/`db`/`minio` were left
  running untouched (no migration, nothing else changed).

  **Verified live, through nginx, in a real browser, against the real
  deployment** — the specific gap that produced this bug, so this is the
  one check that actually matters here. A normal route
  (`GET /api/health`) still returns `200` in ~6ms — the timeout change is
  scoped correctly and doesn't affect anything else. A synthetic 55MB
  file reached the backend (a clean `401`, not a proxy-level rejection) —
  proof `client_max_body_size` now clears real oversized-but-plausible
  uploads. A synthetic 120MB file (deliberately past the app's own
  100MB ceiling) got nginx's own `413` immediately (0.004s, no hang) —
  correct, since nothing legitimate is ever that large. Then, logged in
  as msp_admin at `https://dev.wingrc.us`, uploaded Jarrod's actual
  591KB Datto RMM/Kaseya CRM PDF through the real "Generate from vendor
  documents" UI: nginx's access log shows the request running from
  14:12:53 to 14:14:04 — **71 seconds** — returning `200` with a valid
  dry-run candidate (coverage_basis correctly left unset on every shared/
  provider_satisfies entry, several objective-key mismatches surfaced for
  a reviewer to fix — a content-quality finding, not a bug in this
  slice). Confirmed via a live query that no `product` row was created
  (`Apply Import` was never clicked) — a pure dry-run preview, nothing
  written, exactly as designed.

  **Should this be synchronous? No — recommended, not built here.**
  Reusing the existing scheduler (`scheduler.py`, `JOB_REGISTRY`,
  `job_run`, `run_due_jobs()`) is a mismatch for this, not a fit:

  - `JobSpec.run` takes only a `Session` — there's no way to pass a
    specific request's inputs (uploaded files, `product_key`, category),
    and a job's identity is its fixed registry name, not a per-request
    instance. The scheduler has no concept of "one job, one caller, one
    result" at all.
  - `_is_due()`'s whole model is "check periodically against the last
    run's interval" — built for `expire_stale_invites` running hourly,
    not for "run this one specific thing right now because a user just
    clicked a button."
  - `job_run.result` is a small JSON column sized for a scheduler's own
    log line, not for handing back a multi-control candidate YAML plus a
    preview diff to a specific waiting request.
  - The worker loop's polling cadence is tuned for "sometime in the next
    interval," not a user watching a spinner.

    Forcing this shape onto `JOB_REGISTRY` would mean bolting a
    per-request job concept, arbitrary result storage, and an
    immediate-trigger path onto infrastructure deliberately kept simple
    for periodic sweeps. A small dedicated mechanism, reusing the same
    *pattern* scheduler.py already proved out (a separate worker process,
    Postgres-backed, crash-safe) rather than reusing `JOB_REGISTRY`
    itself, is the better fit.

  What it would need: (1) the POST endpoint returns `{job_id}` (`202`)
  instead of blocking; (2) a poll endpoint
  (`GET .../import-jobs/{id}`) returning status and, once done, the
  result; (3) a small `document_ingest_job` table (status, inputs,
  result JSONB, error, requested_by, timestamps, and a retention/expiry
  — see the wasted-spend note below); (4) uploaded files staged in the
  existing MinIO-backed storage (`StorageClient`) rather than a local
  temp dir, since the worker container is a different process than
  `backend` and needs to read what was uploaded; (5) a frontend poll loop
  replacing today's blocking "Generating…" button state, with a "my
  recent ingestion jobs" list so a dropped connection doesn't strand the
  user with no way back to a completed result. Rough size: comparable to
  the AI-provider-connector or input-hardening slices above — one small
  migration, 2-3 new endpoints, one new worker-side execution path
  (a short-interval poll or `LISTEN`/`NOTIFY`, not `JOB_REGISTRY`), and a
  frontend polling UI. Not a one-day fix, not a large slice either.
  Jarrod decides whether to build this now or ride on the 300s timeout
  for longer.

  **The wasted-spend problem — recommended only.** Even with a 300s
  timeout, a dropped connection (VPN blip, closed laptop, corporate proxy
  with its own limit) still discards a completed, billed extraction. If
  the async job above lands, this is solved for free — the `job_run`-
  style row holds the result regardless of whether the original
  connection is still open. If it doesn't land soon: consider caching
  the completed candidate briefly (short TTL, e.g. keyed to a hash of
  the uploaded file(s) + `product_key`), so an identical retry after a
  dropped connection can fetch the already-paid-for result instead of
  re-billing. Not built here — likely entirely subsumed by the async job
  above, so worth deferring until that decision is made rather than
  building a cache for a problem async would also fix.

- **CI's `backend` job silently broken since the AI-provider-connector
  slice** (2026-09-15, found while chasing an unrelated CI failure
  notification). `.github/workflows/ci.yml`'s `backend` and `integration`
  jobs both ran `pip install -e ".[dev]"`, but `backend/Dockerfile`
  installs `.[dev,ai]` — `anthropic`/`pypdf`/`python-docx` (the `ai`
  extra) are imported at module scope by `test_ai_connector.py`,
  `test_document_ingest.py`, and `test_admin_products.py`, so collection
  failed regardless of the `integration` marker. Every push since
  `20a6709` (the AI-provider-connector commit) had a red `backend` job on
  GitHub — invisible to this project's own bench-verification workflow,
  which builds from the Dockerfile and so always had both extras
  installed. Fixed by installing `.[dev,ai]` in both jobs' Install step,
  matching the Dockerfile; reproduced the failure and the fix in a bare
  `python:3.13` container against a fresh clone (no Docker image, no
  compose env) to match CI's actual environment exactly, rather than
  trusting the bench-stack result for this. Confirmed green on `main`
  after merge: `backend` (285 passed, 876 skipped), `integration`, and
  `image` all succeeded (run
  https://github.com/winsorlabs/wingrc/actions/runs/35006863844).
  **Lesson for this project's own verification practice:** an isolated
  bench-stack pass is not the same claim as "CI is green" when the two
  don't install dependencies the same way — check GitHub Actions status
  directly after a push, don't infer it from a passing bench run.

- **Structured, editable per-control review table for AI-drafted
  baselines** (2026-09-15). Came directly out of using the ingest-504 fix:
  reviewing Jarrod's real Datto RMM/Kaseya CRM produced 21-44 `coverage_
  basis` decisions per run (LLM output varies run to run — see the
  question this replaced, "how do I fix 21 problems," for the concrete
  trigger), and the only way to fix them was hand-editing a raw YAML
  textarea by counting `controls[N]` array indices against a flat list of
  error strings.

  **Backend.** `baseline_import.py`'s `validate()` is now a thin wrapper
  over a new `validate_structured()`, which tags every problem with
  `(row_index, field)` instead of only a formatted string — same
  messages, same order, so every existing caller/test (including
  `/import/apply`'s 422 path) is unaffected. `build_preview()` no longer
  gates row/diff construction behind "the whole file has zero problems"
  (the old `if not problems:`) — it now renders a `ControlEntryDraft` for
  every structurally-parseable entry (one that survived validate's own
  first pass: a dict with a usable `control` id/list) regardless of what
  else is wrong with it, since showing a reviewer the bad row to fix is
  the point, not hiding it until everything else is already right. New
  `POST /admin/products/import/dry-run-structured` takes the same
  `{product, controls}` shape as a plain JSON body instead of a YAML
  file — deliberately not a strict Pydantic body, since `validate()`
  already turns a malformed shape into a reportable per-row problem and a
  strict schema would instead hard-422 on exactly the shapes this
  endpoint exists to describe. `/import/apply` itself is completely
  untouched — the frontend always submits the server's freshly
  re-serialized `yaml` string (computed fresh on every structured preview
  call), never anything it serialized itself.

  **Frontend.** `ToolImportWizard.tsx`'s documents mode replaced the raw
  YAML textarea with a table: one row per control, `classification`/
  `coverage_basis`/`candidate_state` as `<select>` dropdowns (`coverage_
  basis` disabled with "not applicable" for `customer_owns` rows, per the
  hard rule that field is moot there), free-text fields (objectives,
  provider_contribution, customer_action, note, scope_note) as inputs, and
  a repeatable evidence mini-list (artifact/type/kb, add/remove). A row
  with any outstanding problem gets a red background and the specific
  flagged field gets a red border with the exact problem message inline —
  no more counting indices. Re-check is an explicit button (not
  auto-fire-on-blur, per Jarrod's call) that posts the edited draft as
  JSON to the new endpoint; a `dirty` flag (set on any edit, cleared only
  by a successful re-check) blocks Apply so it can never submit a `yaml`
  that no longer matches what's on screen. "Upload YAML" mode is
  completely untouched, per scope decision — a hand-authored file doesn't
  have the same "AI left N fields deliberately blank" problem this exists
  to solve.

  **Bench-verified** on an isolated `wingrc_verify_structured` stack
  (live `wingrc` project confirmed running, untouched, before and after):
  **1165/1165** backend tests (4 new, covering the structured endpoint's
  row/field tagging, edit-then-clear, and a malformed-classification row
  still rendering instead of crashing), ruff clean, **136/136** vitest (2
  new covering the flagged-dropdown/re-check/clear cycle and the dirty-
  blocks-apply behavior), `tsc -b` clean, `vite build` clean.

  **Deployed** 2026-09-15 per `docs/deployment.md` §7c with `--no-deps`
  — `backend`/`worker`/`nginx` rebuilt and recreated (this slice touches
  backend routes and the frontend bundle nginx serves); no migration, so
  no backup per §7a's own scoping; `db`/`minio` confirmed untouched.

  **Verified live, through nginx, in a real browser** — same Datto
  RMM/Kaseya PDF as the ingest-504 fix, same real Anthropic call (this
  run produced 30 controls, 27 of them `shared`/`provider_satisfies`
  needing a `coverage_basis` decision, 3 `customer_owns` correctly showing
  the dropdown disabled). Set all 27 via the new dropdowns (one via a real
  click to confirm the interaction wires up genuinely, the rest via
  scripted native-setter change events for efficiency — the same
  underlying React onChange path either way), clicked Re-check, confirmed
  every red flag cleared and Apply Import unlocked, applied under a
  throwaway key (`dattormm2`, never published, never linked to any org),
  and confirmed via a live query that `baseline_control.coverage_basis`
  matched what was picked in the UI (27 rows `customer_system` as chosen,
  3 `customer_owns` rows at the schema default) — not left at whatever the
  column's default happened to be. Deleted the throwaway product and its
  30 baseline_control/33 evidence_spec rows afterward (`product_document`
  cascades from `product` on delete, confirmed no orphaned row survived).

- **Edit an existing product's baseline mapping; add control rows; wider
  review table** (2026-09-16). Direct follow-on ask: "update/edit the
  Baseline Mapping after import," "add additional lines/controls if
  needed," and the review table using more of the screen with a
  reachable scrollbar.

  **Edit entry point — zero new backend endpoints.** `ToolDetailPanel.tsx`
  gained an "Edit Baseline Mapping" button that opens `ToolImportWizard`
  with a new `editProductId` prop: on mount it fetches the product's
  current mapping (the same `GET /admin/products/{id}` `ToolDetailPanel`
  already used), transforms it into the identical draft shape the AI-
  ingestion path produces, and immediately posts it to the existing
  `dry-run-structured` endpoint to get a real validated preview before
  showing the table — the exact same Re-check/Apply machinery, no new
  write path. The `key` field is read-only while editing (`_seed_product`
  upserts by key; an accidental edit there would silently retarget Apply
  at a different product). A known, accepted limitation: multi-control
  batch grouping (`batch_group_id`) isn't recoverable from the read model
  and is dissolved on a round trip through this screen — cosmetic only,
  nothing currently renders that grouping anyway.

  **"+ Add control"** appends a blank row (both entry points, since they
  share one table). Required a real backend fix to support safely:
  `build_preview()` used to only render a row for entries whose `control`
  field was *already* valid, so a still-blank added row would silently
  vanish the moment Re-check ran. It now renders a row for every dict-
  shaped entry regardless, with its own "control id required" flag
  attached — confirmed live (see below) rather than just in tests.
  Deliberately no row-removal in this slice: removing an *already-applied*
  row wouldn't actually delete anything (`_seed_product` has no
  delete/tombstone mechanism at all, confirmed by reading it start to
  finish), so offering a remove button would silently lie about what it
  does; not asked for here either way.

  **A real bug this slice's own new regression test caught, not just
  inspection:** `_seed_product`'s evidence-spec handling was changed from
  unconditional delete-and-replace to content-based matching (an
  unchanged spec keeps its row — and any `EvidenceTask.baseline_spec_id`
  already pointing at it — instead of that link being severed on every
  apply regardless of whether anything changed). Implementing this
  surfaced a genuine, pre-existing production bug: `/admin/products/
  import/apply` runs under the RLS-enforced `wingrc_app` role with no
  `app.current_org` set (the baseline library is deployment-wide, never
  org-scoped), and `evidence_task` IS org-scoped/RLS-protected — a bare
  ORM `UPDATE evidence_task SET baseline_spec_id = NULL WHERE ...` under
  that role silently matched zero rows, invisible to a plain SELECT too,
  while Postgres's FK constraint check on the following DELETE still saw
  the real row and correctly rejected it with an `IntegrityError`. This
  was already latent in the *original* delete-and-replace code (same bare
  UPDATE shape) — no existing test before this slice combined "a real
  `EvidenceTask` exists" with "re-import that product through the actual
  admin endpoint" in one case, so it never surfaced. Fixed with migration
  `0051_null_task_spec_ref_secdef` — `auth.null_evidence_task_baseline_
  spec_refs(uuid[])`, a SECURITY DEFINER function matching `auth.expire_
  stale_invites()`/`auth.mark_sprs_reminder_sent()`'s established
  precedent exactly (0042/0044): never a blanket RLS bypass, one
  purpose-built cross-org function for the one operation that needs it.
  Also hit, and fixed forward before merging: a 39-character revision id
  broke the `alembic_version` table's 32-char column — the exact same
  failure class `docs/bench-stack-verification.md`'s own opening
  paragraph already names as a real bug this workflow has caught before.

  **Wider modal + reachable scrollbar.** New `.wizard--wide` CSS modifier
  (not a change to the shared `.wizard` class the other four wizard
  components use) widens this one modal to `min(1400px, 95vw)`. Confirmed
  via research before building it that no CSS-only way exists in any
  current browser to pin a container's own `overflow-x` scrollbar to a
  fixed position independent of the container's actual height — a native
  scrollbar isn't a repositionable DOM node. Built the standard real-world
  workaround instead: a slim `position: sticky; bottom: 0` strip inside
  `.wizard-body` (so it tracks the visible scroll viewport, not the bottom
  of the — possibly 20+-row — table) whose `scrollLeft` is mirrored both
  ways with the real table wrapper via `onScroll` handlers, rendered only
  when the table is actually overflowing.

  **Bench-verified** on an isolated `wingrc_verify_editmap` stack (live
  `wingrc` project confirmed running, untouched, before and after):
  **1168/1168** backend tests (3 new — the RLS/evidence-FK fix's two
  cases, the blank-row-survives-Re-check case), ruff clean, migration
  chain (`upgrade → downgrade → upgrade`) clean, **138/138** vitest (2
  new — edit-mode loads pre-validated with `key` read-only, a newly-added
  blank row survives Re-check flagged instead of vanishing), `tsc -b`
  clean, `vite build` clean.

  **Deployed** 2026-09-16 per `docs/deployment.md` §7 with `--no-deps` —
  this slice carries a migration (new function only, no data touched), so
  §7a's backup ran first and was verified (444 TOC entries) before
  `backend`/`worker`/`nginx` were rebuilt and recreated; `db`/`minio`
  confirmed untouched; the new `auth.null_evidence_task_baseline_spec_
  refs` function confirmed present live via `\df`.

  **Verified live, through nginx, in a real browser.** Opened the real,
  published, actively-used RocketCyber product (Acme MSP has it active) —
  confirmed the edit screen loads pre-populated, already clean, with the
  live affected-org warning showing and the `key` field genuinely
  disabled — then deliberately backed out **without saving**, since this
  is real production data a live org depends on, not a throwaway. Built a
  separate, real throwaway product (`editflowtest`, via a plain YAML
  upload — one control, one evidence spec, no org ever activated it) to
  safely exercise the actual save path: edited the note field, clicked
  "+ Add control," clicked Re-check with the new row still blank and
  confirmed it stayed visible flagged (not silently dropped) exactly as
  designed, filled in a real second control id, Re-checked clean, Save
  Changes, confirmed via a live query that both the edited note and the
  new second control landed, that the original (unchanged-content)
  evidence spec kept a stable row, and that the product was correctly
  force-unpublished. Deleted the throwaway product and its 2 baseline_
  control/1 evidence_spec rows afterward. Noted along the way — and left
  alone, since it's real, current work, not test debris — a `datto-rmm`
  product already unpublished in the library from Jarrod's own earlier
  session testing the ingestion tool against his real Datto RMM document.

---

### WINGRC_PUBLIC_URL wiring, AI-classification disclaim flag, PERSON reconcile vocabulary ✅ DONE (2026-09-16)

Three-part slice, landed together.

**1. `WINGRC_PUBLIC_URL` was never actually wired into `docker-compose.yml`**
despite `config.py`'s `Settings.public_url` field already existing —
confirmed via grep before this slice returning nothing. Added to the
shared `x-backend-env` anchor (`${WINGRC_PUBLIC_URL:-}`) so both `backend`
and `worker` see it (the worker is what actually sends review-cycle/SPRS-
reminder notifications). Documented in `docs/deployment.md`. Set live to
`https://dev.wingrc.us` on wl-util-1 and verified end-to-end: forced a
`review_cycle_sweep` run (`app.scheduler._review_cycle_sweep`, called
directly rather than waiting on its 24h due-window) against real cycle
`b327e64c-75c9-4651-ab83-e6a321822e4d` — both reviewers, including the
external Gmail address that can't resolve an in-house hostname (known/
accepted, not designed around), now show a populated `notified_at` and a
cleared `notification_error`, confirming the whole notification chain
works with a real public URL configured.

**2. Disclaim-flag check** (`baseline_import.py:_disclaims_coverage()` /
`DisclaimFlag`) — a deterministic, post-AI, code-enforced check, same
pattern as the existing evidence-minimization rules. Motivated by a real
case: RocketCyber ingestion classified `AC.L2-3.1.8` and `CM.L2-3.4.2` as
`shared` while the model's own note said Kaseya "explicitly states it does
not implement/enforce" them — should've been `customer_owns`. A human
caught it in review; this makes that catch systematic. Scans `note`,
`provider_contribution`, `customer_action`, and `scope_note` (whichever
exist) for disclaim language against `_DISCLAIM_PHRASES`, a module-level,
commented, deliberately over-inclusive constant (a false positive costs a
glance; a false negative puts unearned credit in an SSP) — **expected to
grow** as more real ingestion runs surface phrasings it misses. Flags any
`shared`/`provider_satisfies` row whose own text reads as a disclaim;
**never auto-reclassifies** — advisory only, kept structurally separate
from `row_problems`/`ValidationProblem` (which block Apply). Re-tested
against the real RocketCyber wording that motivated this (both known
phrasings), confirmed a correctly-classified `customer_owns` row with
disclaim language does NOT flag, and confirmed a real `shared` row's
ordinary (non-disclaiming) text does NOT flag. Frontend: reordered the
review table so `Note`/`Provider contribution`/`Customer action`/`Scope
note` sit immediately after `Classification` (supporting text next to the
decision it supports), flagged rows get an amber `.disclaim-flag-row`
style distinct from the red `needs-decision` style — no confidence score,
nothing inviting a reviewer to skip the glance.

**3. PERSON reconcile vocabulary** — closes the noise gap DEVICE/SOFTWARE
already had fixed (`domain.py:DEVICE_SOFTWARE_COMPARABLE_ATTRIBUTES`);
PERSON previously compared every attribute key with no allowlist at all
(`importers/liongard.py` had explicitly called a canonical vocabulary out
of scope for D.2). Defined `domain.py:PERSON_CANONICAL_ATTRIBUTES` —
**`email`, `display_name`, `username`, `enabled`**, deliberately minimal —
derived from the same real-tenant field-reliability data already recorded
in `identity_to_contact_fields()`'s docstring (Goodwin-Bradley, 24
identities, 100% Email/Name coverage). **Treated as telemetry and
deliberately left un-canonicalized** (not canonicalized-then-excluded like
DEVICE's `last_login_user`): `AccountActivity`, `LastLogin`, `LastSeen` —
nothing downstream reads a canonical "last active" value for a person, so
there's nothing to preserve by canonicalizing them at all.
`importers/liongard.py:identity_to_canonical()` now maps raw Liongard
identity fields onto this vocabulary (`enabled` checked via `is not None`,
not truthy — `enabled=False` is a real, meaningful value, and a truthy
check would silently drop every disabled identity's status) and warns on
unrecognized fields, mirroring the device path's existing safety net.
Added `test_domain_attribute_vocabulary.py` coverage pinning the vocabulary
itself (no PERSON-equivalent Pydantic schema exists to sync against, unlike
`DeviceSoftwareAttributes` — confirmed by grep, nothing to drift).

**Live PERSON path remains unexercised** — WinsorLabs has zero identities
in Inventory state today, so this can only be bench-verified, not confirmed
against a real sync. Bench-verified thoroughly instead: two pulls differing
only in volatile/non-canonical fields reconcile to `UNCHANGED`
(`test_person_telemetry_only_drift_reconciles_to_unchanged`,
`test_person_non_canonical_raw_field_change_alone_reconciles_to_unchanged`),
and a real change to each of the four canonical attributes individually
still reports `CHANGED`
(`test_person_real_change_to_each_meaningful_attribute_reports_changed`,
parametrized, `enabled` case specifically toggling True→False to catch the
`is not None`-vs-truthy bug class). Device/software reconcile behavior is
unaffected (same allowlist, untouched).

**Bench-verified** on an isolated `wingrc_verify_0916` stack (live `wingrc`
project confirmed running, untouched, before and after): **1183/1183**
backend tests, `ruff check .` clean, **139/139** vitest, `tsc -b` clean,
`vite build` clean.

**Deployed** 2026-09-16 per `docs/deployment.md` §7 with `--no-deps` — no
migration in this slice (env var + code-only), so no backup step was
needed; `backend`/`worker`/`nginx` rebuilt and recreated, `db`/`minio`
confirmed untouched, `WINGRC_PUBLIC_URL` confirmed present via `printenv`
in both `backend` and `worker` post-deploy.

**Explicitly not started, per this slice's own scope boundary**: real
multi-tool coverage per control (needs a `control_state` data-model
change) and AI research of vendor platform documentation (needs
provenance/fetch-safety/cost design) — both deliberately deferred to their
own future slices, no placeholders or partial scaffolding added for
either.

---

### Multi-tool coverage: let multiple products satisfy the same control ✅ DONE (2026-09-16)

Real case: Jarrod's four Kaseya products (Datto RMM, IT Glue, RocketCyber,
SaaS Alerts) overlap by design on access control, logging, and system
integrity — more than one tool legitimately covers the same objective, and
he does not want that blocked.

**§0 — observed today's actual behavior first, on a bench stack, before
designing anything.** `control_state.sourced_from_product_id` could only
ever name one product. Confirmed live: activating a second product that
covers an objective already `met` with attached evidence **silently
regressed it back to `pending_evidence`**, overwrote `responsibility` with
no merge (last activation wins, full stop), and overwrote
`sourced_from_product_id` — the first product's contribution vanished from
the record entirely. Deactivation was worse: it only ever acted on
whichever product `sourced_from_product_id` currently named, so
deactivating the *first*-activated of two overlapping products did
**nothing at all** — not even a review flag — while the control kept
showing `pending_evidence` attributed to a product that was, by then, the
only one still actually active. This was the live, undocumented behavior
for every overlapping product before this slice.

**The model.** New `control_state_contributor` join table (`control_state_id`,
`product_id`, `baseline_control_id`, `created_at`, unique on
`(control_state_id, product_id)`) records every product currently
contributing coverage. `baseline_control_id` is the live answer to "why
does this product cover this control" — a real FK to the justifying row
(classification, provider_contribution, customer_action, note), not a
denormalized snapshot, so an edit to that baseline text via the
edit-baseline-mapping tool is reflected immediately, not frozen at
activation time.

`sourced_from_product_id` is **retired, not kept as a denormalized
"primary."** Every real consumer (the assessment-board badge, the CRM/
bundle export, deactivation's provenance check) was rewritten to want
either "all current contributors" or "is product X a contributor" — none
needed a single "primary," and keeping the column would have meant
inventing an arbitrary tiebreak rule with no actual use. That is exactly
the "populated but meaningless" trap worth avoiding rather than
accumulating.

**Migration 0052** creates the table, backfills every existing
`sourced_from_product_id` into one contributor row (matched via
`baseline_control.objectives @> objective_key`, the same JSONB
containment the read path already used to resolve a product's key for
display), then drops the column. The backfill counts its own INSERTs
against the source row count and **raises, rolling back the whole
migration transaction**, on any mismatch rather than silently dropping an
orphan — verified this is not just a theoretical safety net: a read-only
query against wl-util-1's real data beforehand confirmed all 28 existing
`sourced_from_product_id` rows (all Acme MSP, RocketCyber + Datto RMM)
matched a `baseline_control` cleanly, zero orphans. Deployed live: the
migration ran clean (no raise), and a post-deploy query confirmed exactly
28 `control_state_contributor` rows, matching pre-migration data
row-for-row.

**§3 decisions:**

1. **Responsibility conflict — Jarrod's explicit call**, asked directly
   rather than guessed at (the ticket flagged this as genuinely two-sided):
   when contributors disagree, the **weakest** (most customer-inclusive)
   classification wins — `shared` beats `provider_satisfies` whenever both
   are present. Rationale: if even one contributing product's own baseline
   mapping says the customer still has a real `customer_action`, that
   obligation is real and must keep surfacing in the CRM/SSP; letting a
   different, unrelated product's `provider_satisfies` claim silently
   override it would hide a genuine customer responsibility from the exact
   document that exists to state responsibilities plainly. Same
   conservative-bias principle as the disclaim-flag check from the
   previous slice (false positive costs a glance; false negative puts
   unearned credit in an SSP). Implemented as
   `assessment.py:resolve_contributor_responsibility()`, recomputed on
   every contributor add/remove from whichever contributors remain.
2. **Evidence tasks — no code change needed.** Checked what the current
   code actually does before proposing anything: `routers/evidence.py`'s
   own module docstring already states no endpoint here ever modifies
   `control_state.status` — evidence sufficiency has always been a pure
   human judgment call, gated by nothing, for a single product exactly as
   much as for many. `EvidenceTaskStateLink` is already many-to-many, so
   every contributing product's own evidence tasks already appear
   together against one control_state with zero changes required.
3. **SPRS scoring — verified unaffected, not just assumed.**
   `assessment.py:compute_sprs()` reads only per-objective `status`, never
   `responsibility` or contributor count — provably unaffected by
   construction. Confirmed directly with an integration test activating
   the same objective via one contributor vs. three and asserting an
   identical SPRS score.

**Behavior:**
- Adding a contributor to an objective that already has one **never
  regresses status** (`assessment.py:contributor_added_status()`) — a
  `met`, evidenced objective stays `met` when a second overlapping product
  activates; this is the exact regression §0 confirmed live before the
  fix. The new contributor row is still recorded and `responsibility`
  still recomputes.
- Deactivating one of several contributors — **Jarrod's explicit
  decision**: the control falls to `needs_review` regardless of whether
  another product still covers it (losing a tool is exactly when a
  coverage claim deserves a second look), matching the exact reasoning
  `activate_org_product`'s reactivation-evidence path already uses
  ("re-confirm coverage is still current"). The surviving contributor(s)
  stay recorded, `responsibility` is recomputed from them, and
  `control_state_history.change_reason` names both the departing and
  surviving product(s) by name so a reviewer can see which tool actually
  went away.
- Deactivating the **last** contributor keeps today's exact pre-existing
  behavior, confirmed rather than assumed: `needs_review`, with
  `responsibility` left completely unchanged (not reset to
  `customer_owns`) — this was already true before this slice and is
  preserved byte-for-byte.
- **Known, deliberately unfixed limitation**: evidence-state-link
  archival on deactivation remains all-or-nothing per `control_state`,
  not scoped to which product's evidence it is (pre-existing behavior,
  unchanged). A state with two contributors has ALL its evidence archived
  if EITHER one deactivates, which can archive evidence that actually
  supports the surviving contributor. Fixing this needs evidence-to-
  product provenance tracking — a genuinely new capability, out of scope
  for "record and reconcile multiple contributors." Flagged here
  deliberately rather than silently left for the next person to discover.
  **Confirmed still present, live, 2026-09-19** by the tenant lifecycle
  consolidation pass's own Done entry below (search "Evidence-link
  archival on deactivation is per-`control_state`") — real evidence
  attached, not just the theoretical case described above.

**Visibility:** the assessment board shows every contributing product's
badge per objective (`ObjectiveRow.tsx`, was a single badge). The CRM
export gained a **Tools** column listing every contributing product per
objective; the SSP's per-objective Implementation Statements view gained
a "Tools:" line alongside RACI. Both are captured into `BundleSnapshot` at
export time like everything else there — verified with a regression test
that a contributor added to a *different* assessment's control_state
never leaks into another assessment's bundle.

**Bench-verified** on an isolated `wingrc_verify_multitool` stack (live
`wingrc` project confirmed running, untouched, before and after):
**1203/1203** backend tests (16 new — 8 pure-function tests for the two
new `assessment.py` functions, 9 integration tests in the new
`test_multi_tool_coverage.py`, 3 new bundle-contributor tests — some
overlap across files), `ruff check .` clean, **139/139** vitest, `tsc -b`
clean, `vite build` clean.

**Deployed** 2026-09-16 per `docs/deployment.md` §7 with `--no-deps` and a
pre-deploy backup (§7a — this migration touches `control_state`): backup
verified (446 TOC entries) before `backend`/`worker`/`nginx` were rebuilt
and recreated; `db`/`minio` confirmed untouched; migration 0052 applied
clean (no raise); live query confirmed exactly 28 contributor rows,
matching the pre-migration count and content exactly; a live browser
walkthrough against `https://dev.wingrc.us` confirmed the assessment
board renders RocketCyber's contributor badge correctly on AU.L2-3.3.1's
objectives against the real migrated data.

**Explicitly not started, per this slice's own scope boundary**: AI
research of vendor platform documentation (needs provenance/fetch-safety/
cost design, next slice) and any change to how the magic loop decides
coverage in the first place (this slice is about recording and
reconciling multiple contributors, not about changing what counts as
coverage) — no placeholders or partial scaffolding added for either.

---

### AI research of vendor platform documentation ✅ DONE (2026-09-17)

Jarrod: "I DO want AI to research each product via online platform
documentation (not sales or marketing slicks) and determine what controls
could be covered." Attached documents come first; web research
supplements them, and can never override what they say.

**§0 — the rule, non-negotiable, enforced in code:** web content may
propose additional coverage; it may never override a disclaim in the
vendor's CRM. This is the same discipline as the evidence-minimization
rules and the disclaim-flag check from the previous two slices — a
confused model is not trusted to police this itself via a prompt
instruction, since that's precisely the failure mode a real RocketCyber
ingestion run already produced once.

**Design: two AI passes, one code-enforced merge.** Pass 1
(`importers/document.py:ingest_document()`) is completely unchanged — the
existing CRM/baseline pipeline, same prompt, same call shape, verified by
its own full pre-existing test suite passing unmodified. Pass 2
(`importers/research.py:ingest_web_page()`) runs one AI call PER approved,
already-fetched page — never combined — so per-claim source attribution
(`baseline.py:ControlEntry.source`, new field, `None` for document-sourced
rows) is a code-level guarantee, not something trusted from a model's own
JSON. `merge_research()` is the actual §0 enforcement point: expands
Pass 1's own output into a per-control-id lookup; a Pass-2 proposal for a
control Pass 1 never mentions is genuinely new coverage and gets added; a
control Pass 1 already covers without disclaiming needs nothing added; a
control Pass 1 disclaims (`customer_owns`, or its own text reads as a
disclaim per the existing `_disclaims_coverage()` check) is **never**
upgraded — a `DisclaimFlag` is raised instead, reusing the exact
mechanism the disclaim-flag slice built, extended here to a cross-source
conflict rather than a single entry's internal self-contradiction.

**§1 — propose, then approve, then fetch.** New
`POST /admin/products/{id}/research/suggest-urls` asks the AI for
candidate documentation URLs with a one-line rationale each — proposals
only, never fetched from this call. An admin approves a subset (or pastes
URLs directly, e.g. when they already know the right admin guide) and
calls `POST /admin/products/{id}/research/fetch`, which is the only call
that ever touches the network, and only for exactly the URLs in that
request — no crawling, no following links found in a fetched page's own
content (only HTTP redirects, a different thing, each hop re-validated).
Scoped to **existing products only**: `ProductDocument.product_id` is a
real NOT NULL FK, and there's no product row yet for a fetched page to
attach to during from-scratch product creation — extending this would
mean deferring document creation to Apply time (mirroring how uploaded
CRM/baseline files already defer for that flow), a reasonable follow-up,
out of scope here. Matches the ticket's own real verification target
exactly (re-running research against the existing `datto-rmm` product).

**§2 — provenance.** Fetched pages become real `ProductDocument` rows
(`kind='web_research'`, migration 0053 — the existing `crm`/`baseline_doc`/
`kb_export`/`other` kinds gain a sibling, no new columns needed: `title`
is the page's own `<title>`, `source_docs_ref` holds the exact URL,
`uploaded_at` already IS the retrieval timestamp) so a compliance claim
sourced from a live page stays reproducible after the page changes — the
whole reason this isn't "just add a fetcher." The merged mapping's
`product.source_docs` lists fetched URLs alongside uploaded filenames,
keeping the YAML's existing convention working; a bare URL already reads
unambiguously differently from a filename with no extra formatting needed.

**§3 — per-claim source attribution.** `ControlEntry.source` (`None` =
from the uploaded document(s); a URL = from that fetched page) flows
through to `ControlEntryDraft`/`ControlEntryOut` and renders as a new
Source column in the review table (a "web research" badge linking to the
URL, or "Document" for the existing case) — the review presentation the
edit-baseline-mapping slice built, extended, not redesigned.

**§4 — fetch safety, `web_fetch.py`, reported explicitly as required:**
- **HTTPS only** — enforced at the top of every fetch and every redirect
  hop; `http://`/`file://`/`gopher://` all refused before any connection
  attempt.
- **Private/special-use address space blocked** — hostname resolved via
  `socket.getaddrinfo`, every resolved address checked (`ipaddress`'s
  `is_private`/`is_loopback`/`is_link_local`/`is_multicast`/`is_reserved`/
  `is_unspecified`, plus explicit unwrapping of IPv4-mapped IPv6 addresses
  like `::ffff:10.0.0.1`, a known filter-bypass trick if left unhandled)
  before any connection. The connection is then **pinned** to the
  validated address(es) via a custom `http.client.HTTPSConnection`
  subclass — resolving once to check and then letting a normal client
  re-resolve at connect time is a textbook DNS-rebinding TOCTOU, closed
  here by construction, not by convention.
- **Every redirect hop re-validated from scratch** (scheme, resolution,
  address check) before being followed, capped at 5 hops.
- **Timeouts and a hard response-size cap** (20s socket timeout, 30s total
  wall-clock budget, 5MB response cap), enforced while streaming, not
  just checked against a Content-Length header a hostile server can lie
  about.
- **No credentials of any kind are ever attached** — there is no code
  path that reads a WinGRC session/cookie/Authorization header into an
  outbound request; the request is built from nothing but the URL and a
  fixed identifying User-Agent.
- **robots.txt honored** before every fetch (not just the first), with a
  permissive default on any failure to fetch/parse it (standard
  behavior — robots.txt narrows a well-behaved fetcher, it isn't the
  security boundary; the checks above are what's load-bearing).
- HTML-to-text extraction uses stdlib `html.parser.HTMLParser` only — no
  new dependency, and no XXE surface since it's a non-validating tag
  scanner, not an XML parser evaluating DTDs.

**A real bug found and fixed via live deploy verification, not by unit
tests alone:** the first implementation resolved a hostname and pinned
the connection to exactly one arbitrary address (via `set` iteration). A
real public site (`example.com`) resolves to both IPv4 and IPv6
addresses; wl-util-1's Docker network has no IPv6 route, so a fetch
picking the IPv6 address failed "Network is unreachable" on a completely
ordinary, legitimate site. Fixed: `_resolve_and_pin()` now returns every
validated address in the resolver's own preference order, and the pinned
connection tries each in turn, falling back on failure — the same
multi-address handling a normal, non-pinned client already gets for free
from `socket.create_connection`. The SSRF validation itself (every
address checked before any connection attempt) was never affected; this
was a pure reachability bug. Caught, fixed, re-verified, and redeployed
before the real end-to-end test below ran.

**A second real gap found the same way:** re-running research against
the actual `datto-rmm` product, one AI-suggested documentation URL
(`help.datto.com/s/article/...`) fetched successfully (HTTP 200) but
rendered its real content via client-side JavaScript, extracting to 0
characters through this module's static parser. An AI call given that
near-empty input still confidently returned 9 fabricated control
proposals, including control ids that don't even match this framework's
own naming convention (e.g. `IA.L1-3.5.1` against a CMMC L2 catalog) —
concrete proof of exactly the hallucination risk §0 exists to guard
against, just via a different vector (empty input) than the CRM-conflict
one. Fixed with the same guard `importers/document.py` already has for
this exact failure mode (`_MIN_EXTRACTED_TEXT_CHARS`): a new
`MIN_PAGE_CHARS` (50 chars) refuses the page — before it's even stored,
and again defensively inside `ingest_web_page()` — with a message
explaining the likely JavaScript-rendering cause, rather than silently
handing the model near-nothing and trusting whatever it invents.

**§5 — cost and size**, reported per run:
- `MAX_PAGES_PER_RUN = 10` (also the practical cap on how many additional
  AI calls one run can add, one per page).
- `MAX_PAGE_CHARS = 50,000` per page, `MAX_TOTAL_WEB_CHARS = 200,000`
  combined per ingestion run.
- `/research/fetch`'s response reports page count, total characters, and
  estimated tokens **before** any AI call is spent (fetching costs only
  HTTP requests) — the "surface cost before the run" requirement.
- `/import/from-documents-with-research`'s response adds pages included,
  characters, estimated tokens, additional AI calls, elapsed seconds,
  controls added, and conflicts flagged — the actual before/after cost
  figures once the run completes.
- Real figures from the live `datto-rmm` run: 8 URLs suggested, 6 failed
  (404 — the model guessing plausible-looking but non-existent
  documentation paths, a known limitation of suggestion without live
  browsing — this is exactly why §1's paste-a-URL-directly path exists),
  1 refused (the empty-page guard above), 1 successfully analyzed
  (4,010 characters, one additional AI call) and correctly proposed
  **zero** additional controls because its actual content didn't support
  any — an honest "no reliable coverage found" result, not a forced one.

**§6 — everything the existing pipeline enforces still applies,
unweakened:** confirmed by the full pre-existing test suite (document
ingestion, evidence-minimization, coverage_basis-left-unset, disclaim-flag)
passing completely unmodified. The disclaim-contradiction check
(`build_preview`'s own self-contradiction scan) runs over the merged
output automatically, with zero new code, since the merge happens before
`build_preview()` is ever called.

**Known, deliberate limitation, out of scope for this slice**: URL
suggestion has no live web-browsing capability, so a meaningful fraction
of suggested URLs will be plausible-looking guesses that 404 (confirmed
live: 6 of 8 for a real product) — mitigated, not eliminated, by the
paste-URL-directly path and the fact that a failed fetch is always
reported per-URL, never silently treated as success.

**Bench-verified** across three rounds (the slice itself, then each of
the two bugs found live): 1278/1278 backend tests, `ruff check .` clean,
139/139 vitest, `tsc -b` clean, `vite build` clean each round. SSRF
defenses directly exercised against the REAL (unmocked) implementation,
live, in both the bench and the production containers: `127.0.0.1`,
`169.254.169.254`, an RFC 1918 address, and plain `http://` all refused;
a real public fetch succeeds.

**Deployed** 2026-09-17 per `docs/deployment.md` §7 with `--no-deps` and a
pre-deploy backup (§7a — migration 0053 touches `product_document`'s
check constraint): backup verified (456 TOC entries) before rebuild;
`db`/`minio` confirmed untouched; migration applied clean. The two live
bugs above were each found, fixed, bench-verified, and redeployed in
sequence before the real `datto-rmm` test was considered final. Procedural
note: the two post-deploy bug fixes were committed directly to `main`
rather than through their own feature branches first — a deviation from
this project's own "verify on a branch before main" discipline, caught
and corrected by bench-verifying each fix immediately afterward and
before its own redeploy, but the branch-first order itself should have
held regardless of how fast the fix was.

**Real test, exactly as asked**: re-ran ingestion on the real, live
`datto-rmm` product (documents.py's `ingest_document()` plus the research
pass) via direct function calls against the real AI provider and the real
CRM PDF already attached to the product — a genuine dry-run, nothing
applied, nothing written. Document-only Pass 1 alone: 30 controls. With
research added: still 30 controls, 0 added, 0 conflicts — the one
documentation page that actually rendered usable content didn't describe
anything the CRM hadn't already covered. Noted in passing: Pass 1's own
output was not byte-identical between two runs of the same PDF minutes
apart (`AC.L2-3.1.12` came back `customer_owns` once and `shared` once) —
ordinary AI non-determinism that predates this slice and applies equally
to the existing single-pass pipeline; not something this slice changes or
could fix.

**Not done this round**: a live browser walkthrough of the new research
panel in `ToolImportWizard.tsx` — this session's browser context had no
saved WinGRC credentials to complete a login, and re-prompting for them
was out of scope for finishing this slice. The panel is exercised by the
frontend's automated test suite and compiles cleanly under `tsc -b`;
Jarrod can try it live from the `datto-rmm` product's "Edit Baseline
Mapping" screen ("🔬 Add web research").

**Explicitly not started, per this slice's own scope boundary**: OCR for
scanned documents (unchanged, known gap); autonomous crawling or link-
following beyond approved URLs; any change to how the magic loop or
multi-tool coverage works; baseline versioning (next slice).

---

**Daily Liongard sync + asset/user onboarding approval (D.3's second
half).** Root `ROADMAP.md`'s D.3 entry is the spec and carries the full
amendment with every design decision's reasoning; this entry is the
session log.

**§0 — the structural gap the spec didn't mention, found before building
anything:** no persisted dry-run existed. `routers/scope.py:
liongard_sync_dry_run` computed a diff and returned it over HTTP; nothing
was stored, so a scheduled job (no browser to carry that diff to an apply
call) had nothing durable to produce for review — directly colliding with
the scheduler's own hard constraint ("a scheduled job may produce a
dry-run for review, never apply one unattended," `scheduler.py`'s own
module docstring). Closed with `liongard_sync_result`/
`liongard_sync_result_change` (migration 0056), the same append-only,
point-in-time discipline as `sprs_snapshot`/`audit_log`/`BundleSnapshot`.
A second sync landing before the first is reviewed marks the first
**superseded** (not merged, not queued) — chosen because merging risks
blending two observation times into one approval record, and queueing
means a reviewer works through a backlog of diffs against a device that's
since drifted again. The interactive dry-run → apply path
(`liongard_sync_dry_run`) is unchanged in shape — `pull_and_reconcile()`
was extracted out of it so both callers share one implementation, not two
that could drift.

**§1 (the task's numbering, not a code bug this time): pending-by-default
applies to the manual "Sync now" flow too, not just the scheduled job.**
A brand-new Liongard-observed entity's `incoming.status` is forced to
`pending_approval` in the one shared `pull_and_reconcile()` function, so
an admin clicking through the existing interactive flow gets the same
gate as the daily job. The alternative — manual apply straight to
`active`, only the scheduler's output gated — would give one connector
two different trust levels depending on who clicked what, exactly the
inconsistency "candidates, never auto-met" exists to prevent. A narrower
related bug found and fixed in the same pass: `reconcile.py`'s diff never
compares `status` (attributes only), and a fresh pull's `CanonicalEntity`
defaults to `status=active` regardless of what's actually stored — so a
mere attribute update (a hostname rename) to an already-pending device,
applied through the *existing* workbook-apply path, would have silently
promoted it to `active` with no approval ever having happened. Fixed in
`pull_and_reconcile()`: once a device is `pending_approval`, it stays
`pending_approval` through attribute-only syncs until an explicit
approve/reject.

**Pending assets in the SSP inventory — resolved by an existing
precedent, not a new decision.** `bundle_service.py`'s component
inventory already includes every device/software row "regardless of
status or in_boundary... flagged via its Status/Boundary columns, never
silently dropped" — checked directly before assuming an answer.
`pending_approval` gets exactly the same treatment every other status
value already gets (`decommissioned` already renders unfiltered too);
nothing new needed. `render_view`/`repo.list_entities` (the CMMC list
exports) have no status filter at all either, same existing behavior,
untouched.

**Rejection asserts `in_boundary=false`, never decommissioned.** The
device exists on the network either way; WinGRC has no authority to
assert it's gone, only that a human decided it isn't part of the CUI
boundary. Sticky: the row now exists in scope, so a later sync reconciles
it as unchanged/changed, never `new` again — verified directly
(`test_re_sync_after_rejection_does_not_re_flag_as_new`).

**Notification: one digest email per org per recipient per sync, never
one per device.** Reuses `review_cycles.py`'s exact `notified_at`/
`notification_error` delivery-tracking shape (a new
`liongard_sync_notification` row per recipient) and its retry-on-next-
tick pattern. Content follows `email_service.py`'s existing rule (no org
name, no count, no device detail) — extended that module's own docstring
to name this as the sixth reviewed call site. "No contact holds
security_officer/it_admin" is recorded on the sync result's own
`warnings`, not silently dropped — confirmed live (see below).

**Checklist: v1 is the org's activated Tools, reviewer-confirmed, not
Liongard-metrics-verified.** Found while grounding the build, not assumed
going in: `connectors/liongard.py` has no per-device agent/metrics pull
at all — only `pull_device_profiles`/`pull_identities` (inventory), never
anything resembling "is DUO installed on this specific device." This
project's own precedent about that same connector (the undocumented
required `Sorting` field, only found by testing live against a real
tenant) argued directly against guessing at an unbuilt, undocumented API
surface from a session with no live-tenant access to verify it against.
Reported to Jarrod before building rather than assumed; his call was to
ship the workflow now with a manually-confirmed checklist
(`asset_approval_checklist_item`, one row per activated product, ticked
by the reviewer) and leave real per-device metric verification as its own
future slice. The checklist source (`OrgProduct`/`Product`) needs no new
connector work at all.

**Re-approval: shipped onboarding-only**, taking the roadmap's own
recommended default. `asset_approval` carries no re-approval mechanism in
this slice, but nothing about its shape needs to change to add one later
— a chained re-approval is additive, not a rewrite.

**SPRS unaffected — asserted directly, not just architecturally.**
`compute_sprs` (assessment.py) takes only `control_weights`/
`objectives_by_control`/`objective_statuses`; there is no scope_entity
read path in it at all. Tested end-to-end anyway
(`test_pending_approval_and_decided_assets_never_touch_sprs`): a real
assessment's `sprs_score` is identical before and after a sync +
approve + reject sequence.

**Verified live against Jarrod's real WinsorLabs Liongard tenant on
first deploy** (not just the bench stack): the scheduled `liongard_daily_
sync` job's first production run found one new device, correctly
recorded "No contact holds security_officer or it_admin for this org —
no notification sent" rather than silently sending nothing or crashing,
and — checked directly by SQL — wrote **zero** `scope_entity` rows,
confirming the scheduler's hard constraint holds in production, not just
in tests.

Bench-verified on an isolated wl-util-1 Docker Compose project (1316
backend tests, ruff clean, 144 frontend tests, `tsc -b` clean, `vite
build` clean) — including restoring a real snapshot of the live database
into an isolated copy and confirming migration 0056 applies cleanly with
existing SPRS scores unchanged, same discipline as the baseline-
versioning slice immediately before this one. Merged to `main`
(`1da4f0f62f`), deployed to `dev.wingrc.us` with a pre-flight `pg_dump`
backup (`/backups/pre-liongard-sync-20260918_142107.dump`).

**Explicitly not built, per this slice's own scope boundary:** real
per-device Liongard-metrics verification of the checklist (above);
continuous re-approval / conformance monitoring (roadmap item G); a
direct-by-scope-entity-id approval path for an asset not tied to a
persisted sync change row (the onboarding flow — sync-then-approve — is
the only path built; a manually-created `pending_approval` row has no
approval entry point yet, a narrow edge case).

---

### Tenant lifecycle consolidation pass ✅ DONE (2026-09-19)

Not a feature slice — a consolidation pass. Two real bugs in the two
weeks before this one (seed_baselines never deleting an unmatched
BaselineControl; a fresh Liongard pull's default status=active silently
promoting a pending_approval device) were both found by asking "what
else writes this?", never by a test — because no test walked a tenant
through more than one feature at once. `backend/tests/test_lifecycle.py`
(`@pytest.mark.integration`, one narrative test, deliberately not split
per-feature) closes that gap: one org, real HTTP throughout (RLS
enforced, not bypassed), onboarding → two overlapping product
activations (RocketCyber + a hand-built Datto RMM sharing AU.L2-3.3.1)
→ Liongard sync with an approve and a reject → evidence collected to
met → an SPRS snapshot → a bundle export kept for later comparison → a
baseline reimport (classification change, text-only edit, dropped
control) that leaves the still-pinned tenant's re-exported bundle byte-
identical in substance to the first → a version move → a product
deactivation → a review-cycle attestation → assessment completion → an
SPRS submission → a final bundle export — then, separately, a
`c3pao_assessor` walking every read path with every attempted mutation
refused, plus a second, never-granted org confirmed invisible under RLS
throughout.

**What broke:** nothing in application code. The walk surfaced no new
functional bug — a genuinely useful result in its own right (see the
near-miss below for why "nothing broke" almost became "something got
broken by the walk itself").

**Report-only findings, confirmed live (not just by reading code),
each a product decision rather than an obvious fix:**

- **`patch_scope_entity` has no `pending_approval` guard.** ~~The ordinary
  manual scope PATCH endpoint (`routers/scope.py`) can flip a
  `pending_approval` entity straight to `active` with no `AssetApproval`
  row, no checklist, no approval-specific audit action — it has no
  awareness of the approval workflow (D.3) at all. Confirmed live: a
  `pending_approval` device PATCHed to `active` returns 200, and no
  `AssetApproval` row is created. The right fix touches who is
  authorized to admit an asset into the boundary and isn't a one-liner —
  Jarrod's call.~~ **Fixed 2026-09-22** — see "Liongard sync: two entry
  points, one gate" below for the full writeup (this same gap, plus a
  worse sibling found live, both closed by one guard in `repo.upsert()`).
- **Evidence-link archival on deactivation is per-`control_state`, not
  per-product.** Flagged during the multi-tool coverage slice, never
  fixed; this pass confirms it still bites with real evidence attached.
  Deactivating one of two products contributing to a shared control
  (`AU.L2-3.3.1`: RocketCyber + Datto RMM) archives RocketCyber's own
  already-collected evidence on that control too, even though
  RocketCyber — not the product actually being deactivated — is the one
  still covering it. `deactivate_org_product` needs product-scoped
  archival, not blanket per-control_state.
- **The `asset_approval` reversal gap is still real.** A device
  rejected, then brought back into boundary through the ordinary scope
  UI, still reads `rejected` in its acceptance record — nothing renders
  that staleness anywhere today. Not exercised further in this pass
  beyond confirming it's unchanged; same fix shape as the first finding
  above (both are "the manual scope path doesn't know the approval
  workflow exists").

**A confirmed SURPRISE, not a bug:** `move_org_product_version`'s diff
against the outgoing baseline version is classification/objective-key
only — never `provider_contribution`/`note`/`scope_note` text, by that
function's own design. A pure text edit is invisible to it, which also
means it stays invisible *after* a tenant moves to the new version: the
edited control's `ControlStateContributor` row keeps pointing at the
OLD version's `BaselineControl` row, permanently, unless something
about that control's classification also changes. No current bundle
renders that text to a tenant, so there's no live consequence today —
but it's a real, load-bearing fact about what "moved to the new
version" actually means, worth having written down before it surprises
someone building on top of it.

**The near-miss, worth its own writeup because it's the same lesson the
whole pass exists to teach, aimed at me instead of at the app:** the
test's first draft picked `AC.L2-3.1.11` as its evidence-collected-to-
met anchor, and `AC.L2-3.1.1`/`AC.L2-3.1.2`/`MA.L2-3.7.1` for the
reimport-mutation controls. All four turned out to be
`coverage_basis: platform_only` — a deliberate reclassification Jarrod
made 2026-07-08 (`6aa20cf10f`, "vendor self-attestation... cannot
trigger false pending_evidence on controls the product does not perform
in the customer's CUI environment") that both `_run_loop` and
`move_org_product_version`'s `_claimed()` exclude entirely: no
`ControlStateContributor` row is ever created for them. Every assertion
built on those four controls was silently exercising nothing. Chasing
the resulting `StopIteration`, a wrong diagnosis nearly shipped: that
`seeds/baselines.py`'s `_BASELINES_DIR` (`parents[2]`, resolving to
`backend/baselines/`) was reading a "stale" directory and should instead
read the repo-root `baselines/` directory CLAUDE.md's file table names
without a `backend/` prefix. That diagnosis was backwards.
`backend/baselines/` has been the actual, loaded-since-the-function-was-
written directory the whole time (confirmed via `git log -p` on
`_BASELINES_DIR` — it has always been `parents[2]`); the repo-root
`baselines/rocketcyber.yaml` is a dead duplicate from the repo's initial
commit that nothing has read since, and it had drifted out of sync with
Jarrod's July reclassification by simply never receiving it. The wrong
fix was committed, bench-verified (1317 tests passed — the full suite
has no assertion that would have caught a data source being swapped for
an equally-well-formed one), and only caught before merge by reading
`git log -p -- backend/app/seeds/baselines.py` on a hunch after the
coverage_basis values looked design-shaped rather than accidental. It
was reverted (`9d2054644e`) in the same branch, never reached `main`.
Two takeaways: a passing full test suite doesn't defend against a fix
that's confidently wrong in a way nothing asserts against, and CLAUDE.md's
own file table (`baselines/` — no `backend/` prefix) is itself now a
known trap for the next reader who trusts it over `git log`.

**Landed:** bench-verified on an isolated wl-util-1 Docker Compose
project — 1317/1317 backend tests, `ruff check .` clean. No frontend
changes in this pass (nothing here touches the frontend surface).
Merged to `main` per the branch's own history (the wrong "fix" and its
revert are both preserved in that history, not squashed away, since the
near-miss itself is part of what this pass produced).

---

### Liongard sync: two entry points, one gate ✅ DONE (2026-09-22)

Found live on `dev.wingrc.us`: device `WL-LT26` (serial `PF3Y6K26`) sat in
the Assets list at `pending_approval` with no way to approve or reject it
anywhere in the UI — not even in Asset Approvals.

**The mechanism, confirmed by reproducing it, not just by reading code.**
Two Liongard sync entry points existed with different write behavior:

- `POST /liongard-sync-results/sync-now` (Asset Approvals' own button) →
  `pull_and_reconcile` → `persist_sync_result`. A NEW entity is forced to
  `pending_approval` and queued as a `liongard_sync_result_change` row
  (`resolution="pending"`). **Never writes `scope_entity`.**
- The Assets screen's "Sync from Liongard" button →
  `POST /integrations/liongard/sync/dry-run` (same `pull_and_reconcile`)
  → `POST /imports/workbook/apply`. Apply took whatever `incoming.status`
  the dry-run handed it — `pending_approval` for a NEW entity — and wrote
  it **straight to `scope_entity`** via `repo.upsert()`, with no
  `liongard_sync_result_change` row behind it at all.

A device written this second way has no exit. `pull_and_reconcile` only
forces `pending_approval` for a change it classifies `NEW`; once a
`scope_entity` row already exists for that natural key, the next pull
reconciles it as `UNCHANGED` (silently dropped before `persist_sync_result`
ever sees it) or `CHANGED` (persisted, but `resolution` is only meaningful
for `change_type="new"` — CHANGED rows are shown for visibility only,
never resolvable). Never `NEW` again, so `approve_change`/`reject_change`
— which both require `change_type="new"` — can never act on it.

This is the fourth instance of one pattern in three weeks, three of the
four on `scope_entity`: a slice adds a gate on a field (D.3's
`pending_approval` review gate), and an older writer already routing
around where that gate now sits keeps writing straight through it. (The
other three: `seed_baselines` never deleting an unmatched `BaselineControl`;
a fresh Liongard pull's `status=active` default promoting a pending device,
fixed inside `pull_and_reconcile` itself during D.3; `patch_scope_entity`
having no `pending_approval` awareness at all, documented as an open
finding by the tenant lifecycle consolidation pass above, now closed by
this same fix.)

**Two options considered — (a) chosen.** (a): the Assets-screen entry
point stops writing `scope_entity` for NEW entities, routing them into the
same approval queue `sync-now` uses; CHANGED/MISSING rows keep their
existing interactive dry-run/apply review, unaffected. (b): give the old
path its own `persist_sync_result` call too, so both paths fully implement
"what a Liongard pull produces" independently. (b) was rejected: it's
exactly the shape that produced this bug and the three before it — two
implementations of the same concept drift the moment one of them changes
and the other doesn't. (a) makes "an asset only enters scope from an
unresolved Liongard pull via approval" true by construction instead of by
convention.

**Where the invariant lives.** Not re-checked per endpoint — a single
guard in `repo.upsert()` (`repo.PendingApprovalWriteError`), the one
function every writer of `scope_entity` funnels through
(`create_scope_entity`, `patch_scope_entity`, `import_apply`, `cli.py`'s
`seed --apply`, and `liongard_sync.py`'s `approve_change`/`reject_change`
itself):

1. Refuses to write `status=pending_approval` at all, unconditionally. No
   legitimate writer needs to — `pending_approval` lives only transiently
   in a `LiongardSyncResultChange.incoming` JSONB blob; `approve_change`/
   `reject_change` always resolve a change into a fresh `scope_entity` row
   they themselves write as `active`, never as `pending_approval`.
2. Refuses to change a row that's *already* `pending_approval`, to
   anything, through this call. The only way out is `approve_change`/
   `reject_change` calling `repo.upsert()` for the first time on a
   natural key with no prior row — which is what normal operation always
   looks like, since (1) means a `pending_approval` row can never
   accumulate a later edit in the first place.

Both checks fire before any write happens, so a batch `apply` call that
hits one refused row commits nothing from that request (nothing commits
until the whole batch succeeds; an uncaught exception here leaves the
session's flushes unrolled-back-but-uncommitted, which `get_session`'s
`finally: session.close()` discards).

`routers/scope.py:liongard_sync_dry_run` additionally now calls the same
`liongard_sync.persist_sync_result` `sync-now` uses whenever its pull
contains at least one NEW row, before returning — so both entry points
converge on the literal same queue, not just on refusing the bad write.
The response carries the new `sync_result_id` field naming what it just
created (`null` when nothing new was found, always `null` for the
workbook dry-run path). `create_scope_entity`/`patch_scope_entity`/
`import_apply` translate `PendingApprovalWriteError` to HTTP 409.

**Stranded-row survey (§4).** Checked directly on the live `wingrc`
database via a read-only query (`scope_entity` rows with
`status='pending_approval'` and no corresponding `resolution='pending'`
`liongard_sync_result_change` row): **exactly one row** — the reported
`WL-LT26` — with zero others. No general cleanup tooling was built as a
result; a one-row problem doesn't justify one, and Jarrod cleared it
directly (delete + re-sync through Asset Approvals, which now correctly
re-classifies it as NEW and lets it be approved). If this ever recurs at
scale, the survey query above is the starting point for whatever tooling
that would need.

**Verification:** `backend/tests/test_liongard_sync_api.py` rewritten
throughout — every test that used to apply a NEW liongard entity via
`/imports/workbook/apply` now either asserts the 409 directly
(`test_apply_of_a_new_liongard_entity_is_refused`) or goes through
`sync-now`'s own approve endpoint instead
(`test_new_entities_reach_scope_entity_only_via_approval_with_liongard_
provenance`, `test_approved_device_shows_display_name_and_last_login_user`
— the latter using the exact `WL-LT26`/`WL-DT26` devices from the live
report). New coverage for the persist-on-dry-run behavior itself
(`test_dry_run_with_new_entities_queues_them_for_approval`,
`test_dry_run_without_new_entities_does_not_persist_a_sync_result`).
`backend/tests/test_lifecycle.py` gains Step 3b, exercising the
Assets-screen entry point for the first time in that file (Step 3 only
ever exercised `sync-now`), and its own "SURPRISE candidate" block
(finding 3, `patch_scope_entity`) now asserts the 409 refusal instead of
documenting the gap, per that block's own stated update-not-delete
instruction. Frontend: `ScopeChangeDiffTable` gains a `newRequiresApproval`
prop (Liongard wizard only; the workbook wizard's own NEW rows stay fully
applicable, a different trust boundary per `liongard_sync.py`'s own
docstring); `LiongardSyncWizard` excludes NEW rows from `selectedChanges()`
and shows a "queued for review in Asset Approvals" hint instead.

Bench-verified on an isolated wl-util-1 Docker Compose project (own
project name, own volumes/network, no published ports, live `wingrc`
project confirmed untouched before and after): 1320/1320 backend tests,
`ruff check .` clean, 146/146 frontend tests (22 files, including
`ScopeChangeDiffTable`'s and `LiongardSyncWizard`'s own suites), `tsc -b`
clean, `vite build` clean. One test (`test_approved_device_shows_
display_name_and_last_login_user`) needed a fix during this run — the
default identity fixture wasn't isolated out, so it counted as a third
NEW entity alongside the two devices and tripped an `==2` approval-count
assertion; not a functional bug, a test-isolation miss caught by actually
running it. Merged to `main`, deployed to `dev.wingrc.us`.

---

### Show the acceptance record on the asset itself ✅ DONE (2026-09-23)

Small slice, asked for after running the approval workflow end to end on
`dev.wingrc.us`: "in the Asset itself a spot that has the approved date
and time and who approved the device." `AssetApproval` already stored
everything (`decision`, `decided_by_name`, `decided_at`,
`rejection_reason`) — this is display, not new state.

**What shipped.** New `GET /orgs/{org_id}/scope/{entity_id}/approvals`
(`routers/scope.py`) returns every `asset_approval` row for that asset,
most recent first, with its `asset_approval_checklist_item` snapshot
nested in. `AssetDrawer` renders it read-only, right under the header: the
decision badge, reviewer name, and timestamp inline; a rejected asset also
shows the reason. An asset with no approval record (manually added,
workbook-imported — the common case) renders nothing, not an empty state.
The checklist snapshot sits behind a "Show checklist" toggle so it never
clutters the row.

**§3 decision: most-recent inline, full history behind a toggle — taken
as recommended.** An asset can accumulate more than one `asset_approval`
row (the schema allows it; a future re-approval slice will produce it,
not reachable through today's UI). Only `approvals[0]` renders by
default; a "View full history (N)" toggle reveals the rest when there's
more than one. Verified directly (`test_approval_history_most_recent_
first_never_hides_earlier_decisions`, constructing a second row directly
since the API can't produce one yet): a second decision never removes the
first from the response, only from the default view.

**§2 verified directly, not just architecturally.**
`test_approval_name_is_the_stored_snapshot_not_a_live_join` stores an
approval under a name different from the authenticated test user's own
current `display_name` and asserts the stored name comes back — proving
`decided_by_name` is never re-derived from a live user lookup, matching
`AssetApproval`'s own docstring reasoning for why `decided_by` is a string
rather than a hard FK.

**§4a taken: `asset_approval.scope_entity_id` is now `ON DELETE RESTRICT`,
not `CASCADE`** (migration `0057_asset_approval_restrict`). Once the
record is visible on the asset, deleting the asset silently taking its
own acceptance record with it became the obvious next question.
`delete_scope_entity` catches the resulting `IntegrityError` and returns
409 with a real message instead of a raw 500. `SET NULL` (the other
option in the same request) was considered and rejected: it needs
`scope_entity_id` to become nullable plus a denormalized natural_key/
entity_type captured at approve/reject time so an orphaned row still
identifies what it was about — a real schema change and a new write path
in `liongard_sync.py`, not a small one. RESTRICT needed neither.

**Migration-length near-miss, worth recording because this project has
hit it before:** the first revision id tried, `0057_asset_approval_
restrict_delete` (35 chars), crash-looped the backend container on the
bench run — `alembic_version.version_num` is `VARCHAR(32)`. This is the
exact same class of bug `docs/bench-stack-verification.md`'s own history
already names ("a 40-char alembic revision id exceeding a column").
Caught by actually starting the container, not by review; renamed to
`0057_asset_approval_restrict` (28 chars) before merge.

**§4b (bundle export) — considered, deferred, not built.** "Who accepted
this device, and when" is exactly what an assessor reads the component
inventory to find out, and would be cheap if `_component_inventory_body`
were a simple per-row renderer — it isn't. `ScopeEntitySnap`/
`BundleSnapshot` are frozen dataclasses shared by both the ZIP export and
the consolidated PDF SSP renderer (`bundle_service.py`); adding approval
data means extending the snapshot, a new query in `snapshot_bundle`, and
template changes in both renderers plus their own tests. Not a small
change — left for Jarrod to decide whether it's worth a slice of its own.

**Verification:** bench-stack on an isolated wl-util-1 Docker Compose
project (live `wingrc` project confirmed untouched before and after):
1330/1330 backend tests, `ruff check .` clean, 152/152 frontend tests (23
files, including the new `AssetDrawer.test.tsx`), `tsc -b` clean, `vite
build` clean. Two more test bugs found and fixed during this same run,
beyond the migration-length one above: a c3pao_assessor test reused
`fake_msp_admin`'s default email under the same org, tripping the
`(home_org_id, email)` unique constraint on `user`; and the history-
ordering test relied on `decided_at`'s server-default `now()` to differ
between two rows created in the same test, but `db_session` wraps each
test in one transaction (see its own docstring) so `now()` is fixed for
both — set `decided_at` explicitly instead, the same fix
`test_audit_log.py` already uses for the identical reason. Merged to
`main`, deployed to `dev.wingrc.us`.

---

### Component inventory and acceptance status, plus device identity ✅ DONE (2026-09-24)

Two parts, landed together because Part A touches the same shared
rendering pipeline (`ScopeEntitySnap`/`_component_inventory_body`) the
previous slice deferred for exactly that reason.

#### Part A — the component inventory and acceptance status

**A0: the premise was checked, not assumed.** D.3's original open
question ("do pending assets appear in the SSP inventory?") was marked
resolved by a since-retired justification — see `ROADMAP.md`'s D.3
section (amended in place) for the full retraction: it held only via the
interactive path that wrote `pending_approval` straight into
`scope_entity`, the exact bug the 2026-09-22 Liongard sync fix closed.
Confirmed directly before building anything
(`test_bundle_inventory_includes_pending_asset_marked_awaiting_acceptance`'s
own premise assertion): a pending entity has no `scope_entity` row at
all, so it could not have appeared in the inventory the old way.

**A1 decision: include pending assets, clearly marked — taken as
recommended.** AC.L2-3.1.1[c] is about devices being *identified*; an
assessor comparing the inventory against the network floor will find an
unapproved device whether or not anyone's clicked approve, and an honest
"Pending Approval" marker is a stronger artifact than an inventory that
quietly omits known hardware. `snapshot_bundle` (`bundle_service.py`) now
also queries `liongard_sync_result_change` for unresolved
`change_type='new'` rows, deduped by `(entity_type, natural_key)` keeping
the most recently pulled one (a device can accumulate more than one such
row if a later sync supersedes the one that first found it, before
anyone decides — persist_sync_result's own supersede policy).
`ScopeEntitySnap.pending: bool` carries the flag; `status`/`in_boundary`
are meaningless and unread for a pending row. **Cost, stated plainly as
asked:** one more query per bundle export, one more dataclass field, and
the renderer branches on `pending` for two columns (Status, Boundary) --
did not distort the pipeline meaningfully; the existing "regardless of
status" convention already meant the table had to handle arbitrary
badges.

**A2: acceptance provenance.** "Accepted By" column added to the
inventory table, reading the stored `AssetApproval` snapshot
(`decided_by_name`/`decided_at`, most recent when more than one exists,
same precedent as `AssetDrawer`) — never a live join, same discipline as
the drawer's own docstring. An asset with no approval record (manual
entry, workbook import) or a still-pending one shows nothing there,
matching "shows nothing rather than an empty or misleading field."

**Point-in-time, extended not duplicated.** `test_lifecycle.py`'s
existing three-export comparison (Steps 6/8/13, previously only
`impl_html`/`scoring_html`) now also captures and compares
`inventory_html` — a new Step 5b leaves one Liongard-observed device
deliberately unresolved so there's something pending to compare, and the
devices approved in Steps 3/3b give it real "Accepted By" content too.
`inventory_html_v2 == inventory_html_v1` and `inventory_html_v3 ==
inventory_html_v1` hold across a baseline reimport and everything after
it (evidence collection, SPRS snapshot, review-cycle attestation,
assessment completion, SPRS submission) — none of it touches
scope_entity or asset_approval, so none of it may move the inventory.

SPRS unaffected (`test_bundle_inventory_pending_asset_does_not_affect_sprs`,
same direct-assertion discipline as the prior slice's `test_pending_
approval_and_decided_assets_never_touch_sprs`) and `c3pao_assessor`'s
read paths are untouched (bundle export's own permission model wasn't
touched by this change).

#### Part B — device identity

**B1: Hostname shown alongside Alias, as Liongard itself shows them.**
`hostname` joins `domain.py:DEVICE_SOFTWARE_CANONICAL_ATTRIBUTES` (and,
by subtraction, `DEVICE_SOFTWARE_COMPARABLE_ATTRIBUTES` — a hostname
rename is a real, reviewer-visible change, not telemetry like
`last_login_user`) and `routers/scope.py:DeviceSoftwareAttributes`, kept
in sync by the existing `test_domain_attribute_vocabulary.py` set-equality
check with no changes needed to that test itself.
`importers/liongard.py:_DEVICE_CANONICAL_FIELDS` gains `"hostname":
"Hostname"`, populated by the same generic mapping loop
`device_profile_to_canonical` already runs — no new code path.
`display_name`'s Alias-first resolution is unchanged. AssetsPanel's Name
column now shows Hostname beneath the Alias-derived name (falling back to
the natural key when Hostname is unset, unchanged for workbook/manual
assets); AssetDrawer shows Hostname read-only, same pattern as Last Login
User.

**Diff-noise check, done not assumed.** Confirmed directly
(`test_first_sync_after_upgrade_shows_changed_once_then_second_sync_is_
clean`, updated; `test_hostname_unchanged_across_pulls_stays_clean`,
new): a pre-existing row missing the new `hostname` key shows CHANGED
exactly once (the same one-time transition cost `display_name`'s own
2026-09-17 rollout paid), then clean forever after — hostname is stable
data, not per-poll telemetry, so it never behaves like the fields the
comparable-attribute allowlist excludes.
`test_hostname_rename_shows_as_changed` confirms the B1 decision's other
half: a genuine rename does show CHANGED.

**B2: the placeholder-serial bug, found while grounding B1.**
`_device_natural_key()` preferred `SerialNumber` over `Hostname` with no
defense against BIOS/OEM placeholder values a manufacturer ships when no
real serial was ever programmed — confirmed live on WinsorLabs' own
tenant: `WL-DT26` ("Jarrods Desktop") is keyed on the literal string
`"System Serial Number"` (ASUS's placeholder). The natural key is the
reconcile identity, so two devices sharing a placeholder would collide
onto one `scope_entity` row and one would silently vanish from the
boundary — the exact failure this product exists to prevent.

`_PLACEHOLDER_SERIALS` (`importers/liongard.py`) is a module-level
frozenset — `"system serial number"`, `"to be filled by o.e.m."`,
`"default string"`, `"none"`, `"0123456789"`, `"not applicable"` —
case-insensitive, trimmed, same shape as `baseline_import.py`'s
`_DISCLAIM_PHRASES` ("expected to grow... add to it freely, here, in one
place a human can find"). A placeholder serial is treated as no serial at
all, falling through to Hostname exactly like a blank one already did.
`device_profile_to_canonical` also emits a one-line warning naming the
placeholder, surfaced through the existing per-row warnings channel.
`test_two_devices_sharing_a_placeholder_serial_produce_two_distinct_
entities` is the actual collision-prevention proof B2 asked for.

**Collision survey, run before any change** (read-only query against the
live `wingrc` database, `wl-util-1`): zero rows anywhere share a natural
key (`uq_scope_entity_identity` — a real DB constraint, not just
application discipline, would have refused a second one). Exactly one
row is keyed on a known placeholder: `WL-DT26`. Its audit-log history
shows two `import_apply` writes (2026-09-14, 2026-09-15), both under the
same placeholder key — consistent with the same physical device being
re-applied twice, not two different devices colliding. **No data has
been silently overwritten.** This is a preventive fix for a risk that's
real at MSP fleet scale, not a repair of already-lost data — the
"stop and ask" condition for a confirmed prior overwrite did not fire.

**Re-keying migration** (`0058_rekey_placeholder_serial`): a straight
`UPDATE scope_entity SET natural_key = <hostname>` for the one affected
row. Safe by construction, not just by care: `asset_approval`/
`review_cycle_item` reference the row by `scope_entity_id` (a stable
UUID), never by `natural_key`, and `liongard_sync_result_change` isn't a
persisted FK to `scope_entity` at all — a natural-key text match computed
fresh at pull time — so re-keying cannot orphan an acceptance record or
re-queue an already-decided device; the next pull computes the same
corrected key via the B2 code fix and reconciles the row as
CHANGED/UNCHANGED, never NEW again. `uq_scope_entity_identity` makes a
collision with some other existing row a hard migration failure, not
silent corruption, if one somehow exists — mirrors the RESTRICT FK's own
fail-loudly property from the previous slice. A row with no usable
Hostname to fall back to makes the migration raise rather than guess;
confirmed live this doesn't happen today.

**The alembic-revision-length test asked for already exists.**
`test_revision_ids_fit_alembic_version_column`
(`backend/tests/test_migrations.py`) was added after `alembic_version`'s
*original* incident (migration 0025, `bfa354e51c`) and walks every
migration script's revision id against the real 32-char column width —
it would have caught both of this session's own `0057` misses (the
35-char first attempt, and would catch `0058` here) had it been run
before pushing. It wasn't, in either case, because this environment has
no local Python/pytest -- the bench-stack run (SSH to `wl-util-1`) is the
earliest point in this workflow it actually executes. No new test added;
folding it in means routing bench verification earlier, not writing a
test that already exists twice.

**Verification:** bench-stack on an isolated wl-util-1 Docker Compose
project (live `wingrc` project confirmed untouched before and after) --
1344/1344 backend tests, `ruff check .` clean (one line-length fix needed
during the run), 156/156 frontend tests (23 files, including
`AssetDrawer.test.tsx` at 8/8 and `AssetsPanel.test.tsx` at 4/4), `tsc -b`
clean, `vite build` clean. `test_revision_ids_fit_alembic_version_column`
confirmed explicitly against `0058`. Merged to `main`, deployed to
`dev.wingrc.us` -- migration `0058` re-keyed WinsorLabs' one affected row
live, confirmed by direct query afterward.

---

### Lists deploy, the unhealthy backend, and Jarrod's three follow-up answers ✅ DONE (2026-10-07)

#### The unhealthy backend: a bare `git pull` on a bind-mounted live checkout

`wingrc-backend-1` was reported as "unhealthy for two days". That was
wrong, and the error was this session's. `docker compose ps` said
`Up 2 days (unhealthy)`: the container had been up since the host rebooted
on 2026-10-05, but it was **unhealthy for about three hours**, not two
days. The evidence:

- The last `/health` 200 was at 17:23:58 UTC. The first 503 was at
  17:24:29.
- In between, at 17:24:22, an interactive `wladmin` login (pts/0, from
  10.10.24.39) ran `git pull` on `/home/wladmin/dev/wingrc`. That
  fast-forwarded the live checkout to the N.3 merge (`d1b51fb`, migration
  0062).
- The backend bind-mounts `./backend:/app` and runs `uvicorn --reload`, so
  the new code went live within seconds. `alembic upgrade head` runs only
  at container start, so the database stayed at 0061.
- No image was rebuilt. nginx kept serving the old frontend.

`/health`'s body said exactly that:
`{"status":"error","migration":"0061_rls_transitive_policies","migration_expected":"0062_document_approval_review","database":"schema does not match this build"}`.

It was not only the probe. Real requests failed:
`GET …/dashboard` → 500, `relation "document_approval" does not exist`.

**The 2026-09-30 `/health` fix worked.** The probe named the exact mismatch
within 30 seconds, and nothing acted on it for three hours. That makes it
the fourth signal in this repo that worked while nobody looked, and the
first one that was *right*.

**The mechanism is the lesson.** On this host, `git pull` alone *is* a
deploy: code changes and schema doesn't. The documented routine (`pull` →
`build` → `up -d --no-deps`) is safe only because `up` restarts the
container, and that restart is what runs the migrations. A pull without the
rest of the routine leaves the stack in the state `/health` exists to catch.
It is worth considering a startup or reload hook, or dropping `--reload` in
the live compose file, so a pull cannot outrun the schema. That is not done
here, and it is Jarrod's call.

#### Deploy (both Lists PRs, #8 and #9)

- `pg_dump` taken first (`/home/claude/predeploy-lists-20261007.dump`, 459
  KiB).
- `alembic` went 0061 → 0062 → 0063 on container start.
- `/health` →
  `{"status":"ok","migration":"0063_document_type_baseline","database":"ok"}`.
- `backend` and `db` are healthy. `minio`, `nginx` and `worker` define no
  healthcheck, so they report only "Up".
- Counts are unchanged except `document_approval` 0 → 1 (0062's intended
  backfill of the one approved version) and the alembic version.
- SPRS is unchanged: Acme −199, Winsorlabs −204.

Live verification used throwaway identities and 2-hour tokens, all deleted
afterwards:

- **Export isolation:** 10 concurrent downloads of
  `3.1.1c-authorized-devices` (5 per org) all returned 200. Each contained
  only its own org's devices (Acme: ASSET-0001..3; Winsorlabs: WL-DT26,
  PF3Y6K26).
- **`c3pao_assessor`:** 200 on the export and the list endpoint for all four
  views. 403 on `PATCH` and `POST /scope`.
- **Lists render populated for the Liongard org:** WL-DT26 fills 7/18
  columns and PF3Y6K26 fills 6/18 (Name, Make, Model, Subtype, Serial, MAC,
  OS). Both came through the canonical-key sources and would have been
  blank before.
- **Sync invariant, against real Liongard data:**
  - Location and Asset Type were set by hand on WL-DT26.
  - A real dry-run reported WL-DT26 CHANGED on `hostname` only. Neither
    overlay field appeared as a diff.
  - WL-DT26's real pulled record was applied through the apply endpoint.
    Both fields survived.
  - Both were then restored to their prior values (unset).
- **Liongard timed out twice first** (`Timed out connecting to Liongard.`,
  the connector's 20 s per-request limit). A timed pull minutes later took
  3.0 s, 1.3 s and 0.5 s per page. The slowness was transient on
  Liongard's side, and the day's 15:21 scheduled sync had succeeded.
- **Expect one `hostname` CHANGED for PF3Y6K26 on the next daily sync.**
  The stored Liongard rows predate the canonical `hostname` key, and the
  worker now runs current code. This was not introduced by the Lists
  slices: their reconcile change only *removes* keys from comparison.

#### Found while verifying: a non-home-org API token can never authenticate

The `user` table's RLS policy is `home_org_id = app.current_org`.
`_resolve_api_token` sets the org to the *token's* org, then calls
`db.get(User, …)`. For a token minted in any org other than the user's home
org, that returns nothing, and the request fails with **403 "Account
deactivated"**. The message is wrong as well as the outcome.

`routers/users.py`'s token-creation path for the caller's own account
(`target_user_id = current_user.id`) accepts any org the caller is a member
of, so a multi-org user (ADR 0009) can mint such a token today.

Not fixed here: it is outside this slice, and auth changes follow
`docs/PLAN-auth-rbac-completion.md`. **Named item: API tokens for
non-home-org memberships.** Either refuse to mint them, or resolve the
token's user through a SECURITY DEFINER lookup like `resolve_api_token`
already does for the token row. Whichever is chosen, the error must stop
claiming the account is deactivated.

#### Follow-up slice (`lists-followup`): Jarrod's three answers

1. **Lists exclude out-of-boundary entities and say so; decommissioned
   entities stay.**
   - `list_projection.project()` drops `in_boundary = false`.
     Decommissioned entities stay with their Decommissioned Date.
   - The count is stated on screen and appended to the sheet's A3
     provenance line ("n entities excluded as out of the CUI boundary").
   - `GET /lists` reports `row_count` (in-boundary) and
     `excluded_out_of_boundary` separately. An assessor who cannot see a
     filter cannot assess it.
   - This applies to all four lists, not just devices. A rejected identity
     was never an authorized user either.
2. **Workbook imports honour the boundary/status invariant.**
   - No import or sync changes an existing row's `in_boundary`, and none
     moves a row out of `decommissioned`.
   - A workbook may still *decommission* a row, because its Decommissioned
     Date column is a source for that. Liongard keeps status outright.
   - **Found while testing that:** it never worked. `reconcile` compared
     attributes only, and for devices only an allowlist that excludes the
     raw date column. So a row whose only change was gaining a
     Decommissioned Date reconciled as UNCHANGED and was never applied.
     `_field_diffs` now reports `status` when an import asserts
     `decommissioned` on a row that isn't. A sync's default `active` still
     never registers.
   - This is the fifth instance of an older writer routing around a newer
     gate, and the fourth on `scope_entity`. The asymmetry was the defect.
     It was small enough to fix here, at the same choke point.
3. **Category aliases.**
   - `importers/workbook.py:resolve_category()` is case-, whitespace- and
     "Asset"-suffix-insensitive for every `ScopeCategory`, plus the long
     forms (Security Protection Asset, Contractor Risk Managed Asset,
     …). ~~Each alias match is logged.~~ **Corrected 2026-10-09:** it was
     logged at INFO, and nothing in the app configures logging, so the root
     logger's WARNING threshold dropped every line. Tests passed (`caplog`
     captures regardless), and a live dry-run of a `CUI` cell resolved the
     category correctly while writing **zero** log lines. Each alias is now
     a per-row dry-run warning, shown to the person confirming the import,
     and a WARNING-level log line. **Named gap:** WinGRC has no logging
     configuration, so *every* INFO line from an `app.*` logger is
     discarded in production. Nothing else is known to rely on one, but
     nothing would notice if it did.
   - The raw "Asset Type" cell is stored in its canonical spelling, so a
     re-import of an export is not a spelling-only edit.
   - **Round-trip guard:** import a workbook spelled the way Jarrod's is,
     plus the sample. Export each view and dry-run the export. Zero
     NEW/CHANGED rows are proposed. This catches the class of mismatch,
     not just one spelling.

---

### Lists: the frontend, the operator overlay, and two export defects ✅ DONE (2026-10-07)

Migration 0063. Grounded in Jarrod's real `Authorized-Entities.xlsx` and
`Windows 11 Workstation Baseline.docx` (seen by the advisory session that
wrote the slice prompt; this session worked from that description, the
repo's `samples/authorized-entities.example.xlsx`, and the code). Two PRs:
`lists-export-isolation` (§0, landed first on its own) and `lists-slice`.

#### §0 — two export defects, found by reading, not by testing

Neither came from a test, a user report, or a monitoring signal. Both were
found by reading `routers/scope.py` while grounding a feature request. That
belongs beside the three "reported green while checking nothing" signals in
the 2026-09-30 entry below: those were green signals that checked nothing;
these had no signal at all. Nothing exercised the export over HTTP, so
there was nothing to go red.

1. **Every org's export was written to one path.** `export_view` rendered
   to `tempdir/{view_id}.xlsx` and returned a `FileResponse`, which reads
   the path only when the response is sent. Two orgs exporting the same
   view could interleave so that one was served the other's device
   inventory. Not reproduced live; the shared path was enough. Fixed by
   `render.py:render_view_bytes()`, which renders into a `BytesIO`. The
   export writes no file, so the shared resource is gone rather than
   narrowed. The regression test calls the handler twice, the way the race
   interleaved (A returns, B runs, then A's body is read). It fails on the
   old code and needs no database, so it runs in CI's `backend` job.
2. **`c3pao_assessor` could not export a list.** The export was a `POST`
   that writes nothing, and `require_write()` rejects read-only roles on
   every non-idempotent method. It is now a `GET`, which is both the fix
   and the correct verb.

#### The sync-does-not-clobber invariant

**An import or sync may not clear or overwrite an operator-entered field it
has no source for.** `repo.upsert()` assigned every field wholesale, so
applying a Liongard CHANGED row did more than the slice prompt suspected:

- It erased any attribute key the sync did not carry. That is every
  hand-entered field, because Liongard's incoming record is its own raw
  record plus canonical keys.
- It reset `scope_category` to `None`, erasing Asset Type.
- **It put a rejected device back in scope.** Every fresh pull carries
  `in_boundary=True, status=active` by default. A device a reviewer had
  rejected (`in_boundary=False`), or one later marked decommissioned, went
  back in-boundary and active if a CHANGED row for it was ever applied,
  for example after a hostname rename. `reject_change`'s docstring said
  rejection was sticky because re-syncing "never re-flips this". That held
  for NEW and not for CHANGED.

This is the fourth time an older writer has routed around a newer gate on
`scope_entity`. The fix sits at the one choke point again.
`repo.upsert(..., operator_edit=False)` defaults to the safe behaviour, so
a writer added later that forgets the flag can erase nothing:

- For an import or sync, an overlay key the incoming entity does not carry
  is kept from the existing row. A Liongard write never sets an overlay key
  at all.
- A `scope_category` of `None` keeps the existing value.
- A Liongard write keeps `status` and `in_boundary`.
- `POST`/`PATCH /scope` pass `operator_edit=True`. An operator can still
  clear a field by sending it as `null`.

`reconcile._field_diffs` skips an overlay key the incoming entity does not
carry. Otherwise every annotated PROCESS/EXTERNAL_SERVICE row would show as
CHANGED on each import (those types still compare every key).

**Verified both ways.** The invariant tests were run against the pre-fix
`repo.py`/`reconcile.py`, with a shim that accepted and ignored the new
keyword argument. Exactly the eight invariant tests failed, including the
rejected-device and Asset Type cases. With the fix, all of them pass.

**Superseded the same day:** workbook imports now honour the same rule (see
the follow-up entry above). Original note: **Workbook import was deliberately
not given the Liongard treatment for `status`/`in_boundary`.** The workbook is a human-authored list of
authorized entities, and its Decommissioned Date column is a real source
for status. If a workbook re-import should also stop re-including a
rejected device, that is Jarrod's call. It was not changed here.

#### The overlay: storage decision and trade-off

The five columns no sync can supply are Location, Asset Type, In Service
Date, Decommissioned Date, and Requested By/Responsible Party:

- **Asset Type is `scope_category`.** That is already a real, indexed
  column, constrained to `ScopeCategory`, and the workbook importer was
  already parsing the "Asset Type" cell into it. A second field for the
  same fact would have been the parallel-mechanism mistake. **Deviation
  from the prompt:** the constraint is the full `ScopeCategory` enum (CUI
  Asset, SPA, CRMA, Specialized Asset, ESP, CSP, Out of Scope,
  Unclassified), not just CUI/CRMA/SPA. Specialized and Out of Scope are
  real CMMC categories, and rejecting them would block legitimate
  classifications. "Laptop" is rejected with a 422. Jarrod's cells say
  "CUI", and `_CATEGORY_LOOKUP` only matches "CUI Asset", so a workbook
  import of his sheet infers no category. That is an importer alias gap,
  out of scope here (no workbook-import changes), and noted for whoever
  next touches the importer.
- **The other four live in `attributes`** under
  `domain.OPERATOR_OVERLAY_ATTRIBUTES` (`location`, `in_service_date`,
  `decommissioned_date`, `requested_by`). They are validated by
  `routers/scope.py:OperatorOverlayAttributes`, with dates as ISO
  `YYYY-MM-DD` or a placeholder. There is no migration. The trade-off:
  they cannot be indexed, filtered, or sorted in SQL. Nothing filters on
  them today, and Asset Type, the one a filter would want, is already a
  column. If Location-based filtering arrives, promote `location` then.
  The vocabulary is kept separate from `DEVICE_SOFTWARE_CANONICAL_ATTRIBUTES`
  on purpose. Canonical keys are what a connector writes and reconcile
  compares; overlay keys are written only by a person and never compared
  against an import that lacks them. Merging the two sets would have put
  overlay keys into the comparable allowlist, and every annotated device
  would have shown CHANGED on every sync.

#### Lists render for synced orgs, not only workbook orgs

Found while grounding: `AUTHORIZED_DEVICES.columns` reads raw workbook
headers ("Name", "Make", "OS"), but Liongard writes canonical keys
(`display_name`, `make_oem`, `version`) beside its own raw record. For a
Liongard-synced org, 3.1.1c would have exported with Name, Make, Model,
and OS blank. `ListView.sources` gives each column an ordered source list
across the three writers' key sets, plus `@scope_category` and
`@natural_key`. `list_projection.py` is the one pure function that
resolves a cell. It is shared by the export and `GET /lists/{view_id}`, so
the screen and the workbook cannot disagree.

**Since decided (same day):** out-of-boundary entities are now excluded, and the
exclusion is stated. See the follow-up entry above.

#### `[PLACEHOLDER - reason]`

A cell like `[PLACEHOLDER - BIOS FW version not collected]` records *why*
a value is missing. It is stored verbatim, never normalized to blank.
`domain.placeholder_reason()` recognizes it. The export renders such cells
highlighted, with a legend line, and the screen shows them with the reason
as a tooltip. Both are distinct from a blank, which means nothing was
recorded. Round-trip is tested: render, then `parse_workbook`, keeps the
reason intact. It does not collide with the importer's bare `[placeholder]`
token, which marks an illustrative example row, not a value.

**N.4 overlap:** this is N.4's variable syntax arriving early. N.4's
`{Company Name}` is a value to be filled in; `[PLACEHOLDER - reason]` is a
value deliberately absent, with the reason recorded. N.4 should read both
when it designs its variable registry, so that an unresolved variable and
a deliberate placeholder are not two unrelated schemes. Recorded in
`docs/PLAN-document-library.md`'s N.4 section.

#### 3.1.1b is service accounts, and nothing populates it

`AUTHORIZED_PROCESSES.entity_type` is `EntityType.PROCESS`, which fits.
Only a workbook import or manual entry ever creates one; Liongard maps
every identity to PERSON. An empty 3.1.1b is therefore the normal state,
and both the screen and the exported sheet say so in place of a bare table
that looks like a failed sync. The sheet puts this above the header, never
as a data row a re-import would read as a process. Manual entry is the
"+ Add process" form on Scope › Lists. No discovery integration was built.

#### Baselines: a naming collision, corrected

The nav's "Baselines" was said to mean the product baseline library.
Jarrod's `Windows 11 Workstation Baseline.docx` showed otherwise. It is an
org-level narrative document (tool inventory, change management, review
cadence, tied to CM.L2-3.4.1/3.4.2) and belongs in the library beside
Policies and Procedures. So:

- `baseline` is a `DocumentType` (migration 0063, purely additive).
- Library › Baselines filters to it.
- AdminArea's "Tools" section is renamed **Product Baselines**, with
  "Product Baseline Library" as the panel title. Two features sharing a
  word is what produced the mistake, and renaming one of them stops it
  recurring.
- `SideNav.tsx`'s comment, which explained the half-wrong distinction, is
  corrected.

#### Cross-references: a requirement, not built

Jarrod's baseline document says *"Authorized devices are recorded in New
Lists/AC/3.1.1c Authorized Devices"*, and it names other lists the same
way. Documents need to reference lists. Link resolution was deliberately
not built here. Recorded in `docs/PLAN-document-library.md` for N.4 to
design, with this example attached.

#### Where things are

- Scope › Lists is the screen (`ListsPanel.tsx`). (Moved into Library › Lists in L.2, 2026-10-13.)
- Library › Lists ↗ points there, with a hover title.
- `GET /orgs/{org_id}/lists` returns the views with counts.
- `GET /orgs/{org_id}/lists/{view_id}` returns the rows as JSON.
- `GET /orgs/{org_id}/exports/{view_id}` returns the `.xlsx`.
- The asset drawer edits Location and the two dates, and relabels scope
  category as "Asset Type (scope category)".

#### Verification

- Bench, wl-util-1, isolated project `wingrc_verify_20261007`:
  - Backend full suite: **1589 passed, 0 skipped**, with
    `WINGRC_MIN_TESTS_RUN=500`. `ruff` clean.
  - Migration 0063 applied on start.
  - Frontend in `node:24-alpine`: `tsc -b` clean, **343/343** vitest, and
    the build passes.
- §0 alone: 1559 passed on the bench, and CI was green on all three jobs.
- SPRS and `control_state` are asserted unchanged across overlay edits, a
  sync apply, every list read, and every export.
- RLS was checked through real HTTP (`_app_session`, `wingrc_app`) for
  every new endpoint: cross-org returns 403 and each org sees only its own
  rows.

**Out of scope, unchanged:** inline grid editing, re-importing the
workbook, N.4/N.5, and any SPRS or `control_state` effect. A list is not
evidence.

---

### API tokens outside the user's home org: refuse creation, say what is true ✅ PARTIAL (2026-10-08)

Found while verifying the Lists deploy. **It fails closed: a correctness and
usability defect, not a security hole.** No token ever authenticates beyond
what it should. A class of tokens simply never authenticates at all.

**Mechanism.** The `user` table's RLS policy is
`home_org_id = app.current_org`. `_resolve_api_token` sets the org to the
*token's* org, then calls `db.get(User, …)`. For a token issued in any org
other than its user's home org, that lookup returns nothing.
`api_token.user_id` cascades on delete, so that is the *only* way the lookup
can come back empty. Until now the request failed as 403
"Account deactivated", which sent whoever was debugging to a user record
with nothing wrong with it. `POST /orgs/{org_id}/api-tokens` with no
`user_id` (self-issue) would mint such a token for any org the caller is a
member of. On-behalf-of tokens could not hit this, because `_get_user` only
finds users homed in the path org.

**Landed now (two separable pieces):**

> **The 422 below is a provisional gate, not the design.** Cross-org tokens
> are evidently *intended*: `_resolve_api_token` already derives the role
> from the membership in the token's org (see "Left for the auth plan").
> The block exists only because the user lookup cannot see across orgs
> yet. A reader who finds the 422 must not conclude that cross-org tokens
> are deliberately unsupported. The fix is to make them work, then remove
> the gate.

1. **Creation is refused.** Self-issue in a non-home org now returns 422,
   with a message naming the home-org rule. The route reads the
   request-cached `get_current_user` identity: `require_org_access`
   returns a `replace()`d copy, so the original still carries the home org.
   The API Tokens panel offers "+ Create Token" only in the home org and
   explains why everywhere else.
2. **The message is true.** A missing user on the API-token path now says
   the token was issued outside its user's home org and needs reissuing
   from there. A genuinely inactive user still gets "Account deactivated".
   Existing cross-org tokens, if any were minted, now explain themselves.

**Left for the auth plan (`docs/PLAN-auth-rbac-completion.md`).** The real
fix is letting a multi-org user hold a working token in a second org.
`_resolve_api_token` already computes the effective role from the
membership in the *token's* org (`_role_for_membership(db, user.id,
row.org_id, …)`), so cross-org tokens were evidently intended. What blocks
them is only the user lookup. It needs a SECURITY DEFINER resolver like
`auth.resolve_api_token`, which is RLS machinery and belongs in that plan.
When it lands, remove the 422 and the panel note together.

---

### Every change a request makes is audited, and a check enforces it ✅ DONE (2026-10-20, bench-verified; backend and tests only)

**Why.** Marking an objective *met* wrote no audit row and no actor. The
SPRS score is computed from exactly those assertions, so it derived from
claims nobody was recorded as making. The evidence section's live walk
found two smaller gaps (upload, activation), and the sweep they prompted
found this one (the Planned entry "Marking an objective met is recorded
nowhere", kept below for its reasoning). Every gap was in the assessment
core, the code written before the audit convention. So the fix is an
enforcing check, not just the rows: the rule existed, and nothing
enforced it.

**The check (`backend/tests/audit_check.py`, wired in `conftest.py`).**
It judges behaviour, not the HTTP verb.
- `_app_session` brackets each request. A cursor-level listener records
  every INSERT, UPDATE and DELETE the request commits (the table, and an
  UPDATE's columns), and the `entity_type` of each `audit_log` row. A
  request's writes are dropped on rollback.
- **Each written table must be accounted for by an audit row of the same
  request naming that table, or by a rule.** "Some audit row exists" is
  not enough: it is satisfied by something irrelevant.
- **Rules are keyed by table, operation and columns, and each carries its
  reason.** A rule either names the parent audit types that account for
  a derived write (a list's tags under `list_definition`, the score
  snapshot under `control_state`), or marks bookkeeping. An UPDATE of any
  column a rule does not name is not covered, so the login counter's rule
  never hides a role change. A derived write that always accompanies an
  audited change needs no exemption, because the check is per request:
  `sprs_snapshot` beside a mark-met row is accounted for, and written
  alone it is not.
- **Five bookkeeping rules, each with its reason:**
  - the session's sliding clock (`user_session.last_activity_at`);
  - a token's `last_used_at`;
  - the failure counter and expired lock a successful sign-in clears;
  - a reviewer opening a cycle (`review_cycle_reviewer.status,
    viewed_at`): Jarrod's call, because it is an observation, already
    read by `close_cycle` and the attestation document;
  - the hash the bundle export caches on `evidence`.
- **SECURITY DEFINER writes are counted.** A function called by SELECT
  writes where no statement shows it. Membership grants go that way on a
  request path (`auth.grant_org_membership`). Each `auth.` function the
  app calls is classified as a writer (what it writes) or a reader. A
  test fails on one that is neither, so a new one is read before it is
  trusted.
- Enforced by default: a test fails if any request it made broke the
  rule. `WINGRC_AUDIT_CHECK=report` writes one JSON line per request for
  measuring, and `off` disables it.

**The companion: every mutating route has a test that reaches a
write.** The check judges only requests the suite makes. With
`WINGRC_AUDIT_COVERAGE` set, which the CI `integration` job now sets as
the only job that runs the whole suite against a database, the session
fails when a POST/PUT/PATCH/DELETE route committed no write in any test
and is not listed with its reason. A listed route that now reaches a
write also fails it, so the list cannot go stale. **The count was 11 of
107** (measured before any fix):
- eight write nothing by design (four baseline-import dry runs and
  drafts, research URL suggestion, the workbook dry run, MFA enrolment's
  and re-enrolment's first steps);
- three have no test reaching a write: logout, reset-MFA (neither has
  any test at all, the Planned item "No test calls logout or reset-MFA"),
  and resolving a review-cycle flag (only the refusal is tested).

**What it found, by route.** Measured on the bench over the full suite
(2,215 requests). The static sweep had listed nine.
- **Fixed, each with a new audit row:**
  - mark an objective's status: `control_state.update`, with the SPRS
    score before and after and the objective key;
  - upload evidence and add a reference: `evidence.upload` and
    `evidence.add_reference`, with title, type, hash and objective, never
    the bytes or a URL;
  - write statements: `implementation_statement.create` and `.update`,
    with status and whether the body changed, not the body;
  - activate a tool: `org_product.activate`, saying whether it was a
    reactivation, and the version pinned;
  - change a task's status: `evidence_task.update`;
  - create an org: **two rows.** `org.create` is deployment-wide
    (`org_id` NULL), like product administration: the request has no
    org, and `audit_log`'s policy admits only the current org or none
    (migration 0060). `org.created` is the first row of the new org's
    own log, written under its context in the same transaction, so a
    tenant's history has a beginning. "When was this org set up, and by
    whom" is asked from inside the org. The context is then put back
    (`test_create_org_writes_the_first_row_of_its_own_log` reads both
    as `wingrc_app`);
  - set a password from an invite or reset token: `auth.password_set`;
  - **rename a user: `user.rename`, found only by the behavioural
    check.** `patch_user` audited role and activation and not
    `display_name`. See the lesson below this entry for why that was
    not cosmetic;
  - start an assessment: `assessment.create`.
- **Found only by the behavioural check:** three GETs that write
  (`/auth/me`, a review cycle's page, the users list). All three are
  bookkeeping under the rules above. A method-based check would never
  have looked at them.

**Its limit, kept in view.** The check matches an audit row's entity,
not its action. `POST /assessments` always wrote a `raci.copy_forward`
row naming the new assessment, so it passed both before and after the
fix: right entity, wrong action. Its `assessment.create` row is kept by
a direct test (`test_raci_copy_forward.py`).

**A concrete case from before the rules (recorded 2026-10-23).** On
2026-09-07 a verification on live made an "Actor Verify Bot" user, its
membership and an API token (`.scratch/verify_actor_live.py`). It used
the token to create a scope entity, then deleted all four rows
(`cleanup_actor_verify.py`). The deletes were raw `DELETE`s on
`api_token`, `org_membership`, `user` and `scope_entity`, with no audit
row. Live's log holds no trace that the user or the token existed. The
plaintext token sat in `.scratch/` until 2026-10-23, and a lookup by its
hash was the only way to show it was dead. Today's rules forbid each
part of that: an unaudited write, a delete where an archive belongs, and
a credential minted outside the route that records it. **The check
would not have caught it, though.** It brackets HTTP requests, and the
cleanup ran on a direct `SessionLocal()` in a script. A write made on a
session the app does not hand out is outside the check's reach. Only
a rule covers it, and it is in CLAUDE.md's Stack & conventions because it
governs what is done, not what was found: a write to a live database
goes through an audited CLI command, or it does not run.

**Two rules accept a broad parent, and both are pinned to the actions
measured making the write.** The same weakness applies to them: a row
that does not describe the write could satisfy the check.
- `evidence` INSERT accepts a `control_state` row, because a list link
  audits on the objective. A mark-met row is a `control_state` row too.
- `evidence_state_link` accepts any of the five things a link is made
  for.

A report run over the suite listed every request writing either table
and the audit actions beside it. The set is closed:
- **evidence INSERT:** seven actions. The `control_state` parent is
  used only by `evidence.list_link` and `evidence.list_move_forward`.
- **evidence_state_link:** eleven actions. A deactivation's and a
  reactivation's links have rows of their own type.
- Mark-met never accompanies either.

Each rule now carries its `actions` (`Rule.actions`). A request writing
the table fails unless one of them is among its committed rows. A new
path fails until its action is added, with friction.

The dedicated tests:
- `test_a_broad_parent_accounts_only_through_the_actions_measured_making_the_write`:
  the evidence and its link beside a list link's row pass. Beside a
  mark-met row they fail.
- `test_the_broad_parent_rules_pin_their_actions`: removing either pin
  fails.
- `test_a_links_audit_row_names_the_objective_and_version_its_evidence_and_link_are_for`
  (`test_list_evidence.py`): the row describes the write. Its entity is
  the objective the link is on, and its `list_version_id` is the
  version the evidence row was made from. This holds for the first link
  and for a second link that reuses the evidence.

The pin still matches action and entity separately, not as a pair on
one row. That is enough while the action names imply their entity.

The check sees request paths only. The CLI and the scheduler write
outside a request and are not judged.

**Checks:**
- **Full suite, enforced, companion on:** 1,866 passed, `ruff` clean.
- **Before the fixes, enforced:** 86 failures across 16 files, every one
  this check's.
- **Mutation:** each new audit row removed in turn, against its route's
  tests. Eight go red through the check (mark-met 34 failures,
  upload/references 44, org 14, statements 12, activation 12,
  set-password 10, task status 4, rename 2). `assessment.create` stays
  green under the check, as its limit predicts, and goes red through its
  direct test.
- **The companion fails a narrow run:** `test_orgs.py` alone reports 95
  routes, exit 1.
- **Mutation, the follow-ups:** each goes red.
  - No `org.created` row: 1 failure.
  - The context left on the new org: 1.
  - Either action pin removed: 2 each.
  - The list link's row naming another version: 1.
  - The list link's action renamed: 1 failure and 14 errors. The
    rename makes the pin fail every request the suite makes that links
    a list, which shows it is enforced across the suite, not only in
    its unit test.
- The check's own tests: 25, in `test_audit_check.py`.

**Not in this slice:** an actor on `control_state_history` (the Planned
item "An objective's history says who changed it"), and tests for the
three routes no test reaches a write.

---

### Lesson: a field that looks cosmetic was provenance (2026-10-20)

Renaming a user wrote no audit row. A display name looks cosmetic, and a
sweep of what each route writes would have passed it. It is not
cosmetic. `detached_by` (evidence links), `deactivated_by` (a tool)
and `saved_by` (a list's link and tag events) store a display name
as text, not a user id. `detached_by` holds either a person or the CLI's
actor label, so it cannot be a foreign key (its column comment). A
rename therefore cuts a stored attribution off from the person it
names. "Detached by Sam Lee" stops
being findable as anyone, and nothing said that it was the name of an
account now called something else. The `user.rename` row is now the
only link back.

It surfaced because the behavioural check flagged `PATCH /users/{id}`'s
`display_name` write, and asking what reads that column found the two
text fields. Neither the check nor the static sweep knows what a column
means. The check only said a write was unaudited, and the question
"what stores this value as a record?" did the rest. That is the same
question as the side-effect lesson below, asked of a value instead of
an action.

**The shape will recur.** A field is provenance when another record
copies its value rather than referencing its row: names, titles and
labels stored as text at the moment of an act. Changing such a field
needs an audit row even when the change looks like a typo fix. A new
"`*_by` as text" column should name, in its docstring, the field it
copies.

### The visual batch: V, L.9 and U ✅ DONE (2026-10-20, bench-verified; frontend only; deployed and walked on live)

Three Planned items in one branch, three commits so a bisect can tell
them apart, in this order: **V, then L.9, then U.** V changes `--muted`
everywhere and L.9 measures contrast, so measuring L.9 first would have
measured against a token about to change. U touches only `api.ts`.
**Backend untouched**: no file under `backend/` changes.

**Two of the three reach back into L.8's page,** which is the regression
surface. V removes the `--muted` override L.8 added to
`.list-library-detail`, and L.9 moves L.8's table CSS and its
`useSideScroll` hook out of `ListLibraryPanel`. So L.8's own three
acceptance checks were re-run on the bench rather than assumed (below).

**How it was measured.** The bench served `main`'s build (`e6b38f0`) and
this branch's side by side over one backend holding a restored copy of
live, plus bench-only rows: 40 more devices in Acme's scope, and a copy of
Winsorlabs' widest template (Authorized Devices & Hardware Inventory, 19
columns) with 40 rows beyond its two examples, one a `[PLACEHOLDER -
reason]`. The copy was dropped at teardown. Headless Chromium at
1366×768 took 34 captures of screens and states on each build:
- the Lists index, a scope-drawn list (top, scrolled, hovered), the
  library list (top, scrolled, scrolled right);
- the dashboard, the board (with a hovered family header and expanded
  objectives), the control drawer with guidance;
- an objective's archived evidence, Tasks, Tools, Assets, Contacts,
  Periodic Review, Org Profile, Network Diagram;
- Documents, a document, its History, a diff, and a refused tag removal
  (the yellow panel);
- Security, Audit Log, and the login form.

On each, every text node was measured against the background it actually
sits on: ancestors composited, opacity applied, and text behind a modal
overlay skipped. Of the 82 `color: var(--muted)` rules, 55 were reached
on screen. The other 27 (wizards, MFA enrolment, a file's size and remove
control, the import report, and the like) are worked from the background
their CSS puts them on.

#### V: `--muted` is `#5c636a`

The token changes, not its uses: `#6c757d` (4.45:1 on `--bg`) becomes
`#5c636a`. L.8's per-page override is gone.

| Where `--muted` sits | Background | Before (`#6c757d`) | After (`#5c636a`) |
|---|---|---|---|
| Page text: hints, sub-lines, table headers, form labels, board meta, document labels (43 contexts) | `--bg` `#f8f9fa` | 4.45 | 5.78 |
| Cards, drawers, dialogs, the login card (38 contexts) | white | 4.69 | 6.09 |
| **An objective's archived evidence** (*No longer attached*) | white, at opacity 0.85 | **3.52** | **6.09** (opacity removed; 4.32 with the token alone) |
| Board family header | `#f1f3f5` | 4.22 | 5.48 |
| Board family header, hovered | `#e9ecef` | 3.95 | 5.14 |
| Control header, expanded or hovered | `#f5f6f7` | 4.33 | 5.63 |
| Control header, resting | `#fafbfc` | 4.53 | 5.88 |
| Completion dot on the active side-nav item | `#e0e7ff` | 3.81 | 4.94 |
| Tool card's role line | `#f0f6ff` | 4.32 | 5.61 |
| Wizard step number (CSS, not reached) | `--border` `#dee2e6` | 3.60 | 4.68 |
| Side-nav item hovered (CSS) | `#dee2e6` | 3.60 | 4.68 |
| Diff −/+ marker on a deletion (CSS) | `#f8d7da` | 3.51 | 4.56 |
| Diff marker on an insertion (CSS) | `#d1e7dd` | 3.61 | 4.69 |
| Diff marker on a replacement (CSS) | `#fff3cd` | 4.23 | 5.50 |
| Dashboard row hovered (CSS) | `#eef2ff` | 4.19 | 5.45 |
| A list tab's count on a hovered tab (CSS) | `#f0f4ff` | 4.26 | 5.54 |

Every `--muted` context is now at or above 4.5:1; the lowest is 4.56.

**Found by measuring: yesterday's archived evidence failed twice over.**
`.ev-archived` drew its rows in `--muted` *and* at opacity 0.85. That was
3.52:1 before this slice, and still 4.32:1 on the new token. It is
already greyed by colour, struck through and written as a sentence (the
L.10 rule), so the opacity went. Its *Download* link and buttons were
being faded with it too.
- **It shipped the day before, in the slice that added it** (the evidence
  section entry below).
- 3.52:1 is above the 3:1 a deliberately faint element is held to, but
  these rows are not that. They are sentences a person reads, saying
  when and why evidence left, so they are held to AA's 4.5:1, and they
  were well under it.
- Nobody measured them, because the slice that added them did not
  measure, and the sweep that did came a day later.

**Convention, from this (2026-10-20):** a slice that draws text on a
background, or at an opacity, that has not been measured before
measures it in that slice, and states the ratio as a number. It does not
wait for a later sweep. This is now in CLAUDE.md's conventions.

**The yellow refusal panel holds no `--muted` text.** Its sentence is the
notice colour. What fails on it is the *Keep it* button's blue (below).

**The literal `#6c757d` uses stay, both deliberately faint, both above
the 3:1 a placeholder needs:**
- **The example cell** (`[PLACEHOLDER]`): `#6c757d` on its `#f1f3f5`
  tint, 4.22:1. It is meant to read fainter than real values (`#212529`,
  15.4:1) so a template example is never mistaken for a record (L.8).
- **The empty-cell dash** (—): `#6c757d`, 4.69:1 on white, 4.45:1 on a
  hovered row. It marks absence beside near-black values. L.9 moved it
  from L.8's page scope to the shared `.list-rows`, so the scope-drawn
  lists now draw the dash the same way. Before, they drew it in
  `--muted`.

There are no other literal `#6c757d` uses in the CSS or in any component.

**Found, not fixed (not `--muted`, so not this slice).**

**`--primary` `#0d6efd` on `--bg` is 4.27:1, and it is the next visual
item (V.1 under Planned), not a backlog line.** It is every ghost button
and text link on the page background: *← All lists*, *Adopt templates*,
the Lists index's titles, *Edit*. That is worse than V's defect. A link
is the element a person is hunting for, whereas secondary text is what
they skim past. It has the same one-token shape.

Smaller ones, under 4.5:1 on the branch:
- `#0d6efd` on the refusal panel's `#fff3cd`: 4.06.
- On the tool card's `#f0f6ff`: `#198754` (*Active*) and `#dc3545`
  (*Deactivate*), 4.17.
- The *Inactive* status badge, `#64748b` on `#f1f5f9`: 4.34.
- The drawer textarea's placeholder, the browser default `#757575`:
  4.37.
- Seven inline `#6b7280` greys (Liongard sync, review cycles, SPRS
  submissions, MFA re-enrol): 4.59 on `--bg`, 4.83 on white. These pass.
- Disabled controls are under 3:1 (*Record review — still current*,
  *No changes*). WCAG exempts inactive components.
- The side nav's ✓ (`#16a34a`, 3.30) is a graphic, held to 3:1.

#### L.9: the scope-drawn lists draw their rows in the library list's table

**One definition, imported by both panels:**
- **The hook and the frame are in `frontend/src/components/ListRows.tsx`.**
  `useSideScroll` moved there unchanged. `ListRowsTable` draws the
  side-scroll cue, the scroll box (`role="region"`, labelled "{title}: N
  rows, M columns") and the `<table className="contacts-table
  list-rows">`. Each caller draws its own header and cells.
- **The style is in one block in `styles.css`:** "The table a list's rows
  are drawn in". This is L.8's `.list-rows*` rules and the scroll hint,
  moved out of L.8's page block.
- `ListLibraryPanel` (L.8's page) and `ListsPanel` (the four scope-drawn
  pages) both import them. Nothing was copied.

**Kept, because they are what makes a projection different from a kept
list:** the "projected from the live scope graph" line, the
excluded-entities note, the empty explanation, and the `[PLACEHOLDER -
reason]` legend. A test asserts all four, plus the shared table.

**Two small things the move needed:**
- **The title row.** On the scope pages the title sat mid-right, pushed
  by `.contacts-panel-header`'s `flex-end`. It now sits left as on the
  library page (`.list-view-header`, those four pages only).
- **The placeholder tint on hover.** A scope-drawn placeholder is the
  `<td>` itself, so the shared hover background would have replaced its
  amber tint. The tint is kept.

**Narrowly those four pages.** None of the other 16 `.contacts-table`
users changed, and neither did the Lists index's own table. A table
component for all of them remains the follow-on L.8 named.

**On the bench, the scope-drawn Authorized Devices (43 rows, 18
columns):**

| Check | `main` | Branch |
|---|---|---|
| First row visible at 1366×768 | yes, bottom at 326px | yes, bottom at 337px |
| Sticky header | the table has no scroll box, so the header leaves with the page | pinned to the box top after scrolling 600px |
| Side-scroll cue | none | "18 columns, wider than the screen. Scroll sideways for the rest →" |

**L.8's page, re-verified against its own three checks** (it now renders
from moved code):
- **First data row visible at 1366×768:** yes, row bottom at 585px.
- **Sticky header:** pinned to the box top after scrolling 600px, on both
  builds.
- **Placeholders distinct from real values:** yes.
  - The example cell is italic `#6c757d` on `#f1f3f5`.
  - The reason cell is italic `#7a5c00` on `#fff4cc`.
  - A real value is upright `#212529`.

  These are identical on `main` and the branch.

Fresh screenshots were taken of the top, scrolled down, and scrolled
right.

**Mutation:** `main`'s `ListsPanel.tsx` against the new test fails it
(the shared region is not there); restored, 6/6. The side-scroll cue's
own test (`ListRows.test.tsx`) drives `clientWidth`/`scrollWidth` both
ways: wider says so and which way, fitting says nothing.

#### U: an error's detail reaches the screen as a sentence

The 28 `api.ts` calls that threw `new Error(body.detail ?? ...)` now go
through `detailText`, which turns FastAPI's 422 list and a `{message}`
detail into the server's own words. A string detail passes through
unchanged.

**Callers that inspect the message: 0.** Each of the 28 changes the
string its callers receive, so the callers were read before any change.
The search covered every read of an error's `.message` in `frontend/src`: comparisons,
`includes`/`startsWith`/`match`/`test`, a `switch`, anything assigned
onward. Every read displays the message (`setError(e.message)` or `e
instanceof Error ? e.message : "…"`), with one exception that is
display too. `UsersPanel`'s blocked delete branches on `ApiError.status
=== 409`, a `send()` error rather than one of the 28, and shows the
message. No branch could change.

**Pinned at the screen, not at the function.** `LoginPage.test.tsx` signs
in with `someone@acme.test`. It runs against the backend's real 422 body,
captured from the bench stack, and asserts the form shows the
validator's sentence:

> value is not a valid email address: The part after the @-sign is a
> special-use or reserved name that cannot be used with email.

It also asserts that no "[object Object]" appears anywhere. Run against
`main`'s `api.ts`, the test fails, and the form renders "[object
Object]". In the browser on the bench, the same address shows "[object
Object]" on `main` and the sentence on the branch.

**Two more sites, outside the 28, were left alone.** Both have the same
flaw, and both are latent: no screen reaches them today.
- **`downloadBundle`: same flaw, same fix.** It does `if (body.detail) msg
  = body.detail`, so an object detail becomes "[object Object]". The
  endpoint's own errors are strings (404s). But its `list_id` query
  parameter is typed `list[UUID]`, so a malformed id gets FastAPI's 422
  list. The bundle picker sends only real ids, so nothing reaches that
  today. The fix is `detailText(body.detail, …)`.
- **`applyBaselineImport`: same flaw, but not quite the same fix.** It
  already handles the endpoint's own 422 shape, `{"problems": [...]}`,
  by joining them, and its other errors are strings. What falls through
  to `detail ?? …` unhandled is FastAPI's own 422 list, for a missing or
  malformed `file` field, which the upload form never sends. The fix is
  `detailText` for the fall-through, with the `problems` branch kept
  first. `detailText` does not know `problems`, and routing it there
  alone would turn the validation list into the fallback status line.
- **Should `detailText` learn `{problems: [...]}` instead? Sized
  (2026-10-20): no, it is a one-off.**
  - Five backend routes raise an object `detail`.
  - Four carry `message`, which `detailText` already reads:
    `documents.py:664`, and `list_definitions.py:362`, `:498` and `:631`.
  - Only `admin_products.py:667`'s baseline import apply raises
    `{"problems": [...]}`, and `applyBaselineImport` is its only reader.
  - The import wizard's `preview.problems` is a successful dry-run body,
    not an error.

  So the branch stays in that one caller, with a comment saying the shape
  belongs to one endpoint. **If a second endpoint ever raises `problems`,
  move the shape into `detailText`.** A helper that knows two of three
  shapes makes every caller learn the third, and that is how 28 call
  sites each reimplemented the same line.

**Verified:** vitest 467/467 (463 before: two `ListRows`, one
`ListsPanel`, one `LoginPage`), `tsc -b` clean, `npm run build` clean,
all on the bench from source identical to the commits (checksummed). No
existing frontend test asserted the old colour or the old table markup,
so none needed updating.

**Live (2026-10-20, `09aee13`): deployed and walked by Jarrod, all as
expected.**
- He opened the Lists index, then one scope-drawn list and one library
  list. They read as two screens of one product.
- He signed in with a bad address and read the sentence, not
  "[object Object]".
- Nothing on this walk writes, so there is nothing to read back. Before
  the walk, the served stylesheet was checked for the new `--muted` and
  `.list-view-header`.

---

### The evidence section stops destroying things and stops hiding things ✅ DONE (2026-10-19, bench-verified, migration 0071; deployed and walked on live 2026-10-20)

One idea in four parts. **An objective's evidence section tells the truth
about what is attached, what used to be, and what happened to it.** The
four parts: the ✕ (it destroyed), archived evidence staying visible (it
hid), the section's two cosmetic faults, and item Y, which is an
independent test.

**The ✕, and the tell.** `routers/evidence.py`'s `delete_evidence`
hard-deleted the link. On the artifact's last link it also deleted the
Evidence row and its stored file. It wrote no audit row. Two exemptions
had been carved out of that, each by the slice that needed to protect
its own thing: a published document's record (item X) and a review
cycle's record (2026-10-19). **That was the tell.** The rule was never
"these two are special"; it is that the product does not delete records,
and the exemptions were the places someone noticed in time. Both are
removed, because they became unnecessary, not because they were wrong.

**One path: `backend/app/evidence_links.py`.**
- `archive()` is the state change of every archive.
- `restore()` is the state change of every restore.
- `detach()` archives a link and writes one audit row naming the
  artifact, the objective and the assessment.
- The ✕ (now `POST …/evidence/{id}/detach`) and
  `review-cycles-detach-unattested` both call `detach()`. One test
  holds both to the same assertion and spies that both reach it.
- The CLI is still needed, for scripted and bulk use: it finds every
  unattested record's link in an org. It no longer has its own
  implementation.
- The list and document unlinks archive through `archive()` and keep
  their own audit actions, because the document's History reads them.
  The list unlink no longer deletes its Evidence row.
- Deactivation, reactivation, moving forward and re-linking
  (`live_link_row`) use the same two state changes.
- **Nothing in the app deletes an `evidence_state_link` row any more.**
  `cli.py`'s reset command still does; it is a whole-tenant wipe.

**Every archive says why** (migration 0071):
- `evidence_state_link.archive_cause` is one of `deactivation`,
  `detached`, `moved_forward` or `attestation_detach`.
- `detached_by` / `detached_at` say who detached it and when.
- Three CHECKs hold the rule in the database: the cause is set exactly
  when the link is archived, and only a deactivation's archive names a
  product.
- **Backfill** from the rows. A link naming a product is a deactivation's.
  One on a review cycle's record is the CLI's (actor read from its audit
  row). A list or document link was moved forward, since their unlink
  deleted until now. Any other archived link refuses the migration rather
  than be given a guessed cause.
- **Previewed on live, read-only:** 4 links → `attestation_detach`,
  1 → `moved_forward` (AC-POL-01 v3), none unknown.

**The reactivation rule, generalised.** Deactivation archives with the
product and reactivation restores by it, so a person's detach names no
product. That includes a detach of a link a deactivation had already
archived: the objective's greyed item offers *Detach for good*, which
clears the product. Reactivation restores only links its own
deactivation archived. It never restores one a person detached, whether
that was before the deactivation or after it. A negative test
(`test_reactivation_restores_only_what_its_deactivation_archived`)
covers both.

**Jarrod's decision (§2), in his terms:**
- "Replaced by X" exists only when a person names the replacement at
  deactivation. The product never infers it.
- The question is optional, not required. Leaving it blank is a real
  answer, and the common one.
- A tool taken away with nothing put in its place should be evident
  rather than silent.

So:
- Deactivate (`ProductCard.tsx`) now asks first, with an optional
  *Replaced by*. Before this, deactivation was one click with no
  confirmation.
- `org_product.deactivated_by` and `replaced_by_product_id` are set at
  each deactivation.
- Where a replacement is named, the archived evidence reads *Replaced by
  Defender — stated by {name} when Heimdal was deactivated, {date}*, and
  the bundle says the same with who stated it.
- Where none is named, it reads *(Archived {date} — Heimdal was
  deactivated)*. On an objective the server counts as having no live
  evidence, a loud alert says nothing was named to replace it, and which
  active tools, if any, still contribute.
- A person's detach reads *(Detached {date} by {name})*.

**Decided: a replacement need not cover the same objective to be
nameable.** The person states a fact about their estate, not a coverage
claim. Refusing their statement because the mapping disagrees would be
the product arguing with the operator. The archived item says whether
the named replacement contributes to this objective, computed from
`control_state_contributor`. Only its own product, or a product that
does not exist, is refused (422).

**Display.** Archived items sit greyed beneath the live evidence, under
*No longer attached*. Each is a sentence, never an `.ev-item` row, and
has no chip, per the L.10 rule. A test asserts this on rendered output.
Moved-forward links are left out, because their newer link is live and
the drift notice covers them.

**Found while building: four readers counted archived links as live.**
- `list_evidence`, the objective's evidence list, had no `is_archived`
  filter.
- So did the evidence manifest endpoint, the control-state
  `evidence_count`, and the dashboard's blocked-objectives widget.

All four now read live links only. This was latent while only a
deactivation archived generic evidence. It became live when the
unattested detach ran this morning: on live, Acme's and Winsorlabs'
AC.L2-3.1.1[a]/[c] still list the detached records as current evidence,
until this deploys. **The planned entry below ("Archived evidence stays
visible") said such evidence "vanishes from its objective". That was
wrong**, and it was written without reading the query.

**What the ✕ defect was worth on live, counted read-only before deploy:
nothing yet.** No org has a generic Evidence row with a live link, and
none has a linked list version. Every artifact live today is a published
document's record or a review cycle's record, the two the exemptions
protected. The defect was waiting for the first uploaded file.

**The section's cosmetics.** `EvidenceSection` now loads the lists and
documents. It draws *Not attached* once, for both, and `ObjectiveLists`
and `ObjectiveDocuments` render only their rows. The template
instruction is said once after the rows. `not_offered` for a template is
now "a template: its rows are examples", and candidates carry
`is_template`. Tested with a list and a document both unattached, the
case the bench never rendered.

**Item Y: `tests/test_model_check_constraints.py`.**
- It builds the models into a scratch schema (`create_all` through
  `schema_translate_map`).
- It compares `pg_get_constraintdef` both ways, keyed by (table, name),
  then drops the schema.
- A unit test requires every model CHECK to be named.
- **Mutation-checked both ways.** One value was removed from
  `ck_org_product_status`: it fails, naming the table, the constraint
  and both definitions. A model CHECK was deleted: it fails, "in the
  database only".
- **First-run findings: none.** There are 54 CHECK constraints in the
  migrated schema, and every one matches its model. The one drift Y was
  written for (attestation, L.4) had already been fixed. Unique
  constraints, indexes and FK `ondelete` remain the follow-on.

**Verified on the bench (`wingrc_verify_evt`):**
- 0071 applied on stack start.
- Full suite: 1838 passed, 0 failed (8m18s, with the count floor).
- Ruff clean.
- No-DB local: 595 passed.
- Frontend: vitest 463/463; `npm run build` (`tsc -b`) clean.

Asserted along the way:
- The detach archives and never deletes: the link row, the Evidence row
  and the stored object all survive, with the object read back.
- One audit row per detach.
- Status and SPRS are unchanged.
- `c3pao_assessor` gets 403 on detach (`test_assessor_readonly`'s
  harness).
- RLS holds through real HTTP.
- No test compares stamps across moments.

**Live-copy rehearsal (`pg_dump` of live, restored into the bench as
`livecopy`, dropped and the dump deleted afterwards):**
- 0071 applied, and its causes matched the read-only preview.
- Each assessment's [a]/[c] lists no live evidence except Winsorlabs [a]'s
  AC-POL-01 v4. Each shows its detached record under *No longer
  attached*, with the CLI's actor and time.
- SPRS recomputed equal to stored (Acme −199, Winsorlabs −204), and the
  statuses are unchanged.
- The bundles still carry each record under *Links archived*.

**Mutations, all caught:**
- Backend, against 144 passing tests in the slice's six files:
  - the detach deletes instead of archiving: 7 failed;
  - no audit row: 5;
  - reactivation restores a person's detach: 1 (the negative test);
  - *Replaced by* inferred from what still contributes: 1;
  - the bundle silent on a product left unreplaced: 1;
  - the blocked-objectives widget counting archived links: 1;
  - the objective's list showing archived links as live: 4;
  - a list's unlink deleting its link again: 1;
  - a detach re-stamping a deactivation's archive date: 1.
- Frontend, each failing one test against an unmutated baseline of
  30/30:
  - an archived item drawn as a live row;
  - the alarm ignoring the server's live count;
  - *Not attached* drawn by each source;
  - the template sentence repeated per row;
  - *Replaced by* inferred on the screen.

**Not done, recorded:**
- **Bundle: a person-detached list or document version is not in the
  bundle's *Links archived*.** That group excludes list and document
  evidence by design: a list version is evidence only through a link,
  and the approved document is in `06_documents`. The artifact itself
  does not vanish.
- **Bundle: a detach from one objective, of evidence still live on
  another, is not shown.** The artifact appears under the other
  objective; the detach from this one does not appear. A per-objective
  "no longer attached" line in the manifest would close both.
- **No UI re-attaches a detached file or location.** Re-attaching a list
  or document is the ordinary Attach, and restores the same row.

**Live walk (Jarrod, 2026-10-20 UTC, `09aee13`, Acme MSP, Datto RMM on
MA.L2-3.7.1[a]): done, and it behaved. Read back from the database, the
audit log and storage.**
- **A first pass showed nothing archived, and that was correct.** Two
  deactivations (16:11 naming RocketCyber Managed SIEM + SOC, 16:14
  naming none) archived zero links. No evidence was attached to the
  tool's objectives, so there was nothing to archive. "Replaced by" is
  drawn only on archived evidence, so the screen had nowhere to say it.
  Naming a replacement never moves a control, by design. This led to
  the first gap below.
- **Detach (16:18:44).** `blockedKey.png`'s link reads `detached`,
  `detached_by` Jarrod, no product. One `evidence.detach` audit row. The
  bytes are still in storage (85,541, a PNG header).
- **Deactivation naming a replacement (16:19:30).** The second file's
  link was archived with cause `deactivation` and Datto RMM's id. The
  objective went `pending_evidence` → `needs_review`. The audit row
  records RocketCyber as the replacement. The screen read "Replaced by
  RocketCyber Managed SIEM + SOC".
- **Reactivation (16:20:59) restored only what the deactivation
  archived.** The second link is live again, with an
  `evidence_state_link.restore` row. The person-detached link stayed
  detached. That is the rule this slice exists for, shown on live.
- **Still unverified on live: a deactivation naming no replacement, with
  evidence attached.** The bench covered it. On live, the one
  no-replacement deactivation (16:14) had nothing attached, so it
  archived nothing and the screen had nothing to show. The case to walk:
  attach a file, deactivate naming nothing, and expect the archived row
  to say the tool was deactivated, with no "Replaced by".

**Found on the walk, recorded under Planned:**
- **The walk found two missing audit rows (upload, activation). The
  sweep they prompted found the worse one: marking an objective *met*
  is recorded nowhere.** Every unaudited route is in the assessment
  core, the code written before the audit convention (the Planned entry
  "Marking an objective met is recorded nowhere").
- **A replacement named for a tool shows only on archived evidence.** It
  is a fact about the tool, and the tool's own record does not show it.

### Review cycles attach only what attests ✅ DONE (2026-10-19, migration 0070; deployed and detached on live, read back)

**Changed before merge (Jarrod, 2026-10-19): the detach archives; it
does not delete.** The first build deleted the link rows, so the bundle
would still list the record. That left the audit row as the only trace
the links had existed. The invariant is that today's state never
rewrites yesterday's record, and a deleted link row is a rewritten
record. So the detach archives, and the bundle's gap became this slice's
work: see the Done entry "The bundle left out evidence whose links were
all archived", recorded on its own because it was never about
attestations.

**The rule: a completed cycle attaches; an unattested or undeliverable
close records and attaches nothing.** `close_cycle` writes, stores and
keeps its record for every status, as before. It is the record of who
was asked and who did not answer, which D.3 called the most valuable part
of the feature. Only when `status == "completed"` does it link the
record to AC.L2-3.1.1[a]/[c] on every in_progress assessment. The
reviewer statuses that are the record's content (`no_response` against
`not_notified`, and the 2026-09-13 fix behind them) are untouched.

**The docstring said the old behaviour, so it was rewritten in the same
commit.** It used to read "linked to AC.L2-3.1.1[a]/[c]'s control_state
rows on every currently in_progress assessment", for every status. That
is item X's lesson again: a docstring that states the old behaviour is
how the next reader inherits the mistake. It now says the rule, why
(attaching would file "nobody reviewed this" as evidence that someone
did), and cites CLAUDE.md's product invariant that a screen must not
state a result nothing computed. `_mapped_control_states`' docstring
(which said the record waits "until a human links it") and item X's
`document_evidence.py` note and test docstring were corrected with it.

**Where an unattached record surfaces: checked, not assumed.**
- **The bundle: already carried.** Evidence never linked into an
  assessment is in `evidence/manifest.html`'s *Unlinked Evidence*, with
  its file under `evidence/unlinked/`. Pinned by a test through
  `snapshot_bundle`. That section used to leave out evidence whose links
  were all *archived* (ADR 0007, on purpose), which would have hidden a
  detached record. It now lists such evidence as archived, with where
  from, when and why (its own Done entry, above).
- **The cycle's page: did not show it, and could not.** Evidence is
  listed per objective, and nothing on a cycle pointed at its record. The
  only route to it was the objective it was attached to, which this
  slice removes. This was the slice's real work:
  - **`evidence.source_review_cycle_id`** (migration 0070), nullable,
    RESTRICT, indexed, the pattern of `source_document_version_id` and
    `source_list_version_id`. `close_cycle` sets it.
  - **Backfilled from what `close_cycle` wrote.** It stamps the cycle's
    `closed_at` and the record's `collected_at` with one value, so a
    record is the attestation in the cycle's org collected at the
    moment the cycle closed. A record two cycles would claim refuses the
    migration rather than guess. On a copy of live (restored on the
    bench, then dropped) it named all 3 records' cycles, 3 of 3, and
    left their links as they were (2, 0, 2).
  - **The cycle's page has a *Record* card**: the record's title,
    linked to its download, then where it is attached, read from the
    live links and never from the status. A completed cycle's says
    "Attached as evidence to AC.L2-3.1.1[a] in …; AC.L2-3.1.1[c] in …".
    Otherwise it says "Not attached as evidence. No reviewer attested,
    so this record does not show that anyone reviewed the users and
    devices. It is kept as the record of who was asked and who did not
    respond." It has no button. `GET …/review-cycles/{id}` carries it as
    `record`.

**Offering it as a candidate: considered and rejected.** Offering would
invite someone to attach "nobody reviewed this" as evidence for the
thing nobody reviewed, which is the same harm with an extra click in
front of it. The page says it is not attached and gives no way to attach
it. Do not reopen this as a convenience.

**The evidence ✕ no longer deletes a cycle's record.** The generic
unlink deletes the Evidence row and its stored file when the last link
goes; it exempted only a document version's row, and now a cycle's too.
**This is the narrow fix only.** The general defect (the ✕ deletes the
artifact and its bytes, writes no audit row, and is presented as
*Remove*) is its own entry under Planned, "The evidence ✕ deletes the
artifact", and is **not** fixed here.

**The detach** (`wingrc review-cycles-detach-unattested --org-id …`).
Dry-run by default; `--org-id` required, and the org's name printed
before anything. It finds whatever links from records of unattested or
undeliverable closes exist when it runs, so a cycle the scheduler closes
before the deploy is included, and a second run reports nothing to do.
Each link is **archived** (`is_archived`, `archived_at`; the row stays)
with one `evidence.attestation_detach` audit row on the Evidence, naming
the objective, the assessment, the cycle and the reason. A link a
product's deactivation had already archived is taken too: its
`archived_by_product` is cleared, so that product's reactivation cannot
restore it, and its archive date is kept. A second run finds nothing and
re-archives nothing. The record (the Evidence row, its stored HTML, the
cycle and its reviewers) is not touched. It does not go through the ✕.
- **Why detach, against item X's precedent.** See the decision in the
  Planned entry: X's links were true evidence chosen by nobody; these
  say something false where they are attached.
- **Rehearsed on a fresh copy of live, with the archiving code**
  (bench, 2026-10-19, then dropped; the first rehearsal ran the deleting
  code and no longer counts). 0070 named 3 of 3 records. For Acme MSP,
  then Winsorlabs: the dry run listed 2 links ([a], [c]), the run
  archived exactly those 2 with 2 audit rows (actor
  `cli:review-cycles-detach-unattested`), and a third run said "Nothing
  to do". Afterwards all 4 link rows exist with `is_archived` true,
  `archived_at` set and no product. The statuses are where they were
  (Acme met/met, Winsorlabs not_met/not_met). Each assessment's bundle
  snapshot lists its record under *Links archived*, from
  AC.L2-3.1.1[a] and [c], with the detach reason. None was by a
  product's deactivation.

**Out of scope, and kept apart:**
- **Acme's [a] and [c] read `met` with no evidence after the detach.**
  They were marked met by hand on 2026-09-13, three weeks before the link
  existed (2026-10-05). The detach neither caused that nor fixes it; it
  is its own question.
- The general ✕ defect (above).
- The blocked-objectives widget's "any link means covered" test (with
  item Z).
- The evidence section cosmetics.

**Tests.** New: `test_review_cycle_record.py`, 7 tests through the
database and HTTP:
- completed attaches, and the page names where (an archived link is not
  attached);
- unattested and undeliverable, parametrized: the record is written and
  stored and says so, has no link, keeps its reviewer status, is on the
  page and in the bundle's unlinked evidence; no status or SPRS change;
- another org gets 404 on its own path and 403 on this org's, for the
  cycle and the download;
- the ✕ on a completed record's links keeps the record and its bytes;
- the detach: the dry run is what the run does; the link rows stay,
  archived, with no product, including one a deactivation had archived
  (whose date is kept); a completed cycle's links stay live; the record
  and its bytes are read back unchanged; one audit row per link with the
  reason; a second run is empty and re-archives nothing; the bundle then
  carries the record as archived, from [a] and [c]; no status or SPRS
  change;
- 0070's backfill run against records written before the column.

`ReviewCyclesPanel.test.tsx`: 3 (unattested: not attached, linked, no
button; completed: names where; open: no card). One existing assertion
changed, deliberately: `test_bundle.py`'s ADR 0007 exclusion test (see
the finding's entry). The scheduler's close tests seed no assessment, so they never
asserted a link; `test_full_attestation_closes_cycle_and_creates_linked_
evidence` still passes unchanged. The determinism guard still scans
`close_cycle`; no query was added to it.

**Verified on the bench** (wl-util-1, isolated `wingrc_verify_att1`,
then `wingrc_verify_att2` for the archive change):
- **Backend:** 1827 passed (1820 on main plus 7; the archive change
  replaced a test rather than adding one), none failed or skipped, with
  `WINGRC_MIN_TESTS_RUN=500`. Re-run in full after the change.
- **Lint and types:** `ruff` clean; `tsc -b` clean.
- **Frontend:** 446 vitest passed (443 plus 3), and the build
  succeeded. The archive change touched no frontend file.

Mutation runs, each restored afterwards:
- Attaching for every status fails 4.
- (First build) Archiving the link instead of deleting it failed the
  detach test. After the change, each of these fails it: deleting the
  link instead of archiving it; leaving `archived_by_product` set;
  letting the query take links it already archived; re-stamping
  `archived_at` on one already archived. Restoring ADR 0007's "any link
  excludes" fails both bundle tests (the deactivation one and the
  attestation one). Dropping the tool's name from the manifest's reason
  fails the deactivation one.
- Writing the detach's audit row under another action fails it.
- Dropping the ✕ exemption fails the ✕ test.
- Letting the page count archived links as attached passed at first.
  The completed-cycle test then gained the archived-link assertion, and
  it fails.

**Live (2026-10-19).** Deployed `8340edf` (PR #47). `/health` reported
that build, migration `0070_evidence_review_cycle` and database ok.
0070 named all 3 records' cycles. No cycle had closed since the count,
and the links were as counted.

The detach ran in the order set. Each step was confirmed by Jarrod
before it wrote:
1. Acme MSP's dry run listed 2 links ([a], [c] in "CMMC L2 Assessment
   2026-09-09"), the same as the rehearsal. Neither had been archived by
   a deactivation.
2. Acme's run archived those 2. A dry run straight after said "Nothing
   to do".
3. Winsorlabs' dry run listed 2 links ([a], [c] in "CMMC L2 Assessment
   2026-09-16").
4. Winsorlabs' run archived those 2. A dry run straight after said
   "Nothing to do".

**Read back afterwards, from the database and from storage, not from
the command's output:**
- **Both Evidence rows are present.** Each is `file`/`attestation` and
  names its cycle (both cycles `closed_unattested`):
  - Acme's `95193c1a…` names cycle `b327e64c…`.
  - Winsorlabs' `c83ff359…` names cycle `bbd7e124…`.
- **Both stored files were read out of storage.**
  - Their byte counts equal `file_size_bytes` (1686 and 1262).
  - Winsorlabs' SHA-256 equals its stored `sha256_hash`. Acme's row has
    no stored hash to compare: the hash is filled on a bundle export,
    and none has run for that assessment since the record was written.
  - Both still read "Cycle closed at its due date without full
    attestation".
- **All four link rows exist.** Each has `is_archived` true,
  `archived_at` set (Acme 14:05:12 UTC, Winsorlabs 14:32:37 UTC) and no
  product. `linked_at` is unchanged (2026-10-05 and 2026-10-07).
- **Four audit rows**, action `evidence.attestation_detach`, actor
  `cli:review-cycles-detach-unattested` (`system`), one per link:

  | Audit row | Evidence | Objective | Was archived |
  | --- | --- | --- | --- |
  | `413407e0…` | `95193c1a…` | [a] | false |
  | `563cd4bb…` | `95193c1a…` | [c] | false |
  | `bef9ecb5…` | `c83ff359…` | [a] | false |
  | `9e034e1d…` | `c83ff359…` | [c] | false |

  Each row's `after_value` is `{"is_archived": true,
  "archived_by_product": null}`. Each carries the cycle id, its status
  and the reason.
- **Statuses unchanged.**
  - Acme [a]/[c] are `met`/`met`, SPRS −199.
  - Winsorlabs [a]/[c] are `not_met`/`not_met`, SPRS −204.
  - Each score equals its assessment's last `sprs_snapshot` (2026-09-29
    and 2026-10-14).
  - No `control_state_history` row was written on any of the four since
    the deploy.
- **What else is live on those objectives.** Winsorlabs [a] keeps
  AC-POL-01 v4's live link (v3's archived, from the X walk). Acme's [a]
  and [c] now have no live evidence while `met`: the separate question
  recorded above.

---

### The bundle left out evidence whose links were all archived -- ADR 0007's third decision, reversed ✅ DONE (2026-10-19, with "Review cycles attach only what attests")

**Found while making the detach archive rather than delete (Jarrod,
2026-10-19).** `snapshot_bundle`'s *Unlinked Evidence* query excluded an
Evidence row if *any* link into the assessment existed, archived or not.
So evidence whose every link a product's deactivation archived was in
storage, was in the database, and appeared nowhere in the bundle an
assessor receives. It had nothing to do with attestations.

**It was not an accident.** ADR 0007 (2026-08-06) decided it, as its
third decision: listing retired coverage in a document built for a C3PAO
would "misrepresent current scope", and silence was "at least neutral".
The manifest said so in a line of grey text. Reversed, because the
silence is the harm: a tool removed with nothing in its place is
invisible to the one reader whose job is to notice it, and the same
query hid the record of an unattested review cycle once it was detached.
ADR 0007's real concern, that retired evidence must not read as current,
is kept by how it is listed, not by leaving it out.

**Now:** the section takes evidence with no **live** link into the
assessment, in two groups:
- *Never linked*, as before.
- *Links archived*: each item says the objectives it was on, when the
  last link was archived, and why, as the rows say it (*"Heimdal was
  deactivated"*, or *"detached: the record of a review cycle that closed
  without attestation"*). The section's header says none of it is
  current evidence for any objective.

It is never filed under the objective it left. List and document
versions stay out of the section, as before: theirs is their own path
(`lists/`, `ssp/06_documents.html`).

**On live (read-only, on the copy, 2026-10-19):** no evidence was hidden
this way. The only evidence with every link archived was AC-POL-01 v3,
a document version, which the section never took. So the hole was
latent, not live. The detach would have been its first live case.

**Tests:** `test_bundle.py`'s ADR 0007 exclusion test asserted the
opposite. Its scenario is kept and its assertion inverted, and it now
archives through `engine.deactivate_org_product` instead of a hand-set
flag: the file is embedded under `evidence/unlinked/`, listed under
*Links archived* with the objective, date and tool, and not in the
objective's folder. The attestation case is in
`test_review_cycle_record.py`'s detach test. ADR 0007 has an amendment
section.

---

### Item X.1: documents get a tag editor, and a tag a link rests on cannot be removed ✅ DONE (2026-10-18, verified on live with X's walk)

**Documents can be tagged from a screen for the first time.** Before
this, no component called the document tag routes. Live had 0
`document_objective_tag` rows, so item X was dormant: no document was
offered anywhere. The document's Edit/Read tab now has *Tagged
objectives* (`DocumentTags.tsx`):
- each tag as `AC.L2-3.1.1[a]` with its objective text;
- a remove control on each;
- a typed-key input, "AC.L2-3.1.1[a], or 3.1.1 for all its objectives".

`DocumentDetailOut.tagged_objectives` carries the keys and text. It sits
beside `tagged_objective_ids`, which named nothing a screen could show.

**The shared refusal was fixed first.** This closes L.10's folded-forward
items 1 and 2. The decision is the one L.10 recorded: the server answers
in fields and the client writes the sentence.
- **One link shape.** `list_library.RestingLink` (version number, full
  objective key, assessment name) is what both `tag_removal_links`
  return, the list one and `document_evidence`'s. Its `str()` is the old
  log sentence, so imports, adoption, the CLI and each 409's `message`
  are unchanged.
- **The checks answer in fields.** Both removal checks return
  `{removable, reason: last_tag | evidence | null, links}`, where they
  used to return `refused`/`blocked_by` sentences. Each DELETE's 409
  carries the same `reason` and `links` beside its `message`. `api.ts`
  raises that as `TagRemovalRefusedError`, so a refusal on the race
  between check and click reads exactly like a refused check.
- **One dialog.** Both screens use `TagRemovalDialog.tsx`. It says the
  fact once, using the title and never the key: "{tag} can't be removed
  from {title} while evidence rests on it:", then one bullet per link,
  "version 2, evidence for PE.L2-3.10.6[b] in *{assessment}*", then
  "Detach it first."
- **L.10's item 3 is not touched.** That is the possible overlap of "Not
  attached" with the evidence buttons. It is still unconfirmed and still
  needs a full-window screenshot before anything changes. (Cleared on the
  live walk: no overlap.)

**Granularity: documents tag objectives and lists tag practices.** That
stays, and a practice key now expands to its objectives. A policy
paragraph can satisfy one objective and not its siblings, so the stored
truth stays per objective. Typing once is the convenience; without it, a
policy covering a whole practice would be L.4's
six-clicks-per-practice friction in a second place.

`DocumentObjectiveTag` made this clean. It is one row per (document,
objective) with a unique constraint and nothing else: no `added_via`, no
event table. So expanding is a loop that inserts the missing rows, and
the model does not change. Each expanded objective:
- is its own row;
- is its own audit row (`document.objective_tag.add`, which now carries
  `objective_key`, so History says which);
- is its own chip, removed alone.

How a typed key is read:
- **Both spellings.** `AC.L2-3.1.1` and `3.1.1` are accepted, through
  `practice_key`, the list input's own parser, with an optional `[x]`
  suffix.
- **Unknown keys are named.** A key is refused (422), naming what was
  typed, when it is not a practice, names an objective the practice
  lacks, or matches more than one framework.
- **Which frameworks.** A key resolves in the frameworks the org has
  assessments in, since a deployment can carry more than one. Live has
  one.
- The `objective_id` body is still accepted.

**The refusal is L.4's rule for documents.**
`document_evidence.tag_removal_links` returns the live links from any
version of the document onto that objective, in any assessment. A
superseded version's link blocks, because a republish leaves it where it
is (item X). Archived links do not block: only moving forward archives
one, and it leaves a live link in its place.
- **The DELETE refuses on its own,** for a link made between the check
  and the click.
- **It is the only path that removes one tag.** Deleting a document
  cascades its tags, but a document with an Evidence row cannot be
  deleted, and every link needs one.
- **`NOT_TAGGED` stays.** It is now reachable only for a tag removed
  before X.1, and live had none.

**Who tags is who defines a list.** That is `DEFINE_ROLES`: `msp_admin`,
`consultant_admin` and `msp_engineer`. The brief named the first two;
L.2.2 settled lists on all three, and documents match that.
- `customer_poc` is refused (403), as for list tags. It writes a
  document's text and does not decide what the document is offered for.
  This narrows the document tag routes, which before allowed any write
  role. Live had 0 tags, so no one lost a tag they had made.
- `c3pao_assessor` sees the tags and no controls, gets 403 on both
  writes, and can read the removal check (a GET, like the list's).

**Tests changed, each saying why:**
- `test_list_builder.py`: its stand-in moves to `tag_removal_links`.
- `test_list_evidence.py`: three tests assert fields.
- `test_document_evidence.py`'s
  `test_only_an_approved_version_on_a_tagged_objective_is_offered`: the
  untag-after-attach is now 409. The row is deleted directly to stand in
  for a tag removed before X.1, so `NOT_TAGGED` is still covered.
- `ListLibraryPanel.test.tsx`: fields. The race test now asserts the
  check-shaped refusal and no log sentence.

**The row-shape rule.** No component this slice touches renders an
attached item beside a not-attached one: the tag list and the dialog
show tags, not evidence. `ObjectiveLists` and `ObjectiveDocuments` are
unchanged, and their shape tests stand.

**Verified on the bench** (wl-util-1, isolated `wingrc_verify_x1a`):
- **Backend:** 1820 passed, none failed or skipped (1813 on main, plus the 7 here).
- **Lint and types:** `ruff` clean; `tsc -b` clean.
- **Frontend:** 443 vitest passed (432 on main), and the build
  succeeded.
- **New tests:**
  - `test_document_tags.py`: 6 through the database and HTTP, plus 1
    route-shape test;
  - `DocumentTags.test.tsx`: 9;
  - `ListLibraryPanel.test.tsx`: 2 more (the fact said once in the
    title, and the only-practice reason).

Mutation runs, each restored afterwards:
- Putting the client prefix back, so the fact is said twice, fails both
  "said once" tests.
- Using the list's key as its name fails the key test and the
  nothing-rests test.
- Making a failed check say "No evidence rests" fails both "could not
  check" tests.
- Offering "Yes" whatever the check said fails 5.
- Dropping the DELETE's own refusal fails 2.
- Expanding a practice key to its first objective only fails the
  expansion test.

**Live walk (Jarrod, 2026-10-19 UTC, `93ab780`): done, all six steps
behaved. It covered X and X.1 together, through the UI.** At X's deploy
(`d0d745c`) live had 0 document tags and 0 document links. Verified by DB
and audit log, read-only:
- **Tagging.** `3.1.1` on Winsorlabs' AC-POL-01 *Access Control Policy*
  added six tags, AC.L2-3.1.1[a] through [f]: one
  `document.objective_tag.add` row each, at 00:17:23, each naming its
  key. The practice has six objectives. The expected text I gave said
  [a] and [b]; that was my count, not a defect.
- **Publishing attached nothing.** Versions 3 and 4 were published and
  approved, each recording 6 published tags. The only link-making rows
  are a person's: the attach (`evidence.document_link`, 00:19:19, version
  3) and a move forward, below.
- **Republishing left the link.** Version 4's publish (00:19:41)
  archived nothing; version 3's link stayed live and read as superseded.
- **A person moved it forward.** `evidence.document_move_forward` at
  00:22:19, in the same session, archived version 3's link and wrote one
  on version 4. The walk's steps did not ask for it, and it is correct: a
  move is a person's act, with its own row. So step 6's refusal named
  version 4.
- **The removal was refused.** No `document.objective_tag.remove` row;
  all six tags are present.
- **L.10's item 3 is cleared.** The full-window screenshot shows no
  overlap: the *Not attached* block sits above the attach buttons.
- **New finding:** a doubled *Not attached* heading when a list and a
  document are both unattached. See "Evidence section cosmetics" under
  Planned.

State after: 6 tags; one live document link (version 4,
AC.L2-3.1.1[a], *CMMC L2 Assessment 2026-09-16*) and one archived
(version 3).

---

### Lesson: a record that rested on a side effect (item X, migration 0069)

A document version's "what was this approved for" was never stored. It was
read back from the EvidenceStateLinks that publish made as a side effect
of approving (`_published_objective_ids`, archived links included). X
removed the side effect, which was the defect, and that removed the record
with it. Nothing failed: the query still ran and returned an empty set, so
every newly published version would have said it covered nothing. The
version-detail screen would then state a result nobody computed. It
surfaced only because reading `VersionDetailOut`'s docstring for X showed
where the "published" basis came from.

**The shape will recur.** When an action's side effect is removed or
changed, ask what else reads that side effect as a record. Look for:
- a query over rows the action produced, standing in for a fact nobody
  wrote down;
- a count or a set "recovered" from links, events or rows made for
  another purpose.

The fix is to write the fact where it is decided, at the moment it is
decided (0069 stores the tags at publish). Earlier rows are backfilled
from the old proxy while it is still the true record, and the proxy is
then read for nothing.

---

### Item X: a published document is offered, and a person attaches it ✅ DONE (2026-10-18, verified on live with X.1's walk)

**What changed.** `publish_document` writes the new version's Evidence row
and links it to nothing. A document with an approved version is offered
on each objective it is tagged with (`document_evidence.py`,
`routers/document_evidence.py`), and a person attaches it on the
objective's evidence section (`ObjectiveDocuments.tsx`, beside
`ObjectiveLists`). A link names a document version and stays there. A
republish leaves it, and the link then says its version is superseded.
Moving forward is per objective: a new link, with the old one archived.
Each attach, move and removal is an audit row
(`evidence.document_link` / `document_move_forward` / `document_unlink`),
logged against the document version, so the document's History shows it.
control_state and SPRS are untouched, as before, and the tests assert it
again.

**The docstring cited the rule in the act of breaking it.** Step 4 of
`publish_document`'s docstring said *"control_state.status is NEVER
touched here -- 'candidates, never auto-met': evidence is attached"*. Its
writer read "candidates" as candidates for being marked *met*, saw that
status was untouched, and attached evidence to every tagged objective in
the same sentence. They believed they were following the rule they were
breaking, and that is why it went unnoticed for weeks. CLAUDE.md's
product invariants now say the wider rule out loud: *"Candidates are
offered and a person attaches them; nothing is auto-attached."* The
docstring now says what the rule means for this path, records the
misreading, and points to the invariant, so the next reader does not
inherit it.

**The republish block archives nothing now.** It used to archive every
live link on every superseded version's Evidence. Before this slice that
was bad but symmetrical: it archived links nobody chose. After it, the
same code would let a person approving version 3 silently undo another
person's deliberate link to version 2, perhaps for reasons unrelated to
that objective. That breaks *today's state never rewrites yesterday's
record*, and it would be worse than the original defect, because the
record erased was chosen. I found no case worth keeping:
- A superseded version's links are the historical record.
- The superseded flag is what tells a reader their status.
- Moving a link is a person's act, per objective.

`test_a_persons_link_survives_another_persons_republish` pins it: Avery
attaches v1, Blake publishes v2, Avery's link is still live on v1, and it
reads as superseded. Restoring the archive block fails four tests.

**Generalise or twin: twin, by count.** `list_evidence.py` has about 350
lines of code under its docstring.
- **Shared verbatim with documents: 27 lines.** These are
  `control_key_for` (8), `live_link_row` (16) and the refusal class (3).
  The first two are now public and imported by `document_evidence.py`.
- **About 75 lines share their shape and differ in every call they
  make:**
  - `link`, `move_forward` and `unlink`. Documents' `unlink` also differs
    in behaviour: it must not delete the Evidence row, which is the
    publish record that `delete_document`'s refusal rests on.
  - `_audit` and `_record`.
- **The rest is list-specific, with no document counterpart:**
  - `is_populated`'s offer rule;
  - practice-level tags (documents are tagged per objective);
  - lazily created Evidence (documents' rows exist from publish);
  - `LinkView`/`links` for the list page and bundle picker;
  - `list_link_event`.

Abstracting a 75-line skeleton over two modules that disagree in every
step would make both harder to follow. On the frontend,
`ObjectiveDocuments.tsx` copies `ObjectiveLists.tsx`'s structure, but
every field, string and API call differs. Lists were out of scope, so
L.10's component was left alone. Both components carry the L.10 rule, and
each has its own rendered-output test for it. Restoring a shared row shape
fails that test.

**Superseded is not drift, and the two sentences say so.**
- A list: *"List version 3 is available. This evidence stays on version 2
  until someone moves it forward."*
- A document: *"Version 2, attached here, is superseded: version 3 is the
  approved one, and version 2 may no longer be in force. This evidence
  stays on version 2 until someone moves it forward."*

A list's older version was true when linked and is still a true record. A
superseded policy may no longer be in force, which is a different fact
for an assessor. The two sentences are pinned side by side
(`test_superseded_is_said_as_superseded_not_as_a_newer_version`). In the
bundle, the manifest and the implementation page print *"version 2
**superseded**: version 3 is the approved one, and version 2 may no
longer be in force"* beside the evidence. The UI shows a *superseded*
badge on the attached row.

**The general rule for any path that links evidence.** Ask two questions:
1. Is the link created by a person's own act?
2. Does it move an existing link?

A link is right only for "yes, no". How each path answers:
- **Attaching here:** passes.
- **Moving forward:** a person's act, per objective, whose whole purpose
  is the move.
- **Publish:** failed both questions.
- **`review_cycles.close_cycle`:** unchanged by X, and it passes only on
  the path a person closes.
  - When the last reviewer attests, `attest` closes the cycle with
    `completed`. That attestation is the person's act, for exactly
    AC.L2-3.1.1[a]/[c], and it moves nothing:
    `test_closing_a_review_cycle_is_a_persons_act_and_moves_nothing` checks
    that a person's link on the same objective is untouched.
  - **The deadline close is a different decision.** I first recorded it as
    passing if the act counted is the opening of the cycle. Jarrod's better
    question was what the attestation records: on a deadline or
    undeliverable close, it records that nobody attested. Decided: attach
    only what attests. See "Review cycles attach only what attests" under
    Planned (not built in X).

**Links already on live: none.** I took the count on live (b70958d,
2026-10-18) before deploying:

| What | Count |
| --- | --- |
| Live document links | 0 |
| Archived document links | 0 |
| Evidence rows naming a document version | 2 (one org) |
| Approved versions | 2 |
| Draft versions | 2 |

The two approved versions were published when nothing would link them,
so there is nothing to leave in place or re-offer.

**Actor:** no history entry claims an attachment nobody made. There is no
document twin of `list_link_event`. A document's History is versions plus
audit rows (N.2's decision: no parallel history table), and every role
that can read the document can read it. L.10's reason for a table, that
the audit log was not readable where the list was, does not hold here.
A link made by publish before X has no attach event. Its record is the
`document.publish` audit row, made by whoever published.

**Decided in the slice:**
- **The version records what it was approved for.** Migration 0069 adds
  `document_version.published_objective_ids`, the document's tags at
  publish.
  - The version detail's "published" objective set used to be read back
    from publish's links. With no links, every new version would have
    claimed to cover nothing: a screen stating a result it never
    computed.
  - Earlier versions are backfilled from the links publish made then,
    which were their record. A version published with no in_progress
    assessment gets `[]`, which is what its record says.
  - No link is read as coverage any more.
  - The test now untags after publishing, and v1 still answers with its
    own set.
- **The bundle's documents page** now reads *"Attached as evidence for"*
  (it said *Objectives*). It lists this assessment's attachments as
  `AC.L2-3.1.1[a]`, where it used to show a bare `a` from any assessment.
  With none, it says *"Not attached as evidence in this assessment"*
  rather than a dash.
- **A published document's Evidence row is not stray evidence.** It is
  excluded from the bundle's Unlinked Evidence section, as list versions
  already were.
  - It is also excluded from the general evidence list on an objective,
    since `ObjectiveDocuments` shows it.
  - The general remove route no longer deletes such a row when its last
    link goes. Before X that route could quietly make a published
    document deletable.
- **A product's deactivation leaves an attached document alone**, as L.4
  did for lists.
- **Copy that described the old behaviour is rewritten:**
  - the publish hint ("attaches it as evidence to every objective…");
  - the delete refusal ("has evidence attached");
  - two docstrings, in reaffirm and the N.3 test.

**Found after the build, before merge (2026-10-18):**
- **The bundle did not carry the text of a superseded version a link
  names. Fixed in this PR, before merge.** L.3/L.4 established that a
  bundle renders the exact version a link names: `lists/` embeds every
  version a live link points at. A document body rendered in one place,
  `ssp/06_documents.html` and the PDF built from the same function, and
  only for `approved` versions. So for a link on a superseded v2:
  - the manifest named v2;
  - the documents page carried v3's text under its own label;
  - v2's text was nowhere.

  Before X this state could not exist, because a republish archived every
  link on the superseded version. Shipping it would have meant the first
  bundle anyone generated named evidence it did not carry. The fix:
  - `06_documents.html` (and so the PDF) has a second section, *Superseded
    Versions Attached as Evidence*. It carries every superseded version
    that a live link **in this assessment** names, with its own text,
    under `doc-<slug>-v<n>`, marked superseded with the version approved
    now.
  - The approved section and its `doc-<slug>` ids are unchanged.
  - Each document evidence line in the manifest and on the implementation
    page now links its version number to that section.
  - A superseded version nothing in this assessment names, including one
    attached only in another assessment, is not carried.

  Pinned by `test_a_superseded_document_reads_as_superseded_in_the_bundle`
  (v1's own text under v1's id, v2's under v2's, two exports identical)
  and `test_a_superseded_version_nothing_names_is_not_carried`.
- **A document tag that a link rests on can be removed.** Closed by
  X.1 (Done entry "Item X.1: documents get a tag editor…").
- **No screen tags a document with an objective.** Closed by X.1. `api.ts` has
  `addDocumentObjectiveTag`/`removeDocumentObjectiveTag`, and no component
  calls them. Live has 0 `document_objective_tag` rows. So on live,
  through the UI, nothing is ever offered, and the live walk below needs
  its tag made through the API. Before X the same fact meant publish never
  linked anything on live, which is why the count was 0.

**Not done; noted for later:**
- A document's page does not list where it is attached. The list page has
  that and the document's History has the events.
- The three L.10 tag-refusal cosmetics stay folded forward. They are in
  the list library's frontend and would widen this diff. Items 1 and 2
  were closed by X.1; item 3 was cleared by the live walk (no overlap).

**Verified on the bench** (wl-util-1, isolated `wingrc_verify_x1303`):
- 1812 backend tests passed, none skipped;
- `ruff` clean;
- `tsc -b` clean, 432 vitest passed, build succeeded.

Mutation runs, each restored afterwards:
- Putting back the republish archive fails 4 tests.
- Putting back publish's auto-link fails 13.
- Dropping the bundle's superseded wording fails 1.
- Rendering an offered document inside the attached row's shape fails the
  shape test.

**Live walk: done with X.1's (2026-10-19 UTC), all steps behaved; the
results are in X.1's Done entry.** It was done with X.1's walk, through
the UI, rather than with the tag made through the API (Jarrod,
2026-10-18). The original steps:
1. Publish a tagged document, and confirm it is offered, not attached, on
   the objective.
2. Attach it.
3. Edit and republish.
4. Confirm the link stayed on the version he chose and reads as
   superseded.
5. Then I verify the result by DB and audit log.

---

### List library L.10: the screens say what is true ✅ DONE (2026-10-18, verified on live)

**Found by Jarrod on live, within ten minutes of L.4's first use.** He
adopted *Physical Access Device Register* into Acme MSP (v1, the
template's rows), tagged it PE.L2-3.10.6, saw it on that objective, removed
the tag, and the list was gone from the objective. It read as a tag
removal that skipped the evidence guard. **It was not:** the database, audit
log and request log showed no attach request, no list evidence row
anywhere, and no link on either PE.L2-3.10.6 objective. v1's rows were all
`[PLACEHOLDER]`, so `is_populated` was false and the list was never offered.
The guard ran (`remove_tag` calls `tag_removal_blockers`) and found nothing,
correctly. Three screens misled the person who designed the rule:

1. **The objective drew an unfilled list as evidence.** Attached, offered
   and not-offered lists shared one row shape (the List chip, the title,
   "version 1"), and only a muted footnote said "Not offered as evidence:
   still showing template examples (2 of 2 rows)." Muting it and adding a
   footnote is exactly what fooled him. Now `ObjectiveLists` has two shapes
   that never look alike. "Lists attached as evidence" are evidence items.
   Every other list sits under "Not attached" as a sentence with no chip
   and no item box: "… can serve this practice and is not attached
   [Attach version N]", or "… is tagged with this practice but is not
   attached, and cannot be: still showing template examples (2 of 2
   rows)."
2. **The tag dialog stated the rule as if it were a result.** "It is refused
   while evidence rests on this list for PE.L2-3.10.6" was rendered
   unconditionally beside an enabled "Yes, remove". It now asks the server
   first (`GET …/tags/{key}/removal`), which runs the DELETE's own checks
   (the last-tag rule, then `tag_removal_blockers`) and returns the
   DELETE's own sentence. The dialog shows "Checking what rests on {key}…"
   with no button. If links rest on the tag, it names them and offers no
   removal. If none do, it says "No evidence rests on this list for {key}."
   and asks. If the check fails, it says it could not check, not that
   nothing rests there. The DELETE still refuses on its own, for a link
   made between the check and the click (tested).
3. **The list's History could not say whether it was ever attached.** It had
   versions and tags and no evidence, and answering took the request log,
   which no operator can read. L.2.2's reason for putting tags in History
   applies directly: two histories in two places is how the question goes
   unanswered. Migration 0068 adds `list_link_event`. Every link, move
   forward and unlink writes one beside its audit row, naming the version,
   objective, assessment (with the name kept as it was) and the person.
   It is append-only by trigger and RLS-gated. History is now "versions,
   practices and evidence": "Version 1 attached as evidence for
   PE.L2-3.10.6[a] in assessment 'FY26'. By …", "moved forward from version
   1 to version 2", "removed from the evidence". A table, not a read of the
   audit log, because `consultant_admin` cannot read the audit log
   (tested: it reads these). An event is a record, not a link: the
   blockers still read live EvidenceStateLinks only, and an unlinked
   list's history blocks no tag (tested; `test_list_builder.py`'s guard
   fired on the new table as designed and is settled with that reason). No
   backfill: no list had been linked on live.

**The invariant, generalised rather than added.** CLAUDE.md's fourth product
invariant was "a check must not match its own invocation". It is now "a
check must actually check: it must not match its own invocation, and a
screen must not state a result it never computed." The process grep and
the dialog are the same failure.

**Why L.4's bench passed all of this.** L.4's 1797 backend and 418 frontend
tests were green, and every one of them was true. The backend tests drove
the server: the offer rule, the refusal, the blockers, the links. The
frontend tests asserted the API calls made and the strings present. None
asserted what a screen communicates: that an unattached list does not
look attached, that a dialog's claim was computed, that History can
answer the question it exists for. **This is not an L.4 gap. L.4 is where
it bit, not where it lives.** Nothing in the product tests what a screen
communicates. The frontend suite throughout asserts calls and strings,
and that is now a product-wide expectation, item Z. So each L.10 fix
carries a test written to fail on its own defect, asserting on rendered
output, not API state:
- the not-attached list has no chip and is not inside an `.ev-item`, and
  "Lists attached as evidence" holds only the attached one, including the
  live case alone;
- the blocked dialog has no "Yes, remove" while checking or when blocked,
  says "No evidence rests…" only when the server said so, and says it
  could not check when the check fails;
- History renders an attach and a detach, and the API returns them,
  including to `consultant_admin`.

**What the run against the old components proved, and what it did not.**
The new frontend tests were run against `main`'s `ObjectiveLists.tsx` and
`ListLibraryPanel.tsx`, and every one failed. **Every one failed because an
element it looks for is absent:**
- "Lists attached as evidence" and "Lists not attached" did not exist;
- the dialog had no labelled group;
- the old not-offered sentence had different words;
- the old History never fetched link events.

A test that fails because the element is absent has proved the components
differ. It has not proved the test would catch a wrong value in an element
that exists: say, a not-attached list given the chip inside the new
"Not attached" group, a "No evidence rests…" shown despite blockers, or a
detach worded as an attach. **None of the runs exercised that.** The
stronger claim, that these tests catch their defects, is untested; do not
read it into "every one failed". The test that would show it is a mutation
run against the new components: put the defect back in an element that
exists (the chip on an unattached row; the removable sentence ignoring
`removable`) and watch the test fail on that assertion.

**Found on the bench:** SQLAlchemy writes Python `None` into a JSONB column
as JSON `null`, which `IS NOT NULL`. `ck_list_link_event_from` rejected
every event but a move forward. Fixed with `JSONB(none_as_null=True)`. The
constraint is what caught it.

**Verification (bench `20261018-l10`, isolated):** migration 0068 applied
clean. Backend full suite **1799 passed** (1797 on main), ruff clean.
`tsc -b` clean, vitest **424** (418 on main), build OK.

**Verified on live (2026-10-18, `b70958d`, migration 0068).** Jarrod ran the
loop on *Physical Access Device Register* in Acme MSP:
- re-tagged PE.L2-3.10.6;
- filled the rows (v2);
- attached v2 to PE.L2-3.10.6[b];
- opened the tag dialog while it was linked;
- detached it;
- removed the tag.

He read each step on screen. From the database, audit log and request
log:
- **Link events:** two `list_link_event` rows, `linked` then `unlinked`,
  version 2, PE.L2-3.10.6[b], *CMMC L2 Assessment 2026-09-09*, Jarrod
  Winsor, with matching `evidence.list_link` and `evidence.list_unlink`
  audit rows.
- **The check while linked:** the first removal check returned blocked
  and no DELETE followed, because the dialog offered none.
- **The check after detaching:** the second check returned removable,
  and only then the DELETE (200, `list.tag_remove`).
- **No state change:** no `control_state_history` rows.

L.4's own live step (a real linked list in a real bundle, read by hand)
is still open; this run did not build a bundle.

**Found on live, folded into the next frontend slice (not their own
slice).** All three are on the tag-refusal screen or next to it. **Items 1
and 2 were closed by X.1** (2026-10-18; see its Done entry), as decided
here: fields from the server, one sentence written by the client, in one
dialog shared with documents. **Item 3 was cleared by the live walk**
(2026-10-19): Jarrod's full-window screenshot shows no overlap, the block
sits above the attach buttons. Nothing from L.10 remains folded forward.
1. **The refusal says one fact three times.** The client wraps the
   server's sentence ("PE.L2-3.10.6 cannot be removed from
   3-10-5a-access-devices-own: evidence rests on it (version 2 linked to
   …)") in its own "PE.L2-3.10.6 cannot be removed:", and the blocker
   bullet repeats the parenthetical. Say it once.
2. **The server's sentence was written for a log.** It names the list by
   its key (`3-10-5a-access-devices-own`), where every other screen uses
   the title. Decision: **the client composes the sentence from
   structured blockers**, and the server's string stays for the log, the
   CLI's import refusals and the DELETE's 409. The removal check
   (`GET …/tags/{key}/removal`) returns a reason (`last_tag` or
   `evidence`) and each blocker as fields: version number, control key,
   objective key, assessment name. The client writes, once, in the
   list's title: "PE.L2-3.10.6 can't be removed from Physical Access
   Device Register while evidence rests on it", then one bullet per
   link, "version 2, evidence for PE.L2-3.10.6[b] in *CMMC L2 Assessment
   2026-09-09*", then "Detach it first." The DELETE's 409, shown only on
   the race between check and click, gets the same structured
   `blocked_by` so the client says it the same way. Per item Z, test it:
   the rendered dialog contains the list title and not its key, and
   states the fact once.
3. **The "Not attached" block may overlap "No evidence attached" and the
   Attach file / Add location buttons.** This is unconfirmed: it may be a
   screenshot crop. Read statically, nothing permits an overlap. The
   evidence section, `.ev-lists`, `.ev-list-others`, `.ev-footer` and
   the drawer's objective containers are all in normal flow, with no
   absolute positioning, no negative margins, no fixed heights and no
   clipped overflow. A static read is not a rendering. Confirm with a
   full-window screenshot at normal width before changing anything; if
   it is real, find the cause in the rendered page, not by guessing at
   margins.

---

### List library L.4: a populated list becomes evidence, and the export says what a list is for (T) ✅ DONE (2026-10-17, bench-verified; live pending)

**What it does.** A list is offered as evidence on each objective of a
practice it is tagged with, once `list_library.is_populated` says its rows
are real; a person attaches it (`list_evidence.py`,
`routers/list_evidence.py`, migration 0067). Nothing is attached for them
(CLAUDE.md's product invariants), and nothing about linking reads or writes `control_state.status` or the
SPRS score -- both negatives asserted directly
(`tests/test_list_evidence.py`). A list that is not offered says why on the
objective and on its own page -- "still showing template examples (2 of 2
rows)", "it has no rows yet", or that it is a template -- because an
adopted copy starts unfilled by design and "my list isn't there" would
otherwise read as the feature being broken. The rule is `is_populated`'s
alone: `not_offered_reason` asks it, and a test flips its answer to prove
the offer follows.

**What the link points at, and the number that decided it.** A
`list_version`, never the list: a nullable
`evidence.source_list_version_id` (RESTRICT), `kind='reference'`, and a new
artifact type `list` -- not `export`, which means a file someone uploaded
from a tool. `ck_evidence_list_reference` makes a list artifact exactly an
evidence row naming a version, and a reference. Not a third `kind`:
**sixteen places branch on `Evidence.kind` today** (13 in the backend,
seven of them in `bundle_service.py`; 3 in the frontend), and each would
have had to learn the new value; N.1's documents had already chosen the
version-column shape (`source_document_version_id`). One evidence row per
list version, shared by every objective it is linked to; its
`collected_at` is when the version was saved. `models.py`'s
`ck_evidence_artifact_type` had not followed 0045's `attestation`; it does
now. Nothing would have caught it, and nothing catches the next one; see
item Y.

**Drift.** A link stays on its version when the list moves on. The drift
is said in L.7's words -- "List version 4 is available. This evidence
stays on version 2 until someone moves it forward." -- on the objective,
on the list's page, and in the assessment's bundle picker. Moving forward
is a deliberate act per objective: a new live link, and the old one
archived, never rewritten. Linking the current version beside an older
live link is refused ("move it forward instead"), so drift cannot hide
behind a second link.

**Confined to the tag set; `tag_removal_blockers` is live.** A list links
only to objectives of practices it is tagged with (409 otherwise), and the
function L.2.2 left returning `[]` now returns every live link from any
version of the list to that practice's objectives, named -- "version 1
linked to PE.L2-3.10.3[b] in assessment 'FY26 L2'". All three tag writers
already asked it (a person, an import, an adopted copy taking a template
version); tests drive the person and the import paths against real links.
`test_list_builder.py`'s guard failed on `evidence` as designed and was
settled the way its message says: one table name added to `_SETTLED`, with
the real-link tests beside it. One thing the tests found about the rule
itself: L.2.2's "the last tag stays" refusal runs first, so on a
single-tag list the evidence blocker is never the reason given.

**A product's deactivation leaves a list link alone.** The link exists
because a person attached a list, not because a tool claimed coverage.
The known limitation -- deactivation archives *every* link on a
tool-sourced state, not just that tool's (see the multi-tool-coverage
writeup) -- did reach this path: it would have archived the list link and
made the tag removable at that moment. `engine.deactivate_org_product` now
skips links to a list version; tested as the negative, with a tool's own
evidence on the same objective still archived beside it.

**The bundle.** Linked lists come first in the picker, still unticked. A
chosen list is embedded as its current version *and* every version a live
link in this assessment names, so evidence resolves to exactly what was
linked; `lists/index.html` says which objectives each version serves,
flags "not current -- linked at version 1; the list was at version 2",
and lists the versions linked but not chosen. The manifest and the
implementation page point a list link at its workbook, or say it was not
chosen. Every new query is ordered to a key; the artifact log and the
cover's second-order hash cover the linked version (asserted). **The
unlinked section now excludes list evidence**: it took every org evidence
row not linked into *this* assessment, so a list linked in another
assessment would have appeared there as a stray artifact. Unlinking a
list deletes its evidence row once nothing links to it, as the generic
unlink does.

**T: the export says what a list is for, who owns it, how often it is
reviewed.** Rows 3-4 of the workbook are now "What goes here: …" and
"Responsible: … | Review cadence: …", worded as the template header
block's rows 4-5, with "not recorded" when empty; the column header moved
to row 8 (`render.LIST_EXPORT_HEADER_ROW`). Every list export's bytes
changed once, deliberately; the determinism and formula guards passed
unchanged, and the two new cells open with a label, so no typed value can
begin one. L.2.2's "a hand-built list's export carries the same header
block an imported one does" was true and compared rows 2-5 while neither
carried these fields -- a test passing while checking less than its name;
it now compares rows 2-7 and pins the values.

**T's round-trip half is withdrawn permanently (Jarrod, 2026-10-18).**
This item's Planned text asked for the export to "read back through the
importer to the same version". It will not. An export is one org's records
for an assessor; a template is an MSP's examples for many orgs. Making the
first readable as the second puts "export a customer's filled register"
one step from "distribute it as everyone's template", which would mean
client data landing in the wrong tenant. CLAUDE.md's product invariants
rule it out: a customer's content never becomes template source. The
importer reads rows 2/4/5/10 and the export writes 2/3/4/8, and that
difference is the intended design, not a gap to close. If curated content
ever needs an interchange format (N.4 and cross-deployment distribution
both point there), it is a new item for template versions only, in the
importer's layout. It does not reopen this one.

**Found, not fixed (out of scope):**

- **Documents attach themselves, and that breaks a hard rule.** Raised to
  its own item, X.
- **The generic evidence unlink writes no audit row**
  (`routers/evidence.py`'s `delete_evidence`). List links have their own
  unlink, which does. Now its own entry under Planned, "The evidence ✕
  deletes the artifact" (2026-10-19).
- **The generic per-objective evidence list shows archived links** (it
  has no `is_archived` filter), so a deactivated tool's evidence still
  appears on the objective. List links are listed separately and only
  when live.
- **Attaching is per objective**, as all evidence is: a list serving a
  practice with six objectives is six clicks. Capture once, reference
  many holds for the data (one evidence row per version); the clicks are
  a UI question.

**Verification (bench `20261017-l4`, isolated):** migration 0067 applied
clean; backend full suite **1797 passed** (1779 on main), ruff clean; tsc
clean, vitest **418** (410 on main), build OK. Live, after merge and
deploy: a real linked list in a real bundle, opened and read.

---

### List library L.7: an org adopts a template, and later takes a newer one ✅ DONE (2026-10-16)

Migration 0066. Jarrod found the hole on the live Lists screen: Winsorlabs
held 45 templates and "This org's lists (0)", under an empty state that
already said *"A list this org keeps for itself, including one copied from
a template, goes here"* -- describing a mechanism that did not exist.

**Delivery was already decided, not decided here.** The monetization
boundary (item N, and `docs/PLAN-document-library.md`) says the engine is
open and template *content* is distributed separately, with no code-level
paywall. `lists-import --org-id` is how a content bundle enters a
deployment -- Jarrod ran it on his own 45, and another operator runs the
same command. No licence check, flag or entitlement table was built. What
was missing was only getting a template from the org that holds it into an
org that keeps a copy. (The list library has no `is_template_derived` /
`template_ref` columns; those are the document library's. A list copy's
`template_list_id` is its equivalent marker.)

**Templates stay org-scoped.** They are rows in the MSP's org, confined by
RLS, and stay there. Moving them to deployment scope like the product
catalog would be simpler for one MSP and wrong the first time two MSPs
share a deployment, since every template would be visible to both. So
adoption is a **copy across orgs, made by a person who defines lists in
both** (`DEFINE_ROLES` membership in each, checked per request). The router
reads the template under its org and writes the copy under this one
(`routers/list_definitions._in_template_org`, through
`rls.set_current_org` both ways, flushing before each switch). Two
`SECURITY DEFINER` reads cover what a tenant cannot otherwise see, each
scoped to `app.current_org` and returning version numbers, never content:
`auth.list_template_heads()` (where each copy's template stands now) and
`auth.list_template_adoptions()` (a template's copies by version, never
whose).

**The adoption record.** A copy is a `list_definition` with
`template_list_id` (the template) and `template_list_version_id` (the
template version it is based on -- reserved since 0064 for exactly this),
`is_template` false; a CHECK keeps that shape, and a unique index allows
one copy per template per org (adopting again reports the existing copy).
It copies title, What goes here, Responsible, Review cadence, columns,
rows and tags as the template has them now. Its key is
`<template key>-own` everywhere, one rule: in the template's own org the
two must coexist, and anywhere else an import of the same library would
otherwise read the copy as its sheet (a test holds a same-org reimport at
"unchanged" with the copy untouched). Tags it got from the template are
`added_via = template_adoption`. Adopting is audited on both sides
(`list.adopt` in the copy's org, `list.template_adopted` in the
template's, naming the copy).

**A seventh cause: `adopted_from_template`.** v1 reads "Copied by <name>
from the template <title> at version <n> on <date>." Its provenance names
the template, its org, key, version id and number.

**Adopted rows are the template's examples, so the copy starts
unfinished -- and "populated" is now defined once, in code.** The brief
read this as falling out of "L.4's populated rule", but that rule existed
only as a recommendation in the plan (section 5) and L.4 is not built. It
is now `list_library.is_populated`: rows, none of which still carries a
fill-in token -- an uppercase `[TOKEN]` anywhere in a cell, so
`[PLACEHOLDER]` and the family the library also uses (`[CUSTOMER_NAME]`,
`[CUSTOMER]`, `[SERVER]`, `[DATE]`, `[CUI_ACCESS_GROUP]`,
`[SECURITY_GROUP]`), whether alone or inside a value such as
"[CUSTOMER]-Admin-Role". `[PLACEHOLDER - reason]` is a value, and so are
lowercase brackets (an objective's `[a]`). The first version matched only
a cell that *was* `[PLACEHOLDER]`; review caught that a copy still saying
`[CUSTOMER_NAME]` would then be offered to an assessor as evidence once
L.4 gates on this, and a survey of the real library found it missed
`[PLACEHOLDER] (customer CSP/app)` too. Both were fixed before merge. L.4
must use it rather than a second rule; the plan's L.4 section says so.
The screen states it on any kept list with examples left ("All 3 rows are
still the template's examples, with a fill-in such as [PLACEHOLDER] or
[CUSTOMER_NAME] left in them. A list counts as filled in, and can be
offered as evidence, only when none remain"), highlights every cell with
a fill-in, and badges the index row. Nothing offers a list as evidence yet, so "an unfilled copy is
not offered as evidence" is a rule waiting for L.4, tested here as the
predicate.

**Staying in sync: the update is available, and nothing happens until
someone takes it.** A copy based on template v2 when the template is at v3
says "Template version 3 is available. This list stays on version 2 until
someone takes it" -- the same shape as N.3's overdue review, which flags
and never acts. Taking it (`list_adoption.take_update`) writes a new
version of the copy, cause `adopted_from_template`, naming the version it
moved from. **The template's rows are examples; once a tenant has replaced
them they are the tenant's.** So an update takes the template's definition
(title, What goes here, Responsible, cadence, columns) and, if the copy's
rows are still the examples it copied, the template's new examples; if the
org has edited its rows, those are kept and matched to the new columns by
name. That carry is not an overwrite and needs no authorization. What
does: a definition field a person changed since the copy last took the
template, or filled cells in a column the template dropped. Then the
update is refused unless that exact edit is named, `<copy key>@vN` -- the
form an import's overwrite takes -- and the replaced version keeps every
value. The template's tags follow by the import's own rule (only
`template_adoption` tags are the update's to remove; a person's never), and
a removal is refused while evidence rests on it. Taking an update needs
membership in both orgs too; a tenant-only user sees that a newer version
exists and cannot take it.

**Roles.** msp_admin, consultant_admin and msp_engineer adopt and take
updates; the role must define lists in the template's org as well as this
one. **customer_poc adopts nothing**, on the rows-only line (an adopted
list is a list defined), and agrees with the brief's reading.
c3pao_assessor writes nothing.

**The screen.** *Adopt templates* beside *+ Add list*: choose the org that
holds the templates (this org first, labelled so), tick one or several,
*Review* shows the set -- what will be copied, with example rows to fill
in, and what this org already keeps -- and only then *Copy N into this
org's lists*. A copy sits under This org's lists, badged "from template
v1", "template v2 available" and "N example rows to fill in", and its page
says which template and version it came from. A template says "Adopted by
3 orgs: 2 at version 1, 1 at version 2", and in an org that is its own
tenant offers *Copy into this org's lists* or *Open this org's copy*, the
copy reading "Copied from this org's template ...".

**Found, recorded rather than built (N.4's):** the templates carry
MSP-specific and substitution text a copy takes verbatim. On live, four
templates' text names "Winsors Labs" (one title, three row sets -- e.g.
"Winsors Labs SOC" in the IR contacts), Responsible across many names the
MSP's own stack ("MSP Engineer (Datto RMM / Liongard / IT Glue)",
"Fortinet FortiGate 60F"), and seven templates in the real library use
tokens beyond `[PLACEHOLDER]`: `[CUSTOMER_NAME]` (two), `[CUSTOMER]` (two,
e.g. "jdoe@[CUSTOMER]"), `[SERVER]`, `[DATE]`, `[CUI_ACCESS_GROUP]` and
`[SECURITY_GROUP]` (3.1.2a). Substitution is N.4's design and was not
half-built; until it lands an adopted copy shows these as written, and its
rows stay examples until someone replaces them. Content fixes in the
library rather than code: the real `3-1-3b` template's two example rows
carry no token at all, so a copy of it reads as filled at once; and
3.13.10a writes a lowercase `[confirm]`, which the rule deliberately does
not read as a token (lowercase brackets are real values), so it wants
normalising to `[PLACEHOLDER]`.

**Ordering: L.7 before L.4, agreed.** Evidence linking on an org whose only
lists are scope projections tests a corner; linking an adopted, filled-in
list is the real workflow. The dependency is one-way.

Bench (`wingrc_verify_20261016l7`): the real 45-list library imported into
an MSP org beside an empty tenant, a throwaway engineer in both. In the
browser: adopted *Visitor Access Log* and *Incident Response & Notification
Contacts* into the tenant (set shown first; both under This org's lists,
badged; the MSP's index then read "adopted by 1 org"); saved template
version 2 of the visitor log with a *Badge #* column; copied it into the
MSP org's own lists (same-org case, reading "Copied from this org's
template ..., version 2"); filled the tenant copy's row (the example notice
went); reviewed and took template version 2 -- rows kept, *Badge #* empty,
v3 "took version 2 ... (this list was based on version 1)". The audit held
`list.adopt`/`list.template_adopted` and
`list.template_version_take`/`_taken` on the right orgs, with the person as
actor. Backend 1757 passed with the 500 floor, ruff clean, `tsc -b`,
vitest 404, build. Ten mutants each caught by `test_list_adoption.py` and
restored: an update ignoring a person's edit; never carrying the org's
rows; no membership check in the template's org; the template org's role
unchecked; v1 recorded as an edit; an update removing a person's tag; the
reason-form placeholder counted as an example; a copy keyed as its
template; customer_poc allowed to adopt; a non-template adoptable. The
fill-in token rule's four, each caught: back to a bare-cell match;
`[PLACEHOLDER]` only; case-insensitive (reads `[a]` as a token); spaces
allowed in a token (reads `[PLACEHOLDER - NOT COLLECTED]` as one).

Out of scope, as briefed: substitution and branding (N.4), documents (N.4
-- lists only here, to converge later), any licence or entitlement
mechanism, and `control_state`/SPRS (a test holds both unchanged across
adopting and taking an update).

---

### List library L.8: the list page puts the rows first ✅ DONE (2026-10-16)

Frontend only: no API change, no migration, nothing under `backend/`.
Jarrod used the live page and asked for three things -- History below the
table and collapsed, column headers near-black and semibold, and "easier
on the eyes". The reason under all three: **the rows are what the page is
for**, and the page put four blocks of context above them (where the copy
came from, What goes here / Responsible / Review cadence, the full
History) and drew the column headers in the lightest type on the screen.

**Order now** (`ListLibraryPanel.tsx`'s `LibraryListView`): title, version,
Export, Edit rows; the practice tags; what to act on -- a newer template
version (its own region, "Newer template version"), the still-examples
notice, an old-version or conflict notice; **the table**, its edit bar
and its legend; then reference -- where the copy came from, the
template's copies, a merge's column map, What goes here, Responsible,
Review cadence; then History in a `<details>`, collapsed, its summary
naming the entry count ("History: versions and practices (4)"); opened,
it shows what it showed before. The legend sits directly under the table
rather than last, because it explains the table. At 1366×768 the first
data row now starts at y=434 (Risk Register) and y=474 (Authorized
Devices); before, it was at 879 and 997, below the fold on both.

**The table.** Headers `#212529` semibold, sentence case (the caps went:
"Treatment (Mitigate/Accept/Transfer)" reads better as written), on
white with a 2px rule under them, lighter `#e9ecef` rules between rows --
rules, not zebra. The table scrolls inside its own box (up to 70vh), so
the header row is sticky; the rule is an inset shadow because a border on
a sticky cell scrolls away under `border-collapse`. A wide table says so:
a shadow on whichever edge has more beyond it, and a line above it --
"19 columns, wider than the screen. Scroll sideways for the rest →" --
measured live (`useSideScroll`, a ResizeObserver), so a list that fits
says nothing. Example cells (any uppercase fill-in, the same rule as
`is_populated`, not only a bare `[PLACEHOLDER]`) are italic grey on a
grey tint; `[PLACEHOLDER - reason]` keeps its amber; real values are
near-black.

**Contrast, measured (WCAG 2.x):** headers and values `#212529` on white
15.4:1; this page's muted text `#5c636a` on the page's `#f8f9fa` 5.8:1;
an example cell `#6c757d` on `#f1f3f5` 4.2:1 (faint on purpose, above
the 3:1 the brief set); a reason cell `#7a5c00` on `#fff4cc` 5.7:1; the
scroll hint `#495057` 7.8:1; the header rule `#868e96` 3.3:1. **Found:
the shared `--muted` (`#6c757d`) is 4.45:1 on the app's `--bg`
(`#f8f9fa`), just under AA** -- it is what every `field-hint`,
`contact-sub` and the old list headers were drawn in. This page overrides
`--muted` to `#5c636a` for itself; the token is left alone app-wide,
which is a decision for its own small slice (it touches every screen).

**Found: no shared table component, and three table styles.** 17
components draw `<table className="contacts-table">` with their own
markup (assets, users, contacts, audit log, tools, scope diffs ...), the
document history uses `doc-table`, the diff view `diff-table`. The header
style lived in `.contacts-table th` -- small grey caps, the same 4.45:1 --
so it was shared CSS, not a shared component. L.8 changed only
`.list-rows` (this table), per the brief: restyling 17 screens is not this
slice. The sharper half of this finding: the four lists drawn from scope
(`ListsPanel`) sit **on the same Lists index** as the ones L.8 restyled,
so two clicks from one screen now open two different-looking pages.
Cross-screen inconsistency is tolerable; same-index inconsistency reads
as a bug. That is the next slice, narrowly -- **L.9**, under Planned --
not all 17. A table component (header, rules, sticky, side-scroll hint)
would end the wider drift; it wants its own slice, after L.9. The
`--muted` gap is item **V**, under Planned.

Bench (`wingrc_verify_20261016l8`): main's build and this branch's,
served side by side over the same backend, with the real 45-list library
imported and two same-org copies -- the Risk Register (one example row
and one real one) and Authorized Devices (19 columns; 2 example rows plus
40 bench rows, one with a `[PLACEHOLDER - reason]`). Screenshots by
headless Chromium at a 1366×768 viewport. Inside the device table
scrolled down and right, the header stays at the top of the box.
`tsc -b`, vitest 404 (the History tests now open the `<details>` and
assert it starts closed with its count; one asserts the order: newer
template version, then the rows, then where the copy came from), build.
The real device template has 19 columns, not the brief's 18.

---

### Tests that depended on the clock's granularity: a sweep ✅ DONE (2026-10-16)

Two red runs had the same shape: a test passed or failed on when it ran
rather than on what it tested -- 519d894's two workbooks saved in the
same second (openpyxl's `core.xml` is second-resolution, so the bytes
matched), and the list bundle test whose two exports straddled the minute
stamped into `lists/index.html`. That is a pattern, so the suite was swept
for it, as it was for bare preconditions: artifacts compared across two
moments when they embed a stamp; a test computing "now" itself against a
value the code computed separately; ordering asserted across rows that
tie on a timestamp; "recent" checks and sleeps; frontend tests reading the
real date.

**One real instance, fixed:**
`test_session_idle.py::test_continuous_activity_still_terminates_at_absolute_expiry`
set `expires_at` one second before Python's `datetime.now()`, but the
check it tests runs in SQL against `now()` -- which, inside the test's one
outer transaction (`conftest.db_session`), is fixed at the moment that
transaction began, before setup. Setup taking a second, or the database
host's clock lagging Python's by one, left the session looking valid and
the test red. It now takes its times from the database's clock
(`_db_now`), the one the code under test reads; so does the heartbeat test
beside it, which had the same mix-up behind a one-minute margin. Proved on
the bench by injecting a two-second delay into setup: the old test fails,
the new one passes.

**Checked and safe** -- with the reason, so the next sweep need not
repeat it: the bundle and list-bundle comparisons strip or derive their
stamps (workbooks take the version's `saved_at`, not wall time); the
scheduler, review-cycle, document-approval and token tests sit days or
minutes from their thresholds; ordering tests either set distinct times,
order by `seq`, or test queries with an id tiebreaker; no backend test
sleeps; no frontend test reads the real date.

**Latent, recorded rather than changed:** several app queries order by a
timestamp with no tiebreaker (`routers/raci.py`, `users.py`,
`assessments.py`, `evidence.py`, `review_cycles.py`, `liongard_sync.py`,
`routers/auth.py`). No test asserts their order across more than one row
today; the first that does will tie on rows created in one transaction.
Add the id tiebreaker to the query then, not a sleep to the test.

The convention is in CLAUDE.md beside the precondition one: a test's time
margin is taken from the clock the code under test reads.

**Missed by the sweep, found by a red run on `main` (40e5f31, 2026-10-16,
started 23:57:23 UTC).** "The bundle comparisons strip their stamps" was
true of the pages and not of the zip's member names: every page sits
under `{slug}_{YYYYMMDD}/`, from `generated_at`, and
`test_lifecycle.py::test_full_tenant_lifecycle` read its second export by
names taken from the first. The two straddled midnight UTC, so
`…_20261016/ssp/02_implementation.html` was not in a bundle whose folder
was `…_20261017/` -- a KeyError, nothing wrong with the product. A day is
coarser than any gap in a test, which is why the sweep's "a stamp finer
than the gap" reading passed it over: **a boundary of any stamp can fall
between two moments**, however coarse. Fixed: `_page(zf, rel)` looks each
page up in its own zip by its path under the folder. Two more tests had
the same latent fault, not yet red, and now compare paths under the
folder: `test_assessment_completion.py`'s before/after name sets, and
`test_list_bundle.py`'s byte-identical check, whose artifact-log lines
carry the full path. The pages link to each other relatively, so the
folder name (and the log's paths) is the only place the date reaches.
Found and proved on the bench with a mutant that moves every export's
folder date a day on: under it, main's versions of the first two fail
(the lifecycle test with CI's exact KeyError), and the whole suite run
once turned up the third; with the fix, all 1757 pass under the mutant
and without it.

---

### List library L.2.2: the List Builder, and control tags with a history ✅ DONE (2026-10-15)

Migration 0065. Jarrod asked, from L.2.1 on live, for **Add List** -- a
builder -- and **Add Tags** on an existing list.

**One definition schema, two ways in.** The builder fills exactly the
fields the template header block does: title, row 2's practices, What
goes here (row 4), Responsible and Review cadence (row 5), and row 10's
columns. Practices are typed as row 2 writes them and keyed by the same
rule (`list_templates.practice_key`), then held to the 110 requirements
-- `_REQUIREMENTS`, checked by a test against the seeded catalog, not
taken on trust. A hand-built list is keyed as an import keys a sheet
(the slugged name), so one key space serves both; a duplicate title is a
409 naming the existing list, never a merge. No model field was missing:
every builder field already existed on `list_version`. **"Imported
from" has no convention equivalent, and was not given a content field.**
It is a person's note (`provenance.origin_note` on v1), shown under
*Where it came from* as "a note by <name>, not an import source" -- never
under *Imported from*, which stays the import's `source_ref`, and never
near `source_sha256`. Lineage the product computed and a person's
recollection cannot be confused on screen or in the record.

**A sixth cause: `created_by_hand`.** A list built here is none of the
five (`edit`, `first_import`, `source_changed`, `importer_changed`,
`source_reapplied` -- the brief's "person_edit" is `edit` in the code).
Calling its v1 `first_import` would claim a source there was not. The
history says: "Written by <name>, from nothing: built by hand in WinGRC
on <date>, not imported from a template." Label: *built by hand*.

**A hand-built list whose key a later import produces is a conflict --
confirmed from the code, with a correction to the reasoning.**
`diff_import`'s conflict rule keys on the current version's `via ==
"api"`, not on `saved_by`. v1 is written `via: api`, so the rule covers
it; a test pins it, and a mutant that writes v1 with any other `via` is
caught. Overriding with `--overwrite-edit <key>@v1` writes the import as
`first_import` with `replaces_edit_in_version: 1`, and the history now
says so ("It replaced the edit in version 1"); the definition stays the
org's own (`is_template` unchanged) -- an edge left as it is, since it
needs an explicit override to reach.

**The slice's real work was in tags, and it predates the builder.** An
import replaced a list's whole tag set with its source's row 2
(`set_tags`: delete all, insert the source's). So the next import after
a person added a tag would have **silently removed it** -- the dry run
would even have counted the list as `new_version` for the tag
difference. Now each tag records who put it there
(`list_control_tag.added_via`: `template_import` | `api`), and an import
manages only its own: it adds what row 2 names and the list lacks, and
removes only import-added tags row 2 stopped naming
(`list_library.import_tag_changes`). A person's tag is never the
import's to remove, and does not register as a change in the dry run.

**Tag history: recorded beside the versions, shown in the same place.**
Tags are not version content, so a tag change writes no version. Every
add and remove is a `list_tag_event` (who, when, how, the version
current at the time), append-only like `list_version` (a trigger refuses
UPDATE), RLS-scoped, and also an audit row (`list.tag_add`,
`list.tag_remove`; `list.create` for a built list). The list's history
section is now *History: versions and practices* -- versions and tag
changes interleaved, newest first, each tag line saying who: "Tagged
CM.L2-3.4.2: this list became a candidate for it. By <name>." or "... By
WinGRC importer, from the source sheet's practice row." One history, in
the place a person looks first; the audit log stays for when someone
already suspects something. Tags that existed before this migration are
backfilled as one *added* event each, dated with their list and noted
"Recorded when tag history began (migration 0065)" rather than
presented as observed at the time.

**The tag-removal rule, written before L.4 so L.4 inherits it.** A list
linked as evidence covers the controls it is tagged with (PLAN §5), so
removing a tag while such a link exists would silently narrow what an
assessment rests on. **Removal is refused while evidence rests on the
tag, by a person and by an import alike, and the refusal names the
link** (409 `blocked_by`; `ImportRefused` with nothing written).
Adding needs no ceremony beyond the record. The rule lives in one place,
`list_library.tag_removal_blockers`, which returns nothing today because
nothing links a list as evidence yet. **L.4 must make it return every
non-archived `EvidenceStateLink` from the list's evidence to a
`control_state` of that control.** Checked against the evidence model
first: links are per `control_state`, which belongs to one control, so a
link blocks exactly one tag -- no conflict. Two tests hold L.4 to it: one
stands a link in and asserts both writers stop on it; the other fails as
soon as any table references `list_definition`/`list_version` beyond the
four settled ones, until the rule is wired to it. Removing a list's last
tag is also refused: a list serves at least one practice, as row 2
requires.

**Columns.** Editing now adds, renames and removes columns. Rows are
positional, so a rename keeps its cells (not remove-and-add); a removal
drops them from the new version only. Before saving, the edit bar says
plainly what goes -- "Saving removes the column Escort (1 filled cell)
from version 3: 1 filled cell will not be in it. Version 2 keeps it,
with every value." -- and the save button reads "Save as version 3,
removing 1 filled cell". The edit's audit row names `columns_added` and
`columns_removed`.

**Who: a customer contact fills lists in; it does not define them.**
Defining a list -- building one, its columns, and its tags -- is the
practitioner's: msp_admin, consultant_admin and msp_engineer
(`DEFINE_ROLES`, `LIST_DEFINE_ROLES`). **`customer_poc` edits rows
only**: a version it saves must keep the columns it was based on (403
otherwise), and it gets 403 adding or removing a tag. Tagging is the more
consequential half of defining, since once L.4 links a list as evidence a
tag changes what the list claims to cover. `c3pao_assessor` writes
nothing. The branch first shipped `customer_poc` tagging but not
building, following the brief's conclusion while its stated reason
("adding a list to their own org is reasonable") argued the other way;
the advisory session resolved it as above, rows-only unless Jarrod says
otherwise. msp_engineer was not in the brief's list; included because it
already rewrote any list's rows and columns. The role test walks every
role through build, tag add, tag remove, a rows-only save and a column
change, and checks a refusal leaves the list as it was.

**Found, not changed:** the list export's header block does not carry
What goes here, Responsible or Review cadence -- the convention's rows
4-5 -- for any list, imported or built. The builder's export matches an
imported list's (tested), so the round trip holds; adding them changes
every export's bytes, so it is its own slice (Planned, item T). And the
login form shows a 422 as "[object Object]" (Planned, item U). And a
bundle test compared the raw hash of `lists/index.html`,
which carries a minute stamp by design; it failed on the bench when the
two exports straddled a minute. It now compares that line by path (the
page itself was already compared unstamped).

Bench (`wingrc_verify_20261015b`): the real 45-list library imported on
main (81 tags), then the branch's migration over it -- 81 tags read
`template_import`, 81 backfilled events, none unmatched -- and the
import rerun read 45 unchanged. In the browser, as a throwaway engineer:
built *Bench Visitor Log* (v1 *built by hand*, its two tags and the
version in the history by name, the origin note apart from any import
source); added a row (v2); removed a column, with the warning shown
before the save (v3; v2 still holds the column and its value in the
database); tagged the *Authorized Wireless Networks* template to
CM.L2-3.4.2 -- in the CM.L2-3.4.2 view at once -- and an import rerun
then wrote nothing and kept the person's tag (`api`). Backend 1734
passed with the 500 floor, ruff clean, `tsc -b`, vitest 391, build.
Mutation-tested, each against `test_list_builder.py` and restored: an
import removing a person's tag; a person's removal ignoring evidence; an
import's removal ignoring evidence; v1 not `via: api`; v1 called
`first_import`; customer_poc allowed to build -- each caught.

Out of scope, as briefed: evidence linking (L.4), cadence notification
(L.5), the scope-graph join (L.6), exporting a hand-built list as a
template (N.4), and any change to `control_state` or SPRS (a test holds
SPRS and every control state unchanged across building and tagging).

---

### An import never replaces a person's edit unless that edit is named ✅ DONE (2026-10-14)

No migration. The follow-up the cause work exposed: `source_reapplied`
recorded that an import had put the source back over a person's edit,
but only after the fact. Answered from the code at the time: the dry
run compared the planned content with the current version and nothing
else, so a list about to lose a colleague's edit printed exactly like a
list whose workbook changed; and `apply_import` wrote every
`new_version`, whatever the current version was -- a changed source and
an importer change replaced edits too, not only a re-apply.

**The rule is the sync's:** an import has a source for the template,
not for a person's edit, so it does not overwrite one. A list whose
current version is an edit (`via: api`) and whose content the import
would change is a `conflict` (`list_library.diff_import`), whatever the
cause. A tags-only difference is not one -- it writes no version and
touches no edit.

**The dry run says so first,** before the counts:
`CONFLICTS: n list(s) a person edited since import`, one line per list
naming the edit as `list_key@vN`, who saved it, when, and the fields
the import would change. **The apply leaves every conflict as it is**
and the `list.import` audit row lists them (`conflicts`).

**The override names the edits, never "edits in general".** A blanket
flag is a foot-gun -- passed to get past one known list, it silently
clobbers the ones nobody looked at. `--overwrite-edit list_key@vN`,
repeated once per edit, authorizes exactly that edit, as asset approval
is per item. The ref carries the version, so an edit someone saves
after the dry run is a different edit the same authorization does not
cover. Any ref that is not a current conflict -- a typo, a list with no
edit, an edit since saved over -- refuses the whole apply with the list
of current conflicts, before anything is written (`ImportRefused`).
`--overwrite-edit` without `--apply` is refused outright. A replaced
edit is recorded on the version (`replaces_edit_in_version`) and in the
audit row (`overwrote_edits`).

Bench (`wingrc_verify_20261014k`, the real 45-list library, two lists
edited over HTTP by a throwaway engineer): the dry run printed both as
`…@v2` conflicts with the editor and time, 43 unchanged; an apply with
nothing named left both edits current; naming `@v1` was refused with
nothing written (no audit row either); naming one edit replaced that
list only (v3, `source_reapplied`) and left the other as a conflict;
the audit rows say which. Backend 1693 passed with the 500 floor, ruff
clean. Mutation-tested, each run against `test_list_library.py` and
restored: dropping conflict detection fails four tests; accepting an
unknown ref fails the refusal test; letting one ref authorize every
conflict fails the named-edit test.

---

### List versions say why they exist ✅ DONE (2026-10-14)

No migration (`list_version.provenance` is JSONB). Prompted by L.2.1's
rename reaching live: as built, the rerun would have written v2 of the
Winsorlabs device template with `via: template_import`, the *same*
source hash as v1, no reason, and a screen line reading "from the
template import" -- a reader a year on could not tell the product did it
rather than someone changing the template.

**Every version now carries a cause** (`list_library.CAUSES`, stored as
`provenance["cause"]` when written, derived the same way for versions
written before -- the history is append-only, so the derivation cannot
drift, and a test asserts stored and derived agree for every case):

- `edit` -- a person saved it (the API; `saved_by` names them).
- `first_import` -- the first import of its source.
- `source_changed` -- an import read a changed source sheet.
- `importer_changed` -- WinGRC's importer changed; the source did not.
  Same source in, different content out.
- `source_reapplied` -- an import of an unchanged source put its content
  back over a later edit. Not new behaviour (an import has always
  compared against the current version, edits included), but until now
  it would have read as a source change; the version now names the edit
  it replaced (`replaces_edit_in_version`), as does any import that
  replaces one.

**"Did the source change" is judged by the sheet, not the file.** The
importer recorded only the workbook's file hash, which cannot say
whether *this* sheet changed (a workbook holds several), misses a
merged list's second workbook entirely, and moves on every re-save
(openpyxl stamps the save time). `SheetResult.sheet_sha256` hashes the
raw cells the list was read from -- the sheet's own, plus each absorbed
sheet's for a merge -- and the cause compares that when both versions
have it, else the file hash (every version before this). Each import
version also records `importer_build` (the `WINGRC_BUILD_SHA` it ran
under), `previous_import_version` and `changed_fields`, and the
`list.import` audit row lists each version it wrote with its cause.

**On screen first, not only in the audit log.** The list page has a
*Version history* (`ListLibraryPanel.tsx`; wording in
`lib/listLibrary.ts`'s `whyVersion`) with a cause badge, the author --
a person's name, or "WinGRC importer" for anything an import wrote --
and one sentence per version, e.g. "Written by WinGRC, not by a person:
the importer changed how it reads the source, and the source sheet is
unchanged since version 1. Changed: title, columns, rows." The header
line, the version picker and the index's updated column carry the same
label.

**The pattern this exposed: importer changes create version churn on
customer templates.** Any change to how the importer reads a sheet --
a merge rule's title or placement, a column normalisation, a header
parse -- writes a new version of every list it touches, in every org
the library is imported into, on the next rerun. L.2.1 cost one version
of one list of 45. A future column tweak that touches every sheet would
write v3 across all 45 at once, each a real version an assessor may be
handed. Before shipping one: run the dry-run against a copy of each
org's library and read the `new_version` count; prefer changes that
leave already-imported content alone; and expect the history to say
`importer_changed` for each, which is now what it says. Kept lists
(adopted copies, L.4) should not be re-imported at all -- that is the
point of adopting.

Bench (`wingrc_verify_20261014c`), rehearsing the live rerun exactly:
the real 45-list library imported by the pre-L.2.1 importer (main at
`036c170`, the shape live's v1s have), then the branch's importer. Dry
run and apply each proposed 1 `new_version` (the device template:
title, columns, rows) and 44 unchanged; v2 carries `cause:
importer_changed`, `previous_import_version: 1` and its
`changed_fields`; the `list.import` row names v2 by id with that cause;
a second apply wrote nothing. In a browser the index's *Last changed*
says "importer changed" for that one list and "imported" for the other
44, and its page's *Version history* reads v2 "importer changed ·
WinGRC importer -- Written by WinGRC, not by a person: the importer
changed how it reads the source, and the source sheet is unchanged since
version 1. Changed: title, columns, rows." Backend 1689 passed with the
500 floor, ruff clean, `tsc -b`, vitest 378, build. Mutation-tested,
each run against `test_list_library.py` and then restored: judging the
source by the file hash alone fails the sheet-first test and the
importer-change test; dropping `source_reapplied` fails it and the
edit-then-reimport test; not storing the cause fails four. (A first
version of the importer-change test leaned on two fixture builds landing
in different seconds for its file-hash premise; it now re-saves the
workbook with a changed document property instead.) **Resolved, with
its cause read from the log, not inferred:** the red `integration` run
on 519d894 failed `source_sha256 != source_sha256` with the same hash on
both sides -- both builds wrote byte-identical workbooks because
openpyxl's `docProps/core.xml` timestamp landed in the same second. A
fixed timestamp would fail every run, and it passed on the bench and on
7f63555, so the clock is the only cause; the re-save fix is confirmed,
not lucky. That line was a precondition dressed as an assertion: the
behaviour under test is the sheet hash staying put, and distinct file
bytes are only what makes that mean anything, so its failure read as
"the product is wrong" when the fixture had not set up the condition.
It now says so (`"fixture must produce distinct file bytes or this
test proves nothing"`), and a sweep gave the same treatment to its five
siblings -- the `# sanity:` asserts in `test_assessment_completion`,
`test_audit_auth` (two), `test_audit_log` (moved up to where the rows
are seeded, from after the checks it guards) and
`test_baseline_version_move`, and `test_cli_reset_dev`'s non-zero
check. Checks of the product's own output were left as they are.

On live the rerun was held until this merged and deployed, so v2 of the
Winsorlabs device template would be written by an importer that says
why. Done 2026-10-15 at 6510da2 (this, the conflict rule and the
formula sweep): the dry run with `--org-id` showed new_version 1
(`3-1-1c-authorized-devices`: title, columns, rows), unchanged 44,
conflict 0, and the apply wrote exactly that one version. v2's
provenance reads `importer_changed`, previous import v1, no `saved_by`,
`importer_build` 6510da2; v1 predates the sheet hash, so the cause came
from the file-hash fallback, the file hash being unchanged. The
`list.import` audit row is `system`, with that one version and an empty
`conflicts`. Over HTTP the device list's summary and version history
return `importer_changed` (v2) and `first_import` (v1); the other 44
read `first_import`.

---

### List library L.3: export, and lists in the assessor bundle ✅ DONE (2026-10-14)

No migration. Spec: `docs/PLAN-list-library.md` §5 and §6. A list joins
the bundle as one more contributor; no export, zip or hash code was
written.

**Which lists a bundle carries: the ones the operator chose, and no
others.** Not "every list in the org": most of an MSP's lists are
unadopted templates and a bundle is one tenant's assessment. Until L.4
links a list as evidence, the operator picks on the assessment board
(*Lists in bundle*, `BundleListsPicker.tsx`), which sends repeated
`list_id` on the existing `GET …/bundle`. None chosen is a bundle with no
list files and a `lists/index.html` that says none were chosen -- the
page always exists, so an absent list reads as not chosen rather than
forgotten. A `list_id` this org does not have is a 404, never dropped.

**Named by version, not by list.** `snapshot_bundle(…, list_ids)` copies
each chosen list's current `list_version` into a frozen
`ListVersionSnap`; the bundle writes `lists/{list_key}_v{n}.xlsx`,
names the version id in the workbook's header, in `lists/index.html`
(with each file's SHA-256), and in the `bundle.export` audit row
(`after_value.list_versions`). Editing the list afterwards cannot reach
a rendered bundle, and a later bundle names the later version.

**The workbook is a function of the version alone**
(`render.render_list_version_bytes`). `render_view()` did not fit:
it projects scope entities through a `ListView`, and a version already
is its rows, so bending it would invent a projection. The new renderer
keeps its header shape (title, practices, provenance note) and adds the
version id. It holds no wall-clock time: openpyxl stamps the save time
into every zip entry and into `docProps/core.xml`'s `modified`, and
`_pinned()` rewrites both to the version's own `created_at`. So two
exports of one version are the same bytes, standalone or in any bundle,
and an assessor can check a file against another export's hash.
A cell is always text: openpyxl writes a string starting with `=` as a
live formula, so those are forced to string type (found writing this,
not reported). Since widened to every workbook and the
full prefix set: see "No workbook evaluates what someone typed".

**Hashed like everything else.** `lists/index.html` and each workbook
get an `artifact_log.txt` line, so the cover's second-order *Hash Value*
covers them. The cover gains a *Lists* row saying how many were chosen.

**Ordering.** Both new snapshot queries are totally ordered (definitions
by `list_key, id`; tags by `list_id, control_key, id`) and sit in
`bundle_service.py`, which the existing static guards scan wholesale, so
the guards needed no extension. Mutation-tested on the bench, each
mutation run against the test meant to catch it and then restored:
dropping the definitions ORDER BY, dropping its `id` tiebreak, and
dropping the tags ORDER BY each fail the static guards (the tags one
also fails the perturbed byte comparison); keeping openpyxl's save time
fails `test_the_workbook_carries_no_wall_clock_time` (byte comparison
alone cannot catch it -- two exports inside one second match); joining
every version instead of the current one fails
`test_the_bundle_names_the_version_it_embedded_and_hashes_it`.

**The standalone export is a `GET`**
(`GET /orgs/{org}/list-definitions/{id}/export`, `?version=n`),
rendered in memory, audited as `list.export` with the version id, and
returned with `X-WinGRC-List-Version-Id`. An assessor exports; the same
role's `POST …/versions` with a current base is still a 403. The list
page has *Export vN (.xlsx)* for the version on screen, enabled for
read-only roles.

**Not changed:** `control_state`, SPRS, evidence. A list in a bundle is
not a score (`test_lists_in_a_bundle_do_not_move_the_score`).

Bench (`wingrc_verify_20261014l3`): backend 1685 passed in one process
with the 500 floor, ruff clean, `tsc -b`, vitest 374, build. With the
real 45-list library imported and an assessment started (SPRS −204): a
bundle choosing the merged device template and the risk register carried
exactly those two workbooks; both appear in `artifact_log.txt` with
hashes matching the files; the cover's Hash Value is the SHA-256 of that
log; `lists/index.html` names both version ids. The standalone export was
byte-identical twice and equal to the bundle's copy. After an edit (v2),
the first bundle's copy still equalled an export of v1, and a new bundle
named v2 and not v1. No `list_id`, no list files. Assessor: export 200,
edit 403. SPRS −204 throughout.

---

### No workbook evaluates what someone typed ✅ DONE (2026-10-14)

No migration. L.3 found that a list cell starting with `=` became a live
formula in the assessor's Excel, and fixed it in the one renderer it was
writing. That made it a property of one renderer, not of the product,
and only of `=`.

**Every workbook leaves `render.py` through `_save()`**, which writes
any string cell starting with a formula prefix -- the OWASP set: `=`,
`+`, `-`, `@`, leading tab, leading carriage return -- as text with
Excel's *quote prefix*. openpyxl writes `=...` as a formula outright;
the others it stores as text, but Excel re-reads them as formulas when
the cell is edited, or when the sheet is saved as CSV and opened again.
With the quote prefix the cell is shown as typed and stays text on edit.
The value is not altered: the CSV advice (prepend an apostrophe) would
change the record an assessor is handed, and a list version's bytes are
a function of its rows.

**Which workbooks:** all of them. `render_view_bytes` (the four
scope-drawn lists, `GET …/exports/{view_id}`, and the CLI's `render`)
and `render_list_version_bytes` (the list export and the bundle's
`lists/`). Nothing else in the app builds a workbook, and nothing else
in the bundle is a spreadsheet the product wrote: evidence files are
embedded as uploaded -- they are the customer's artifacts, hashed as
received, and rewriting one would break that.
`tests/test_workbook_formulas.py` pins both: a payload in every
attribute of every scope view (a Notes field, a name) and in a list
version's title, headers and rows; no `<f>` element in any sheet; and
a static check that `render.py` calls `.save(` once, inside `_save`,
and that no other module builds a workbook. Over HTTP,
`test_a_cell_is_text_never_a_formula` checks the list export, the
bundle's copy of it, and a scope export.

**Mutation-tested** with a `+cmd|' /C calc'!A0` family of payloads, each
mutant against `test_workbook_formulas.py`, `test_lists.py` and
`test_list_bundle.py` and then restored: no pass at all (openpyxl's
default), `=` only (the L.3 fix), the type without the quote prefix,
and the set without carriage return each fail every renderer's test
(the four scope views and the list version); `render_view_bytes`
saving around `_save` fails the scope tests and the single-save check.

**On live** (read-only count, 2026-10-14): no list-library cell, title
or header starts with a prefix, so live list exports and bundles keep
their bytes. One scope attribute does -- a person's *Phone*, `+1…` --
which is exactly what this is for: Excel would have taken it for a
number or a formula on edit.

Bench (`wingrc_verify_20261014f`): backend 1692 passed in one process
with the 500 floor, ruff clean (no frontend change). The HTTP test was
mutated there too: with the prefix set cut to `=` it fails on the
`+cmd|' /C calc'!A0` cell (`quotePrefix` false); restored, it passes.

---

### List library L.2.1: the index keeps the kinds of list apart ✅ DONE (2026-10-14)

No migration. L.2's live check found the symptom: two device lists three
rows apart in one flat index, and the one labelled `3.1.1c` was the one
serving a single practice. The cause was that the index treated a
**sourced list** (live from scope; its rows are real devices in a real
boundary) and a **template** (its rows are examples) as siblings.

- **Three sections, one table** (`ListLibraryPanel.tsx`,
  `lib/listLibrary.ts`'s `kindOf`): *Live from scope*, *This org's lists*,
  *Templates*, each with a one-line statement of what its rows are. The
  middle one is where an adopted template copy will land; it shows,
  empty, with that sentence, so the place exists before adoption does.
  Family, practice and search filters cut across all three, and a
  section with no matches hides while filtering.
- **The merged template is named for its breadth**:
  *Authorized Devices & Hardware Inventory*. The merge is WinGRC's
  construction, so the name is recorded on the merge rule
  (`list_templates.DEVICE_MERGE.title`), not typed into the content.
- **Device Type and Baseline Ref sit after Model** (`added_after`),
  columns 5 and 6, one fixed order for everyone. Beside Asset Type was
  tried first, on the real sheet in a browser: that is column 11 and
  still behind the horizontal scroll, so it did not meet the
  requirement. A placement whose anchor column is missing raises, like
  an unmapped column: a merge is never guessed.
- **Not done, on purpose:** no column order that depends on the practice
  the viewer arrived from (a record has one answer), and no example rows
  written into the empty CM columns. Those rows are Jarrod's content; the
  merge box says why they are empty (the examples came from the 3.1.1c
  sheet of his workbook) and that he can fill them with Edit rows.

**Landing on an org imported before this.** A rerun of `lists-import`
proposes exactly one `new_version` (the merged list: title, columns,
rows; the earlier version untouched) and 44 unchanged. On the bench the
real library's rerun did exactly that
(`test_the_merged_list_rename_lands_as_one_new_version` pins it). On live
that is a write to a Winsorlabs template, so it runs only when Jarrod
says so.

Bench (`wingrc_verify_20261014`, real 45-list library): backend 1675
passed in one process with the 500 floor, ruff clean, `tsc -b`, vitest
371, build. In a browser, the index shows the live `3.1.1c Authorized
Devices` under *Live from scope* and *Authorized Devices & Hardware
Inventory* under *Templates* with its three practice chips, without
opening either.

---

### List library L.2: the screen, and editing ✅ DONE (2026-10-13)

No migration. Spec: `docs/PLAN-list-library.md` §6.
`frontend/src/components/ListLibraryPanel.tsx` and `lib/listLibrary.ts`.

**One place called Lists.**

- Library › Lists is the only Lists entry.
  - Until now there was also a Scope › Lists (the four scope-drawn
    ListViews).
  - The Library entry was only a pointer to it: two features shared one
    word, and that is what confused people in the first place.
- Scope › Lists is gone. The four scope-drawn lists appear on the one
  screen, marked *live from scope*. They are still edited where entities
  are edited.
- A library list whose `projection_view_id` names one of them links to it.
  The library list is kept by hand, and the scope list follows the scope
  graph. Joining them is L.6.
- The `list` document type is no longer offered: the UI drops it, and the
  API refuses a new one, pointing at Library › Lists. No live document
  had used it.

**Browsing.**

- Family chips, a practice filter and search.
- A list is one entry in each family and practice view it belongs to, and
  one entry overall. Its tags are visible, and clicking one filters by it.
- Each list shows its version, when it last changed, and who saved it, or
  "template import".
- Badges: *template*, *changelog* (the 8 with provenance; the text is
  readable on the list) and *merged*.
- The merged device list states that it is one list serving its practices,
  and shows the recorded column mapping with the added columns marked.
  The mapping comes from `list_templates.MERGES`, matched by
  `provenance.merged_from`.
- The screen says what is **not** in the library, from the last import's
  record:
  - 9 sheets not imported: 5 needing normalization and 4 non-conforming
    files;
  - the merge, listed separately because it is not missing;
  - the 6 archived files, not ingested by decision.
- That record is a `list.import` audit event the CLI now writes on every
  apply, a no-op one included. `GET …/list-library/import-report` reads
  the latest. There is no second store: the import is an audited action,
  and its audit row is the record.

**Editing, and what the second editor sees.**

- Add, edit and remove rows, then "Save as version n+1", with an optional
  change note.
- The version records who saved it. The audit row (`list.version_create`)
  records rows added and removed, by content.
- A save on a stale base gets a structured 409 naming the newer version,
  who saved it, and when. The second editor sees:
  - **"Version 4 was saved by [name] at [time] while you were editing
    version 3. Your edit was not saved, and it is still here."**
  - What the other editor changed: rows added and removed.
  - How many of their own changes re-apply exactly.
- The draft tracks where each row came from (`origin`). Re-applying is
  therefore exact for every row the other editor did not touch.
- A row both editors touched is listed, not guessed: "left as they have
  it". An edited one can be added as a new row by ticking a box.
- "Re-apply my changes onto version 4" puts the result in front of them
  to review. Saving it is an ordinary save onto version 4.
- Save is disabled while the conflict is open, because the base is stale
  until they re-apply or discard. Found in the browser walkthrough: it was
  enabled, and could only have 409'd again.
- `c3pao_assessor`: "Edit rows" is disabled and says why. The API returns
  403 if bypassed.

**Verified on the bench, against the real library** (a fresh import of
Jarrod's `CMMC Prep` folder into a bench org):

- 45 created, then 0 changes on the rerun, with the same per-status counts
  as the 2026-10-10 live import.
- Over HTTP:
  - Each of the merged list's three tags reaches it once.
  - A second engineer's stale save gets 409 naming the first; version 1 is
    byte-identical afterwards (2969 bytes); a direct `UPDATE` is refused
    by the trigger.
  - The assessor reads every endpoint, and gets 403 on write.
  - Another org gets 403 on this org's paths and 404 on a list id through
    its own, with an empty library of its own.
  - SPRS (−204) and all 320 control states are byte-identical across an
    edit, on an org with an assessment.
- In a browser, as two engineers in two tabs: B saved version 4 while A
  was editing version 3. A got the message above, re-applied, and saved
  version 5, which holds both edits.
- Suite and checks: full backend 1673 passed, ruff clean, `tsc -b` clean,
  vitest 368 passed, build clean.

**What the real library made obvious that the dry-run counts did not:**

- **No list in the real library spans three families.**
  - 8 lists carry three tags, but each sits within one family, except the
    merged device list, which spans two.
  - So "reachable from all three family views" is proven in unit tests
    (`lib/listLibrary.test.ts`, `ListLibraryPanel.test.tsx`). On real data
    it holds for the device list's two families and three practices.
- **The merged device list carries `CM.L2-3.4.2` as well as
  `CM.L2-3.4.1`.** The absorbed 3.4.1a sheet names both, so the list
  serves three practices, not two.
  - It reads as one list. But it is 19 columns wide, and 3.1.1c's
    `Asset Type` (a CMMC category) sits beside the merge's `Device Type`
    (a device class).
  - Both decisions are recorded (§8, 2026-10-10). Whether an assessor
    wants that union is still Jarrod's call, not a bug.
- **Ordering by list key scattered the families** (PE before RA before
  AC). The index now sorts by the standard family order, then practice
  number.

**Live (2026-10-14).**

- **Deploy.** `3042b07` deployed with `deploy/deploy.sh`, after a host
  reboot.
  - The host came back clean: five services up, IP still .35, no bench
    containers, `/health` ok at the prior build.
  - Scans: backend 296 findings, nginx 179.
- **Database checks, read-only.**
  - `pg_trigger` shows `list_version_no_update` (BEFORE UPDATE, enabled).
  - The backend's own session reports `current_user = wingrc_app`
    (no superuser, no BYPASSRLS).
  - RLS is on for all three list tables.
- **Winsorlabs, read-only, against the real 45 lists.**
  - The import rerun proposed 0 changes. Its no-op apply wrote the first
    `list.import` record, which the screen reads.
  - The merged device list is reached once from each of its three
    practices.
  - An assessor reads every endpoint and gets 403 on a write whose base
    was current, so only the role could have refused it.
  - Acme and Test Customer A engineers get 403/404.
- **Edit checks: one small list, created by importing a one-sheet library
  into Test Customer A.**
  - Winsorlabs templates were not touched. Deleting an org on live was
    ruled out: that path is unexercised, and inventing it for a test is
    the bigger risk.
  - Over HTTP as `wingrc_app`: two engineers. A saved v2. B's stale save
    got the structured 409 naming A, and nothing was written. v1 is
    byte-identical afterwards (678 bytes). A direct `UPDATE`, as the
    owning role inside a rolled-back transaction, was refused by the
    trigger.
  - In a browser, with the deployed frontend's own files: B saved v3
    while A was editing v2. A saw the conflict, with their draft intact
    and Save disabled, re-applied, and saved v4 holding both edits.
  - The test list is tagged `AC.L2-3.1.4 / AU.L2-3.3.9 / CM.L2-3.4.5`
    (none of the real lists span three families). It appears once in each
    of the AC, AU and CM views.
  - **The test list ("Verification rows", 4 versions) is left in
    Test Customer A on purpose.** It goes when live is rebuilt.
- **Counts.**
  - SPRS is unchanged: Acme −199, Winsorlabs −204.
  - Control states are unchanged.
  - Every other difference is the test list, two `list.import` events and
    three `list.version_create` events.
- **Cleanup.** Throwaway identities and the predeploy dump were deleted
  after the checks.

**What a person sees on the merged device list (live, observed).**

- The list's own page reads as one list serving three practices. It
  leads with "One list serving 3 practices in 2 families", then the three
  practice chips, then a boxed explanation and the column-mapping table.
- **The index weakens that claim.**
  - Two device lists sit three rows apart: *3.1.1c Authorized Devices*
    (live from scope, `AC.L2-3.1.1` only) and *Authorized Devices*
    (template, three practices).
  - Only the one serving a single practice carries the "3.1.1c" id.
- **In the rows, the CM half is two empty columns at the far right.**
  - `Device Type` and `Baseline Ref` sit seven columns after
    `Asset Type`, behind a horizontal scroll.
  - The example rows are all 3.1.1c's, so a reader arriving from
    `CM.L2-3.4.1` sees an access-control device list with two blank
    columns.
- None of this is wrong in the data. Whether to change it (a combined
  title, CM example rows, column order) is Jarrod's call, not a fix taken
  here.

---

### CI triggers on branch pushes only; one run per commit ✅ DONE (2026-10-10)

**What happened.** Commit `fdf6747` (PR #17) ran CI twice, once for the
branch push and once for the `pull_request` event:

- The concurrency group (Sep 30) **cancelled** the push run.
- The pull_request run's `backend` job **failed** in its `Test` step,
  while `integration` passed.
- At the time it did not reproduce, either locally or in that job's
  no-database configuration on Linux (543 passed). It was first recorded
  here as unexplained. The next commit (`bfbf96f`) was green, because it
  rewrote the test.

**Cause, resolved 2026-10-13: the test asserted on Rich-rendered text, and
CI renders it differently.** The failing test checked
`"org-id" in result.output` on `lists-import` run without `--org-id`. That
text is Click's usage error, rendered by Typer through Rich.

- When `GITHUB_ACTIONS` (or `FORCE_COLOR`/`PY_COLORS`) is set, Typer forces
  terminal mode (`typer/rich_utils.py`, `FORCE_TERMINAL`).
- Its option highlighter then styles `--org-id` hyphen-segment by segment:
  `-`, `-org`, `-id`, each wrapped in its own ANSI escape. The substring
  `org-id` no longer exists in the output.
- Nothing else sets that variable, so every reproduction outside Actions
  printed plain text and passed.
- Reproduced 2026-10-13 with typer 0.26.8 and rich 15.0.0:
  - with `GITHUB_ACTIONS=true` the assertion fails at 80, 60 and 40
    columns;
  - without it, it passes at all three.
- Width plays no part: the panel line is 26 characters and never wraps.
  The 80-column panel in the trace is Rich's default width in either
  case.

The product was right: the command refused with exit 2, as intended. The
test was wrong, in a way only the CI environment could expose.

**The inverse of the reported-green incidents.** Those signals said green
while checking nothing. This one said red while the code under test was
correct, because the assertion depended on the environment. Part of the
noise was the setup's fault too: every PR commit carried a cancelled check
by design, which reads as trouble and isn't. The Sep 30 entry below
predicted that wrinkle. A throwaway PR probing the dual trigger at the time
would have shown it before a real merge did.

**Sibling sweep (2026-10-13).** Every other `CliRunner` assertion
(`test_cli_reset_dev.py`, `test_cli_rotate_credential_keys.py`) checks text
the command prints with `typer.echo`. That text is Click's plain output,
which Typer's highlighter never touches. Only Typer's own error and help
rendering goes through Rich. The one assertion on Rich text was this test,
and it has been rewritten to check the exit code and that the import is
never reached. Empirically, the no-DB suite passes with
`GITHUB_ACTIONS=true COLUMNS=40` (543 passed). The integration job already
runs those CLI tests under `GITHUB_ACTIONS` and passes. The rule: assert
on exit codes and effects. Assert on rendered CLI text only when the
command itself `echo`es it, never on Click/Typer's own messages.

**The change.**

- `on: push: branches: ['**']`; `pull_request` is removed.
- One run per commit, and the push run's checks land on the PR's head
  commit, which branch protection reads.
- The concurrency group stays, as a safety net for rapid pushes, not as
  the mechanism that picks a survivor.

**Rejected:** `push: [main]` plus `pull_request`. That is exactly the
configuration the Sep 30 entry below abandoned, because a pushed branch
then gets no run until someone opens a PR. Every slice since has been
CI-verified on its branch before a PR existed.

**Accepted costs:** fork PRs get no CI, and CI tests the branch head, not
GitHub's speculative merge with `main`.

**Verified on the change's own branch:**

- Commit `1d16039` produced one run (event `push`) and exactly three
  check runs (`backend`, `integration`, `image`), all success, none
  cancelled.
- Commit `0b32289`'s run **was** cancelled. It was superseded 20 seconds
  later by the push of `3478b1b`, which was green and was the PR's head.
  That is the safety net doing its job on a rapid push. It is not a
  duplicate run of one commit. (It was reported at the time as "none
  cancelled", which was wrong.)
- PR #19, once opened and merged: no `pull_request` run appeared, and the
  merge commit `e636fd8` had one `push` run, green.

---

### List library L.1: the model and the template importer ✅ DONE (2026-10-10)

Migration 0064. Spec: `docs/PLAN-list-library.md`.

**The inversion, and the evidence for it.** `catalog.py` treats a list as a
projection over the scope graph. That holds for roughly ten of the 83
sheets in Jarrod's library. The other seventy are standalone registers (the
Change Log, Incident Log, Risk Register, POA&M, Visitor Log, Sanitization
Log, Training Roster) whose rows exist nowhere else.

So a list is now a **definition plus its own rows**:

- `list_definition` holds identity.
- `list_version` holds content.
- `list_control_tag` holds the practice tags.
- "Projected from the scope graph" is a property some lists have
  (`projection_view_id`, metadata only until L.6), not the premise of all
  lists.

The four `ListView`s and `/orgs/{org}/lists` are unchanged. The new API
lives at `/orgs/{org}/list-definitions`. The clearest single piece of
evidence came from real data: `3.1.1a`'s rows are accounts, not people
(plan §2a).

**Versioned from this migration.**

- `list_version` is append-only, and a database trigger rejects any
  `UPDATE` of it.
- Editing rows (`POST …/versions`) creates a version, names the version it
  was based on, and is refused with 409 if that version is stale.
- The prior version stays byte-identical, which is tested.
- Rows carry no identity key, so a `[PLACEHOLDER - reason]` cell is stored
  verbatim and can never become one.

**The template convention is the import contract** (plan §3):

- Row 1 is the title. It is merged and sits in column C, which is why a
  naive A1 scan reports zero conformance.
- Row 2 holds the practices, multi-valued, with the family written on the
  first id only.
- Row 4 is the description; row 5 holds the responsible party and the
  cadence.
- Row 9 is the section title, row 10 the columns, and row 11 onwards the
  rows.
- **The unit is a sheet, not a workbook.** `Hardware and Software Asset
  Inventory.xlsx` is two lists.

`wingrc lists-import <library> [--apply]` is a dry-run by default. It
targets the MSP org from `deployment_settings` and runs inside that org's
RLS.

**Real figures** (dry-run over Jarrod's `CMMC Prep` folder, 2026-10-10):

- 44 workbooks, 83 sheets.
- **45 lists importable**, including the merged device list.
- **5 need normalization**, which the importer reports and does not
  flatten:
  - multi-section `3.1.1a`, `3.1.2a` and `3.1.5a` (26–28 sections each:
    the tool is a column value, not a list boundary);
  - wide `3.1.5c` (a repeated column triple);
  - wide `3.13.3ab` (17 side-by-side sections).
- **So 50 distinct list definitions among the parseable sheets.** The
  plan's estimate was 45–50 across all 83 sheets; that was inference from
  column analysis. 50 is the fact, and it excludes the four unparseable
  files below, which hold at least two or three more lists once
  normalized (3.1.7a alone collapses 26 sheets into one).

**Four non-conforming files**, reported by name and not parsed (no second
parser is written for them):

- `AC/3.1.15/CMMC_3.1.15_Remote_Privileged_Execution.xlsx`
- `AC/3.1.20/CMMC_3.1.20_External_Connections.xlsx` (also superseded; see
  below)
- `AC/3.1.7/CMMC_3.1.7a_Privileged_Functions.xlsx` (26 sheets, one per
  tool)
- `MA/CMMC_372_Controls.xlsx`

**The three settled decisions, applied:**

1. **`New Lists` imports as v1.** Changelog markdown is attached verbatim
   as provenance on the 8 lists a changelog names. The six `Archive/Lists`
   files are listed in the report as deliberately not ingested: importing
   May as v1 and June as v2 would assert a two-step history that did not
   happen.
2. **3.1.20:** `External Systems and Connections.xlsx` wins. The `CMMC_`
   file is non-conforming anyway, so the rule is consistent rather than a
   special case.
3. **The device lists are one list**, tagged `AC.L2-3.1.1`, `CM.L2-3.4.1`
   and `CM.L2-3.4.2`:
   - The union turned out incoherent as a *literal* union: the same facts
     under different names and granularity. That stopped work and went to
     Jarrod.
   - His choice: 3.1.1c's finer-grained schema plus `Device Type` and
     `Baseline Ref`, by an explicit column mapping (plan §8).
   - An absorbed column with no recorded mapping fails the import, so a
     merge is never guessed.

**`--org-id` is required; there is no default target.** The first version
defaulted to `deployment_settings.msp_org_id`, which on the live
deployment names **Acme MSP**, a leftover demo org. Jarrod confirmed
Winsorlabs is the real MSP. A run without the flag would have put 45 MSP
templates into the demo tenant; it was caught only because the target was
checked before the first live run.

This is the same shape as every "older writer routing around a newer
gate" defect, applied to targeting rather than fields. The command now
refuses to run without `--org-id` and prints the target org's name before
acting. The setting itself is not changed here: see the readers audit.

**Imported lists are MSP templates** (`is_template`, in the MSP org). A
client copy will record its template version in
`template_list_version_id`, reserved now.

---

### One natural key for every importer, and source precedence ✅ DONE (2026-10-09)

Found by a live dry-run of Jarrod's populated workbook, which would have
duplicated WL-DT26 and Jarrod Winsor (see "Workbook importer: scope" in
Planned).

- **`app/natural_key.py` is the one key derivation.**
  - Devices are keyed by serial, then hostname. A serial counts as absent
    when it is blank, a `[PLACEHOLDER…]` cell, or an OEM placeholder,
    including an annotated one ("System Serial Number (not set by OEM - …)").
  - People are keyed by email, then username, then name.
  - The Liongard and workbook importers both call it. The placeholder
    vocabulary moved there from `importers/liongard.py`; migration 0058
    keeps its own frozen copy, as migrations must.
  - `tests/test_natural_key.py` asserts both importers produce the same key
    for the same device, WL-DT26 verbatim included. A row with no usable
    identifier is skipped and logged, never keyed by placeholder text.
- **A name-keyed person never merges into an email-keyed one.** The keys
  differ, so reconcile cannot match them. The workbook dry-run adds a
  "possible match, not merged" warning for a person to confirm.
- **Source precedence.**
  - On a connector-sourced row (Liongard, Datto RMM, Entra), an import
    from a non-connector source never overwrites a field the connector
    supplied: the canonical device/person vocabulary, minus
    `responsible_contact_id`, which no connector supplies. It never takes
    over the row's `source` either. Enforced in `repo.upsert()`.
  - The dry-run drops each such field from the diff and reports it as
    "Conflict, not applied".
  - Fields only the workbook supplies (the overlay, Asset Type, owner)
    still land.
- **MISSING rows are never acted on.** This is now pinned by a test that
  applies a dry-run's *full* change list, missing rows included.
- **"Workbook wins" made to stick.** Checking what an apply of the real
  file would *write* showed three gaps, all fixed here:
  - The workbook's Location, In Service Date, Decommissioned Date and
    Requested By lived only under raw column keys. The next same-source
    Liongard refresh dropped them. They now also land under the protected
    overlay keys.
  - A workbook write onto a Liongard row replaced Liongard's raw record.
    Cross-source writes now merge; same-source writes still replace, so a
    connector's raw record refreshes rather than accumulating stale keys.
  - The dry-run hid overlay changes behind the device allowlist. Reconcile
    now shows overlay fields an import actually carries.
- **A stated gap is never a value.**
  - `[PLACEHOLDER - still in service]` in Decommissioned Date used to
    decommission the device. It no longer does.
  - Placeholder cells, and an OEM placeholder serial as `asset_tag`, no
    longer become canonical attributes. The raw cell keeps the reason.
  - A conflict is reported only when the import *states* a different
    value. Silence is not disagreement.
- **Real file, re-checked after the fix** (offline, in memory, against
  Liongard-shaped copies of live's devices):
  - WL-DT26 and PF3Y6K26 match as CHANGED, not NEW+MISSING.
  - The two placeholder-serial devices key by their names (`WL-HV1`,
    `jarrod.winsor_Android (personal/BYOD)`).
  - The only conflicts are the OS spellings. The only applied changes are
    workbook-only fields and genuinely new values.
  - The users tab is unchanged in scope: it stays L.1's.

---

### Document library N.3: approval and the review cadence ✅ DONE (2026-09-30)

Migration 0062. Turns the library from "you can write policies" into
"policies are approved, and re-reviewed on a schedule", which is the reason
a compliance product has one.

#### §0 The record design, and why this shape

**`document_approval`** — append-only, one row per approval decision,
referencing the `DocumentVersion` it is about, with
`approval_type IN ('initial', 'reaffirmation')`.

Re-approving an unchanged document must not create a new
`DocumentVersion`, and the reason is N.2: a version whose body is
byte-identical to its predecessor fakes an edit, and it would answer "what
changed in this policy" with noise for every annual review that changed
nothing. But re-approval is a real, durable event — "reviewed and still
current on <date> by <person>" is precisely what a periodic-review control
wants to see. So the decision is recorded separately from the thing decided
about, the shape `AssetApproval` already uses for scope entities and
`ReviewCycleReviewer` for attestations.

Chosen over the alternatives:

- **A `reaffirmed_at` column on `DocumentVersion`.** Holds one
  reaffirmation, not a history, and a periodic-review control wants the
  history. It also mutates a row that N.1 declared immutable except for a
  named set of approval fields, which is the kind of exception that grows.
- **A generic audit-derived answer** (read `audit_log` for
  `document.reaffirm` events). N.2 deliberately surfaced document history
  from `audit_log` rather than building a parallel table, so this was the
  consistent-looking option. Rejected because `audit_log` is a record of
  *actions by authenticated users*, and the approver is frequently not the
  actor and often not a user at all (see §2). Deriving a compliance fact
  from the audit trail would also make it depend on retention policy, and
  `audit_log.org_id` is nullable, which the RLS work already showed is a
  sharp edge.
- **Storing `next_due_at` on the approval row.** See the overdue section:
  it would go stale on any cadence change.

**Exactly one `initial` approval per version**, enforced by a partial
unique index (`uq_document_approval_initial ... WHERE approval_type =
'initial'`) rather than by application code, because it is a real
invariant. Reaffirmations are unconstrained — each is a distinct event, and
two in one day is odd but not wrong.

#### What the existing N.1 columns now mean

`DocumentVersion.approved_at` / `approved_by_contact_id` are now **a
denormalized snapshot of the initial approval only**. They are kept because
`publish_document` sets them and because the bundle and the SSP PDF already
read them, and they are documented in the model as **not authoritative**.

Nothing reads them to answer a cadence question, and that restriction is
the point: they know nothing about reaffirmations, so a document
re-approved four times still shows its original `approved_at` there. Every
"when was this last reviewed" read goes to `document_approval`.

The rejected alternative was the tidy-looking one: leave the columns as
"the first approval" and let the new table carry "subsequent approvals".
That is the `sourced_from_product_id` mistake — two places that both look
authoritative, where a reader has to already know which holds what. One
place answers every approval question; the columns are a cache of one row
in it and say so, and
`test_initial_approval_agrees_with_the_denormalized_version_columns`
asserts the two actually agree rather than trusting the comment.

#### §2 The cadence tracks the approver, not the actor

Two people, deliberately not merged, and N.3 had to pick which one the
cadence follows:

- **The named approver** (`Contact`) is whose decision satisfies the
  cadence. N.1 chose `Contact` because the person granting approval — a
  customer's President signing a policy — is often not a WinGRC login at
  all. `approver_name` is denormalized at decision time and the FK is `ON
  DELETE SET NULL`, so the record survives the contact being deleted.
- **The authenticated actor** who recorded it is captured by `audit_log`
  via `log_event`'s actor resolution, and is deliberately **not**
  duplicated onto the approval row. `document.reaffirm` events carry the
  approver in their context so both facts are visible together, and
  `GET .../history` already shows the actor.

These are usually different people. The UI says so in as many words —
"Reviewed and approved by" names the Contact, and the hint states "You are
recorded separately as the person who entered it — see History" — because
this is exactly the distinction a UI quietly loses, and the
no-one-click-approval-links rule means someone authenticated always records
a decision they may not have made.

#### §3 Cadence, who is notified, and the digest shape

`Document.cadence_months` (default 12) is a **third, distinct** cadence:
not `Organization.review_cadence_months` (the per-org users/devices review
cycle) and not `ReviewCycle.cadence_months`. Kept distinct in the UI too —
the document one appears only on a document, labelled as that document's
review cadence.

`scheduler.py:_document_review_digest`, daily, per-org over
`auth.orgs_with_documents()` (a SECURITY DEFINER function, because
`document` is RLS-protected and cross-org discovery needs the same
mechanism `auth.orgs_with_liongard_mapping()` uses — not a blanket bypass;
the job still calls `set_current_org` per org).

**Recipients: each due document's own named approver, plus every contact
holding `security_officer` or `it_admin`.** The approver because the
control is "the named approver reviewed this", so telling anyone else first
is indirection; the role holders because that is D.3's routing precedent
and because it is what makes a deleted or never-set approver not mean
silence. Deduplicated by contact, with the reason recorded ('approver'
beats 'role', being the more specific fact).

**"Nobody holds that role" is counted and logged, never swallowed** —
`orgs_with_no_contact`, exactly as `_liongard_daily_sync` does. That
specific bug has been fixed twice in this codebase, so it is the function's
documented contract rather than an edge case.

**A digest, one email per recipient per org per run**, never one per
document: an MSP with forty tenants and a dozen policies each would
otherwise emit hundreds per sweep, which is how a notification channel
becomes noise. The body names **no document, count, or org** —
`email_service.py`'s content rule is a nudge plus a link, because mail
leaves the deployment's trust boundary. The count is recorded server-side
on `document_review_notification.document_count`, and a test asserts the
document id and title do not appear in the body.

**Reuse of `review_cycles.py`, since cross-cutting rule 6 asked.** The
*machinery* is reused: `email_service.send`, the digest shape, and the
`notified_at`-set-once / `notification_error`-always-current discipline,
which is a third verbatim copy rather than a cleverer abstraction because
the rule is the thing worth keeping identical. The *tables* are not, for
two concrete reasons:

1. `ReviewCycleReviewer` is `cycle_id`-scoped to a `review_cycle`, which is
   the org's users-and-devices attestation cycle with its own snapshot
   items, response window and attestation document. Reusing it would mean
   inventing synthetic cycles that attest to nothing.
2. Its reviewers come from `auth.org_reviewer_candidates()` and are
   **Users**. Document approvers are **Contacts**, often with no login.

D.3 hit exactly this and answered it the same way —
`LiongardSyncNotification` is a separate table of identical shape. This
follows that precedent rather than distorting the review cycle.

#### §4 Overdue flags; it never invalidates

Passing the cadence changes **nothing**: the `Evidence` row stays, its
`EvidenceStateLink`s stay unarchived, `control_state.status` is untouched,
and the SPRS score does not move. Auto-detaching evidence on a date would
silently change what an assessment rests on with no human decision behind
it — the failure this codebase refuses in `sprs_snapshot` (never
rewritten), baseline versioning (a reimport never touches an activated
tenant) and bundles (frozen at export). An overdue policy review is a
*finding*; a finding is something a person looks at, not something a clock
enacts.

**Derived, never stored.** No `next_due_at` column and no `overdue` flag:
`document_reviews.review_state` computes the verdict from the newest
approval plus `cadence_months` on every read. A stored due date would go
stale the moment an operator changed the cadence — a document moved from
annual to quarterly should become due three months after its last
approval, not on a date computed under the old policy. There is a test for
exactly that, changing only `cadence_months` and watching the same approval
flip from `current` to `overdue`.

Four statuses, and `never_approved` is deliberately distinct from
`overdue`: a document that has never been approved has not lapsed, it has
never been through the control at all, and an operator does something
different about each.

**Visible in three places**, per §4: every `DocumentOut` carries the
verdict (so the library list shows it without a second round trip, and a
field the list already has is harder to forget to render), the document
detail shows it with dates, and a dashboard card puts it where an engineer
running an assessment will see it. The card says in words that an overdue
review is a finding rather than an expiry. `review_status=needs_attention`
filters the list.

**Raised, not settled** (as instructed): whether overdue review state
belongs in the SSP narrative or the exported bundle. Nothing was added to
either.

#### §1 `under_review` now has a workflow, and approval still attaches to a version

`under_review` stops being decorative as the state a draft sits in while
awaiting approval, with publishing from it being the approval path.
Deliberately **not** used to mark an approved version as being re-reviewed:
that would mutate an approved artifact's status, and the periodic-review
state is derived instead, which is also what lets the scheduled job change
nothing.

Editing an approved document still creates a draft and leaves the approved
version exactly as approved — already true, and now asserted here too
(`test_reaffirm_leaves_the_approved_version_byte_identical` snapshots body,
status, version number and both approval columns), because approval
workflows are where someone eventually adds a convenience path that mutates
in place.

#### A pre-existing defect found by the tests

`DocumentVersion.approved_by_contact_id` was declared in N.1 with **no
`ON DELETE` clause** — the only nullable contact FK in the schema without
one, where `ReviewCycleReviewer.user_id`,
`SprsSubmission.submitted_by_contact_id`,
`LiongardSyncNotification.contact_id` and `AssetApproval` all use
`SET NULL`. The effect: deleting a contact who had ever approved a document
raised `ForeignKeyViolation`, so it could not be deleted at all, which
breaks ADR 0006's anonymize/hard-delete path outright.

Found by a test in this slice deleting an approver, not by reading the
schema. Fixed to `SET NULL` in 0062; nothing is lost, because
`document_approval` is authoritative and denormalizes `approver_name`.

#### §6 The 28 over-marked tests — and the premise was wrong

The retag happened: `integration` moved off the module and onto the tests
that need a database in four files, and `-m integration` dropped from 1027
to 999 tests accordingly.

**But the stated benefit does not exist, and measuring it is what showed
so.** The premise — mine, carried into this slice's brief — was that those
28 were excluded from the ~44s no-DB job, so retagging would recover free
coverage. It does not. **A marker never causes a skip; the `db_session`
fixture does**, by calling `pytest.skip()` when `WINGRC_TEST_DATABASE_URL`
is unset. CI's `backend` job runs plain `pytest -q` with **no `-m`
filter**, so those tests already ran there.

Verified rather than argued: the no-DB total was **458 before and 458
after**, and one freed test PASSES (not SKIPS) with no database on the
pre-retag tree.

What a misplaced marker actually breaks is **selection, not coverage**:
`-m integration` ran 28 tests needing no database, and
`-m "not integration"` dropped tests that would have run fine. Still worth
fixing — the markers now mean what they say — but it is accuracy, not
recovered coverage, and `CLAUDE.md`'s bullet from 2026-09-29 has been
corrected where it claimed otherwise.

#### Verification

Bench, CI-shaped `python:3.13-slim` against a `postgres:18` service:

- **Migration chain** `upgrade → downgrade → upgrade` clean, including the
  backfill and the FK swap.
- **`backend` job (no database):** `ruff` clean, **473 passed** (458 + 15
  new pure cadence tests), floor 200 holding.
- **`integration` job:** **1030 passed, 1 skipped** (999 + 31 new), floor
  500 holding.
- **Frontend:** `tsc -b` clean, **336/336** vitest across 27 files
  (330 + 6 new), `vite build` clean.
- Two errors caught by the toolchain rather than by review: the revision id
  `0062_document_approval_and_review` was 33 characters against
  `alembic_version.version_num`'s `varchar(32)` (there is already a test
  for that), and the first `ReaffirmControl` placement both broke JSX and
  sat inside the `canEdit` gate, which would have hidden the cadence
  verdict from `c3pao_assessor` — the role most likely to want it.

The tests are mostly **negatives**, because the design is mostly refusals:
no new version, byte-identical body, no new diff-history entry, no evidence
detached, no control state touched, no SPRS movement, and a scheduled job
that writes only notification rows. A test that only checked "a
reaffirmation row appeared" would pass against an implementation that also
quietly archived evidence.

---

### A silently-skipping test job now fails ✅ DONE (2026-09-30)

**This closes the third of three signals in this repo that reported success
while checking nothing.** The pattern is more useful than any one of them,
so all three together:

| Signal | What it reported | What it was actually checking | Guarded by |
|---|---|---|---|
| `/health` returning a static dict | container healthy | nothing — a completely unmigrated database passed | `/health` now runs `SELECT 1` and compares `alembic_version` against the head the image ships |
| `test_lifecycle.py`'s three-export comparison | bundle export reproducible | that rows happened to come back in the same order twice | totality guards in `tests/test_bundle_determinism.py`, static because the byte comparison could not catch a dropped tiebreak |
| The `integration` job with `WINGRC_TEST_DATABASE_URL` unset | green | nothing — every DB test skipped cleanly | **this slice** |

The shape is the same every time: **a green signal whose green does not
depend on the thing it is supposed to be checking.** The third was the
cheapest to trigger — one environment variable — and the only one that
could recur without anybody writing a line of code.

#### The mechanism, and why this one

A session hook in `backend/tests/conftest.py`, gated on
`WINGRC_MIN_TESTS_RUN`. It counts tests whose body actually executed and
fails the run when that falls below the floor.

Considered and rejected:

- **Parsing pytest's summary line** in a workflow step. Rejected because
  the summary's wording shifts with pytest version, with `-q`/`-v`, and
  with installed plugins, so the grep rots silently — and a guard that rots
  silently is the category of bug being fixed, not a fix for it. It also
  needs a second step to read output, and *reading output is precisely what
  was not happening*: raw CI log download requires `Actions: read`, which a
  watcher on a public repo does not have.
- **A workflow-level `--co -q` count compared against a floor.** Same
  output-parsing fragility, and it would not protect a bench run.

The hook fails the run in-process, so nothing downstream has to notice, and
it behaves identically in CI, on the bench, and locally.

**The floor lives in the environment rather than in a constant**, and the
variable's *value is the threshold*: presence enables the check. That makes
a narrow run (`pytest tests/test_health.py`) unable to trip it by
construction — the default is off and only CI turns it on — so there is no
list of "legitimate partial runs" to maintain and no way for the guard to
become the thing people route around.

**Counts executed, not collected.** A collected-but-skipped test is exactly
what is being caught, so counting collection would make the guard
self-defeating. A setup-phase error also counts as not run, which is right:
a fixture failing wholesale leaves nothing verified.

#### The floors, and why they never need touching

**200 for `backend`** (458 run there today) and **500 for `integration`**
(1027). Both under half the current count, deliberately. The guard answers
"did this run verify anything at all", not "is the count exactly right", so
it needs no edit when the suite grows, when tests move between the two
jobs, or when a slice adds a file. A floor that tracked the real number
would need updating every slice, would be updated carelessly, and would
eventually be lowered to make a red run go away — which is the failure mode
it exists to prevent. **If a floor failure appears, the run verified
nothing; do not lower the number.** `CLAUDE.md` says so where someone will
read it.

#### Mutation-verified, not assumed

Eight cases in the CI-shaped container. Both mutations fail on the first
attempt:

| Case | Tests run | Exit | |
|---|---|---|---|
| A1 `backend` job shape, floor 200 | 458 | 0 | passes unchanged |
| **A2 mutation:** select only DB tests that cannot run | 28 | **1** | floor trips |
| B1 `integration` job shape, floor 500 | 1027 | 0 | passes unchanged |
| **B2 mutation:** `WINGRC_TEST_DATABASE_URL` unset — *the actual incident* | 28 | **1** | floor trips |
| C1 narrow run, no floor set | 3 | 0 | does not fire |
| C2 narrow run **with** a floor set | 3 | **1** | proves the variable is the gate, not luck |
| C3 `--collect-only` with a floor set | — | 0 | exempt by design |
| C4 malformed floor (`twenty`) | 3 | **4** | fails loudly rather than disarming |

C2 matters as much as B2: without it, C1 passing would only show the guard
*didn't* fire, not that the environment variable is what stops it.

**An incidental finding, not fixed here:** both mutations report **28 tests
running with no database at all**, meaning 28 tests carry
`@pytest.mark.integration` without needing one. That is the mirror image of
the marker-scope problem fixed on 2026-09-29 — over-applied rather than
over-broad — and it means those 28 are skipped from the `backend` job for
no reason. Harmless, and the floor catches wholesale skipping regardless of
it, but worth a pass sometime.

#### Verification

Bench, CI-shaped `python:3.13-slim` container against a `postgres:18`
service: **458 passed** no-database and **1027 passed, 1 skipped** with a
database, `ruff` clean, **330/330** vitest across 26 files, `tsc -b` clean,
`vite build` clean. No frontend files changed; run because the slice's
verification list called for it rather than because it could plausibly
break.

#### Housekeeping closed out

- `/home/claude/.secrets/` removed from wl-util-1. It held **0 bytes** — no
  token ever landed there, so nothing was exposed. An empty `.secrets` path
  is worse than none: it implies a credential exists, so the next reader
  either hunts for one or assumes something is configured that is not.
- `ci-red-fix` deleted locally and on the remote, after confirming it was an
  ancestor of `origin/main` rather than assuming.
- The bench worktree at `/home/claude/bench/ci307` removed. Its files were
  root-owned by the containers that wrote them, so `git worktree remove`
  failed with `Permission denied` and it had to go via a root container —
  noting the mechanism because every bench worktree will have the same
  problem.

#### CI now runs on every branch push, not just `main`

**The reason is the routine, not the coverage.** "Watch CI go green" is the
step in the landing sequence that decays first, because it is the only one
that depends on a human being present at a particular moment: with
`push: [main]` plus `pull_request`, a pushed branch produced **no run at
all** until somebody opened a PR. So the verification step was gated on an
unrelated social act. It decayed exactly that way once already — six
consecutive red runs — and it recurred immediately in this slice, where the
work was finished, pushed, and bench-verified while CI had still never
executed it.

It also has no local substitute. The `backend` job's no-database
configuration exists nowhere else: the bench always has a database, so the
whole class of failure that produced runs #308-#312 is invisible outside
that job. A branch run makes the signal exist the moment work is pushed,
whoever is or isn't watching.

`pull_request` stays, for forks, whose branch pushes never reach this repo.

**Deduplication, and the bug in the obvious expression.** A same-repo PR
would otherwise run twice per push. A `concurrency` group collapses them,
but grouping on `github.ref` — the natural first guess — **does not work**:
it is `refs/heads/<branch>` on a push and `refs/pull/<n>/merge` on a
pull_request, so the two events land in two different groups and nothing is
deduplicated. The group uses `github.head_ref || github.ref_name`;
`ref_name` is the short branch name, and `head_ref` is set only on
pull_request and equals the source branch, so both events collapse onto one
group.

`cancel-in-progress` is false on `main` and true elsewhere: superseding is
right on a feature branch, but a follow-up push must never kill the run
whose result is the record for a merge commit.

The residual wrinkle, recorded in the workflow itself so a future blocked
merge is diagnosable in seconds rather than re-derived: the loser of that
collapse leaves *cancelled* check runs behind. The survivor is the later
run, so it is normally the one branch protection reads, but a merge blocked
by a check reading "cancelled" rather than "failed" is this, and another
push clears it.

#### Branch protection is now on

`main` requires status checks: **`backend` and `integration` required,
`image` deliberately not**, because `image` keeps
`needs: [backend, integration]` and a skipped required check counts as
satisfied. The merge path is a PR rather than a fast-forward push, so
branches-not-direct-pushes is enforced rather than conventional and
"watch CI go green" is a precondition for merging rather than a courtesy.
Recorded in `CLAUDE.md`'s land-it routine.

---

### CI was red on `main` for six consecutive runs ✅ FIXED (2026-09-29)

**How long: six runs, roughly 13 hours of wall clock, 2026-09-28 23:12 to
2026-09-29 12:03.** Every push to `main` in that window was red. Mapping
runs to pushes:

| Run | Pushed | Commit | Failed in |
|---|---|---|---|
| #307 | 09-28 23:12 | `df55f52` finish the RLS track | `integration` |
| #308 | 09-29 10:19 | `2d872af` `wingrc_app` cutover | `backend` |
| #309 | 09-29 10:31 | `453d087` record the cutover | `backend` |
| #310 | 09-29 10:32 | `490fd21` gitignore the credential files | `backend` |
| #311 | 09-29 11:57 | `022dfed` bundle determinism | `backend` |
| #312 | 09-29 12:03 | `3d39d00` record the determinism pass | `backend` |

**There were two failures, not one** — the run durations said so before
the logs did, and they turned out to be unrelated.

#### Failure 1: `/health` became a readiness probe (#308-#312, sub-minute)

The `wingrc_app` cutover made `/health` prove the database answers and
the schema matches the build (`150f2a6`). That is the point of the
change and it stays. But the `backend` job runs with **no database at
all**, and two test files had been using `/health` as a conveniently
ungated endpoint: `test_cors.py` (three tests) and
`test_auth.py::test_health_is_ungated`. All four asserted `200` and got
`503`.

Fixed with `conftest.HealthProbeSession` — a stub session answering
exactly the two statements `/health` issues and raising on anything
else, plus a `health_probe_client` fixture. **The assertions were not
weakened to accept either status.** A route that 503s still gets CORS
headers, so accepting either would have passed against a completely
broken app, and `test_health_is_ungated` would have kept passing if the
route later grew an auth dependency. A 503 proves ungatedness only by
accident.

#### Failure 2: the bundle nondeterminism (#307, ~4 minutes)

`tests/test_lifecycle.py:766` — `assert impl_html_v2 == impl_html_v1`,
the baseline-reimport check that an already-generated bundle does not
change for a tenant pinned to the old version. The diff was the
contributing-tools list: `RocketCyber Managed SIEM + SOC, Datto RMM` in
one render, the other order in the next.

This is exactly the defect the determinism slice found and fixed, from
the other end. **It reproduces in the full suite and passes when the
file is run alone**, which is why it survived local verification and why
it first appeared in CI rather than on the bench. Already fixed on
`main` by that slice; no further change needed. It is recorded here
because the correlation is the useful part: the nondeterminism was not
theoretical, it had already turned `main` red once before anyone
attributed it.

#### Marker scope: the correction

`pytest.mark.integration` was applied at **module** scope in three
files. That is wrong whenever a file mixes DB and non-DB tests, because
a `-m "not integration"` run silently drops the non-DB ones. Moved onto
the individual tests in `test_health.py` (3), `test_bundle_determinism.py`
(4) and `test_rls_context.py` (5).

This mattered most in `test_bundle_determinism.py`: its three static
guards exist *because* behavioural testing missed the defect they
protect against, so a module marker made them skippable in precisely the
configuration where they are the only protection. They now run with no
database (4 items pass, 4 skip).

A scanner flagged more files than this, and only these three were
changed — e.g. `test_dashboard.py`'s `dashboard_json` fixture is
DB-backed, so its module marker is correct. The over-report is noted so
the next person does not re-run the scan and "fix" the others.

#### Why the bench did not catch either

Two different reasons, and neither is "the bench is unreliable":

- **Failure 1** needs a run with *no* database. Bench verification
  always has one, so `/health` always answered. The `backend` job is the
  only place that configuration exists, and nothing local reproduced it.
- **Failure 2** needs the *full* integration suite in one process. The
  slice that introduced it was verified per-file.

**The methodology finding, which outlives this bug: running a file in
isolation is not running the suite.** `test_lifecycle.py::test_full_tenant_lifecycle`
**passes** when its file is run alone and **fails** inside the full
integration run — reproduced both ways, twice each, at `df55f52`. Order- and
state-dependent defects live precisely in that gap, and per-file
verification cannot see them by construction: it is a different experiment,
not a cheaper version of the same one. Anything that depends on row order,
shared fixture state, sequence allocation, or one test's residue is
invisible to it.

So "I ran the tests for the files I touched" is not bench verification, and
neither is a green per-file run on a slice that changes a query, an
ordering, or anything a later test reads. The full suite in one process is
the verification; a targeted run is a debugging convenience. Recorded in
`docs/bench-stack-verification.md` as a rule, since that file is what gets
read before a slice is called verified.

Both reproductions were run on the bench in a `python:3.13-slim`
container with CI's exact environment. **Two of my own repro attempts
were unfaithful and produced false findings**, which is worth recording
because it nearly went into the report as fact:

1. A container with no `pg_dump` "found" a failing
   `TestPreflightBackupReal::test_creates_a_nonempty_dump_file`. The
   GitHub runner ships `postgresql-client`; with a real (older)
   `pg_dump` present the existing version-mismatch skip guard works and
   the test passes.
2. A container missing WeasyPrint's Pango/cairo libraries "found" 63
   failures. The workflow installs them; the job definition, not memory,
   is the source of truth for what a faithful repro needs.

#### Workflow hardening

- **Runner pinned to `ubuntu-24.04`.** `ubuntu-latest` becomes Ubuntu 26
  on **2026-10-19**. This suite depends on runner-level detail an image
  swap changes silently — the system `pg_dump` version, which
  `test_cli_reset_dev.py`'s skip guard is written around, and
  WeasyPrint's system libraries. Riding it means finding out whenever
  GitHub rolls it; pinning makes the upgrade its own commit with its own
  run. **TODO: move to `ubuntu-26.04` deliberately after 2026-10-19.**
- **Node 24 deprecation:** `actions/checkout@v4` to `@v7`,
  `actions/setup-python@v5` to `@v7`, `actions/upload-artifact@v4` to
  `@v7`.
- **Third-party actions pinned to releases:**
  `aquasecurity/trivy-action@master` to `@v0.36.0` (a floating `master`
  ref runs whatever upstream pushed, on every push, with repo access)
  and `anchore/sbom-action@v0` to `@v0.24.2`.
- **`integration` no longer has `needs: backend`.** See below — this is
  the structural half of the answer.

#### What would make the next red run noticed within one commit

**A skipped job is not a passed job — but a skipped *required status
check* is treated as satisfied.** That is the trap, and it is why the
first move is structural rather than procedural:

1. **Done: `integration` runs unconditionally.** Gating it on `backend`
   meant that from #308 onward it never ran at all, which hid #307's
   genuine failure for five consecutive runs. It also meant that
   requiring `integration` in branch protection would have let a broken
   `backend` wave merges through on a skipped check. Both jobs now
   always run and can be required honestly. Cost: a few runner-minutes
   on a red push. Benefit: every failure visible at once instead of one
   per fix-and-push cycle — which is the entire difference between one
   commit and six.
2. **Recommended: branch protection on `main`, requiring `backend` and
   `integration`.** For a solo maintainer this sounds heavy, but the
   delta is small *because the work already happens on branches* — it
   only changes the final step from a fast-forward push to a PR merge.
   This is the mechanism that would have stopped #307 becoming #312.
   **Require only `backend` and `integration`, not `image`** — `image`
   keeps `needs: [backend, integration]`, so it legitimately skips on
   failure, and a required check that skips counts as satisfied.
3. **Not recommended: relying on notification.** GitHub already emails
   on failed runs for your own pushes. Six were missed, so that channel
   demonstrably is not read. A webhook to wherever the maintainer
   actually looks would work; the default will not.
4. **Done: the no-database suite is the cheap local gate, at ~10s.** This
   item originally read "a pre-push hook is not viable" on the strength of
   a 17m34s measurement in the Windows dev venv against 5s in a Linux
   container — a 200x gap I attributed to unexplained Windows slowness, and
   the standing hypothesis was Defender scanning `site-packages` on import.
   **Both were wrong, and chasing it found a production defect.**

   Measured, in order: collection is **3.83s**, so imports are not the cost
   and Defender is not implicated (real-time protection and script scanning
   are both on, and unchanged throughout). The cost was four tests at
   **260.0s each** — `test_cors.py`'s three plus
   `test_auth.py::test_health_is_ungated`, totalling 1043s of a 1054s run.
   Exactly the four that were failing.

   **A correction, since the first version of this entry overclaimed.** It
   said "the slowness was the bug, not the platform". That is wrong, and run
   #313 disproved it: CI's `backend` "Test" step took **12s on #312**
   (pre-fix, with all four tests failing) and **12s on #313** (post-fix).
   On Linux the identical bug cost nothing at all. So the bug was
   *necessary* — no bug, no connection attempt, no delay — but the platform
   supplied the entire 260x. It is the interaction, and describing it as
   either one alone is wrong in a way that would mislead the next reader.

   The mechanism, isolated: a raw TCP connect to `localhost:5432` with
   nothing listening is refused in **4.10s**, but psycopg took **260.03s**
   to raise `OperationalError`, because **libpq applies `connect_timeout`
   per resolved address** and `localhost` resolves to both `127.0.0.1` and
   `::1`. A deliberate 300s setting was measured taking **600.64s**,
   confirming the doubling rather than inferring it.

   **So `/health` — a readiness probe, whose whole job is to answer
   immediately — took 260s to return 503 with Postgres unreachable.** That
   is a production defect, not a test-speed annoyance: a load balancer or
   container healthcheck polling it gets a four-minute hang instead of a
   fast negative, and it was shipped by the cutover slice that introduced
   the probe.

   **The production scenario is a dropped SYN, not a refused one**, which is
   why the Linux/Windows split above does not make this Windows-only. A
   *refused* connection (nothing listening, port closed) is fast on every
   platform — that is why CI never saw it. A *dropped* one (host firewall
   set to DROP, a dead IP, a severed network path, a hung Postgres that
   accepts no new connections) hangs until something bounds it, on Linux
   exactly as on Windows. Without `connect_timeout` nothing bounds it, so
   the readiness probe hangs precisely in the outage it exists to detect.
   Fixed with `db_connect_timeout` (default 5s) in
   `config.py`, passed through `db.build_connect_args()`, and pinned by
   `test_health.py::test_engine_sets_a_connect_timeout`. Measured after:
   **260.03s → 10.02s** (5s x the two addresses `localhost` resolves to; a
   single-homed `postgres` host is 5s).

   With that fixed the no-DB suite is **10.32s / 458 tests** on the same
   Windows venv, so the gate is cheap after all and is now part of the
   standing routine in `CLAUDE.md`. Note the shape of this: the thing that
   looked like tooling friction *was* the defect, and treating the 200x as
   an environment quirk would have shipped a broken readiness probe.

5. **Open: make "did it actually run the tests?" a CI assertion, not a log
   read.** This workflow already carries a comment about a past incident
   where every `@pytest.mark.integration` test skipped silently and the job
   "reported green while testing nothing" — the failure mode is known and
   has happened. It is still checked by eye, and only by someone who can
   download the log. A `--co -q` count compared against a floor, or
   `-p no:randomly --strict-markers` plus asserting a minimum collected
   count, would turn it into a failure. Not done here: it is a change to
   the workflow being merged, and this slice's own rule was not to fix
   forward on `main`. Worth doing next.

6. **Branch-protection configuration, for the record.** Require **`backend`
   and `integration`, deliberately not `image`** — `image` keeps `needs:
   [backend, integration]`, so it legitimately skips when either fails, and
   **a skipped required check counts as satisfied.** That distinction is the
   thing that makes the protection real rather than decorative: requiring
   the job that skips on failure would wave through exactly the runs it was
   added to stop.

#### Verification

Reproduced and fixed against CI's own configuration, on the bench, in a
`python:3.13-slim` container with the workflow's package list and a
`postgres:18` service:

- **`backend` job (no database):** `ruff` clean, **458 passed, 1047
  skipped** (was 4 failed / 453 passed; +1 is the new connect-timeout
  guard).
- **`integration` job:** migration chain `upgrade` to `downgrade` to
  `upgrade` clean, **1027 passed, 1 skipped, 476 deselected**.
- **Determinism guards in both configurations:** 4 static items pass
  with no database, 4 DB items skip.
- **#307 reproduced twice** at `df55f52` (1 failed, 1021 passed) and
  confirmed to pass when `test_lifecycle.py` is run alone — the
  order-dependence is the finding, not an anomaly.
- **CI green on `main`: run #313** (`2623f4cd`), the merge commit —
  `backend` success 44s, `integration` success 245s, `image` success 80s.
  **All three actually ran**; none skipped, which is the point of dropping
  `needs: backend`. Step timings confirm the work happened rather than
  being skipped over: `Integration tests` **185s** and `Migration chain`
  **4s** against the live `postgres:18` service (a skipping run is ~1s),
  and `backend`'s `Test` step 12s.
  *Exact pytest counts are not recorded here because raw log download
  requires `Actions: read` and this was watched unauthenticated — the
  durations are the evidence, and they are inference from duration, not a
  count. See the note below on making this a CI-enforced assertion instead
  of something read off a log.*
- **Readiness-probe latency, measured before and after:** `/health` with
  Postgres unreachable went **260.19s → 10.19s**, and the underlying
  `SELECT 1` **260.03s → 10.02s**, across three consecutive calls each.
  The no-DB suite on the Windows dev venv went **1054.59s → 10.32s**.

---

### Bundle export made deterministic, and the test made reliable ✅ DONE (2026-09-29)

**The lesson, first, because it generalises: a passing test that depends on
unspecified ordering is not a passing test.** `test_lifecycle.py`'s
three-export comparison is the assertion underneath baseline versioning,
multi-tool coverage, the acceptance record and the SSP inventory — every
one of those slices was verified against it. It was passing on row-order
luck. A green suite meant "the rows happened to come back in the same order
twice", which is a different claim from "the export is reproducible", and
it hid a real integrity defect for weeks.

#### The per-query audit

A correction to the previous slice's figure first: it reported "fifteen
other queries lack an ORDER BY". That came from a naive scanner that missed
multi-line `.order_by()` chains. The real count is **17 queries, of which 2
were unordered on a render path** — the earlier number was wrong and is
corrected here rather than quietly restated.

**Unordered, now ordered** (both feed rendered collections):

| Query | Ordering | Why it is stable |
|---|---|---|
| `contact_documentation_role` | `contact_id, role` | `uq_contact_documentation_role` makes the pair unique |
| `raci_assignment` + `contact` | `control_state_id, raci_letter, contact.name, contact_id` | `uq_raci_assignment` closes it; rendered twice, inline per objective and again in the CRM matrix |

**Ordered but able to tie, now total** — a key that can tie is not a stable
key, so each gained a unique final term:

| Query | Was | Tie | Now |
|---|---|---|---|
| contacts | `name` | two people share a name | `+ Contact.id` |
| asset approvals | `scope_entity_id, decided_at desc` | same-instant decisions | `+ id` |
| Liongard changes | `entity_type, natural_key, pulled_at desc` | two pulls, same instant | `+ id` |
| evidence per control state | `collected_at` | **routine** — a task collect writes several rows in one transaction | `+ title, id` |
| unlinked evidence | `collected_at` | same | `+ title, id` |
| evidence tasks | `title, control_id` | two tasks sharing a title on one control | `+ id` |
| findings | `severity, title` | same severity and title | `+ id` |
| SPRS submissions | `submitted_date desc, created_at desc` | a date, not a timestamp | `+ id` |

Ordering keys are meaningful rather than arbitrary wherever possible —
tools by name, evidence by collection date then title, findings by
severity — so the bundle stays scannable. The unique term is the tiebreak,
not the sort.

**Order cannot affect output** — stated per query rather than as a blanket
claim, because the next person changing a renderer needs to know which
guarantee they are leaning on:

- `system_description` — `uq_system_description_org` means the filter
  matches at most one row, so `.first()` has nothing to choose between.
- the document objective-key lookup — carries `.distinct()` (Postgres
  forbids ordering a `SELECT DISTINCT` by expressions outside the select
  list) and the consumer sorts: `DocumentSnap.objective_keys` is built with
  `sorted()`. That sort is the guarantee.
- the evidence hash write — an `UPDATE`, not a collection read.

**Already total, and now says so**: `product.key`, `scope_entity`'s
`(entity_type, natural_key)`, `(control_id, objective_key)`, and
`(doc_id, version_number)` were each already unique under a named
constraint. Each carries a `DETERMINISM-TOTAL` comment naming that
constraint, because "it looks unique" is how a tiebreak gets dropped two
refactors later.

#### Wider than that file — the same defect class elsewhere

- **The review-cycle attestation document.** `close_cycle` read its
  reviewers and its items with no ordering and rendered both straight into
  `_render_attestation_html`, which is uploaded to storage and recorded as
  an `Evidence` row. That is a compliance artifact, not a screen. Both are
  now ordered.
- **`repo.list_entities`.** Unordered, and it feeds `render.py`'s `.xlsx`
  list exports — the assessor-facing Lists deliverable. Ordered by
  `(entity_type, natural_key)`, total under `uq_scope_entity_identity`.
- **The consolidated SSP PDF.** Issues no queries of its own; every section
  reads the frozen `BundleSnapshot`, so it inherits the fixes. Verified
  rather than assumed.
- Nothing else found: `render.py` itself touches no database, and the other
  queries in `review_cycles.py` serve API responses that render no stored
  artifact.

#### Making the test stop being lucky — and what mutation testing revealed

Both were needed, and finding out why was the useful part.

`tests/test_bundle_determinism.py` builds a fixture that **ties on
purpose** — four contacts in two duplicate-name pairs, five evidence rows
sharing one `collected_at`, one contact with three documentation roles,
four RACI holders of the same letter on one control state, three products
contributing to one objective. (`test_ties_really_exist_in_the_fixture`
guards that, so the others cannot pass vacuously.) It then exports five
times, and exports again across an unrelated write, since an `UPDATE`
writes a new tuple version and can move a heap scan's output order.

**Then mutation testing showed the behavioural tests are not enough.**
Removing the RACI `ORDER BY` entirely: not caught by any byte comparison.
Dropping `Contact.id` from the contacts ordering: not caught either. A
four-row sort simply does not reorder on demand, and rewriting whole tables
usually preserves relative order. Hoping for a flip is what the original
defect already proved unreliable.

So the guards are **static**, and each was verified by mutation to fail on
the first run:

1. Every query in the snapshot builder must be ordered or carry
   `DETERMINISM-EXEMPT: <reason>`. Catches a removed `ORDER BY`.
2. Every `ORDER BY` must end in a primary key or carry
   `DETERMINISM-TOTAL: <constraint>`. Catches a dropped tiebreak — the one
   no behavioural test caught.
3. The two artifact-producing functions outside that file
   (`review_cycles.close_cycle`, `repo.list_entities`) are checked by name.
   Scoped to the function rather than the module deliberately: scanning
   those files wholesale flagged ordinary API queries that render nothing,
   and a guard that cries wolf gets silenced rather than fixed.

The behavioural tests remain, as the check that the orderings actually
produce stable bytes rather than merely existing.

#### Verification

Bench project `wingrc_verify_det`, live `wingrc` untouched. **1504/1504
backend, three consecutive runs each from a dropped-and-recreated test
database** — three rather than two because two is what caught the original
defect and caught it only intermittently; the point of three is that the
nondeterminism is now gone by construction, so the repetition is
corroboration rather than the mechanism. `ruff` clean, **330/330
frontend**, `tsc -b` and `vite build` clean; no frontend files changed.

Both mutations (removed `ORDER BY`, dropped tiebreak) fail on a clean
checkout's first run; clean code passes 8/8.

**The live before/after, and a measurement error worth recording.** The
first live comparison appeared to show every section's hash changing. It
had not: the probe hashed the section bytes *without stripping the
`generated` stamp*, which is per-export by design, so it was comparing
clocks. Re-done properly by restoring the pre-deploy `pg_dump` into the
isolated bench stack and rendering the **same live data** on both the
pre-change and post-change code: **byte-identical across all five
sections for both orgs.** Consistent with the data — live currently has no
duplicate contact names, no evidence sharing a `collected_at`, and no RACI
rows at all, so there was nothing for the new tiebreaks to reorder. The
fix changes what *can* happen, not what did.

On the deployed instance, two exports across a real intervening write are
byte-identical. (The first attempt at that write was rejected by
row-level security because the ad-hoc script never set `app.current_org` —
an incidental confirmation that the `wingrc_app` cutover is doing its job.)

#### Loose ends from the cutover

- **The scheduler job body has now executed on live** under `wingrc_app`:
  `expire_stale_invites` succeeded at `15:20:27Z`, after the worker
  restarted at `14:20:25Z`. That gap is closed.
- **Frontend build determinism** (`package-lock.json` + `npm ci`) remains
  open and is **unrelated to this slice**, despite sharing the word. This
  one is about row order inside a rendered artifact; that one is about
  which dependency versions a build resolves. Closing this does not close
  that.

---

### The application now runs as a non-owner role — `wingrc_app` cutover complete ✅ DONE (2026-09-29)

**Tenant isolation on this deployment is now enforced by PostgreSQL, not by
router filtering.** The application connects as `wingrc_app`: not the table
owner, not a superuser, `rolbypassrls = false`. Every table carrying an
`org_id`, and every table reachable transitively from one, is
policy-gated (migrations `0060`/`0061`). A query that forgets its org scope
now returns nothing instead of another tenant's rows, and that is a
property of the database rather than of every developer remembering a
`WHERE` clause.

For a multi-tenant compliance product this is the milestone the whole RLS
track existed to reach, and it is worth naming as one rather than filing as
config.

#### The healthcheck came first, deliberately

`/health` returned a static dict. That is how an `alembic upgrade head`
which ran, logged success, exited 0 and silently rolled back left a
completely empty database while `docker compose ps` reported the backend
healthy throughout — nothing in the check touched a table, so nothing
noticed. (That rollback was itself self-inflicted, in the previous slice: a
privilege probe run on alembic's own connection before
`context.begin_transaction()` opened a transaction alembic nested inside
and never committed.)

It was fixed and verified *before* the credentials changed, because it is
the instrument the cutover is judged by and the cutover's two failure modes
are "cannot authenticate" and "RLS returns nothing" — a healthcheck that
touches no table reports healthy through both.

Two checks now, both cheap enough for a 30-second interval:

1. `SELECT 1` as the application's own role — proves the connection
   authenticates and the database answers.
2. `alembic_version` against the head the image ships — the one that would
   have caught the rollback directly.

(2) is a readiness condition, not a warning: a deploy **is** unhealthy
while the schema does not match the code. It costs nothing in this
deployment shape, because the backend's command is
`alembic upgrade head && exec uvicorn` (uvicorn never starts before
migrations finish) and the worker has healthchecks disabled. A multi-replica
rollout would see a real window, and being marked unhealthy during it is
still the right answer. `alembic_version` carries no RLS policy and is
readable by `wingrc_app`, so it behaves identically either side of the
cutover.

#### The flip

- `wingrc_app` password generated on the host into a mode-600 file, applied
  via stdin so it never reached a command line, and **never printed to a
  transcript, log or commit**. Stored where the deployment's other secrets
  live, same rule as `WINGRC_CREDENTIAL_ENCRYPTION_KEYS`.
- `WINGRC_DATABASE_URL` → `wingrc_app`; `WINGRC_MIGRATION_DATABASE_URL` →
  the owner, set in the same change. Appended to the existing `.env`
  (which holds real secrets) rather than rewritten, with the previous file
  kept as `.env.pre-cutover.bak`.
- `docker-compose.yml` now reads `${WINGRC_DATABASE_URL:-<owner default>}`,
  so a cutover is a config change rather than an edit. The default stays
  the owner because a fresh install has no `wingrc_app` password — there is
  no shippable default for a credential.
- **The worker connects too**, and is easy to forget because it serves
  nothing. It is also where `app.current_org` changes mid-session (the
  per-org scheduler loop), so it exercises `rls.py`'s hook harder than the
  API does.
- **Rollback, written down before starting:** comment out the one
  `WINGRC_DATABASE_URL` line in `.env` and restart. Pre-flight `pg_dump` at
  `/backups/pre-cutover-20260929T141914Z.dump`.
  **Correction (2026-10-23): that rollback would have failed.** `.env`
  held a second, older `WINGRC_DATABASE_URL` on line 2
  (`wingrc@localhost`, copied from `.env.example`). Commenting out the
  cutover line made compose fall back to it, and no container can reach
  `localhost:5432`. "Restart" also does not reread `.env`. The stale
  line was removed from live on 2026-10-23, and the effective config was
  unchanged by it. The rollback now reads: set `WINGRC_DATABASE_URL` to
  `WINGRC_MIGRATION_DATABASE_URL`'s value (the owner), then
  `docker compose up -d backend worker`. It names the owner URL
  explicitly rather than relying on a fallback, so it survives the owner
  password changing (the item "The compose file ships default
  credentials").

#### What "verified" meant

Under RLS a missing or wrong `app.current_org` does not raise — it returns
zero rows with a 200. **So "the app starts and serves 200s" is not evidence
of anything, and an empty result where there used to be data is a cutover
failure even when the status code is fine.** Every check below asserts that
data *is* returned.

On the bench, with the app actually running as `wingrc_app`:

- 1496/1496 backend tests, twice, from a clean test database.
- Real authenticated HTTP (API token): scope 35, control states 320,
  documents 1, contacts 1, SPRS −204, a 357 KB bundle, cross-org 403.
- The per-org scheduler loop opened **one review cycle per org** — the
  hardest exercise of the org-context hook available.
- Deployment-wide (`org_id = NULL`) audit writes accepted, which is what
  `0060`'s `IS NULL` arm exists for and the cutover is when it first
  matters.
- The CLI still works as the owner via an explicit override.

On the live deployment, against real data, the decisive check was running
the same probe twice — once with an owner override, once as configured —
and diffing:

> **identical apart from `current_user` and `bypassrls`.** Per-org scope
> entity counts, contact counts, evidence counts, document counts,
> assessment counts, SPRS scores (−199 and −204), control-state counts
> (320 each) and both bundle section SHA-256 hashes all matched exactly.

Cross-org isolation, with the contrast that makes it meaningful:

| Scoped to | As `wingrc_app` | As owner (bypass) |
|---|---|---|
| Acme MSP | own 320, Winsorlabs **0** | own 320, Winsorlabs **320** |
| Winsorlabs | own 320, Acme **0** | own 320, Acme **320** |

The owner column is the point: the zeros are RLS doing its job, not an
empty table.

Also verified live: `/health` reporting `0061_rls_transitive_policies` and
`database: ok`; real authenticated HTTP returning non-empty data and
refusing an unknown org; both cross-org `SECURITY DEFINER` functions
(`auth.expire_stale_invites`, `auth.msp_role_users`) executing as
`wingrc_app` and returning rows; the scheduler evaluating all five jobs
without error under the new role; deployment-wide audit writes accepted;
and the CLI's `rotate-credential-keys` and `seed-catalog` working as the
owner. The temporary API token minted for the HTTP checks was deleted
afterwards.

**One honest gap:** no scheduler job *body* executed on live post-cutover
during the verification window — only `expire_stale_invites` was near due
and the scheduler judged it not yet so. The job path itself ran cleanly
under the new role, and a full per-org loop executed on the bench. The next
hourly run will close it without intervention.

#### A pre-existing bug found on the way, and a correction

`test_lifecycle`'s three-export comparison was failing intermittently. It
reproduces on `main` as well as on the cutover branch, so **the previous
slice's reported "1491/1491" was a lucky green run, not proof** — recorded
here because a single green run on a known-intermittent test is exactly the
kind of evidence that should not have been presented as conclusive.

Root cause: the bundle's contributors query had no `ORDER BY`, so Postgres
was free to return the same two products in a different order on two
executions, and the implementation section rendered
`Tools: Datto RMM, RocketCyber…` one time and
`Tools: RocketCyber…, Datto RMM` the next. That is a point-in-time
integrity problem rather than a cosmetic one: the bundle's whole premise is
that an already-generated export renders the same thing. Fixed with an
explicit `ORDER BY`; the suite is now green twice from a clean database.

**Fifteen other queries in `bundle_service.py` also lack an `ORDER BY`.**
Most feed dict lookups where order cannot matter, but not obviously all of
them. Reported rather than fixed here — a blind sixteen-query refactor does
not belong in a cutover slice, and bundle determinism deserves its own
pass. That is the next natural piece of work on this file.
**Corrected 2026-09-29 by that pass:** the figure was wrong. It came from a
scanner that missed multi-line `.order_by()` chains; the real count was two
unordered queries plus seven whose keys could tie. See the Done entry above
for the full audit.

Separately, `conftest.py` now clears `WINGRC_MIGRATION_DATABASE_URL` when it
redirects to the test database. Without it, `env.py`'s
different-database guard fired during test bootstrap and the entire suite
aborted (1042 errors) on any stack that had cut over. The guard was right;
the harness was wrong. It was only found by running the suite on a stack
that had actually cut over, which is the argument for doing the bench
cutover before the live one.

#### What still holds owner rights, and why

- **Migrations.** DDL needs the owner; `WINGRC_MIGRATION_DATABASE_URL` is
  that credential and is consumed only by `app/migrations/env.py`.
- **The CLI**, per the decision already recorded in `docs/deployment.md`:
  `wingrc-admin` creates the first org and user before any org context
  exists, `reset-dev` deletes across every tenant, the seed commands write
  deployment-wide reference data, and `scope`/`render` look read-only but
  call `get_or_create_org`, which writes. Privileged commands now need an
  explicit owner override on the command line; that is documented, and the
  operational consequence — the CLI's access is controlled by who can reach
  the host and the credential, not by RLS — is stated there too.

`FORCE ROW LEVEL SECURITY` remains unset and should stay that way: it would
subject the owner to RLS and break exactly those two.

---

### RLS track finished: six transitive policies, and the migration-connection split ✅ DONE (2026-09-29)

Cutover *preparation*, deliberately not the cutover. Migration `0061`; no
frontend change. After this, the only thing between the current state and
the application connecting as a non-owner is setting a password and
flipping one variable.

**The rule this slice inherited**, generalised from the last one's most
useful finding: *adding a policy to a table is a change to every
post-commit read of that table*, because `app.current_org` dies with the
committing transaction. Confirmed for each of the six rather than assumed
— `routers/contacts.py:add_role` does exactly this on
`contact_documentation_role`, and
`tests/test_rls_transitive_policies.py` asserts it directly. The hook is
new and these tables had never been gated, so "the hook covers it" was not
something to take on trust.

#### The six policies

All six carry no `org_id`; each is gated by an `EXISTS` over a parent that
is already gated. That composes because PostgreSQL applies a table's own
policies to references of it *inside another policy's expression*, so the
child inherits its parent's scoping rather than restating it — one
definition of "which org", not seven. `0060`'s `raci_assignment` policy
had already proved the mechanism under `SET ROLE wingrc_app`.

| Table | Join | `USING` and `WITH CHECK` |
|---|---|---|
| `contact_documentation_role` | `contact_id` → `contact` | `EXISTS (SELECT 1 FROM contact p WHERE p.id = …contact_id)` |
| `control_state_contributor` | `control_state_id` → `control_state` | `EXISTS (SELECT 1 FROM control_state p WHERE p.id = …control_state_id)` |
| `control_state_history` | `control_state_id` → `control_state` | same shape |
| `evidence_state_link` | `control_state_id` → `control_state` | same shape |
| `evidence_task_state_link` | `control_state_id` → `control_state` | same shape |
| `review_cycle_reminder_log` | `cycle_id` → `review_cycle` | `EXISTS (SELECT 1 FROM review_cycle p WHERE p.id = …cycle_id)` |

**`USING` and `WITH CHECK` are identical for all six, and both are spelled
out — "identical, deliberately" rather than silence.** `0060`'s `audit_log`
finding is the reason: a `FOR ALL` policy with no explicit `WITH CHECK`
silently reuses `USING` as the check, which for an EXISTS predicate means
an INSERT gets validated against a condition written for a read. They
match here because the rule genuinely is symmetric — a row may be read iff
its parent is visible, and written iff its parent is visible. There is no
case where you may create a link to a control state you cannot see.

**No nullable join columns**, enumerated rather than read: every FK column
used is `NOT NULL` (`pg_attribute.attnotnull`). So the `NULL = <uuid>` →
NULL trap that shaped `audit_log`'s policy cannot arise, which is why these
six are plain and carry no `IS NULL` arm.

**Where a table has two gated parents** — `evidence_state_link` and
`evidence_task_state_link` (control state *and* evidence side),
`review_cycle_reminder_log` (cycle *and* reviewer) — either would isolate
correctly, since both sides are org-gated and in the same org by
construction. Each policy joins through the parent whose FK column has its
own dedicated index, and the two link tables both use `control_state` so
there is one join shape rather than two.

#### Cost, measured at realistic volume rather than reported from an empty table

Live row counts are useless for this: the largest of the six holds 37 rows
and `evidence_state_link` is empty. So the bench was seeded to a plausible
MSP shape — 2 tenants, 10,000 control states, 10,000 evidence links,
10,000 history rows — and the plans compared as the owner (RLS bypassed)
against `wingrc_app` (enforced).

- **The shape the application actually issues — a lookup by
  `control_state_id` — is unaffected.** `Index Scan using
  ix_evidence_state_link_control_state_id` and `Index Only Scan using
  ix_control_state_history_control_state_id` are both preserved under the
  policy, at 8 shared buffers. The policy costs essentially nothing on the
  indexed path.
- **The worst case, a full-table aggregate with no narrowing predicate,
  goes 0.84 ms → 3.13 ms** (114 → 206 shared buffers) at 10k/10k.
- **The reason it is only that:** PostgreSQL *hashes* the EXISTS subplan
  rather than re-evaluating it per row. The plan shows one
  `Index Scan using ix_control_state_org_id` over the parent, hashed, then
  probed — so the cost is O(child + parent), not O(child × parent), which
  is the thing worth knowing before putting six of these in.

Every join column is indexed, verified rather than assumed:
`ix_contact_documentation_role_contact_id`,
`ix_control_state_contributor_control_state_id`,
`ix_control_state_history_control_state_id`,
`ix_evidence_state_link_control_state_id`,
`ix_evidence_task_state_link_control_state_id`,
`ix_review_cycle_reminder_log_cycle_id`.

#### The migration-connection split

`WINGRC_MIGRATION_DATABASE_URL`, consumed by `app/migrations/env.py` and
nothing else. Chosen over a separate init container because it is one
variable in the existing config surface rather than a new deployment
topology, and because the failure modes can be checked in-process.

Unset falls back to `WINGRC_DATABASE_URL`, which is correct while that is
still the owner — including on a fresh install, where nothing extra needs
configuring. Two guards keep a wrong configuration loud, because a
migration silently running as the wrong role or against the wrong database
is how a cutover gets undone without anyone noticing:

1. **Same database.** A migration URL naming a different host/port/database
   than the app URL is refused, with both targets printed. Migrating one
   database while the app serves another is the failure that otherwise
   looks exactly like success.
2. **Can actually do DDL.** Before running anything, the connected role is
   checked for `CREATE` on `public`. This turns the post-cutover mistake
   ("forgot to set the migration URL") from a permission error partway
   through some migration into one message naming the variable.

**A bug found while building guard 2, worth recording because it is the
same silent-success shape this whole track is about.** The check was
initially run on alembic's own connection, before
`context.begin_transaction()`. That implicitly opened a transaction alembic
then nested inside and never committed: every migration ran, alembic logged
success, `alembic upgrade head` **exited 0**, and closing the connection
rolled the entire thing back, leaving an empty database. The check now runs
on its own connection. Two things about how it surfaced are worth keeping:
the bench caught it only because a *fresh* volume was used, and
`docker compose ps` still reported the backend **healthy** throughout —
`/health` touches no table, so a completely unmigrated database passes the
healthcheck. That is not fixed here, and is worth its own look.

#### The CLI stays privileged, documented rather than fixed

Agreed with, after checking whether a clean split exists. It does not:

- `wingrc-admin` creates the first org and user — there is no org context
  for RLS to scope to, by definition.
- `reset-dev` deletes across every tenant.
- `seed` / `seed-catalog` / `seed-baselines` write deployment-wide
  reference data belonging to no tenant.
- `scope` and `render` *look* read-only but call `get_or_create_org`,
  which writes. That is the one worth knowing about: the obvious candidates
  for demotion are not actually read-only.
- Only `views` touches no database at all.

Forcing these through `wingrc_app` would mean weakening that role or adding
bypasses — both worse than an honest "this tool runs as the owner". The
operational consequence is written into `docs/deployment.md`'s new
"Database roles: which credential does what" section: the CLI is a
privileged administrative tool whose access is controlled by who can reach
the host and the credential, not by RLS, and after the cutover it is the
main thing still holding owner rights.

#### Verification

Isolated bench project `wingrc_verify_20260929_rls2` (own volumes, no
published ports; live `wingrc` untouched). **1491/1491 backend tests**
(1481 before), `ruff check .` clean, **330/330 frontend**, `tsc -b` and
`vite build` clean — but see the cutover entry above: `test_lifecycle`
was intermittently failing on `main` at this point due to an unordered
bundle query, so this green run was luck rather than proof. Fixed there — no frontend files changed. Fresh-install path checked
explicitly by tearing the volumes down and rebuilding: `alembic current` →
`0061_rls_transitive_policies (head)`, 54 tables.

Coverage added: isolation in **both directions per table** — a cross-org
read returns nothing and a cross-org insert is refused — plus a test that
ordinary in-org writes still succeed, because a policy that blocks
everything also passes an isolation test. `test_lifecycle.py` now renders
the same bundle twice, once under `SET ROLE wingrc_app` and once as the
owner with RLS bypassed, and requires the implementation section and
evidence manifest to be byte-identical; `evidence_state_link` is what
`bundle_service` filters on, and a policy hiding rows there raises nothing
— it just hands an assessor a thinner SSP.

**That comparison caught a real mistake of mine on its first run**, which
is the best argument for it existing: placed between the v1 and v2 exports,
`snapshot_bundle`'s `recompute_sprs` wrote between two exports the walk
compares byte-for-byte, and the *pre-existing* baseline-reimport assertion
failed. Moved after the three-export comparison, where nothing downstream
depends on it.

#### What remains before the `wingrc_app` cutover

Everything except the flip itself.

1. **Set a password on `wingrc_app`** (`ALTER ROLE`). It has
   `rolcanlogin = true` and `rolpassword IS NULL`, so it cannot
   authenticate — deliberate, per migration `0016`.
2. **Point `WINGRC_DATABASE_URL` at it, and set
   `WINGRC_MIGRATION_DATABASE_URL` to the owner** (same host/port/database,
   different user).
3. **Verify.** The suite already runs under `SET ROLE wingrc_app`, so the
   behaviour is covered; what the cutover itself needs proving is that the
   deployment starts, migrates and serves under the new credentials.

No schema work, no policy work and no code work is left. Grants were
verified complete in the previous slice (0016's `ALTER DEFAULT PRIVILEGES`
held for every table created since; `wingrc_app` can EXECUTE every `auth`
SECURITY DEFINER function), and every table carrying an `org_id` or
reachable transitively from one now has a policy.

Still worth knowing, and not blocking: `FORCE ROW LEVEL SECURITY` remains
unset everywhere and should stay that way — it would subject the owner to
RLS and break migrations and the CLI. The cutover is the mechanism, not
FORCE.

---

### Post-commit RLS gap closed, and four missing tenant-isolation policies ✅ DONE (2026-09-28)

Jarrod's decision on the open question N.2's §8 audit raised: the
`after_commit` hook, not the build-the-response-before-committing
convention. Migration `0060`; no frontend change.

**Why a hook and not a convention.** `SET LOCAL app.current_org` is
transaction-scoped, so `commit()` discards it and any read issued
afterwards runs with no org context. Under an RLS-enforcing role that
matches zero rows. The deciding case is
`routers/assessments.py:upsert_statements`, which committed and then ran
`select(ControlState)`: a zero-row select is not an error, so the endpoint
answered **200 with `control_state_id: null` for every statement** — no
exception, no log line. A convention only protects against mistakes
someone can notice, and here the failure looked exactly like success.
Supporting it: this codebase has four documented instances of an older
writer routing around a newer guard, none caught by the rule holding, and
the `repo.upsert` guard worked precisely because it moved the invariant to
the place every writer must pass. Same move, session layer.

**`after_begin`, not `after_commit`.** SQLAlchemy documents `after_begin`
as the place to apply transaction-scoped settings and hands it the
`Connection`; the `Session` is explicitly documented as unable to emit SQL
from `after_commit`. `after_begin` also covers more: it fires on the first
statement after a commit, which is exactly when the lazy reload, the
`refresh()`, or the fresh `select()` happens.

**How the hook knows the org, and when it refuses to guess.**
`app/rls.py:set_current_org()` is the single chokepoint — it issues the
`set_config` *and* records the value on `session.info`. The hook re-applies
only that recorded value. No fallback, no inference from request context,
no default: a session that never set an org gets nothing, which is the
correct answer for the CLI and for the suite's owner-role scaffolding
sessions. The one place the value legitimately changes mid-session is
`scheduler.py`'s per-org loop, which calls `set_current_org` per iteration,
so the recorded value tracks the loop rather than pinning the first org it
saw (asserted directly). Every other caller sets one value for the life of
the request.

**Converting the call sites found six writers the first sweep missed** —
all in `routers/auth.py`, all raw f-string `SET LOCAL`. That is why
`tests/test_rls_context.py` carries a source guard asserting `rls.py` is
the only module that writes the GUC: a direct write elsewhere leaves
`session.info` stale, and the hook would then confidently re-apply the
*wrong* org after a commit — a silent cross-tenant read, strictly worse
than the silent empty result this replaces. Structural, not remembered.

**The honest cost, recorded rather than glossed:** the hook is implicit. A
future reader of `upsert_statements` will not see why its post-commit read
works. Mitigated with a comment at the hook and one at that call site —
not at every call site, which nobody maintains.

**Test-harness fidelity, which took the most thought.**
`conftest.py:db_session` uses `join_transaction_mode="create_savepoint"`,
so an app-level `commit()` only releases a savepoint, the real transaction
never ends, and no new one begins — meaning `after_begin` never fires under
the suite. Left alone, the fix would have worked in production and failed
in every test, the exact inverse of what that harness is for. So
`_app_session` now models **both** halves of a commit boundary: it still
RESETs the GUC (transaction ended) and then calls the *production*
`reapply_current_org` (next transaction begins), rather than
reimplementing it. Because that means the suite alone would pass even if
the event were registered on the wrong hook or not at all,
`tests/test_rls_context.py` proves the wiring separately against real
`COMMIT`s on its own session, cleaning up after itself.

**Migration 0060 — four policies, found by query rather than by reading.**
Enumerating `pg_policy` against every table carrying an `org_id` disagreed
with the preceding reading pass in two directions at once:

- It surfaced **`audit_log`** and **`system_description`**, which the
  reading pass had not flagged at all.
- It showed **`raci_assignment` has no `org_id` column**. N.2's writeup
  said it "carries `org_id` but has no tenant-isolation policy" — the
  second half was right, the first was wrong. It is scoped transitively
  through `control_state_id`, which is why its policy is an `EXISTS` over
  `control_state` rather than a column comparison. That N.2 entry has been
  corrected in place.

**`audit_log` is deliberately shaped differently, and the usual shape
would have broken production at cutover.** Its `org_id` is nullable on
purpose — deployment-wide actions log with `org_id=None` (product baseline
administration, credential-key rotation, practitioner notes), 45 of 317
rows on the live dev database. A `FOR ALL` policy with no explicit
`WITH CHECK` uses its `USING` expression as the check, and `NULL = <uuid>`
is NULL rather than true, so every such write would have started failing
the moment the app stopped bypassing RLS. The policy carries an
`org_id IS NULL OR ...` arm, asserted by a test that inserts a NULL-org row
under `wingrc_app`. It widens nothing for readers:
`routers/audit_log.py` already filters `org_id == org_id` in its own query
and is `msp_admin`-gated, so those rows stay invisible in the UI.

**An ordering dependency worth naming:** re-running the post-commit sweep
afterwards shows `routers/contacts.py` (×3),
`routers/orgs.py:upsert_system_description` and
`routers/raci.py:create_raci_assignment` all do a post-commit `refresh()`
on tables *this same migration newly gates*. Without the hook, adding these
policies would itself have introduced three new instances of the bug. The
two halves of this slice are not independent, and shipping the policies
first would have been wrong.

**Re-sweep result.** Every remaining post-commit read is either on a
non-RLS table (`organization`, `integration_connection`,
`assessment_objective`), in the CLI (owner role, no org, hook correctly
no-ops), or in a session the hook covers. Reachable failures: zero.

#### Owner bypass, and what remains before the `wingrc_app` cutover

**Nothing here is enforced yet, and that is the point.** The app connects
as `wingrc`, which is both table owner and (in the compose dev setup) a
superuser; PostgreSQL bypasses RLS unconditionally for both unless
`FORCE ROW LEVEL SECURITY` is set, and it is **not set on any table**
(`relforcerowsecurity = false` everywhere, verified by query). So every
policy in this database — the twenty-eight that already existed and the
four added here — is currently inert against the application's own
connection. Stating that plainly because it is what makes this whole class
of bug latent rather than live.

`FORCE ROW LEVEL SECURITY` is *not* the fix and should not be applied: it
would subject the owner to RLS too, breaking migrations and the CLI. The
cutover — the app connecting as a non-owner — is the correct mechanism.

**This slice deliberately does not perform the cutover**, so that a failure
has one possible cause rather than two. What remains, verified against the
schema at `0060` rather than assumed:

1. **`wingrc_app` has no password.** `rolcanlogin = true`,
   `rolpassword IS NULL` — it cannot authenticate at all. Deliberate:
   migration `0016_app_role` says a real per-environment secret has no
   business in a migration file. An operator sets it via `ALTER ROLE`.
2. **Migrations run on the app's own connection string.**
   `backend/Dockerfile`'s `CMD` is
   `alembic upgrade head && exec uvicorn …`, and `docker-compose.yml`
   repeats it — both on `WINGRC_DATABASE_URL`. Flipping that one variable
   to `wingrc_app` makes migrations fail outright: no DDL privilege. The
   cutover needs migration execution separated from the runtime
   connection (a second credential, or a distinct migration step). **This
   is the substantive work, not the password.**
3. **Decide what the CLI and admin commands connect as.**
   `wingrc reset-dev` and `manage.py bootstrap-admin` go through the same
   `db.py:SessionLocal` and do work that is deliberately privileged
   (bulk deletes, creating the first org and user before any org context
   exists).
4. **Six tables have no RLS at all**, being org-scoped only transitively:
   `contact_documentation_role`, `control_state_contributor`,
   `control_state_history`, `evidence_state_link`,
   `evidence_task_state_link`, `review_cycle_reminder_log`. Each is
   reachable only through a parent that *is* gated, and the app always
   joins — but after cutover they are the tables where router-level
   filtering is the only layer. Each needs its own `EXISTS` policy and its
   own thought about the join and the `WITH CHECK`, which is a coherent
   follow-up pass rather than something to append to `0060` unexamined.
   Recorded here so the set is not lost.

Everything else is ready, and this is the part that was pleasantly
surprising: **grants need no work at all.** `0016`'s
`ALTER DEFAULT PRIVILEGES` held for every table created since — zero tables
are missing SELECT/INSERT/UPDATE/DELETE for `wingrc_app` — and it can
EXECUTE every `SECURITY DEFINER` function in the `auth` schema.

**Verification.** Isolated bench project `wingrc_verify_20260928_rls` on
wl-util-1 (own volumes, no published ports; live `wingrc` untouched
throughout). Migration `0060` applied cleanly on backend start
(`alembic current` → `0060_rls_policy_gaps (head)`). **1481/1481 backend
tests** (1468 before), `ruff check .` clean, **330/330 frontend**, `tsc -b`
and `vite build` clean — no frontend files changed in this slice.
`test_lifecycle.py` passed unchanged. The new coverage is 15 tests across
`test_rls_context.py` (hook against real commits, org tracking across
commits, no-op without an org, the source guard, the listener actually
registered on the `Session` class) and `test_rls_policies.py` (cross-org
isolation for all four tables queried under `SET ROLE wingrc_app`, the
NULL-org audit row staying writable, and `WITH CHECK` refusing a
cross-org insert), plus `test_post_commit_rls.py` inverted from asserting
the bug to asserting the fix.

**Harness coverage, since it determines whether other latent instances
would surface:** 51 of the 53 test files that drive HTTP requests install
`_app_session`, which does `SET ROLE wingrc_app` per request. The two that
do not are `test_auth.py` (exercises the auth path that establishes org
context in the first place) and `test_cors.py` (no database). So RLS
enforcement is effectively suite-wide already — which is why N.2's audit
found the `upsert_statements` bug at all.

---

### Document library N.2: browser editing, readable diffs, audit surfacing ✅ DONE (2026-09-28)

`docs/PLAN-document-library.md` has the five-slice plan. N.2 makes the
library usable: before this, a document could be created and published
through the API, but there was no way for a person to write one. No
migration — N.2 is entirely behaviour on N.1's `0059` shape.

**§0 decision: the body format is GFM-subset Markdown, and the deciding
argument is not diffing.** `DocumentVersion.body`'s own comment deferred
this. The obvious framing is Markdown-diffs-well versus HTML-round-trips-
well, and on that framing it is a close call. What settles it is that a
body has to render in *two* places — the React app, and the backend's
HTML → WeasyPrint PDF/bundle path — so both are injection surfaces on
operator-supplied content. Markdown lets both renderers *construct* their
own output and escape all text, so raw markup in a body is never
forwarded: injection becomes structurally impossible rather than
filtered. That is `svg_sanitize.py`'s allowlist-rebuild philosophy applied
to a second untrusted-content path. HTML storage instead forces a
correct-by-audit sanitizer onto both paths, and would have introduced the
first `dangerouslySetInnerHTML` in `frontend/src/` — an invariant the
codebase currently holds everywhere. Editor JSON round-trips perfectly and
does not diff at all, which is disqualifying for the one question this
feature exists to answer.

**What it costs, confirmed with Jarrod before the slice started rather
than discovered in N.4:** no merged table cells — GFM cannot express them
and `markdown_doc.py` deliberately does not add an HTML escape hatch that
would hand the sanitizer problem straight back — and no inline images
until N.4 adds an upload path for `DocumentVersion.storage_key`. The image
limit is reversible (an N.4 `wingrc-asset:<uuid>` scheme plus an
allowlisted `img`); the merged-cell one is not. N.4's `.docx` conversion
can target GFM: pandoc emits it directly, and mammoth's HTML output
converts onward. The plan doc's already-accepted cost ("complex Word
formatting is lost and his existing set gets re-styled once inside
WinGRC") is what that amounts to.

**§3 sanitization, stated as the two separate claims it is.** Bodies are
stored *verbatim* — nothing is stripped on save, because an author may
legitimately be documenting markup in an appendix — and neutralised on
render, which is where `svg_sanitize.py` already draws the line.
`markdown_doc.py` (backend) and `lib/markdown.tsx` (browser) are
configured identically: `html=False` so source markup is escaped to text
rather than forwarded, linkify off so prose that merely looks like a URL
does not silently become a clickable external reference in a document an
assessor reads, the `image` rule disabled, an explicit http/https/mailto
link allowlist replacing markdown-it's built-in *blocklist*, and GFM
column alignment rewritten from `style="text-align:…"` into a class —
refusing `style` for the same `url()`-smuggling reason `svg_sanitize.py`
refuses it. Behind that construction-time guarantee, `render_html` runs an
allowlist verification pass over its own output and raises
`UnsafeRenderError` rather than returning anything unexpected, so a
dependency upgrade that starts emitting a new tag fails loudly instead of
silently widening what reaches a browser and WeasyPrint. The browser half
renders markdown-it's token stream into React elements and never touches
`dangerouslySetInnerHTML` at all.

**Two renderers is the real risk, and it is bounded by a shared corpus.**
`markdown-it` (JS) and `markdown-it-py` are ports of each other, so the
pair was chosen deliberately; `backend/tests/fixtures/markdown_corpus.json`
(29 cases, hostile inputs included) is replayed by both
`tests/test_markdown_doc.py` and `src/lib/markdown.test.tsx`. That test
earned itself immediately: it caught the React renderer building an
element for markdown-it's *hidden* tight-list paragraphs, so every `- a`
rendered as `<li><p>a</p></li>` in the app and `<li>a</li>` in the PDF.
That is exactly the class of drift nobody notices until an assessor asks
why the document looks different in the export.

**§1 editor: TipTap 3, on a schema constrained to exactly the serializable
subset.** ~30 ProseMirror packages into a frontend whose only runtime
dependency was React; they are build-time only (Vite bundles them, the
nginx image ships built assets) so air-gap is unaffected, but it is a real
supply-chain surface in a compliance product and Jarrod accepted it
explicitly. Versions are pinned exactly, not by caret. The editor is
code-split (`React.lazy`), which matters: TipTap tripled the main bundle
to 1.06 MB before splitting and it is back to 504 kB after, with the
editor's 559 kB arriving only when someone opens a document —
`c3pao_assessor`, who can read diffs and history but never edit, never
loads it at all.

`lib/documentSchema.ts` disables `underline` (StarterKit 3 ships it;
Markdown has no underline, so an author who could press Ctrl+U would lose
that formatting silently on save) and carries no image node. **The schema
is asserted, not assumed** — `documentSchema.test.ts` checks the resulting
node and mark lists exactly, because TipTap option names move between
majors and an unrecognised key is silently ignored, so "we passed
`underline: false`" is not evidence that underline is off. A second test
pairs with it from the other direction: every node and mark the schema
*does* hold has a Markdown sample that round-trips.

`lib/tiptapMarkdown.ts` is owned in-repo rather than using TipTap 3's own
per-node Markdown specs. Two reasons, both recorded in the module: the
inbound direction has to agree with the renderers, and those are
markdown-it, so `markdownToDoc` consumes `parseMarkdown`'s token stream —
one `MarkdownIt` instance, three consumers — instead of routing the editor
through `marked` and reintroducing the divergence this slice exists to
avoid; and owning the outbound direction is what lets the supported subset
*be* the subset. Consolidating onto TipTap's specs is a reasonable future
simplification, not a correction. GFM column alignment is carried as an
`align` attribute rather than flattened on save. `tiptapMarkdown.test.ts`
asserts the fixpoint `md → doc → md′` with `md′ === md″` over the shared
corpus, so a save is idempotent and a policy cannot drift a little on
every edit.

**§1 concurrent editing: optimistic concurrency, not a lock.** The caller
states which version it edited from (`base_version_id`, required once a
document has any version — omitting it is a 422, not an unchecked write),
and a stale base returns 409 carrying the version that won, so the UI says
"someone else saved version N while you were editing" and offers the diff.
A lock would need a lease, an expiry and a steal path, because an engineer
who closes a tab must not strand a policy document for whoever needs it
next, and every one of those mechanisms is state that can itself be wrong.
The conflict check is stateless and cannot lose an edit: the loser's text
is still in their browser and re-saves onto the new base, and because
versions are append-only the racing save is still recorded, just off a
different parent.

**§1 autosave: none, deliberately.** A version per keystroke is absurd and
a mutable scratch version would break append-only, so the working copy
lives in the component and in `sessionStorage` for crash/reload recovery
only, keyed per `(document, base version)` so a recovered draft can never
be applied on top of a *different* base than it was written against. It is
**offered** on return, never silently applied. The accepted cost: version
history is noisier than a system with autosave-into-a-draft, which is
exactly why the diff view compares any two versions rather than only
adjacent ones.

**§2 diffs.** `document_diff.py` is pure (no DB, no ORM) and returns
structured rows, not a patch string the client has to parse. Three things
make it readable rather than merely correct: word-level spans inside a
changed line, so a one-word edit highlights that word and not the
paragraph; collapsed unchanged runs, so a 60-line policy with one edit
renders 9 rows instead of 60; and paragraph-granular lines, which is the
unit a human actually reviews. Version 1 is not a caller special case —
`from_version` comes back null with `is_initial` set, and the view renders
"initial version" rather than an empty panel.

**§2 metadata changes: answered honestly rather than fudged.**
`DocumentObjectiveTag` hangs off `document`, not `document_version`, so
tags are not versioned state and "the tags as of version 3" is not a
question the schema can answer for an unpublished version. For an
*approved or superseded* version it can be answered exactly, and is: the
objective set is recovered from the `EvidenceStateLink` rows that version's
own `Evidence` row produced, archived ones included — a republish archives
them, and "what did v2 cover" must survive that. Each side of a diff is
labelled `published` or `current`, and where the two differ the server
emits a note that the UI renders verbatim. The gap that leaves is filled
by the audit timeline, which carries the individual tag add/remove actions
— which is §4's own instruction, not a workaround.

**§4 audit: no parallel history table.** The versions are the content
record, `audit_log` is the action record, and `GET …/history` joins them at
read time. Actor resolution moved up into `audit.py`
(`parse_actor_uuid`/`resolve_identities`/`identity_out`, previously private
to `routers/audit_log.py`) rather than being copied — two routers
rendering "who did this" must agree on ADR 0006's anonymized-vs-deleted
distinction, and a second copy of that fallback chain is the drift this
codebase keeps getting bitten by.

**§5 point-in-time: extended, not regressed.** Approved documents now
render into `ssp/06_documents.html` and the consolidated SSP PDF through
the same `markdown_doc.render_html`. `DocumentSnap` carries the approved
version's *own* Markdown — never "whatever is current for this document" —
and rendering stays on the render side of `render_bundle`'s pure-function
split, which also means a rendering fix ships to future exports only and
cannot retroactively alter a ZIP already handed to an assessor. Only
`approved` versions reach the bundle: a draft is not a compliance record
and a superseded one is the previous answer. `test_lifecycle.py`'s
three-export comparison was extended to cover the new section rather than
gaining a parallel check, and its Step 3d policy now carries a real
Markdown body with a script tag, an event-handler attribute and a
`javascript:` link in it, so the assessor-facing bytes are asserted both
to render the structure and to neutralise all of it.

**§8 audit — the post-commit RLS read pattern. Reported, not quietly
fixed, per the instruction.** The shape N.1 hit is real but the predicate
is narrower than "any read after commit": `db.py`'s `SessionLocal` sets
`expire_on_commit=False`, so already-loaded attributes survive. What does
not survive is (1) a column populated by a server-side SQL expression
during an UPDATE (`onupdate=func.now()`), which SQLAlchemy expires and
re-fetches lazily, and (2) any explicit `refresh()` or fresh `select()`
issued after the commit — because `SET LOCAL app.current_org` is
transaction-scoped and `commit()` ends that transaction.

Exposure, from an AST sweep of every function in `backend/app` that reads
through a session after its first `commit()`:

- **12 models carry `onupdate=func.now()`; 8 of those are on RLS-gated
  tables** (`control_state`, `evidence_task`, `poa_m_item`,
  `implementation_statement`, `org_product`, `scope_entity`,
  `org_liongard_environment`, `document`). Only four routers ever serialize
  `updated_at` at all, and three are already safe: `documents.py` builds
  responses before committing (N.1's fix), `scope.py` sets `updated_at` in
  Python and skips the post-commit `refresh()` on purpose (two existing
  comments), and `orgs.py`'s `refresh()` calls are on `organization` /
  `system_description`, which have **no RLS policy**.
- **Every other `session.refresh()` after a commit is on a non-RLS table**
  — `contact`, `contact_documentation_role`, `raci_assignment`,
  `integration_connection`, `assessment_objective`. (Worth its own look
  later on the auth/RBAC track, separately: `contact` and `raci_assignment`
  have no tenant-isolation policy, so they rely purely on router-level
  filtering. Not this slice's business, and not a bug today.)
  **Corrected 2026-09-28** by the slice that fixed this: the claim that
  both *carry `org_id`* was half wrong. `contact` does; `raci_assignment`
  has no `org_id` column at all and is scoped transitively through
  `control_state_id`. Enumerating `pg_policy` also found two tables this
  reading pass missed entirely (`audit_log`, `system_description`).
  Reading migrations was the wrong instrument for the question; the
  catalog query was the right one. See the Done entry above.
- **`auth.py` already does the right thing** — `_resolve_session` and
  `_resolve_api_token` re-issue `SET LOCAL app.current_org` immediately
  after their heartbeat commit, with a comment saying why. That is the
  existing precedent for a systemic fix.
- **One live instance, and it fails silently:**
  `routers/assessments.py:upsert_statements` commits and *then* runs
  `select(ControlState)` to fill `StatementOut.control_state_id`. A
  zero-row select is not an error, so under RLS the endpoint answers 200
  with `control_state_id: null` for every statement — no exception, no log
  line. Confirmed empirically, not by reading:
  `tests/test_post_commit_rls.py` asserts the wrong-but-current behaviour
  with a note that fixing it means inverting the assertion.

**Why it is latent rather than live today**, and why that is the thing to
act on: the app still connects as `wingrc`, which is table owner and (in
compose) a superuser, and Postgres bypasses RLS unconditionally for both —
migration `0016_app_role`'s own docstring says the cutover to `wingrc_app`
has not happened. The test harness does `SET ROLE wingrc_app` per request
precisely so this class surfaces *before* the cutover, the same way
`docs/PLAN-auth-rbac-completion.md` records it catching the session-fixation
gap. So this is not forty call sites needing quiet fixes; it is a small,
enumerated set plus one silent bug, and a decision to make at the cutover:
re-issue `SET LOCAL` on a SQLAlchemy `after_commit` event (fixes the whole
class, including `refresh()`, and matches what `auth.py` already does by
hand), versus keeping the current explicit convention of building responses
before the commit. **Jarrod's call; deliberately not made here.**

**Verification.** Bench-stack on an isolated wl-util-1 Docker Compose
project (`wingrc_verify_20260928_n2`, own volumes, nginx on 18080/18443 via
an uncommitted bench-only override since the live stack owns 80/443) —
live `wingrc` project confirmed `running(5)` before, during and after, and
the bench project torn down with `down -v`. **1468/1468 backend tests**
(N.1 left 1371), `ruff check .` clean, **330/330 frontend tests** (was
156), `tsc -b` clean, `vite build` clean. Plus a real browser walkthrough
against that stack: created a document, typed Markdown with a `<script>`
tag in it and watched the preview render the heading and escape the script
as literal text with no console output at all; saved three versions and
confirmed each one appended rather than mutating; opened Compare and saw
"phishing-resistant" highlighted as a word-level span inside an otherwise
unchanged line, with the objective-basis note and the actor-resolved
timeline beneath it; opened version 1 and got "initial version" rather than
a broken panel; and drove the concurrent-edit case with two tabs — the
second saver got "someone else saved a newer version… your text has not
been lost", the winning version number named, a working "see what changed"
link, and their own text still sitting in the editor.

**Not done here, on purpose:** approval workflow and review cadence stay
N.3 (`under_review` remains the informational marker N.1 made it),
templates/variables/branding stay N.4, suggestions stay N.5,
`storage_key` is still carried and unexercised, and `.docx` import is
N.4 — confirmed to be able to target this format, not built.

**Worth a decision later, not acted on:** the frontend has no
`package-lock.json` and `deploy/nginx/Dockerfile` runs `npm install`, so
TipTap's ~30 transitive dependencies float even though TipTap itself is
pinned. For a compliance product a committed lockfile plus `npm ci` is the
thing that actually makes the build reproducible. That changes the deploy
path, so it is its own decision rather than a side effect of this slice.
Note also that `docs/bench-stack-verification.md` already tells a verifier
to run `npm ci`, which cannot work today — the real build path is
`npm install`.

---

### Document library N.1: core record shape, versioned from day one ✅ DONE (2026-09-24)

`docs/PLAN-document-library.md` has the full five-slice plan. This is N.1,
the foundation N.2-N.5 build on. `Document`/`document_objective_tag`
confirmed genuinely absent before starting (grounding check, not assumed);
`importers/document.py` confirmed to be the unrelated AI vendor-CRM
extractor, not this feature.

**§1 decision: document/document_version split, and why.** Roadmap item
P's exact lesson (`ProductBaselineVersion`'s own docstring) — baseline
mappings were built mutable, an already-activated tenant's justification
got silently rewritten by a later reimport, versioning retrofitted across
five tables and two migrations afterward — applies harder to documents: a
document IS the evidence, not a pointer to it. `document_version` is
append-only from migration `0059`, the first migration this feature has.
Split: `document` holds identity/tags/cadence (`doc_id`, `doc_type`,
`title`, `cadence_months`, `current_version_id`, the `is_template_derived`/
`template_ref` monetization markers); `document_version` holds body,
status, and approval (`body`, `storage_key`, `status`, `approved_at`,
`approved_by_contact_id`). `current_version_id` mirrors `Product.
current_version_id` exactly, including the same nullable-then-backfilled-
in-the-same-transaction resolution for the identical circular-FK reason,
and migration `0059` resolves the create-ordering the same way migration
`0054` did (create `document` without the column, create `document_
version`, `ALTER TABLE` to add it).

**Naming:** `cadence_months`, not `review_cadence_months` —
`Organization.review_cadence_months` already names the unrelated per-org
users/devices review cycle; `ReviewCycle.cadence_months` made the
identical naming choice for the identical reason when it copied that same
column. A third, distinct cadence needed a third, distinct name.

**Status transition table** (enforced in `routers/documents.py`, not a DB
trigger — same "application validation, not a CHECK against a sibling
column" precedent `baseline_import.py`'s conditional coverage_basis check
already uses):

| From | To | How |
|---|---|---|
| (new version) | `draft` | Always the starting state, regardless of what version it supersedes |
| `draft` | `under_review` | Manual PATCH, current version only |
| `under_review` | `draft` | Manual PATCH, current version only |
| `draft` / `under_review` | `approved` | `POST .../publish` only — never a bare PATCH |
| `approved` | `superseded` | Automatic, only as the side effect of a *later* version of the same document being published |
| `superseded` | (none) | Terminal |

This is "candidates, never auto-met" applied to documents: approval is a
deliberate, audited action with real side effects, not a status flag
anyone can flip.

**§3 decision: republish supersedes and archives, never deletes.** At most
one `approved` version per document. Publishing while another version is
still `approved` flips that older version to `superseded` and archives
(`EvidenceStateLink.is_archived`) the `EvidenceStateLink` rows its own
`Evidence` row produced — the `Evidence` row itself is never touched or
deleted. This is why "the version that was approved, not whatever is
current" (the other §3 question) has a clean answer at all:
`Evidence.source_document_version_id` is a fixed FK set once at publish
time, so an old `Evidence` row stays permanently resolvable to the exact
version that earned it, and a bundle already exported keeps rendering
those frozen bytes regardless of what happens to the document later. A
*fresh* export after a republish correctly shows only the new version's
evidence (`EvidenceStateLink.is_archived.is_(False)` is already
`bundle_service.py`'s existing filter — no changes needed there at all).
Verified directly: `test_republish_supersedes_prior_version_and_archives_
its_evidence_links`. **Reopened as item X (2026-10-18):** the version
pinning above stands, but the evidence moving to the new version, and
publish attaching it at all, happen without anyone choosing them, against
CLAUDE.md's product invariants. **Closed by item X (2026-10-18):** publish
attaches nothing, and a republish leaves every link where a person put it.

**What roadmap N's original spec kept vs. changed** (its field list dates
to 2026-09-07, before any of this was real code):
- **Kept as specified:** `doc_id`/`doc_type`/`title`/`storage_key`/
  `is_template_derived`/`template_ref`/`approved_at`/`approved_by_
  contact_id` — same fields, now correctly distributed across the two
  tables per §1 rather than one.
- **Changed:** `status`/`version`/`body` move from a single mutable
  `document` row onto append-only `document_version` rows — the entire
  point of this slice. URL keys on `{document_id}` (the internal UUID),
  not roadmap N's illustrative `{doc_id}` path — every other resource in
  this API keys its URL on the internal id, not a natural key
  (`routers/scope.py`'s own precedent), and `doc_id` is operator-supplied,
  human-facing text, not a routing identifier.
- **Added, not in the original spec:** `cadence_months` (N.3 needs it; a
  later `ALTER TABLE` for one column costs nothing today).
- **Deliberately not built in this slice:** any endpoint that writes or
  reads `storage_key` — it's carried in the schema per roadmap N's own
  field list, but there's no upload/download path here, matching how
  `is_template_derived`/`template_ref` are also carried-but-unexercised
  until N.4. A real file-backed version, and the id-based-storage-path
  convention `DocumentVersion`'s own docstring already documents for
  whenever that lands, is N.4's concern — that's also where `.docx`->text
  conversion is decided.

**Found and fixed during the bench run, not a design issue:**
`document.updated_at` has `onupdate=func.now()`; the `current_version_id`
update in create/patch (and the version/evidence writes in publish)
triggers it, and reading that column *after* `session.commit()` ran the
deferred refresh with `app.current_org` already reset by `_app_session`'s
own commit wrapper (`tests/conftest.py`'s own documented trap) — RLS then
matched zero rows and raised `ObjectDeletedError` on a row very much still
there. `ScopeEntityOut` avoids this by never exposing `updated_at` at all;
that wasn't the right fix here since the field is genuinely useful on a
document. Fixed by building the Pydantic response before `session.commit()`
in every mutating endpoint, not after.

**Verification:** bench-stack on an isolated wl-util-1 Docker Compose
project (live `wingrc` project confirmed untouched before and after) --
1371/1371 backend tests, `ruff check .` clean (routers/documents.py added
to the B008 per-file-ignore list, matching every other FastAPI router),
156/156 frontend tests (no frontend changes in this slice), `tsc -b`
clean, `vite build` clean. `test_lifecycle.py` gains Step 3d: publishes a
real document against `IR.L2-3.6.1[a]` before Step 4 marks it met by
hand, asserting `control_state.status` is unaffected by publish, and
extends the existing three-export point-in-time comparison (`impl_html_v1`
now contains the published `doc_id`, compared byte-for-byte against
`impl_html_v2` at Step 8) rather than adding a parallel check. `c3pao_
assessor` confirmed read-only across every mutation; RLS confirmed through
real HTTP, not a direct query; SPRS confirmed unaffected by document
existence or publish, asserted directly. Merged to `main`, deployed to
`dev.wingrc.us`.

---

## Planned

### N. Document Library

**Superseded 2026-09-24 by `docs/PLAN-document-library.md`** (slices
N.1–N.5) — read that file, not this entry, before touching this item. It
also reconciles `ROADMAP.md` item F ("Template document library"), flagged
2026-09-07 as likely-duplicating this section and never resolved until now;
both this inline spec and F's are superseded by the plan doc, which is the
single current design. **No longer unstarted** — this line said "no
`Document` model, no `routers/documents.py`" and went stale the moment
N.1 landed on 2026-09-24; N.1 and N.2 are both done (see the Done entries
above). The plan doc's own status line, not this one, is the source of
truth on progress.

The prerequisite check below (item M / `Contact`) still holds and needed no
re-verification; the rest of this entry is left for historical context
only, superseded by the plan doc's own grounding section and cross-cutting
rules (versioning from day one, variable substitution at import not
render, MSP template sets, approval + review cadence, environment/tool-
driven suggestions — none of which this original spec anticipated).

**Original spec below (superseded):**

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

### P. Baseline versioning ✅ DONE (2026-09-17)

Added 2026-09-11, flagged by the G.9 Tools baseline-library screen
(`docs/PLAN-gui-restructure.md`'s G.9 section) but **predates that
screen** — it is `seed_baselines`'s own long-standing gap, only made more
reachable once a runtime admin action could trigger it, not something
this screen introduced.

**The problem:** `seed_baselines` upserted `Product`/`BaselineControl`/
`BaselineEvidenceSpec` rows by key. Editing `baselines/rocketcyber.yaml`
and re-seeding (via the CLI, or via G.9's import screen) retroactively
changed the compliance claims of every tenant that already activated that
product — their `control_state` was set under the *old* mapping, and the
justification for it was silently replaced under them, with no record
that anything changed. This was the same class of problem this codebase
already refuses to allow elsewhere: `sprs_snapshot` is never
retroactively rewritten, the audit log is append-only, bundle exports are
point-in-time snapshots.

**§0 — observed today-behavior, established on a bench stack before
designing anything:** activating a product, collecting evidence to
`met`, then editing that product's baseline and re-applying left the
tenant's `control_state` completely undisturbed (the write path never
touched existing rows at all) — the danger wasn't "your `met` status
flips," it was invisible: the *justification text* behind an unrelated
already-`met` status could change out from under the assessment with no
record, and — the worse case, confirmed below — a removed control simply
kept being honored forever with zero indication anything was wrong.

**§1 bug, confirmed and fixed:** `seed_baselines`/`_seed_product` upserted
`BaselineControl` by `(product_id, control_id)` and never deleted an
unmatched row; `baseline_import.py`'s `ControlChange.change_type` had no
`"removed"` value, so the dry-run preview couldn't even show it. Dropping
a control from a baseline (vendor CRM corrected, or a reviewer deleting a
fabricated AI-ingested row) was a **total no-op** — the old row stayed
live and the magic loop kept honoring it, with the UI reporting a clean
apply. Fixed by the same versioning change below: a control absent from a
new import simply has no row in the new version. Regression-tested
end-to-end in `test_admin_products.py::
test_dropped_control_is_shown_removed_excluded_from_new_activations_and_orphans_nothing`.

**Option 1 (immutable baseline versions) chosen.** Option 3 (warn-only)
was rejected for the reason flagged when this item was written: G.9's
affected-org-count made the risk *visible*, which was correct as a first
step, but a warning on an action that had become a routine weekly
workflow (edit-after-import, re-runnable AI ingestion) is a warning
reviewers learn to click past — it doesn't answer "what did version N
actually claim," which is the question that matters months later.
Option 2 (implicit pinning by timestamp/hash) was rejected for the reason
already written here: it's option 1 with the versioning made implicit
instead of a first-class, queryable table — harder to answer "which
tenants are on which version" from, and this project's whole convention
(`sprs_snapshot`, `audit_log`, `control_state_history`) is durable,
explicit records, not derived-on-read state.

The footprint matched this entry's own description, not materially
larger: new `ProductBaselineVersion` table (`product_id`,
`version_number` — no redundant "what changed" column; that answer lives
in `audit_log`'s existing `product.import` action, whose `after_value`
now carries `version_number`/`version_created`/`removed_controls`, per
this item's own §3 instruction not to invent a parallel record);
`Product.current_version_id`; `OrgProduct.baseline_version_id` (the pin);
`BaselineControl.baseline_version_id` (NOT NULL, new unique constraint on
`(baseline_version_id, control_id)` replacing the old per-product one).
`seeds/baselines.py`/`baseline_import.py` rewritten so a reimport diffs
against the *current* version's content and, only if anything actually
differs, creates an entirely fresh version with new
`BaselineControl`/`BaselineEvidenceSpec` rows (new UUIDs) — the previous
version's rows are never mutated or deleted again. A no-op reimport
(byte-identical content) is now a true no-op, including leaving
`is_published` untouched — previously every reimport unconditionally
forced `is_published → False` regardless of whether anything changed.
`engine.py` gained `move_org_product_version` (below) and had `_run_loop`/
`activate_org_product` updated to resolve `BaselineControl` by the
pinned/target version, never by bare `product_id`. Two migrations:
`0054_baseline_versioning` (schema + backfill) and `0055_footprint_version`
(extends `auth.product_deployment_footprint()` with per-org
`version_number`, since "which tenants are on which version" needs an
answer from the UI).

**A design assumption worth recording:** `bundle_service.py` needed *zero*
changes. It already resolves baseline data exclusively through
`ControlStateContributor.baseline_control_id` — never through bare
`product_id` — so once `BaselineControl` rows stopped being mutated in
place, point-in-time bundle correctness fell out for free. In practice
today's bundle only renders `Product.name`/`key` (deliberately
un-versioned identity, not a claim) through that join, not baseline free
text (`provider_contribution`/`customer_action`/`note` are Tools-library-
admin-only today) — so this is currently a correctness property of the
*mechanism*, verified by reading and by the reimport tests below, rather
than something with its own bundle-diff test. It matters the moment a
future slice renders that text into the SSP (a plausible near-term
addition per this file's own layer-5 description) — the join is already
safe for that.

**`ControlStateContributor` docstring correction (§0's own requirement):**
its pre-existing docstring argued `baseline_control_id` should resolve
*live* text because "if that baseline_control's own text is edited later
... this row's explanation should reflect the live text, not a stale
snapshot." That reasoning predates versioning and is now wrong about what
"edited later" means: `BaselineControl` rows are immutable once created,
so there is no live-edit case left for that join to resolve against — it
was always going to show the text as it was at the version the pinning
`OrgProduct.baseline_version_id` names. Corrected in place in
`models.py`, not deleted, so the reasoning error is visible rather than
silently replaced.

**Publishing stays per-product, not per-version** — an explicit decision,
not left implied per §4's instruction. There is no workflow in this app
for two versions to be independently published at once: a reimport always
targets a brand-new current version and (when it actually changes
anything) already forces `is_published → False`, exactly as before.
`Product.is_published` is unchanged as a column and unchanged in meaning;
`Product.current_version_id` is the new "which version would a fresh
activation use" pointer it now gates.

**Version-move semantics** (`engine.py:move_org_product_version`, wired
at `POST /orgs/{org_id}/assessments/{assessment_id}/products/{product_id}/move-version`):
a deliberate, explicit, per-tenant action — never automatic on import.
Diffs the org's currently-pinned version against the target version
per objective: newly-covered objectives get a contributor added exactly
like a first activation (`pending_evidence` or whatever
`contributor_added_status` says); objectives whose coverage was dropped
lose their contributor and land in `needs_review` (responsibility
recomputed from any surviving contributor, same precedent
`deactivate_org_product` already established); objectives whose
classification changed have their contributor repointed at the new
version's row and land in `needs_review`; objectives unchanged between
the two versions are left completely untouched, including which specific
(immutable) `BaselineControl` row their contributor points at. Unlike
deactivation, evidence is never archived — evidence already collected
survives a version move. New evidence specs the target version introduces
are fanned into tasks via the same dedup path activation uses (factored
out as `_fanout_evidence_tasks`, shared by both). Every touched objective
gets a `control_state_history` row and a `control_state.update` audit
entry; the move itself gets one `org_product.baseline_version_move` audit
entry naming both version numbers. SPRS recomputes at the end. 12 tests in
`test_baseline_version_move.py` cover gained/lost/changed/unchanged
objectives, evidence survival, the audit/history trail, SPRS regression,
and the three rejection paths (same version, inactive `OrgProduct`,
version belonging to a different product).

**Non-negotiables, verified:**
- **Existing assessments unchanged**: migration 0054 backfills every
  existing `Product` to version 1 and pins every existing `OrgProduct` to
  it, verified against wl-util-1's real data (not just an empty test DB)
  by restoring a `pg_dump` of the live database into an isolated copy,
  running the migration, and diffing counts: 4 products → 4
  `product_baseline_version` rows, all 103 `baseline_control` rows
  versioned, both `org_product` rows pinned, zero orphaned
  `control_state_contributor` rows.
- **SPRS unaffected**: the same real-data copy's two assessments scored
  −199 and −204 before the migration; identical after, both as the stored
  column value and as a fresh `recompute_sprs()` call.
- **"Which tenants are on which version" answerable from the UI**:
  `GET /admin/products/{id}/footprint` now returns `version_number` per
  org (verified against the real-data copy: Acme MSP shows `version 1`
  for both RocketCyber and DattoRMM); `GET /admin/products/{id}/versions`
  lists every version; `ToolDetailPanel.tsx`/`ToolsLibraryPanel.tsx`/
  `ProductCard.tsx` render all of it, including a "Move to vN" action on
  the assessment board's product card when an org's pin is behind the
  product's current version.

**Landed:** bench-verified on an isolated wl-util-1 Docker Compose project
(1292 backend tests, ruff clean, 139 frontend tests, `tsc -b` clean, `vite
build` clean) per `docs/bench-stack-verification.md`, merged to `main`
(`fb23b453b5`), deployed to `dev.wingrc.us` with a pre-flight `pg_dump`
backup (`/backups/pre-baseline-versioning-20260917_194732.dump` in the
`backend_backups` volume) per `docs/deployment.md`'s `--no-deps` sequence,
and re-verified against the real post-deploy database (SPRS scores
identical, counts as above).

---

### Q. Role-aware, user-arrangeable dashboard

**Not started.** Specification only — recorded 2026-09-28 so the design gets
made deliberately rather than assembled under time pressure. Independent of
the document library: it neither blocks nor is blocked by N.1–N.5, and can
be scheduled whenever.

**Jarrod's ask, 2026-09-28**, with an annotated screenshot of the current
G.3 dashboard:

> a more scope driven dashboard with the data most relevant to those
> managing the environment... making the "Widgets" resizable and movable on
> the dashboard with the default looking more like my example with
> Scrollable asset and user lists etc. Right now the widgets are all over
> the place.

**The sketch**, described so the intent survives without the image: SPRS
score and family completion together in the top-left. **Active users** and
**active PCs** as two large, scrollable lists filling the whole right-hand
side — the dominant elements, not cards. Tasks centre-left. POA&M summary
and recent activity as a strip along the bottom.

#### This is two dashboards, not one layout

The framing matters more than the drag-and-drop, and getting it wrong turns
a product decision into a preferences screen.

The current dashboard answers **"how is the assessment progressing"** —
family completion, SPRS, statement counts by status, evidence expiring,
blocked objectives, the needs-review queue. That is a *consultant's*
question, asked in the run-up to an assessment.

Jarrod's sketch answers **"what is in this environment and what needs
attention"** — who has access, what devices are in the boundary, what is
pending approval, what changed recently. That is an *engineer's* question,
and it is the one an MSP asks daily.

So the feature is **role-aware default layouts, with per-user arrangement on
top** — not "let people drag boxes around." Without the defaults, every
engineer has to rebuild Jarrod's layout by hand and most never will; the
customization would exist and go unused, which is the usual fate of a
layout editor shipped without opinionated defaults.

Map defaults onto the existing role vocabulary (`auth.py:_ROLE_RANK`) and
decide each one deliberately rather than shipping a single default and
calling the rest customization:

| Role | Default view |
|---|---|
| `msp_engineer` | The scope/environment view — Jarrod's sketch |
| `msp_admin` | Assessment view (plus the admin-only widgets it can already see) |
| `consultant_admin` | Assessment view — compliance data without identity/security admin |
| `customer_poc` | Whatever is genuinely actionable for them; decide, don't default it to the consultant view |
| `c3pao_assessor` | Read-only evidence and completeness view |

#### Why the current dashboard reads as "all over the place"

Worth recording, because the cause is specific and is not widget choice.
`styles.css`'s `.dashboard-grid` is
`repeat(auto-fill, minmax(280px, 1fr))` — a uniform auto-fill grid, so
every widget gets an identical footprint and the whole arrangement reflows
purely by viewport width. The SPRS score (one number) occupies the same
space as the family heatmap (fourteen rows), and nothing expresses
hierarchy or grouping. That is the actual complaint; a layout engine is one
fix for it, but so is a deliberate default grid, and the second is a
prerequisite for the first being worth anything.

#### Grounding — what exists, verified 2026-09-28

- **`routers/dashboard.py` is assessment-scoped**:
  `GET /orgs/{org_id}/assessments/{assessment_id}/dashboard`. Jarrod's
  sketch is **org-scoped** — active users and devices in the boundary have
  nothing to do with which assessment is open, and a new tenant with no
  assessment yet still has an environment worth showing. **This is the
  structural decision the slice turns on**: either the scope widgets get
  their own org-scoped endpoint, or the dashboard stops being one endpoint.
  Do not assume the existing route can simply grow.
- **It is one aggregation endpoint returning all nine widgets in one
  payload**, deliberately (its own module docstring explains the choice,
  mirroring `OnboardingStatus`). That shape is a good fit for a fixed
  dashboard and a poor one for an arrangeable dashboard where a given user
  may be showing three widgets: every load would compute all nine
  regardless. Revisit the round-trip-versus-waste trade explicitly; it was
  right for G.3 and may not survive this item.
- **Recent activity is already excluded from that payload on purpose** —
  `audit_log` is `msp_admin`-only, and folding it in would mean either
  leaking it to `customer_poc`/`c3pao_assessor` or conditionally omitting a
  field per role. The frontend calls `GET /orgs/{org_id}/audit-log` directly
  with its own gate intact. **That is already the precedent for the rule
  below**: the gate lives on the endpoint, not on whether a widget is
  rendered.
- **Asset and user list widgets likely need no new backend** —
  `GET /orgs/{org_id}/scope?entity_type=…` already returns scope entities,
  `routers/users.py` covers users, and `routers/liongard_sync.py` covers
  pending approvals. **But see the cost note below: `get_scope` has no
  pagination and no `status` filter** (only `entity_type`), so it returns
  the tenant's entire inventory and cannot answer "just the ones pending
  approval" server-side. Both are small additions, and both are needed
  before a live list widget is reasonable.
- Every aggregation in `dashboard.py` fetches raw rows and rolls them up in
  Python rather than in SQL, deliberately. Adding list widgets means more
  raw rows, not fewer.

#### Design questions to settle when this is picked up

- **Where layout lives.** Per user *per org*, almost certainly — the same
  person consulting on six tenants wants the same arrangement in each. A
  JSONB column or a small table; the choice is not the interesting part.
  **The interesting part is that this is *preference*, not compliance
  data.** It needs no versioning, no audit trail, no point-in-time
  treatment, and no append-only discipline. Stated explicitly here because
  this codebase applies that heavier discipline nearly everywhere and
  applying it here out of habit would be wrong — losing a layout is an
  annoyance, not an integrity failure.
- **Reset to the role default must be one click and must not feel
  destructive.** People experiment with layouts and need a way out.
- **The widget catalogue.** Movable widgets imply a registry: what exists,
  what data each needs, which roles see it by default. **RLS and role
  checks stay on the endpoints, never on widget visibility.** A hidden
  widget is a UI convenience; it must never be the only thing between a
  role and data it should not have. If a widget is hidden from a role
  because that role may not see the data, the endpoint must refuse it too —
  and the endpoint is the thing to test.
- **Scrollable live lists change the dashboard's cost profile.** Today it
  renders counts. Asset and user lists mean real queries on every load, per
  org, and an MSP with forty tenants will notice. Paginate or cap, and
  **measure before and after** — this is the first screen after login and
  its latency is the product's first impression. (The auth-path throughput
  work is the precedent for how to measure it: a real concurrent-load run,
  not a `TestClient` timing.)
- **Empty states.** The current screenshot is mostly zeros and "Nothing
  waiting on review." A layout that looks right full and useless empty is a
  common failure, and a brand-new tenant sees the empty version first —
  which is also the first impression an evaluating MSP gets.
- **Grid library** is the implementer's call with justification;
  `react-grid-layout` is the obvious candidate. The constraint that
  matters: it must degrade sanely at laptop width, because that is where
  this is actually used. Note the frontend dependency budget — N.2 already
  added TipTap (~30 packages, code-split); a second heavy UI dependency
  deserves the same supply-chain scrutiny and the same code-splitting.

#### Out of scope for this entry

No scaffolding, no columns added in anticipation. When this is picked up it
gets its own plan doc and its own prompt, like the document library did.

---

### Workbook importer: scope, and the shared natural key (2026-10-09)

Check 3 of the Lists follow-up deploy ("import Jarrod's real workbook")
**passed as a dry run; the apply is deferred to L.1.** The dry run proved
what the check existed for:

- `CUI` resolved to `CUI Asset`.
- `[PLACEHOLDER - reason]` cells survived verbatim.
- No alias row warning appeared, because live predated that fix.

It wrote nothing. The apply was not run because the dry run showed it
would have created duplicates:

- WL-DT26 under its placeholder serial, because Liongard keyed it by
  hostname.
- Jarrod by name, because Liongard keyed him by email.
- 32 malformed "persons" from a multi-section users tab.

**Missing rows are never acted on:** every apply path writes NEW/CHANGED
only. The real WL-DT26 and Jarrod would have survived beside their
duplicates, not been replaced.

**Scope:** the workbook importer reads the Authorized-Entities *sample*
shape, one header per tab. Jarrod's real 44-workbook library follows a
richer convention (`docs/PLAN-list-library.md` §3) and needs L.1's
importer. The two importers should converge, not coexist. Multi-section
tabs are deliberately not taught to this one: §2a of that plan explains why
`3.1.1a`'s rows are accounts, not people.

**Next slice:** one natural-key derivation shared by both importers
(placeholder serial → hostname fallback; email before name for people,
with a name-keyed row surfaced as a possible match, never auto-merged), and
source precedence on CHANGED rows (a connector-supplied field is reported
as a conflict, never overwritten by a workbook).

### R. Deploy the image CI scanned, not an equivalent one (registry)

**State (2026-10-09):**

- Live runs images built on the host from one pinned commit
  (`deploy/deploy.sh`; `/health` reports `build`). Since the
  deploy-scan-record change, those exact images are also Trivy-scanned at
  deploy time, with a per-deploy record kept on the host.
- CI's `image` job builds and scans the backend image *and discards it*.
  It never builds nginx.
- The artifact serving requests is therefore *equivalent* to the one CI
  scanned (same commit, same Dockerfile) but not *identical*. Dependencies
  resolve at build time, and nginx's `npm install` has no lockfile. CI's
  scan result does not attach to anything that runs.

**The fix:** CI pushes both images to a registry, tagged by commit, and
live pulls them by digest. Then the checkout on the host stops mattering
at all.

**Trigger — do this when either becomes true; not before:**

- someone other than Jarrod can deploy, or
- a customer or assessor asks what was scanned.

Either makes "equivalent" stop being good enough.

### S. Backup, retention and restore-test policy for WinGRC's own deployment

**WinGRC's own production database has no defined backup, retention, or
restore-test policy.** It holds two tenants' compliance records.

Until 2026-10-09, 24 ad-hoc pre-deploy `pg_dump` files (Sep 12 – Oct 7)
papered over that. They sat in `~claude/backups` and the live
`wingrc_backend_backups` volume. They are being deleted deliberately, once
the Lists follow-up deploy's verification passes, because an untracked
full copy of production data is a liability, not a backup.
They were never a regime: unscheduled, unencrypted, unrotated, on the same
host as the database, and never restored. **The restore has never been
exercised.** That is the part that usually turns out to matter.

Not designed here. Whoever picks this up decides at least:

- schedule;
- retention and rotation;
- off-host destination;
- encryption at rest;
- who can read the dumps;
- a periodic restore into an isolated stack, with row counts and a
  `recompute_sprs` check (the technique in the bench-verification doc's
  live-copy procedure).

The restore test is the deliverable. The dump alone is not.

**Consequence as of 2026-10-09: the live database has no backup at all.**
That afternoon the 24 ad-hoc deploy dumps were deleted:

- 4 in `~claude` on wl-util-1;
- 20 in the `wingrc_backend_backups` volume. An earlier count of "21" was
  a miscount.

So was an orphaned Docker volume, `70ccd0…` (created 2026-09-23 and left by
a bench live-copy restore). It turned out to hold a **full copy of
production**: Acme MSP, Winsorlabs, Test Customer A, the same scope rows,
5 users and 2 contacts. Nothing tracked it, which is the argument for this
item in one example.

No database copy of any kind now exists on that host. That is the correct
state for ad-hoc deploy dumps and the wrong state for a production system;
this item is what fixes it.

**The restore has never been exercised. Not once, by anyone.**

### T. The list export carries what the list is for, and who owns it ✅ DONE (2026-10-17, with L.4)

Shipped inside L.4: the export now carries What goes here, Responsible
and Review cadence. The other half, an export that reads back through the
importer as a template, is **withdrawn permanently** (2026-10-18). This
is not a deferral. An export is a customer's records, and CLAUDE.md's
product invariants say a customer's content never becomes template
source.

### U. The login form shows a validation error as "[object Object]" ✅ DONE (2026-10-20)

**Built in the Done entry "The visual batch: V, L.9 and U".** No caller
inspects the message, and the login test reads what the form shows.

**Found on the L.2.2 bench (2026-10-15).** Signing in with an address
the validator rejects (a reserved domain such as `.test`) returns
FastAPI's 422, whose `detail` is a list of objects, and the login form
shows **"[object Object]"**. Pre-existing and out of L.2.2's scope -- but
it is on the first screen anyone sees, an assessor given access
included.

The cause is general: `api.localLogin` does `new Error(body.detail ??
...)`, and 28 calls in `frontend/src/api.ts` share that line. Any of them
renders an object `detail` (a 422 list, or a `{message, ...}` 409) the
same way. `api.ts`'s `detailText` (added in L.2.2 for the list routes)
already turns both shapes into a sentence. The fix is to route the 28
through it, with a test that a 422 on login shows the validator's
message.

### V. The app's secondary text passes AA contrast ✅ DONE (2026-10-20)

**Built in the Done entry "The visual batch: V, L.9 and U"**, with every
`--muted` context measured. The objective's archived evidence was the one
that failed even on the new token, because it was also drawn at opacity
0.85.

**Found in L.8 (2026-10-16).** The shared `--muted` token (`#6c757d`,
`styles.css`) is **4.45:1** on the app's `--bg` (`#f8f9fa`) -- under
WCAG 2.x AA's 4.5:1 for body text. It is the colour of every
`field-hint`, every `contact-sub` line and every `.contacts-table`
header: 81 `var(--muted)` uses in the CSS, plus a few literal `#6c757d`.
In a compliance product, an accessibility defect on every screen is not
cosmetic; and "just under AA" is the kind of finding that stays just
under AA for a year if it is a note rather than an item.

**The slice:** change the token, not its uses -- `#5c636a` is 5.8:1 on
`--bg` and 6.1:1 on white, and is what L.8 already uses on the list page
(`.list-library-detail` overrides `--muted`; that override goes when the
token changes). Then measure every place `--muted` sits on something
other than `--bg` or white (tinted badges, notices, table hovers) and
report each ratio, since a token that passes on one background can fail
on another. Check the literal `#6c757d` uses too: L.8's example cell is
`#6c757d` on `#f1f3f5` at 4.2:1, deliberately faint and held to the 3:1
a placeholder needs, so it stays -- say so rather than sweeping it up.
Frontend only; screenshots of three or four screens before and after.

### Marking an objective met is recorded nowhere ✅ DONE (2026-10-20; the Done entry "Every change a request makes is audited, and a check enforces it")

**Found 2026-10-20, by the sweep that the evidence section's live walk
prompted. Confirmed in code.** `PATCH .../control-states/{id}`
(`patch_control_state`) sets an objective *met*, *not met* or any other
status. It writes no audit row. The `control_state_history` row it does
write has no actor column. So nothing records who asserted that a
control is met.

This outranks everything else here. "Who said this is met?" is the
first question an assessor asks. The SPRS score is computed from
exactly those assertions (`recompute_sprs` over `control_state`), so
today the score derives from claims nobody is recorded as making.

**How it was found.** The walk showed that detaching evidence is
audited and uploading it is not, and that deactivating a tool is audited
and activating it is not. A sweep of every mutating route (107
POST/PUT/PATCH/DELETE) followed each into `app.*` for a `log_event`
call. Each route it flagged was then read by hand, because the static
pass misses calls made through a module attribute. List and document
links, their move-forward and unlink, and MFA enrolment were flagged,
and are in fact audited.

| Operation | Route | Audited? |
|---|---|---|
| **Mark an objective's status (*met*, *not met*, ...)** | `PATCH .../control-states/{id}` (`patch_control_state`) | **No, and no actor anywhere** |
| Upload evidence to an objective | `POST .../control-states/{id}/evidence` (`upload_evidence`) | **No** |
| Attach evidence as a link/reference | `POST .../evidence/references` (`add_references`) | **No** |
| Activate or reactivate a tool | `POST .../products/{id}/activate` | **No `org_product.activate`.** The restores it does are audited. A first activation restores nothing, so it leaves no audit row: the magic loop's flips are in `control_state_history` only |
| Change an evidence task's status (e.g. to *n/a*) | `PATCH .../evidence-tasks/{id}` | **No** |
| Write implementation statements | `PUT .../controls/{id}/statements` (`upsert_statements`) | **No.** The statement row keeps who edited it, but there is no audit row |
| Start an assessment | `POST .../assessments` | **Only** its RACI copy-forward; no `assessment.create` |
| Create an org | `POST /orgs` | **No** (nor the memberships it provisions) |
| Set a password from an invite | `POST /auth/set-password` | **No** (the invite is audited) |
| Collect a task's evidence (file or reference) | `.../evidence-tasks/{id}/collect` | Yes, `evidence_task.collect` |
| Approve or reject an asset | `.../changes/{id}/approve`, `/reject` | Yes |
| Create, version, publish or tag a document | `documents.*` | Yes |
| Adopt a template; create, version or tag a list | `list_definitions.*` | Yes |
| Link, move forward or unlink a list or document as evidence | `list_evidence.py`, `document_evidence.py` | Yes, plus their own event tables |
| Create, edit or delete a contact; add or remove a role | `contacts.*` | Yes |
| Scope create, edit, import apply | `scope.*` | Yes |
| RACI assign, bulk assign, unassign | `raci.*` | Yes |
| Users, memberships, tokens | `users.*` | Yes |

**The pattern is old code against new, not destructive against
constructive.**
- No destructive route is unaudited. But not every constructive route
  is unaudited either.
- Every unaudited route is in the assessment core: control state,
  evidence, tasks, statements, activation, assessment and org creation.
  That is the code written first.
- Every module built later (documents, lists, contacts, scope, RACI,
  users) audits additions and removals alike.
- The convention ("every state change writes an `AuditLog` row") was
  set after the core was written, and never applied backwards. Removals
  were audited later, one at a time, when someone worried about
  destruction, as `delete_evidence`'s exemptions were.

**That makes enforcement the fix, not the rows.** The rule exists and
nothing enforces it. The rows are mechanical once the list is fixed. A
route-table test that fails when a mutating route writes no
`log_event` is what keeps the next module, or the next route in an old
one, from drifting back. It follows the pattern of I.1's deny-by-default
harness for authorization. The framing also says where else to look:
any old-core write path outside the routers, such as the CLI and the
scheduler.

**The slice (backend only):**
- The enforcing test first. **Preferred: a behavioural check, which
  tests what a route writes. The fallback is a method-based check, which
  tests its HTTP verb.** This is not an even choice left to the slice.
  If it were, the cheaper one would get built and its exemption list
  would exist forever.
  - **Why behaviour.** The verb is a proxy for behaviour, and the proxy
    drifts. `require_write()` gates by HTTP method for the same reason,
    and the kiosk role ran into it. All four of the likely exemptions
    (the workbook import dry run, the baseline-import dry runs, research
    URL suggestion, MFA enrolment's first step) are POSTs that write
    nothing. A behavioural check never includes them, so there is
    nothing to exempt. It also stays true when someone adds a fifth
    dry-run POST without thinking about the test.
  - **The shape.** In the test harness, a session `after_flush` listener
    records which tables a request inserted into, updated or deleted
    from. If any of them is not `audit_log`, the request must also have
    written an `audit_log` row. Installed in `conftest.py`, it checks
    every request the existing suite already makes, at no extra cost per
    route.
  - **What it costs, and what it does not cover.** A route is checked
    only on the paths some test drives. So it needs a companion: every
    mutating route has at least one test that reaches a write. That one
    can be static (route table against test coverage).
  - **Its exemption list moves to tables, and it does not vanish.** Some
    writes are bookkeeping, not a state change a person makes: a login's
    session row, a notification, an SPRS snapshot, a rate-limit
    counter. Those tables are exempt, each with a written reason. That
    list is shorter, and changes far less often, than a list of routes.
  - **Every exemption carries its reason, and the test enforces it.**
    Whichever check is built, its exemptions are a dict from name to a
    written reason, and the test fails on an empty reason. An exemption
    list is only as strong as the friction of adding to it. If one line
    silences a new entry, the rule becomes a formality, which is how
    `delete_evidence`'s exemptions accumulated, one sensible entry at a
    time. Writing the reason is what makes someone ask whether they are
    exempting or evading. The precedent is `test_list_builder.py`'s
    `_SETTLED`, with one step further: there the reasons are a comment
    above a set, so nothing requires one per entry.
  - **The method-based fallback,** if the behavioural check proves
    impractical, says why in the Done entry. Its exemption list (POSTs
    that write nothing) must show each route writes nothing, not just
    assert it. **If either list ends up empty, say so in the Done
    entry:** an empty list is a stronger claim than a short one.
- Then one audit row per unaudited route above: before and after
  values, `via`, and for evidence the title, artifact type and
  objectives linked, never the bytes or a URL. Each gets a test that
  fails without its row.
- `org_product.activate` says whether it was a reactivation, and the
  baseline version pinned.
- Not in this slice: an actor on `control_state_history` (the next
  entry).

### No test calls logout or reset-MFA

**Found 2026-10-20 by the audit check's companion.** Two
security-relevant routes have no test at all:
- `POST /auth/logout`: whether it revokes the session server-side, and
  that the cookie is cleared, is asserted nowhere;
- `POST /orgs/{org_id}/users/{user_id}/reset-mfa`: clearing an
  enrolment, the backup codes and the account's activity, and its
  `user.mfa_reset` audit row, is asserted nowhere.

This is separate from the audit work. Zero coverage on routes a session
and an MFA enrolment depend on is its own finding. Both are listed in
`tests/audit_check.py`'s `NOT_REACHING_A_WRITE` with that reason. The
companion fails the run once a test reaches their writes, until each
entry is removed.

**In the same item, smaller:** resolving a review-cycle flag is tested
only as a `customer_poc` refusal (403). A resolve that writes is not, so
it too is listed until a test reaches it.

### Docstrings' universal claims: measure first -- low priority

**Recorded 2026-10-21.** CLAUDE.md's convention brings an existing claim
under a test only when its code is touched. That leaves the claims in
files nobody edits, and those are the kind a later change makes false
without anyone noticing.

**This item measures and changes nothing.** Grep docstrings and comments
for universal claims: "never", "always", "only", "every", "none", "no
production path", and closed lists of callers or cases. Report the count
and the list. Five is an afternoon's work, done in place. Fifty means
choosing the load-bearing ones, the claims a reader would act on. The
count decides which, so nothing is fixed before it exists.

### An objective's history says who changed it

**Recorded 2026-10-20, split from the item above on purpose.** Once
that slice lands, the audit log will answer who marked an objective
*met*. The objective's own history will still not:
`control_state_history` has no actor column, and that history is what a
person actually reads. Two histories in two places is how the question
goes unanswered. L.2.2 met this for lists: tag events went into the
list's own History rather than leaving them to the audit log. L.10 made
the same call for evidence links (`list_link_event`, migration 0068;
see its Done entry).

The fix has the same shape: an actor on `control_state_history` (a
migration), written by every path that writes a history row, and shown
in the objective's history. Existing rows stay actor-less and say so;
they are not backfilled from guesses. The audit rows from the item above
are the record from that slice on.

### A replacement named for a tool shows on the tool's own record

**Found on the evidence section's live walk (2026-10-20); framing
Jarrod's.** A named replacement is a fact about the tool, not only about
the evidence that moved. It belongs on the deactivated product's own
record: "Datto RMM, deactivated 20 Oct, replaced by RocketCyber (stated
by Jarrod)". The archived-evidence line becomes one place it surfaces,
not the only one.

**Sizing (2026-10-20): small, but not frontend only.**
- `org_product` already stores `deactivated_at`, `deactivated_by` and
  `replaced_by_product_id` (migration 0071).
- Only the archived-evidence read (`routers/evidence.py`) returns them
  today. The tools listing does not, so it needs those three fields and
  the replacement's name: a small backend change, then the line on the
  Tools screen.
- **Found while sizing: a reactivation leaves the replacement in
  place.** Live has one now: Acme MSP's Datto RMM is `active` again (the
  walk's reactivation, 2026-10-20 16:20 UTC) with
  `replaced_by_product_id` still RocketCyber. **Decided (Jarrod,
  2026-10-20): show the replacement only while the tool is
  `decommissioned`.** The column is left alone, and the deactivation's
  audit row keeps the statement. Test a reactivated tool: it shows no
  replacement.
- Still never inferred, and still never credits the replacement.

### V.1. Links and ghost buttons pass AA contrast (`--primary`) -- next visual item

**Found in V (2026-10-20), measured.** `--primary` (`#0d6efd`) is
**4.27:1** on the app's `--bg` (`#f8f9fa`), under AA's 4.5:1. That covers
every ghost button and text link on the page background, among them *←
All lists*, *Adopt templates*, the Lists index's list titles, *Edit*,
*Generate Assessor Bundle* and *Add tag*. On the refusal panel's
`#fff3cd` (*Keep it*) it is 4.06:1.

**The before, so the after can be checked** (WCAG 2.x, `#0d6efd`):

| Foreground on background | Where | Before |
|---|---|---|
| `#0d6efd` on `#f8f9fa` (`--bg`) | ghost buttons and links on the page | 4.27 |
| `#0d6efd` on `#fff3cd` | *Keep it* on the refusal panel | 4.06 |
| `#0d6efd` on `#ffffff` | links and ghost buttons in cards and drawers | 4.50 |
| `#ffffff` on `#0d6efd` | primary buttons | 4.50 |
| `#ffffff` on `#0b5ed7` (`--primary-hover`) | primary buttons, hovered | 5.84 |

**Aim for margin, not 4.5:1.** Two of those sit at exactly 4.50:1. That
passes on paper and fails on screen. The ratio assumes solid fills, but
antialiased text draws its edge pixels at values between the text and
the background. So the contrast a reader actually gets across a glyph is
lower than the computed number, and most so for small and thin type,
which ghost buttons are.
- V landed at 5.78:1 on `--bg`, not 4.51, for this reason.
- V.1 should land with similar headroom on `--bg` and on white, and keep
  white-on-primary well clear of 4.5 too.
- **Do not "fix" a later reading back down to 4.51.** The margin is the
  point.

**Trap: rest and hover move together (Jarrod, 2026-10-20).** Today the
hover fill `--primary-hover` (`#0b5ed7`, white 5.84:1) is darker than the
resting `--primary` (4.50:1). That gap is what makes hover read as a
state change.

Darken the resting token toward ~5.8 and leave `#0b5ed7` alone, and hover
becomes the same as rest, or lighter. Hover then stops reading as a
change of state, or reads backwards. For example, a resting `#0a58ca`
(white 6.44:1, 6.11:1 on `--bg`) would sit *darker* than the untouched
hover at 5.84.

So:
- **Pick the new resting value first.**
- **Derive the hover from it:** darker by a step a person can see. For
  scale, `#094db1` is white 7.75:1 against `#0a58ca`'s 6.44.
- Never tune the two independently.

Put both in the before/after table, rest and hover side by side. This
looks fine in a contrast table and wrong on screen, so check it on the
bench by hovering, not only by computing.

**Why it comes before the backlog.** A link or a button is what a person
is looking for. Secondary text, which is what V fixed, is what they read
past. Failing contrast on the thing you hunt for is the worse defect.

**The slice has V's shape:**
- Change the token, not its uses.
- Then measure every background a `--primary` foreground sits on. That
  includes white, `--bg`, the tool card's `#f0f6ff`, the notice panels,
  and hovered rows.
- Measure the white text on `--primary` and `--primary-hover` fills too:
  the primary buttons, since darkening the blue raises those.
- State each ratio as a number, before and after, against the table above.

The bench walk from V is in the Done entry "The visual batch: V, L.9 and
U", and can be rerun as it stands. Frontend only.

**Also in the found list, to fold in or leave by name:**
- `#198754` and `#dc3545` on the tool card, 4.17;
- the *Inactive* badge, 4.34;
- the textarea placeholder, 4.37.

### L.9. The scope-drawn lists look like the rest of the Lists index ✅ DONE (2026-10-20)

**Built in the Done entry "The visual batch: V, L.9 and U".** The table
frame and hook are in `components/ListRows.tsx`, its style in one
`.list-rows` block, and both panels import them.

**Found in L.8 (2026-10-16).** Library › Lists shows the four lists drawn
from scope (`ListsPanel.tsx`) beside the library lists. L.8 restyled the
library list page (`.list-rows`: near-black semibold headers, sticky,
row rules, a side-scroll cue); the four scope-drawn pages still use
`.contacts-table`'s small grey caps. Two entries on one index now open
two different-looking pages, which reads as a bug rather than as two
screens.

**The slice, narrowly:** those four pages only -- not the other 16
`.contacts-table` users, and no shared component yet. Give
`ListsPanel`'s table the same treatment as `.list-rows` (the header, the
rules, the sticky header inside its own scroll box, the side-scroll cue
from `useSideScroll`), reusing L.8's CSS and hook rather than copying
them: move both somewhere both panels import, so there is one
definition. Keep what is particular to these lists: the "projected from
the live scope graph" line, the excluded-entities note, the empty
explanation, and the `[PLACEHOLDER - reason]` legend. The first row
visible at 1366×768, and contrast measured as in L.8. Frontend only; if
it needs an API change, stop.

### The compose file ships default credentials, one of which makes credential encryption decorative -- a product defect, first

**Found 2026-10-23.** Three defaults, in order of consequence. The order
is by where each one travels. The MinIO and Postgres defaults let in an
attacker who already has network access to the deployment. The Fernet
default goes further, because it decrypts data that leaves the
deployment.

**1. The default Fernet key: integration-credential encryption is
decorative.** `docker-compose.yml` sets
`WINGRC_CREDENTIAL_ENCRYPTION_KEYS: ${WINGRC_CREDENTIAL_ENCRYPTION_KEYS:-dev:DFKu…}`,
a key committed to a public repo. A deployment that does not set its own
encrypts every integration credential an operator enters (Liongard, the
AI provider, SMTP) under a key anyone can read. Whoever holds a copy of
the database can then decrypt all of them. Database copies move
routinely: backups, `pg_dump`s, restore tests, cloud storage. MinIO's
and the owner's defaults stay inside the compose network; this one
leaves with every backup.

`docs/deployment.md` does not mention the key at all. Only a
commented-out line in `.env.example` does. So an operator who follows
the guide never sets it, and gets encryption that is decorative. That
defeats a constraint D.1 set when it chose encryption at rest: a
deploy-time key held by the MSP, so this is "the MSP holding its own
credential, not WinGRC-the-vendor holding a customer's" (`ROADMAP.md`
item D). A key committed to the product's own repo is a vendor key, and
every deployment that does not override it shares it.

This is part of the finding. Until 2026-10-23 the constraint behind
D.1 existed only in conversation. It is now recorded in D.1 in Jarrod's
words: "I never want this product to be at risk for shipping with any
Winsorlabs keys to any products." Nothing could test against an unwritten
rule. It was broken in the same commit as the guard meant to uphold it.

**A guard that is correct, deliberate and unreachable, and it shipped
that way.** `crypto.py` refuses to store or read a credential when the
variable is unset ("fails closed", per the comment beside the default
and D.1's design). Compose guarantees the variable is never unset, so
the refusal cannot fire. A guard whose precondition is always satisfied
upstream is not a guard.

It belongs with the static `/health`, the silently-skipping
`integration` job, the self-matching process check and the rest of W's
list: a signal that reports fine because it checks nothing. But it
failed differently from each of them, and the difference changes what
review has to do:
- **The `integration` job drifted.** It worked when written: on
  2026-06-30 `conftest.py` read `WINGRC_DATABASE_URL`, which the job
  set. A later change made `conftest.py` read only
  `WINGRC_TEST_DATABASE_URL`, and the job silently stopped being able to
  fail. Something upstream changed after the check was right.
- **`/health` and the process grep were wrong in themselves.** `/health`
  returned a static dict from the first scaffold. The grep matched its
  own invocation. Either could be caught by reading the check alone.
- **This guard is neither.** The default and the guard arrived together
  in `c4f999e50b8`, each defensible read on its own. The guard is
  correct code. The default is an ordinary dev convenience. Neither was
  ever wrong alone, and the guard was never reachable through compose.

So checking a guard in isolation cannot catch this, and neither can
re-checking it after later changes, since nothing later changed. Review
has to ask what supplies the guard's precondition, and whether anything
always supplies it. For a fail-closed check on an unset variable, look
at every place that sets the variable, a compose default included.
Live is not exposed: its
backend's only key is `prod-wl-util-1-2026-09-11`, and the dev key is
absent (checked 2026-10-23 by label and substring,
without printing either; W's Fernet row).

**2. MinIO: the deployment guide instructs an override compose
prevents.** `docs/deployment.md` §1 tells the operator to "Set real
values for `WINGRC_STORAGE_ACCESS_KEY` / `WINGRC_STORAGE_SECRET_KEY`
(don't ship with the `wingrc` / `wingrc-dev-secret` defaults)".
`docker-compose.yml` writes both as literals in the shared environment
block, and MinIO's `MINIO_ROOT_USER`/`MINIO_ROOT_PASSWORD` the same way.
A literal is not `${…}`-interpolated, so nothing in `.env` reaches it.
An operator who follows the guide sets the values, sees no error, and
runs on the public defaults. A default credential is a weak setting.
This is worse: a false claim about the product's security posture, in
the guide a customer reads first, which ignores the operator who did the
right thing without saying so.

**3. The database owner.** `POSTGRES_PASSWORD: wingrc` is also a
literal. That role owns every table and bypasses RLS. The repo is
public, so every deployment inherits a known credential for its most
privileged role, and a default password in a compose file is the first
thing an assessor greps for. Nothing in the guide claims otherwise, so it
is a weak setting rather than a false statement. It is still a product
defect, not a deployment one: wl-util-1 has it because the product gave
it. W's credentials rows depend on this item and come after it.

**The same class, in the same file:**
- `WINGRC_DATABASE_URL`'s fallback and `WINGRC_TEST_DATABASE_URL` embed
  `wingrc:wingrc`;
- `.env.example` line 2 is an owner URL at `localhost`. Copied into
  live's `.env`, it became the stale line that broke the cutover's
  rollback (the correction under that Done entry).

The cutover entry's own sentence applies here: "there is no shippable
default for a credential". The `wingrc_app` URL followed it; nothing
else did.

**The fix.**
1. **Compose refuses to start without each credential, and names it.**
   `WINGRC_CREDENTIAL_ENCRYPTION_KEYS`, `MINIO_ROOT_USER`/
   `MINIO_ROOT_PASSWORD`, `WINGRC_STORAGE_ACCESS_KEY`/
   `WINGRC_STORAGE_SECRET_KEY` and `POSTGRES_PASSWORD` each become
   `${NAME:?NAME is required -- set it in .env, see docs/deployment.md}`.
   They get no default, and none is a literal. The database URLs are built
   from the same variables, so `wingrc:wingrc` leaves the
   `WINGRC_DATABASE_URL` fallback and `WINGRC_TEST_DATABASE_URL` too.
   With no default, `crypto.py`'s refusal is reachable again. It stays as
   the second line, for a deployment that does not use compose. CI's jobs
   use their own service containers, not compose, so they are unaffected.
   The bench procedure (`docs/bench-stack-verification.md`) uses compose
   and gains a step to generate its values.
2. **`docs/deployment.md` §1 stops instructing something the code
   prevents.** It says these values are required, not suggested. It names
   each one, says how to generate it (the Fernet key included, which the
   guide has never mentioned), and says that compose will not start
   without them. It also specifies `.env` as mode 600 or 640 (live's was
   664; W's `wingrc_app` row).
3. **The procedure for an existing deployment to move off a default.**
   `POSTGRES_PASSWORD` and `MINIO_ROOT_*` are read only when the volume is
   initialised, so changing them does nothing to a running service. The
   owner's procedure, written once and run on wl-util-1 (the decision
   recorded in W: changed here, not as a separate exercise):
   1. `ALTER ROLE wingrc PASSWORD` through psql's stdin;
   2. update `POSTGRES_PASSWORD` and `WINGRC_MIGRATION_DATABASE_URL` in
      `.env`;
   3. `docker compose up -d backend worker`, since a restart does not
      reread `.env`;
   4. `alembic upgrade head` on boot is the proof the owner URL works.

   MinIO's root credential and storage keys have their own procedure,
   which needs reading before it is written. A deployment that ran on the
   dev Fernet key rotates onto its own with `wingrc rotate-credential-keys`,
   which already exists. It treats every credential entered under the dev
   key as exposed in any database copy made since, and reissues it at the
   provider. wl-util-1 never ran on it.
4. **`.env.example` tidied.** Empty values for every required variable,
   with a comment on how to generate each. Line 2's owner URL at
   `localhost`, which became live's duplicate and broke the cutover's
   rollback, is removed.

**Verification: a deployment missing each value refuses to start and
names it.** On a bench stack, run `docker compose up` once per required
variable with that one unset, and record the error naming it. Start with
`WINGRC_CREDENTIAL_ENCRYPTION_KEYS`. Starting cleanly with everything set
proves nothing, because that was already true before the fix.

**Considered and declined (2026-10-23), so they are not re-proposed:**
- **A security advisory, `SECURITY.md`, a security contact.** Declined.
  Nobody has ever deployed WinGRC except Jarrod on wl-util-1, so there is
  no one to notify. GitHub's public data agreed: 0 stars, forks and
  watchers, no releases, no published image, every issue, PR and commit
  from `winsorlabs`. And Jarrod does not want an inbox. A general contact
  channel is a future item ("A contact channel for bugs and feature
  requests").
- **An in-product `msp_admin` banner when a stored credential's
  `credential_key_version` is `dev`.** Declined. Once compose refuses to
  start without a key, a deployment can barely reach that state, so the
  banner would have almost nothing left to warn about.

**Open:**
- `chown wladmin .env` on live, which needs sudo -- Jarrod, as `wladmin`.

**Editing live config: check the file afterwards.** Removing the stale
`.env` line on 2026-10-23 used `sed -i` as `claude`. `sed -i` writes a
new file and renames it over the old one, so a file you do not own
becomes yours, silently, while the mode is copied. Live's `.env` is now
`claude:wladmin`. After any in-place edit of live config, `stat` it and
compare owner and mode with what was there before. The prior owner was
not recorded, which is how this went unseen.

### A contact channel for bugs and feature requests -- future, not now

**Recorded 2026-10-23.** WinGRC has no channel for anyone outside
WinsorLabs to report a bug or ask for a feature, and none is wanted
while nobody else runs it. Decide the channel when that changes, and
make the decision without publishing an address or creating an inbox
Jarrod does not want. GitHub's private vulnerability reporting is one
candidate for security reports: an in-repo form, and it publishes no
address. A security advisory and `SECURITY.md` were declined for now
(the item "The compose file ships default credentials").

### W. What on this deployment expires or rotates, and what says so first

**Found 2026-10-17**, when dev.wingrc.us's certificate had been expired
three days and the first anyone knew was a browser warning. It is the
seventh case of a signal reporting fine while checking nothing (static
`/health`, export comparison passing on row order, the silently-skipping
`integration` job, six red runs on `main`, a log assertion that could not
fail, `is_populated`'s exact match) and the first a person found by
hitting it. The deliverable is a list and one loud check per row, not a
monitoring build-out.

| Thing | Expires / rotates | What says so | State |
|---|---|---|---|
| TLS certificate, `dev.wingrc.us` + `storage.dev.wingrc.us` | 90 days (Let's Encrypt), renewed by hand | `tls_expiry_check` banner for msp_admin inside 30 days; `deploy/tls/check-expiry.sh` on the host | ✅ 2026-10-17 -- alarm deployed (`c524b2d`) and shown red against the real expired certificate first; renewed the same day (expires 2027-01-15, fingerprint `09:50:5D:26…` on both names), after which the check read 89 days and the banner cleared. Both directions exercised on live, not a fixture. Next due: banner from 2026-12-16 |
| `wingrc.us` domain registration (DNSimple) | yearly | nothing in WinGRC; DNSimple's own renewal mail | unchecked -- confirm auto-renew is on |
| Integrations credentials: `ai`, `liongard`, `smtp` | whatever the issuer sets (an API key's expiry is set at the provider, not here) | a failing test-connection, or a failed job -- after the fact | unchecked; record each issuer's expiry date beside the connection |
| Fernet key (`WINGRC_CREDENTIAL_ENCRYPTION_KEYS`, label `prod-wl-util-1-2026-09-11`) | never expires; rotation is a deliberate act (key versions exist) | n/a | no expiry; rotation procedure undocumented |
| `wingrc_app` database password | never expires | n/a | **Finding 2026-10-23: its copy in live's `.env` was world-readable.** The password was generated into a mode-600 file, applied through stdin and never printed (the cutover's Done entry; a 2026-10-23 audit printed only its length). But `.env`, which carries it in `WINGRC_DATABASE_URL`, sat at mode 664 in the same directory. For how long is not recorded; `sed -i` keeps the mode, so the 664 predates the 2026-10-23 edit. What actually kept other accounts out was `/home/wladmin` at 750, a parent directory's mode nobody set for this. The host has no accounts besides `wladmin` and `claude`, and both are in group `wladmin`, so no other login account could have read it (root aside). Mode set to 640 on 2026-10-23. The owner is still `claude` (the `sed -i` lesson under the item "The compose file ships default credentials"), and returning it to `wladmin` needs sudo, which `claude` lacks. **Not rotating:** no reader outside the two accounts that already hold it was possible, and its other copies are where they belong (`.wingrc_app_pw` at 600, `pg_authid`, the backend and worker environments; a search of `/home/claude` and `/home/wladmin` found no others). Rotating would be housekeeping with a short connection-error window, not a fix. Open: `chown wladmin .env` on live, Jarrod as `wladmin`. The guide specifying `.env` as 600 or 640 is part of the fix in the item "The compose file ships default credentials" |
| Owner (`wingrc`) database password | never expires | n/a | **Decided 2026-10-23: change it, as part of the item "The compose file ships default credentials", not separately.** Its value is the compose default, which is public in the repo, so it is known to anyone who reads it. Exposure is limited (the db port is unpublished and `pg_hba` wants `scram-sha-256` off-host), but the role bypasses RLS. Changing `POSTGRES_PASSWORD` does nothing to an initialised volume, so the change is an `ALTER ROLE wingrc` plus `WINGRC_MIGRATION_DATABASE_URL` and a recreate of backend and worker. That procedure is the item's to write once |
| API token in `.scratch/hit_live_endpoint.py` (wl-dt26) | n/a | n/a | **Closed 2026-10-23.** Dead: no `api_token` row on live matches its hash. The row was deleted on 2026-09-07 with its user, unaudited (the audit Done entry's concrete case). The file was deleted 2026-10-23 |
| Entra SSO client secret | up to 24 months, set in Entra | nothing | not configured on live; becomes a row the day it is |
| API tokens (`wingrc_…`) | per token, `expires_at` | the token itself stops working | none live |

**Public-CA issuance for this name is DNS-01 or nothing while the
deployment is LAN-only.** HTTP-01 needs Let's Encrypt to reach port 80 on
a public address; there is none and that is deliberate. Do not re-propose
certbot's HTTP-01 or an automated DNS-01 with DNSimple's personal-plan
token (it reaches every zone, the registrar and billing). Renewal is
`deploy/tls/renew.sh`, by hand, prompted by the banner. A brief of
2026-10-17 proposed exactly that token from the certificate facts alone,
without reading this item; it was stopped and withdrawn. The one thing
that would change the trade is a **scoped, single-zone token** -- DNSimple
offers those on Teams/Enterprise plans, not personal ones -- and whether
this account has them is Jarrod's to read (Account › Access tokens).

**Unattended renewal without that token: `_acme-challenge` CNAME
delegation, evaluated and not built.** Each `_acme-challenge` name becomes
a permanent CNAME (added once, by hand, replacing any TXT there) into a
separate zone whose credential is disposable; Let's Encrypt follows it, so
a break-in on wl-util-1 can issue certificates for two LAN-only names and
nothing more. What it would take here: `acme-dns` must be an authoritative
DNS server Let's Encrypt can query on port 53 from the internet, which
this LAN box cannot be, so it means a public host to run and keep alive.
The other shape is a cheap second domain at a provider with per-zone
tokens -- but certbot's DNS plugins write to the name they are given and
do not follow a CNAME, so the client changes too (lego or acme.sh, which
do; to be confirmed before building). Either way: one more provider or
service, a second credential for this list, and CNAMEs that fail every
renewal closed if wrong. Not worth it while live is a LAN dev box; revisit
with "goes public" below.

**What the outage cost:** browser-facing evidence downloads and logos
(signed URLs on `storage.dev`) hit the same warning for three days. The
app itself was unaffected -- backend and worker reach MinIO at
`http://minio:9000` inside the compose network and never touch the
certificate, and their logs since 2026-10-14 hold no TLS errors. That
plaintext hop is item 3's FIPS-profile gap, not a skipped verification.

**The eighth case, in this item's own tooling:** the watcher on the first
manual renewal used `pgrep -f renew.sh`, which matched its own command
line, so it reported "still running" by construction after the script had
exited. Use `ps -eo pid,args | grep "[r]enew.sh"`. The rule: a check that
cannot tell itself apart from the thing it checks is not a check.

**What changes when wingrc.us goes public:** HTTP-01 becomes possible and
renewal can be automatic -- and the host becomes reachable, which is the
decision, not a side effect. The same moment is when the FortiGuard
categorisation of the name matters (this machine's FortiGuard filter
already catches `dev.wingrc.us`); the two meet there. No FortiGuard item
exists in this file yet; one was said to be open elsewhere.

**Next, in this item:** the domain and Integrations rows -- a recorded
expiry date per credential, checked the same way the certificate is, by
the same banner -- and a rotation procedure for the Fernet key. A DNS
credential, if one is ever taken, is a row here the day it is.

### W.1. wl-dt26's checkout: a repack that rewrites a live pack and fails -- urgent

**Not the deployment, the dev machine** (`C:\dev\wingrc`), but the same
shape as W: it said so on every commit, and was called benign for weeks.

**Why this is urgent rather than untidy.** On 2026-10-23 the failing
repack produced a pack whose name matched an existing one
(`pack-c7187bd4…`). It rewrote that pack's `.pack` and `.rev` (08:51)
in place, then failed to replace its read-only `.idx` (08:44 the day
before). Its `unknown object type 0 at offset …` lines came from that
window. Afterwards `git verify-pack` on the old `.idx` passed and
`fsck` was clean. That held only because the regenerated bytes happened
to match the old index exactly. It is determinism, not safety. A
different git version, object order or delta choice breaks the match,
and an old `.idx` over new `.pack` bytes is real corruption, with no
clean `fsck` to fall back on.

**The failure.** Background maintenance's `geometric-repack` fails at
the same step every time: `renaming pack to '…/pack-<hash>.idx' failed:
Permission denied`. `git fsck` being clean shows nothing is corrupted
yet. It does not show the repack finished, and it never has.

| When | Packs | Packed |
|---|---|---|
| 2026-10-21 | 327 | 620 MB |
| 2026-10-22 | 345 | 659.5 MiB |
| 2026-10-23 08:51 | 346 | 663 MiB |
| 2026-10-23, after one fetch + one commit | 347 | |

Git's auto-gc threshold is 50.

**The number to collect is packs per git operation, not per day.** The
count follows activity: 18 on a day of commits, fetches and a merge,
1 on a day with almost none. A per-day rate from two points (~9/day)
was offered and was wrong in exactly that way. How soon a fresh clone
reaches 50 is packs-per-operation times how much work is done in it. So
record the count before and after each fetch, commit and merge.

**What stops the attempts.** `gc.auto 0` (set 2026-10-23) does not: the
next fetch and the next commit both still failed. Git 2.54 runs
`geometric-repack` as a `git maintenance` task, which `gc.auto` does
not gate. It was named without checking which mechanism runs the task.
`maintenance.auto false` (set 2026-10-23) is the knob for
`git maintenance run --auto` after commands. It works, read from the
output: the commit that first wrote this paragraph (`3781fe1`), its
push and the next fetch printed no repack error, and the count stayed
at 347 through all three. Both settings stay in place: neither
consolidates anything, so packs keep accumulating, silently. A manual
`git gc` still works once the permission problem is fixed.

**The test for the cause: a clone outside `C:\dev`.** If something
applies permissions to that tree, a new directory under the same root
inherits them, and a clone there would fail the same way and teach
nothing. Clone elsewhere, with automatic maintenance left on in that
copy so it is exercised. If it runs a week of normal work without a
repack failure, the cause is local to `C:\dev`, and no admin rights or
ACL read are needed to know it. If it fails too, the cause is
machine-wide (AV, indexing), and that is the next thing to read.
`.idx` files here are read-only, and `backend/.pytest_cache` also
refuses reads.

**Before any clone:** the read-only audit of 2026-10-23 found nothing in
git that a clone would lose. No unpushed commits, an empty stash, and
every local branch contained in `origin/main`. The untracked files
would be lost: `.scratch/`, `Claude outputs/`, `cleanup_yaml.py`,
`.claude/settings.local.json` and the `.git/info/exclude` lines. So
would a raw `wingrc_` API token in `.scratch/hit_live_endpoint.py`,
which needs revoking whether or not a clone happens.

### X. Publishing a document attaches it as evidence unchosen -- a hard-rule violation, live ✅ BUILT (2026-10-18)

**Built as the Done entry "Item X: a published document is offered, and a
person attaches it".** That entry records what the slice decided. The text
below is the spec as written, kept as it was. One change from it: a
migration was needed after all (0069, `published_objective_ids`).

**Found in L.4 (2026-10-17).** This is not an inconsistency between two
designs. It breaks two of CLAUDE.md's product invariants: candidates are
offered and a person attaches them, and today's state never rewrites
yesterday's record. `routers/documents.py`'s `publish_document` does two
things nobody chooses:

1. **Publish attaches.** It links the new version's Evidence row to every
   tagged objective in every `in_progress` assessment in the org. No one
   is offered the document; it is simply there.
2. **Republish moves the evidence forward.** It archives every live link
   on the superseded version's Evidence and attaches the new one in its
   place. What an objective's evidence says changes because someone
   approved a document, not because anyone looked at that objective. That
   is a silent drift, and it is live in the document path today.

It does not touch `control_state.status` (the docstring says so and that
holds), so no objective is marked met. What it moves is the evidence an
engineer and an assessor read when they decide one, and the bundle's
manifest and implementation page follow it.

**The slice:** documents get L.4's shape.
- A published version is *offered* on its tagged objectives, and a person
  attaches it.
- A republish leaves existing links on the version they name, and says so
  ("Document version 3 is approved. This evidence stays on version 2 until
  someone moves it forward.").
- Moving forward is a deliberate act per objective: a new link, and the
  old one archived.
- Audited the way `evidence.list_link` / `list_move_forward` are.

`list_evidence.py` and `ObjectiveLists.tsx` are the pattern. Whether to
generalise them or write a document twin is decided in the slice, by
counting what the two share.

**Probably no migration.** Each published version already has its own
Evidence row naming it (`source_document_version_id`), so publish keeps
creating that row and stops linking it.

**Decisions the slice has to make and record:**
- **A superseded document is not like an older list version.** A list's v2
  was true when it was linked. A superseded policy is no longer in force.
  So the drift notice for a document should probably be stronger: flagged
  as superseded in the manifest and the bundle, not just "a newer version
  exists". It still moves only when a person moves it.
- **Links already on live.** Every link publish has made so far was made
  unchosen. Count them first:
  `SELECT count(*) FROM evidence_state_link l JOIN evidence e ON e.id = l.evidence_id WHERE e.source_document_version_id IS NOT NULL`,
  split by archived and live. My recommendation is to leave the live ones
  where they are. Removing them would be its own silent move. The drift
  notice then shows wherever they now trail. Re-offering them would be the
  alternative.
- **Tests that assert the current behaviour**
  (`test_republish_supersedes_prior_version_and_archives_…` and the
  publish-links tests). The scenario is still reachable but its outcome
  changes, so update the assertion and write down why. Do not delete them.
- **Review-cycle closing attaches too.** `review_cycles.py`'s `close_cycle`
  links the attestation to every mapped control state. Check it against
  the same rule in the slice and record the answer. My read: it is
  different in kind. Closing the cycle is the person's act that produces
  the attestation for exactly those objectives, and no earlier link is
  moved. But that is to be said in the slice, not assumed here.

**Size:** smaller than L.4. The link model, drift wording and UI pattern
already exist. No migration expected and no bundle-layout change beyond
the superseded flag. Backend: publish/republish, four routes, the
`publish_document` tests rewritten. Frontend: documents on the objective
beside lists. One slice, bench-verified like L.4, with the live link count
taken before the deploy.

### X.1. Documents get a tag editor, and a tag a link rests on cannot be removed ✅ BUILT (2026-10-18)

**Built as the Done entry "Item X.1: documents get a tag editor, and a tag
a link rests on cannot be removed".** That entry records what the slice
decided. The text below is the spec as written, kept as it was. Changes
from it:
- **Practice keys expand.** A typed practice key expands to its
  objectives.
- **The removal check answers in fields.** It returns `reason` and
  `links` rather than `blocked_by`, and the list's check changed the same
  way.
- **The roles are three.** They are `DEFINE_ROLES`, which includes
  `msp_engineer`.

**Why together.** Item X makes a published document an offered candidate
on its tagged objectives, but no screen tags a document. `api.ts` has the
two calls and no component uses them, and live has 0
`document_objective_tag` rows. Without an editor, X ships dormant: a
feature reachable only by API. The refusal on its own would be a second
slice reachable only by API. The editor is what makes X usable, and the
refusal is the rule it must obey from its first day. Neither is worth
shipping alone.

**The refusal is L.4's `tag_removal_blockers` rule, for documents.** It
matters more after X than before. The links are a person's deliberate
act now, so removing the tag silently narrows what a chosen link rests
on. Today the link survives, and the objective shows "it is no longer
tagged with this objective". The record of why it is there goes.

**The slice:**
- **Backend:**
  - `DocumentDetailOut` carries `tagged_objectives` as
    `{objective_id, control_key, objective_key, text}`. Today
    `tagged_objective_ids` is bare ids, which no screen can show.
  - `POST …/objective-tags` also accepts `objective_key`
    (`"AC.L2-3.1.1[a]"`), resolved against the catalog. It is refused when
    the key matches nothing, or more than one framework.
  - `document_evidence.tag_removal_blockers(document_id, objective_id)`
    returns the live links from any version of the document onto that
    objective, in any assessment.
  - `DELETE …/objective-tags/{objective_id}` refuses with 409 and
    `blocked_by`, as `list_definitions` does.
  - `GET …/objective-tags/{objective_id}/removal` runs the DELETE's own
    checks (L.10).
- **Frontend:** a *Tagged objectives* block on the document's Edit/Read
  tab. Each tag is shown as `AC.L2-3.1.1[a]` plus its text, with a remove
  control. It works like the list library's tag input: type the objective
  key, then Add. Removing asks the server first and says what it found,
  in L.10's dialog form, with no Yes when blocked. c3pao sees the tags and
  no controls.
- **Tests:**
  - Backend (~8): add by key, with an unknown or ambiguous key refused;
    detail carries keys; refused while a link rests on the tag, with the
    blocker named; allowed once unlinked; a link in another assessment
    blocks; the check and the DELETE agree; c3pao refused.
  - Frontend (~5, item Z's form): rendered tags, add, blocked dialog with
    no Yes, removable dialog, read-only.
- **Then:** `NOT_TAGGED` becomes reachable only for a tag removed before
  X.1. Keep it, and say so.

**Size:** about L.10's.
- **Editor:** about 100 frontend lines, because it copies the list
  library's tag input and L.10's dialog rather than inventing a picker.
- **Backend:** about 60 lines.
- No migration.

**One slice: yes.** A browsable objective picker over 320 objectives
would not fit, and would be its own slice if wanted later. The typed key
keeps it the size of what L.2.2 and L.10 already built.

### Review cycles attach only what attests ✅ DONE (2026-10-19, deployed; both live detaches run and read back)

**Built as the Done entry "Review cycles attach only what attests".** It
records what the slice found and decided. The text below is the plan,
kept as it was. Changes from it: the record had to be made findable
(migration 0070 and the cycle page's *Record* card), and the bundle now
lists evidence whose links were all archived. The detach archives the
links (the plan said "removed"; the first build deleted them, and was
changed before merge).

**Next, ahead of the evidence section cosmetics** (Jarrod, 2026-10-19).
Two records of reviews that never happened are attached as evidence on
live assessments, against AC.L2-3.1.1[a] and [c], and the dashboard
counts them as coverage.

**The rule: attach only what attests.** A `completed` cycle attaches its
attestation to AC.L2-3.1.1[a]/[c]. A deadline close (`closed_unattested`)
or an undeliverable close (`closed_undeliverable`) keeps its record and
attaches nothing:
- The record is the Evidence row and the cycle with its reviewers.
- Non-response stays recorded, which is what D.3's §3 wanted kept.
- It is not filed as an attestation of objectives nobody reviewed.

**What `close_cycle` does today.** The scheduler (`scheduler.py`, past
`due_at`) calls `close_cycle(status="closed_unattested")`. If no reviewer
was ever notified, `close_cycle` upgrades that to `closed_undeliverable`.
Either way it writes:
- `kind='file'`, `artifact_type='attestation'`;
- title *"Periodic user/device review -- <date>"*;
- an HTML body whose outcome line reads *"…without full attestation"* or
  *"…no reviewer could be notified…, so no review was actually
  attempted"*.

It links that to [a]/[c] on every in_progress assessment, exactly as a
completed cycle does.

**The deciding fact: a screen reports what nothing computed** (CLAUDE.md's
fourth invariant). The dashboard's *blocked objectives* widget
(`routers/dashboard.py:_blocked_objectives`) treats any link as evidence
present. So an objective nobody reviewed drops off it the moment a
deadline close attaches. Other places that read it as an attestation:
- the control-states evidence count (`routers/assessments.py`);
- the bundle manifest, which shows only the title and an `attestation`
  tag.

None of this moves control_state or SPRS.

**On live (counted 2026-10-19, read-only).** There are three cycles,
each opened by the scheduler and each closed by it a day after `due_at`
as `closed_unattested` (`review_cycle.closed_unattested`, actor
`system`). Every reviewer was notified and none attested; each is
`no_response`. No person completed any of them.

| Record | Org | Linked to | Assessment | Other live evidence there | Status |
| --- | --- | --- | --- | --- | --- |
| *…-- 2026-09-13* | Acme MSP | [a], [c] | CMMC L2 Assessment 2026-09-09 | none, none | met, met |
| *…-- 2026-09-14* | Test Customer A | nothing | (no in_progress assessment) | | |
| *…-- 2026-09-15* | Winsorlabs | [a], [c] | CMMC L2 Assessment 2026-09-16 | [a]: AC-POL-01 v4 (the X walk); [c]: none | not_met, not_met |

So three of the four links are the only evidence on their objective, and
the blocked-objectives widget counts each as evidence present.

Acme's two `met` statuses were set by hand on 2026-09-13 at 22:08
(`control_state_history`), three weeks before the record was linked
(2026-10-05). The attestation did not make them met, and detaching it
moves no status. After the detach they are `met` with no evidence
attached. That is the board's existing "mark met" behaviour, a separate
question, recorded here so it is not read as a consequence of this
slice.

**The two existing links: detach them (Jarrod, 2026-10-19).** This
departs from item X's precedent deliberately. Do not reach for X's rule
by analogy:
- **X's links were true evidence, unchosen.** X recommended leaving the
  links publish had made, because removing them would be its own silent
  move (live had none in the end). Those links pointed at real approved
  documents that genuinely were evidence; only the choosing was wrong,
  and a person could keep or move them.
- **These links say something false.** Filed against [a] and [c], each
  record asserts that a review happened, when its own outcome line says
  it did not. Leaving them is leaving that statement on the assessment.
  X's question was who chose a true piece of evidence; this one is
  evidence that is untrue where it is attached.

How the detach is done:
- **Never silently.** Each link is removed with an audit row on the
  Evidence that states the reason (the cycle closed at its deadline and
  no reviewer attested; only what attests is attached), with the cycle,
  the objective key and the assessment.
- **The record stays.** The cycle, its reviewers, and the Evidence row
  with its stored file all stay. The non-response record is preserved;
  it stops claiming to satisfy an objective.
- **Not through the evidence ✕.** `routers/evidence.py`'s unlink deletes
  the Evidence row and its stored file when the last link goes, exempting
  only a document version's row. Used on these, it would destroy the
  non-response record. The slice gives an attestation the same
  exemption. Until it ships, nobody clears these by hand.
- **One command, per org.** Dry-run by default, listing each link it
  would remove; `--org-id` explicit; run once for Acme MSP and once for
  Winsorlabs. It removes only links from `closed_unattested` and
  `closed_undeliverable` cycles, never a completed cycle's.

**The slice:**
- `close_cycle` links only when `status == "completed"`.
- The no-response Evidence row is still written, still reachable from the
  cycle, and not linked.
- The tests that assert a deadline close links (`test_review_cycles.py`,
  the scheduler tests) keep their scenarios. They assert no link and say
  why.
- Item X's `test_closing_a_review_cycle_is_a_persons_act_and_moves_nothing`
  keeps covering the completed path.
- The existing deadline-closed links on live are detached, as above.
- New tests: removing the last link leaves an attestation's Evidence and
  its file; the command removes only deadline and undeliverable closes'
  links, writes one audit row per link with its reason, and leaves a
  completed cycle's links and every Evidence row.

Small: the `close_cycle` condition, the unlink exemption, the command,
and the tests.

**Separately: the widget's "any link means covered" test is weak on its
own.** `_blocked_objectives` asks whether *any* `evidence_state_link`
row exists. It does not filter `is_archived`, so a link archived by a
product's deactivation counts as evidence present. It does not ask
whether the evidence is current either:
- a list link that has drifted counts;
- a superseded document version counts;
- an expired artifact counts.

The deadline-close attestation is one case of the weakness, not the whole
of it. The widget needs a stated definition of "has evidence". At least
live links only, and probably the same set the bundle's per-objective
evidence uses. That belongs with item Z: a screen's count says what it
computed.

### Evidence section cosmetics: one "Not attached" heading, one template sentence ✅ DONE (2026-10-19)

**Built in the Done entry "The evidence section stops destroying things and stops hiding things".** Item 2's "the server's per-list
reason stays as it is" did not hold: the reason itself carried the
instruction, so it now says only "a template: its rows are examples".

**Found on X and X.1's live walk (Jarrod, 2026-10-19).** Frontend only,
no migration. Small.
1. **The heading is doubled.** On an objective where a list and a
   document are both unattached, `ObjectiveLists` and
   `ObjectiveDocuments` each render *Not attached*, one after the other.
   It reads as a bug. The heading belongs to the objective's evidence
   section, not to each source: move it into `EvidenceSection`, drawn
   once when either has an unattached item, and have each component
   render only its rows. The bench missed it because no test renders
   both with unattached items. Add that test, and assert one heading
   (item Z).
2. **The template sentence repeats.** Each template list's not-attached
   sentence ends with `list_evidence.py`'s reason, "a template: its rows
   are examples. Adopt it into an org and fill it in", so three
   templates give three near-identical paragraphs in a row. Say it once
   for the templates together; the server's per-list reason stays as it
   is.
3. **L.10's remaining items: none.** Items 1 and 2 were closed by X.1,
   and item 3 was cleared by the live walk.

### Archived evidence stays visible on its objective ✅ DONE (2026-10-19, design (2))

**Built in the Done entry "The evidence section stops destroying things and stops hiding things", as design (2):** Jarrod settled it.
"Replaced by X" exists only when a person names it at deactivation. **This
entry's first claim was wrong:** archived evidence did not vanish. The
objective's list (`routers/evidence.py:list_evidence`) had no `is_archived`
filter, so it showed archived links as live -- and so did three other
readers. The text below is the plan as written.

**Jarrod, 2026-10-19. Written up only; nothing here is built.** Today,
evidence archived by a product's deactivation vanishes from its
objective (`EvidenceSection.tsx` lists live links only). He wants it
shown greyed and labelled, so that a tool removed with nothing put in
its place is *evident* rather than silent. The bundle already does the
minimum (2026-10-19): evidence whose links were all archived is listed
under *Unlinked Evidence › Links archived*, with the objectives, the
date and the reason. This item is the objective's own display.

**Two forms, and only the first may be computed:**
- **"(Archived)": the alarm case, unambiguous.** The objective's links
  were archived and nothing live covers it. Show the artifact greyed
  with its archive date and the reason the rows record, and the
  objective reads as having no current evidence.
- **"(Replaced by X)": not inferred, ever.** Heimdal out and Defender in,
  both covering the objective, *looks* like a replacement and might not
  be one: a different scope, a different configuration, an overlap
  planned for months. The product does not assert an inference as fact
  anywhere else, and a bundle an assessor reads is the worst place to
  start.

**Two honest designs; the trade:**
1. **Show both, claim nothing.** "Heimdal (archived 12 Sep)" greyed
   beneath "Defender (active)" on the same objective. A reader sees the
   replacement; the product never says it. Cheap, and never wrong.
2. **Make it a person's act.** Deactivating a product asks whether
   something replaces it. "Replaced by Defender" then means someone said
   so, with a name and a date, and a bundle can carry it as a statement
   rather than a guess. More work, and it adds a step to deactivation.

**Recommendation (the brief's): (2) if the phrase is ever to appear in an
assessor bundle, (1) otherwise.** (1) is a prerequisite for (2) either
way, since the greyed display is the same; build (1) first.

**The reason a link was archived must be said, not assumed to be a
deactivation.** Since 2026-10-19 there are two:
- a product's deactivation (`archived_by_product` set);
- the detach of an unattested review cycle's record (no product; the
  audit row `evidence.attestation_detach` gives the reason). Acme's and
  Winsorlabs' AC.L2-3.1.1[a]/[c] will show this.

Moving a list or document link forward also archives the old one, but
that objective has the newer link live, so the drift display (L.4, X)
covers it, not this. Read the reason from the rows, as the bundle's
manifest does (`bundle_service.py`), not by guessing.

**Reactivation, designed with it rather than after it.**
`engine.activate_org_product` restores links it archived (by
`archived_by_product`) and sets the restored states to `needs_review`.
The display then:
- shows the restored artifact as live again, not greyed: its link is
  live;
- relies on the objective's `needs_review` badge for "re-confirm this is
  still current"; the display adds no second warning for it;
- does not restore a link the detach archived. That link has no
  product, so no reactivation finds it (2026-10-19), and it stays
  greyed.

**What it touches:**
- **The blocked-objectives widget**
  (`routers/dashboard.py:_blocked_objectives`) counts any link, archived
  included, as evidence present. An objective whose only evidence is
  archived must read as having none. The widget's definition of "has
  evidence" is recorded under "Review cycles attach only what attests"
  (Planned) and goes with item Z: a screen's count says what it
  computed.
- **The readiness bars** (queued behind the attests slice, brief dated
  2026-10-18; no entry in this file yet). An objective whose only
  evidence is archived reads **red** on the Evidence segment, with the
  archived artifact visible beneath it. That is the case the bars exist
  to catch. When that entry is written, its red/yellow/green for
  Evidence cites this entry's definition (live links only; archived
  shown, never counted) rather than writing its own.

**Not in this item:** any change to `control_state.status` or SPRS
(deactivation already sets `needs_review`); inferring replacement in any
form.

### The evidence ✕ deletes the artifact, unaudited, and says "Remove" ✅ DONE (2026-10-19)

**Fixed in the Done entry "The evidence section stops destroying things and stops hiding things":** it is a detach that archives the
link, keeps the artifact and audits, through one path shared with the CLI.
Counted on live before the deploy, it had destroyed nothing yet.

**Recorded 2026-10-19, while building "Review cycles attach only what
attests", which fixed one narrow case of it.** The objective's evidence
✕ (`EvidenceSection.tsx`, title *Remove*, no confirmation) calls
`routers/evidence.py`'s `delete_evidence`:
- It deletes the link, and when that was the artifact's last link it
  deletes the Evidence row and the stored file too.
- It writes no audit row (first noted, as one line, in L.4's "Found,
  not fixed").
- It reads as detaching evidence from one objective, and can destroy the
  artifact for every objective and every assessment.

Exempt so far: a published document version's record (item X) and a
review cycle's record (2026-10-19). Those exemptions are not the fix.
The fix decides what *Remove* means (detach only, with the artifact kept
until someone deletes it deliberately, or a delete that says so), and
gives every path an audit row.

### Kiosk pages: visitor sign-in -- not built; gated on three decisions

**Jarrod, 2026-10-19. Written up only; nothing here is built.** It queues
behind "The evidence section stops destroying things and stops hiding
things".

**Three things gate it, and each is Jarrod's:**
1. the retention period for check-in rows (§5);
2. who "the Security Officer" is, as a recipient (§7);
3. whether it waits on L.6 (§4).

Kiosk pages are simple screens for compliance tasks done by people who
are not WinGRC users. The category has one member, visitor sign-in, and
that is the one specified here.

**What he described.** A tablet in a lobby. A visitor types their first
name, last name and company, and presses Next.
- **Pre-registered** (someone in the org registered them in advance):
  they get a *Check in* option. Checking in emails the person who
  registered them.
- **Not pre-registered:** a second screen asks the purpose of the visit
  and who they are here to see. Submitting emails the Security Officer.

Either way, the check-in populates the visitor log list.

#### 1. A kiosk is a role, not an anonymous page (Jarrod's decision)

**The mechanism is his:** a generic kiosk user account. On login it
lands on the kiosk screen, and it can reach nothing else.

**Why that beats a URL token: it protects by scope rather than by
secrecy.**
- A lobby tablet is physically accessible and permanently signed in, so
  any credential on it should be assumed known.
- A token in a URL is worth something only while it stays secret.
- A role that can do one thing is worth almost nothing when stolen,
  which is the property actually wanted.
- It needs no new credential type. Sessions, RLS and
  `app.current_org` (through `rls.set_current_org`) work as they do
  everywhere else.

**The finding: it fits neither existing authorisation axis.** Today
there are two:
- **rank** (`auth._ROLE_RANK`: msp_admin 5 down to c3pao_assessor 1);
- **a read-only set** (`_READ_ONLY_ROLES`, gated by HTTP method in
  `require_write()`).

A kiosk must **write one specific thing and read almost nothing**. That
is the opposite shape from `c3pao_assessor`, which reads everything and
writes nothing. Ranking it below c3pao_assessor would not express it:
rank is a ladder, and this is a different axis.

**So a kiosk role is authorised by an explicit allowlist of endpoints,
not by rank and not by method.** Everything not on the list returns 403
for it, including every GET.
- The gate belongs where every route already passes: the
  `get_current_user` / `require_org_access` chain, not each router. Then
  a route added later is denied to the kiosk by default, rather than
  allowed until someone remembers. That is the same reason
  `require_write()` is applied at router level (I.2).
- I.1's deny-by-default harness (`tests/test_route_guards.py`) walks
  every registered route, but it checks only that each carries an auth
  dependency. A kiosk case goes further: it requests every authenticated
  route as a kiosk identity and asserts 403 for each one not on the
  allowlist, so the list in the code and the routes that exist cannot
  drift apart.
- Adding a role is a schema change: the role CHECK constraints, which
  item Y now holds to the model, and `roles.ts` on the frontend.
- Getting this wrong is how a lobby tablet ends up able to read an
  org's assessment. The allowlist is the security boundary of the whole
  feature.

**Four things cut against normal practice. Each is defensible only
because of the allowlist, and each is written down rather than left
silent:**
- **The account is shared.** Several people use one identity, which the
  product otherwise never encourages. That is acceptable here because
  the identity is a device, not a person. The audit record should say
  so: a check-in is attributed to the kiosk (e.g. `actor_type='kiosk'`),
  never to a name that implies a human.
- **MFA.** Every local login today enrols in or verifies TOTP
  (`routers/auth.py`), and a shared lobby tablet cannot practically use
  it. Kiosk accounts are therefore an MFA exception, which in a CMMC
  product needs a written rationale. The rationale is the allowlist: the
  account can do one thing. The exception is tied to the role. A user
  given any other role is back under MFA, and a kiosk account cannot be
  given another role (below).
- **Session lifetime. Decided: a daily login by lobby staff, with no
  idle timeout for the kiosk role.**
  - The 3.1.11 idle timeout (`session_idle_minutes`, 15) would sign a
    quiet lobby tablet out all day. The likeliest outcome is a password
    written on a sticky note on the tablet, which is worse than either
    option.
  - The kiosk session instead ends at a fixed time each day, configured
    per kiosk, and staff sign it in each morning. A stolen session is
    then good for at most one day of doing one thing.
  - 3.1.11 asks for a session ended "after a defined condition". For
    this role the condition is the end of the day, written down here
    and in the SSP text when this is built.
- **No escalation path.** A kiosk account must never be grantable
  another role, another org, or API-token creation. Assert each one
  directly: the membership grant, the role change, and token creation,
  all refused for a kiosk identity, by HTTP. This is the one property
  that would turn a harmless credential into a serious one.

#### 2. An anonymous stranger's typing reaches a compliance record

**Formula injection, for real this time.**
- The visitor's name lands in a list, and lists export to `.xlsx`.
- The sweep that widened `render._FORMULA_PREFIXES` to `= + - @`, tab
  and CR did so for template content written by an operator. This is
  the first path where the input is genuinely hostile: a visitor can
  type `=HYPERLINK(...)` as a surname.
- `render._save()`, the single exit every workbook leaves through,
  should already handle it. **Verify that; do not assume it.** Pin it
  with a test that goes in through the kiosk endpoint and reads the
  exported workbook back, not one that calls the renderer.

**Outbound email triggered by whoever is standing at the tablet.** The
session is authenticated, but the person typing is not vetted. Anyone in
the lobby can make the product send mail to the Security Officer.
- **The ceiling:** a per-kiosk count per hour, stored in the database.
  The only rate limiter today (`auth.check_login_rate_limit`) is
  in-process memory per worker, which several workers and a restart both
  defeat.
- **At the ceiling: record the check-in and stop emailing.** Do not
  refuse it. Recording keeps the access log honest, which is the point
  of the log; a refused check-in is a visitor who was there and isn't in
  it. Reaching the ceiling is recorded on the kiosk, and its settings
  page says so. There is no generic notification table: each feature has
  its own, e.g. `document_review_notification`.
**Rule: email never gates a check-in.** The check-in row is written and
the visitor is told they are checked in before any mail is attempted, and
no failure, delay or absence of mail can block or undo it. This is a
deployment-shape constraint, not a preference: the SMTP connector is
optional, and GCC High and air-gapped installs may have no relay at all.
On those the kiosk works exactly as it does elsewhere, minus the email. A
visitor at a tablet is never stuck behind SMTP.
- A send that fails, or was never attempted, is recorded on the check-in,
  the way `review_cycles.record_notification_result` records a
  reviewer's.
- A test pins it: with no mail connector configured, and with one that
  raises, a check-in succeeds and reads back from the log.

**The pre-registration lookup is an enumeration oracle.** Type a name,
learn whether that person is expected today. The kiosk role cannot
*read* the expected-visitor list, but Next answers questions about it
one name at a time. That is the same disclosure by a slower route,
reachable by anyone standing in the lobby.

**Decided (Jarrod, 2026-10-20): the three-field match, and the one-tap
check-in stays.** *Check in* is offered only when first name, last name
and company all match a pre-registration for today. Screen one already
collects all three, so the flow is unchanged.
- **This reverses this entry's earlier recommendation** (always collect
  the full details and never show which path was taken). That option
  disclosed nothing, but it removed the one-tap check-in for every
  pre-registered visitor. The cost to the flow was larger than the risk.
- **The lookup is held to the per-kiosk ceiling** that §2 already builds
  for email: the same database count, counting Next presses as well as
  sends. At the ceiling the kiosk stops offering *Check in* and every
  visitor goes to screen two. The check-in is still recorded and still
  routed to the registrant behind the screen, so the visitor is never
  refused.
- **No match, and a match on another day, look the same:** both go to
  screen two. Matching folds case and surrounding whitespace, and
  nothing else, so a near miss is not a hint.

**The residual disclosure, stated rather than designed away.** Someone
who already knows a visitor's full name and company can stand at the
tablet and learn whether that person is expected here today. They learn
it when *Check in* appears; nothing else on the screen changes.
- It needs physical presence in the lobby, which is itself logged
  (the check-in row, and the lobby's cameras if it has them).
- It confirms a guess the person could already make. It cannot list who
  is expected, or find a name it was not given. The ceiling bounds how
  many guesses fit in an hour.
- What is learned is that someone from a named company is expected
  today. For most orgs that is low-value.
- **Considered and rejected (Jarrod, 2026-10-20): a per-registration *no
  one-tap* flag** for a visiting company whose name is itself sensitive.
  The mechanism already exists: the operator does not pre-register that
  visitor. No *Check in* appears, they go to screen two, and nothing is
  disclosed. A new field would buy nothing that leaving out the
  pre-registration does not already buy.
- The SSP's PE text should say this in one sentence, so an assessor
  reads it from the org rather than finds it.

#### 3. A check-in must not create a list version

`list_library.create_version` is how list content changes. Versions are
append-only and a trigger enforces it. A busy lobby would write hundreds
of versions a day, and make the list's history useless for the thing it
is for.

**So the visitor log is a sourced list: a projection over check-in
records**, the way the four scope-drawn lists project the scope graph.
- Check-ins are rows in their own table. They are append-only, and
  written only by the kiosk endpoint. The list renders them.
- **It is read-only as a list.** A visitor log cannot be hand-edited,
  which is correct for an access record. A correction is a new row that
  names the one it corrects.
- **It exports and goes into a bundle through the same path as the
  other lists, but what is pinned is a point in time, not a version
  number.**
  - It reads "Visitor log as of 2026-10-19 21:04 UTC, 412 check-ins",
    never "version N".
  - There is no precedent to match: the four scope-drawn lists are not
    in the bundle at all today, and library lists pin a `list_version`
    id.
  - "As of" is reproducible only while no row before it has been
    removed, so retention (§4) and the bundle's claim interact. A bundle
    exported before rows expired stays correct, because it holds the
    rows. A re-export afterwards says less, and must say so (*N rows
    removed by retention before this date*) rather than read as complete.
- **Does it wait on L.6? Recommendation: no.**
  - L.6 is the scope-graph join (`scope_entity` into the ~10 projected
    lists). This projection is over a new table, not scope.
  - What they share is the plumbing for a list whose rows are drawn,
    not stored. Today `projection_view_id` is metadata only.
  - Whichever is built first designs that plumbing for both. Building
    this first is the smaller, cleaner instance of it.
  - **One rule of L.6 does not carry over.** A synced row is an
    observation about the world, which a person accepts. A check-in is
    an event the product witnessed, so it lands directly; nobody accepts
    or rejects that a visitor came in. The next projected list will need
    sorting into one kind or the other, and that is the test that sorts
    it.
- **It is the pattern, not a one-off.** This is the first realisation of
  what Jarrod described when he asked for the list library: "some of
  these lists populated from activities in WinGRC (like asset approval
  → adds to 3.1.1 authorized devices list)". An activity writes records
  of its own, and a list projects them.

#### 4. Personal data

Visitor names, employers, who they came to see, and why. That is PII,
collected from people who are not customers and have agreed to nothing.
- **Who can read the log:** the same roles that read any list, and
  `c3pao_assessor` read-only. Not the kiosk: it writes check-ins and
  reads nothing back, not even the one it just wrote.
- **How long rows are kept, and what removes them: Jarrod's decision,
  and gate 1.**
  - Retention interacts with the control. An access log deleted too
    soon fails the practice it exists to satisfy.
  - Whatever the period, removal is a scheduled job that writes an audit
    row with the count it removed, never a person deleting rows.
- **Is a visitor's own row ever disclosed to them?** No, not through the
  kiosk; it reads nothing. A request from a visitor goes to the org,
  which reads the log like any other list.
- **The screen clears completely after each check-in.** Every field,
  any autocomplete, and anything in the browser's form history: the
  inputs set `autocomplete="off"`, and the page resets its state rather
  than navigating back. The next person in the queue sees nothing of the
  last. Test it on rendered output (item Z): after a check-in, every
  input is empty and nothing of the previous visitor is in the DOM.

#### 5. Which practices it serves: it writes rows and claims nothing

- The visitor log list exists on live. Acme MSP has
  `3-10-4a-visitor-log-own`, a hand-kept list with one row, adopted from
  Winsorlabs' template `3-10-4a-visitor-log`. Both are tagged
  PE.L2-3.10.4 and PE.L2-3.10.5 (read-only query, 2026-10-19).
- The kiosk feeds those practices' evidence, and **a check-in does not
  mark anything met.**
- The projected log is evidence a person may attach, under the same rule
  as everything else (L.4: offered, never attached). It changes no
  `control_state.status` and no SPRS score.
- How the projected list relates to the existing hand-kept one, whether
  it replaces it or sits beside it, is settled when it is built. Either
  way, the hand-kept list's versions are left as they are.

#### 6. "The Security Officer": open question, gate 2

**The brief said the product has no Security Officer. It does, as a
documentation role.**
- `ContactDocumentationRole` includes `security_officer`.
- Two notification paths already route to it:
  `document_reviews.NOTIFY_ROLES` and `liongard_sync.NOTIFY_ROLES`, both
  `("security_officer", "it_admin")`.

What is open is whether the kiosk uses that or something narrower:
1. **The documentation role:** the existing precedent, org-wide.
2. **A RACI assignment on PE.L2-3.10.4.** RACI is per assessment, and a
   lobby is not, so this would need a rule for which assessment.
3. **A per-kiosk setting naming a contact.** This is the most direct,
   and it also covers an org with two sites.

**Whichever is chosen, nobody holding it must be handled, not dropped.**
- On live today, neither Acme MSP nor Winsorlabs has a contact with
  `security_officer`; only Test Customer A does. So the empty case will
  occur on day one.
- `document_reviews.notify_candidates_for_org`'s contract applies: an
  empty recipient list is the caller's to handle explicitly. That exact
  bug (a notification silently dropped because nobody held the role) has
  been fixed twice in this codebase.
- For the kiosk: the check-in is recorded, the missing recipient is
  recorded on it, and the kiosk's settings page says it has nobody to
  notify, before the first visitor arrives.

#### 7. Do not build a kiosk framework (decision)

He said "kiosk type pages", with visitor sign-in as the example.
- **Build the visitor kiosk concretely.**
- **Design the kiosk role and its endpoint allowlist generally.** That is
  the part that would be painful to retrofit, and the part a second
  kiosk would reuse unchanged.
- **Leave page-building generality alone until a second real kiosk
  exists.** One example is not enough to know what varies: the fields,
  the branching, the notification and the record could each differ, or
  none might.

Recorded so the next person does not start with an abstraction.

**Not in this entry:** checking a visitor out. A visitor log usually has
a time out, and his description has none. It is the first thing a
second iteration would want, and is not designed here.

**Cross-reference.** `docs/PLAN-auth-rbac-completion.md`'s "Beyond
I.1–I.9" note points here. A sixth role with a new authorisation axis is
a change to that model, not a new row in `_ROLE_RANK`.

### Y. A test that models.py's CHECK constraints are the database's ✅ DONE (2026-10-19)

**Built in the Done entry "The evidence section stops destroying things and stops hiding things"**
(`tests/test_model_check_constraints.py`). First run: no drift across the
54 CHECKs. Mutation-checked both ways.

**Found in L.4 (2026-10-17).** `models.py`'s `ck_evidence_artifact_type`
had been missing `attestation` since migration 0045, five weeks, and
nothing noticed. Nothing compares the two. The tests run against the
migrated schema (`conftest.py` runs `alembic upgrade head`), so the model's
copy of a CHECK is never exercised at all. It is documentation that
compiles. Alembic's autogenerate does not compare CHECK constraints
either, so `alembic check` would not have caught it. A model copy that
drifts misleads the next reader in exactly the way the attestation one
could have: a new artifact type added to the model alone would pass
review and fail on the first insert in production.

**The test (one integration test, general over every table):**
- In the migrated test database, create a scratch schema.
- Run `Base.metadata.create_all` into it, via `schema_translate_map`.
- Read `pg_constraint` (`contype = 'c'`) with `pg_get_constraintdef` for
  both schemas, keyed by (table, constraint name).
- Assert that the name sets match in both directions and that each
  definition is identical.

Comparing two strings Postgres itself produced avoids hand-normalising
expressions. Postgres rewrites `IN (…)` to `= ANY (ARRAY[…])` and adds
casts and parentheses, and a test that parsed `sqltext` would be fragile
in exactly the way this one must not be. Drop the scratch schema after.
Mutation-check it before trusting it: remove one value from a model CHECK
and watch it fail with the table, the name and both definitions in the
message.

**What it will find on its first run is the real work.** Attestation is
fixed; others may not be. Fix each one in the direction of the database
(migrations are what live runs), unless the model is the one that is
right, in which case that fix is a migration and its own decision. Each
is decided on its own, not swept up in a batch, and each is reported.

**Size:** small. One test file of roughly a hundred lines, plus whatever
drift it turns up. Things to confirm while building: that `create_all`
into a second schema works with pgvector's type (it lives in `public`,
which stays on the search path), and that no model CHECK is unnamed (an
unnamed one gets a different generated name each side, so the test should
require names). Unique constraints, indexes and FK `ondelete` drift the
same way and could use the same comparison. They are a follow-on, not
this item.

### Z. A screen's tests assert what it tells a person -- a product-wide expectation

**Found in L.10 (2026-10-18).** Three L.4 screens misled the person who
designed the rule within ten minutes of first use, under 418 green
frontend tests. Those tests assert which API calls a screen makes and
which strings appear. Nothing in the product tests what a screen
*communicates*: whether two different states look different, whether a
claim on screen was computed, whether a record can answer the question
it is there for. L.4 is where that bit, not where it lives. Every screen
built so far was tested the same way.

**The expectation, for every frontend slice from now on:** each screen
that shows a state a person acts on gets a test on rendered output for
what that state communicates. Specifically:
- **states that mean different things are not drawn alike:** assert on
  structure (which group, which element, which chip), not only on text;
- **any sentence that states a result is driven by the computation it
  reports:** test it with that result both ways, and with the computation
  failing;
- **a record's screen answers its question after the fact:** an attach
  and a detach both still visible.

Each such test is shown to catch its defect by putting the defect back
into an element that exists and watching that assertion fail. Not by a
run against old code where the element is absent, which proves only that
the components differ (L.10's entry says what its own run did and did
not prove).

**Not in this item:** auditing the existing screens. That is a separate
pass, and should be sized by listing which screens show an actionable
state. The candidates are the evidence section, the bundle picker, the
document approval and review states, and the onboarding approval queue.
This item is the rule going forward, so the next slice inherits it rather
than rediscovering it.

## Sequencing

```
Document library (N)
```

(The Personnel connector row that used to hang off this diagram — "→
Personnel connector pull (Liongard / M365 → contacts)" — has been removed.
It never actually depended on the document library; see this file's own
Done section, "Personnel connector — Liongard identities → contacts
import", for the correction and the real dependency.)

---

## Deferred

- **Document-library template content** — paid add-on seed script; depends on document library (N) mechanism being live. **Verified 2026-09-07: N is still fully unstarted** — no `Document`/`document_objective_tag` model, no `routers/documents.py`. (`importers/document.py` is a different, already-shipped feature — AI extraction of a *product baseline* from a vendor CRM/PDF, not the tenant-facing template library N describes. Don't confuse the two on a future pass.)
- **AI implementation statements** — generation worker behind BYO-AI provider abstraction; scaffolding exists. **Verified 2026-09-07, more specifically than before:** `config.py`'s `ai_provider` setting and `backend/app/ai/` are real and already load-bearing — `importers/document.py` (the vendor-CRM/baseline extractor) is a working consumer of that same abstraction today. What's still missing is the per-objective draft-statement path itself: no `draft-statement` endpoint exists on `assessments.py`, and `ImplementationStatement` rows are still authored by hand. The provider plumbing this item needs already exists; the feature-specific generation logic does not. **Correction, 2026-09-19:** `config.py`'s `ai_provider` setting no longer exists — the provider is now resolved from the encrypted `"ai"` connector via `ai/__init__.py:get_ai_provider(session)`, not a string setting; see this file's Done entry, "AI provider credential moved into the connector registry." The rest of this item's status is unchanged: the provider plumbing exists, the draft-statement generation logic does not.
- ~~**Scope connector** — Liongard / Datto RMM → `scope_entity`~~ **Shipped
  2026-09-11 (D.2) — no longer deferred.** See this file's own Done
  section, "D.2 — Liongard device/user pull into scope_entity" entry.
  Datto RMM was not built (Liongard only); a Datto connector, if wanted
  later, is a fresh item, not a reopening of this one.
- ~~**Asset & user onboarding approval workflow — daily Liongard sync**
  (D.3's second half; the periodic review/attestation half shipped
  2026-09-13, see this file's own Done entry).~~ **Shipped 2026-09-18 —
  no longer deferred.** See this file's own Done section entry ("Daily
  Liongard sync + asset/user onboarding approval") for the full writeup.
- ~~**Evidence download hardening** — replace presigned direct-to-MinIO
  download URLs with the backend streaming evidence bytes itself.~~
  **Shipped 2026-09-14 — no longer deferred.** See this file's own Done
  section entry for the full writeup, including what's still pending
  (the §4 load measurement, and the bench-stack verification run).
- **Frontend build determinism** — generate and commit `frontend/package-lock.json` (none is committed — one has been observed untracked on wl-util-1 from a local `npm install`, but that's not what this item is about), then switch `deploy/nginx/Dockerfile` from `npm install` to `npm ci` for reproducible builds. Low priority, not blocking anything currently in flight. **Verified 2026-09-07: still open** — `git ls-files frontend/package-lock.json` returns nothing (not committed), `deploy/nginx/Dockerfile` still runs `npm install`, not `npm ci`.
