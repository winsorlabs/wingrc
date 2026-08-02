# Plan — Auth/RBAC completion (roadmap item I) + frontend admin surface

**Status:** I.1 ✅ merged · I.2 ✅ merged · I.3 ✅ merged · I.4 ✅ merged · I.5 ✅ closed (5 deviations — see I.5; 308/308 integration tests green on wl-util-1, browser smoke test confirmed) · I.6 ✅ merged (all 6 items) · I.7 ✅ merged (users + API tokens admin panels, invite-redemption page) · I.8 implemented pending commit/review (see I.8) · I.9 not started
**Baseline:** 0088757
**Scope:** close the gaps identified in the audit of item I, then land the frontend
surface those endpoints require.

Slices are numbered `I.1`–`I.9`. Each lands as one or more commits directly on
`main`, each ends green, each is independently shippable. Do not batch them.

**Reconciled 2026-08-02:** this originally said "each is a branch." I.1–I.7 all
landed via direct commits to `main` instead (confirmed against `git log` and
`git branch -a` — no merge commits, no per-slice branch ever existed). The
substance of the constraint — ends green, independently shippable, small
commits, pushed promptly so wl-util-1 can pull — held every time; the branch
mechanic never did. Wording corrected to match actual practice rather than
leaving an aspirational rule seven slices ignored. I.8 was cut on a branch
(per the now-superseded text) and fast-forwarded into `main` with no merge
commit before this line was corrected.

---

## Sequencing rationale

`I.1` lands first despite being the least dramatic. It adds no schema and no API
surface, but it produces the regression harness (deny-by-default route test) that
protects every slice after it, and it closes the audit gaps that make the later
role changes traceable. Fixing the assessor hole first and the audit second means
the role changes made *during* the fix are themselves unlogged.

`I.2` is the live security hole and goes immediately after.

Frontend does not start until `I.7`, because the assessor read-only UI cannot be
built honestly until the backend enforces read-only — otherwise the UI is the only
control, which is not a control.

---

## I.1 — Audit completeness + deny-by-default harness

**Goal:** every privilege-affecting mutation is logged; new unguarded routes fail CI.

**Why first:** no migration, no API change, pure additive. The route test is the
guardrail for I.2–I.6.

### Changes

`backend/app/routers/users.py`
- `patch_user`: emit `user.role_change` when `body.role` differs from current, with
  `before`/`after` in context. Emit `user.activation_change` when `is_active`
  changes. Follow the existing `before`/`after` context shape used in
  `test_deactivation.py::test_audit_entries_have_before_and_after_values`.
- `deactivate_user`: emit `user.deactivate`.
- `create_api_token`: emit `api_token.create` with `{name, role, expires_at,
  on_behalf_of}`. Never log the raw token or its hash.
- `revoke_api_token`: emit `api_token.revoke`.
- `create_api_user`: already emits `api_user.create` — confirm it also covers the
  token minted in the same transaction.

`backend/tests/test_route_guards.py` (new)
- Walk `app.routes`. For each route, resolve the full dependency chain
  (router-level `dependencies=` plus per-endpoint `Depends`) and assert it
  contains `get_current_user`, `require_org_access`, or `require_role`.
- Explicit allowlist constant at the top of the file:
  `/health`, `/auth/login`, `/auth/callback`, `/auth/set-password`,
  `/docs`, `/openapi.json`, `/redoc`.
- The allowlist must be a literal list, not a prefix match. A new public route
  should require a deliberate edit to this file.

`backend/tests/test_audit_auth.py` (new)
- Role change writes one `user.role_change` row with correct before/after.
- Deactivation writes `user.deactivate`.
- Token create/revoke write their rows.
- No audit row contains the raw token value (assert the token string is absent
  from every logged context in the test).

### Exit criteria
- `pytest` green, `ruff check` clean.
- Deliberately adding an unguarded route to a scratch router fails
  `test_route_guards.py`.

---

## I.2 — Assessor read-only enforcement

**Goal:** `c3pao_assessor` cannot mutate anything.

**Current state:** the role exists in `_VALID_ROLES`, `_role_rank`, and both CHECK
constraints, and is enforced nowhere. Every mutating endpoint in `assessments.py`,
`evidence.py`, and `contacts.py` inherits only router-level
`Depends(require_org_access())` with no roles passed.

### Design decision

Router-level dependencies cannot branch on HTTP method through the signature, so
use a method-inspecting dependency rather than splitting every router in two:

```python
_READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

def require_write():
    """Rejects read-only roles on any non-idempotent method.

    Applied at router level so new mutating routes inherit the gate by
    default rather than by remembering to add it.
    """
    def _check(
        request: Request,
        current_user: CurrentUser = Depends(get_current_user),
    ) -> CurrentUser:
        if request.method in _READ_METHODS:
            return current_user
        if current_user.role in _READ_ONLY_ROLES:
            raise HTTPException(
                status_code=403,
                detail="Read-only role cannot modify data",
            )
        return current_user
    return _check
```

`_READ_ONLY_ROLES = frozenset({"c3pao_assessor"})` — a set, not a scalar, so a
future `auditor` or `viewer` role is a one-line change.

Note the deliberate coupling: this must be added to the router `dependencies=`
list, *not* left to per-endpoint decoration, so that the failure mode for a
forgotten guard is "assessor is blocked from a new route" rather than "assessor
can write to a new route."

### Changes
- Add `require_write` to `backend/app/auth.py`.
- Add to `dependencies=` on: `assessments`, `evidence`, `contacts`, `bundle`,
  `orgs`, `users` routers.
- `users.py` has no router-level dependency today. Add
  `dependencies=[Depends(require_write())]` at the router level; the existing
  per-endpoint `require_org_access(...)` gates stay as-is (defence in depth).

### Tests — `backend/tests/test_assessor_readonly.py` (new)
Parametrise over every mutating endpoint. For each, assert 403 for
`c3pao_assessor` and non-403 for `customer_poc`:
- `PATCH /orgs/{o}/assessments/{a}/control-states/{cs}`
- `PUT` statements
- `POST` / `DELETE` evidence, task evidence, references
- `POST` / `PATCH` / `DELETE` contacts and contact roles
- `POST` / `PATCH` products (activate/deactivate)
- `POST /orgs/{o}/users`, `PATCH`, `DELETE`, `POST /api-tokens`
- `PATCH /orgs/{o}/profile`, logo upload, system description

Plus positive coverage: assessor **can** `GET` control states, evidence list,
contacts, profile, and **can** `GET` the bundle export.

### Open question — assessment scoping

The roadmap says assessor access is scoped to *one or more assessments*. Today the
model scopes to the whole org, so an assessor sees every assessment the org has.

Two options:
- **(a) Ship org-scoped now.** Correct for a C3PAO assessing the org's single
  active assessment. Defer per-assessment grants.
- **(b) Add `assessor_assessment_grant`** (migration `0018`) and filter reads by
  it. Correct if an assessor should see one assessment and not the org's other
  in-flight work — likely relevant once you have six client environments and
  reuse assessors across them.

**Recommendation:** ship (a) in I.2, and take (b) as I.2b only if a real assessor
engagement needs it. Do not build the grant table speculatively; it touches every
read path in `assessments.py` and `evidence.py` and is much cheaper to add once
the read paths have settled.

---

## I.3 — Token/role coherence

**Goal:** an API token cannot outlive the privilege of the user behind it.

**Current state:** `_resolve_session` re-reads `user.role` each request, so session
role changes take effect immediately. `_resolve_api_token` returns `row.role` —
the role frozen on the token at mint time. `patch_user` mutates `user.role` and
commits without touching `api_token`. A demoted admin's token stays admin.

### Changes

`backend/app/auth.py` — in `_resolve_api_token`, take the lower of token role and
current user role by rank:

```python
effective_role = min(row.role, user.role, key=lambda r: _ROLE_RANK[r])
```

Move `_role_rank` out of `routers/users.py` into `auth.py` as `_ROLE_RANK` and
import it in the router, so there is one ranking definition rather than two.

This is preferred over revoking tokens on demotion: revocation silently breaks a
running integration, whereas clamping degrades it predictably and the 403s are
attributable in the audit log.

Also in `patch_user`: when `is_active` goes false, revoke live sessions the same
way `deactivate_user` does. `_resolve_session` already 403s on inactive users, so
this is consistency rather than a hole — but the two paths should not differ.

### Tests — extend `backend/tests/test_api_tokens.py`
- Token minted at `msp_admin`, user demoted to `customer_poc`, token now resolves
  as `customer_poc` and is refused an admin-only endpoint.
- Token minted at `customer_poc`, user promoted to `msp_admin`, token stays
  `customer_poc` (promotion does not escalate an existing token).
- `patch_user(is_active=False)` revokes sessions.

---

## I.4 — Session inactivity timeout (3.1.11)

**Goal:** sessions terminate after a defined inactivity period, not just at the
8-hour absolute expiry.

**Current state:** `auth.resolve_session` checks `revoked_at IS NULL AND
expires_at > now()`. No `last_activity_at`, no sliding window.

### Changes

Migration `0018_session_idle.py`:
- `user_session.last_activity_at TIMESTAMPTZ NOT NULL DEFAULT now()`
- Index on `(token_hash)` already exists; add nothing further.

`auth.resolve_session` — add the idle predicate. Pass the window as an argument so
the policy lives in config, not in the migration:

```sql
CREATE OR REPLACE FUNCTION auth.resolve_session(p_hash VARCHAR, p_idle_seconds INT)
...
  AND s.last_activity_at > now() - make_interval(secs => p_idle_seconds)
```

`config.py`: `session_idle_minutes: int = 15`, env `WINGRC_SESSION_IDLE_MINUTES`.
15 minutes is the common DoD-aligned default; 800-171 3.1.11 leaves the period
org-defined, so it must be configurable and it must appear in the SSP.

**Write-amplification guard:** updating `last_activity_at` on every request is a
write per request. Throttle — only update when the stored value is older than
60 seconds:

```sql
UPDATE user_session SET last_activity_at = now()
WHERE id = :sid AND last_activity_at < now() - interval '60 seconds'
```

This bounds the idle check's accuracy to ±60s, which is immaterial against a
15-minute window and turns a per-request write into roughly one write per minute
per active session.

### Tests — `backend/tests/test_session_idle.py` (new)
- Session with `last_activity_at` inside the window resolves.
- Session past the idle window 401s even though `expires_at` is in the future.
- Activity within the window extends the session.
- Absolute `expires_at` still terminates a continuously-active session (idle
  timeout must not become a renewal mechanism).

### Note
Document the configured value in the WinGRC SSP for 3.1.11. The platform should be
able to produce evidence for the control it implements.

---

## I.5 — Password lifecycle

**Implemented with 4 deviations from the spec below** — recorded here rather
than silently absorbed, per the scope check done before implementation
started. See "Deviations from this spec" at the end of this section for the
full reasoning behind each; the "Changes" list below is left as originally
written so the diff against actual scope stays visible.

**Goal:** a locked-out user can be recovered without destroying their MFA
enrolment; password reuse is prohibited.

**Current state:** `/set-password` handles invite redemption only. No reset flow,
no admin unlock. After three lockouts `requires_admin_reset` is set, and the only
endpoint that clears it is `reset_user_mfa`, which also nulls `totp_secret`, sets
`mfa_enrolled = False`, and deactivates the account. The documented recovery path
for a forgotten password is therefore "destroy the user's MFA enrolment."

### Design decision — no SMTP dependency

The invite flow returns `invite_token` in the response body for the admin to
deliver out of band. Password reset must follow the same pattern. GCC High and
air-gapped deployments cannot assume a mail relay, and adding one to the critical
path of account recovery would make the auth layer undeployable in exactly the
environments this product targets.

### Changes

Migration `0019_password_history.py`:
- `password_history(id, user_id FK, password_hash, created_at)`
- Index `(user_id, created_at DESC)`

`backend/app/auth.py`
- `check_password_reuse(db, user_id, password, generations) -> bool` — pull the
  last N hashes, run `verify_password` against each. Note this is N PBKDF2
  verifications at 600k iterations; with N=5 that is real CPU time, so it belongs
  only on the set/reset path, never on login.
- `record_password(db, user_id, hash)` — insert, then trim beyond N.

`config.py`: `password_history_generations: int = 5`.

`backend/app/routers/users.py`
- `POST /orgs/{org_id}/users/{user_id}/unlock` (`msp_admin`) — clears
  `locked_until`, `failed_login_count`, `lockout_count`, `requires_admin_reset`.
  Does **not** touch MFA. Emits `user.unlock`.
- `POST /orgs/{org_id}/users/{user_id}/reset-password` (`msp_admin`) — mints a
  reset token using `generate_secret()`, stores the hash and an expiry reusing the
  existing `invite_token_hash` / `invite_expires_at` columns, returns the raw
  token once. Emits `user.password_reset_issued`. Revokes the user's live
  sessions.

`backend/app/routers/auth.py`
- `/set-password` — call `check_password_reuse` and reject with 422 listing the
  policy violation, alongside the existing `validate_password_policy` and
  `check_pwned_password` checks. Call `record_password` on success.

### Reusing invite columns
Reset and invite are the same mechanism (one-time token, hashed at rest, short
TTL, redeemed at `/set-password`). Reusing the columns avoids a parallel code
path. If a user is issued a reset while an invite is outstanding, the reset
overwrites it — acceptable, since an outstanding invite means they never set a
password in the first place.

### Tests — `backend/tests/test_password_lifecycle.py` (new)
- Reuse of any of the last 5 passwords is rejected; the 6th prior is accepted.
- Unlock clears lockout state and leaves `totp_secret` and `mfa_enrolled` intact.
- Reset issues a working one-time token; the token is single-use; expired tokens
  are rejected.
- Reset revokes live sessions.
- Non-admin gets 403 on both endpoints; assessor gets 403 (I.2 gate).

Plus one regression test per deviation below (verify-not-enroll branch,
already-active-user redemption, fresh-invite behavior unchanged).

### Deviations from this spec (implementation-time)

Found during the pre-implementation scope check and while implementing —
none of these were silently folded into the "Changes" list above.

1. **`/set-password`'s `next` value is conditional on `user.mfa_enrolled`,
   not hardcoded `"enroll"`.** The spec above assumed only the invite case
   (`Current state` describes `/set-password` as invite-only). But "Reusing
   invite columns" also makes `/set-password` the redemption endpoint for
   `reset-password`, and a reset target is an *existing*, possibly
   already-MFA-enrolled user — forcing them through `next: "enroll"` would
   silently regenerate their TOTP secret and invalidate a working
   authenticator entry just because they forgot their password. Now mirrors
   the same `phase = "enroll" if not user.mfa_enrolled else "verify"` branch
   `/auth/login` already uses. `test_reset_of_already_enrolled_user_responds_verify_not_enroll`
   covers the backend contract, but the actual bug this deviation prevents
   is client-side (the frontend routing to the wrong step) — see deviation
   2's browser verification note below for the check that closes that gap.

2. **Extracted `MfaVerifyFlow.tsx`, used by both `LoginPage.tsx` and
   `InviteAcceptPage.tsx`.** Deviation 1 means `InviteAcceptPage` now needs a
   `mfa_verify` step it didn't have before (it previously assumed, in a code
   comment, that `next` was always `"enroll"`). Rather than duplicate the
   verify form across two pre-auth surfaces, extracted it the same way
   `MfaEnrollmentFlow` was already extracted — an auth-critical form
   duplicated across two pages drifts. **Browser-verified** (manual, against
   dev.wingrc.us — pytest alone exercises the backend's `next` value but
   never the frontend branch that consumes it): invite → redeem → enroll
   MFA, then admin-issued reset-password → redeem → confirmed routing to
   MFA *verify*, not re-enrollment, and the authenticator entry from initial
   enrollment still worked at that verify step. Also exercised in the same
   pass: lockout after 3+ failed logins showing "Locked until … (lockout
   #N)" in UsersPanel, Unlock clearing it, and account deactivation.

3. **`_user_out()` (`backend/app/routers/users.py`) now returns
   `locked_until` and `lockout_count`,** not just `requires_admin_reset`.
   Without `locked_until` the unlock UI has no way to tell a 1st/2nd lockout
   (which sets `locked_until` without tripping `requires_admin_reset`) apart
   from an account that has never failed a login. `lockout_count` is what
   makes `requires_admin_reset` legible to an admin instead of appearing
   from nowhere, and is the context needed to judge whether unlocking is
   appropriate.

4. **`auth.find_user_for_invite` (migration 0015) no longer requires
   `is_active = FALSE`** — found while implementing, not during the earlier
   scope check. That predicate was correct when the function only served
   invite redemption (a newly-invited user is always inactive). Reset reuses
   the same function for an *active* user redeeming a reset token — under
   the old predicate the function silently matched zero rows for any active
   user, so every reset token would 400 as "invalid" regardless of how
   correctly it was minted. Token hash + expiry is the real authorization;
   `is_active` was never load-bearing for it. Fixed in migration
   `0019_password_history.py` alongside the `password_history` table this
   slice already needed. `test_reset_token_redeems_for_already_active_user`
   is the regression test — it fails without this fix.

5. **`password_history` gained a `seq BIGINT GENERATED ALWAYS AS IDENTITY`
   column (migration `0020_password_history_seq.py`), and both ordering
   queries in `auth.py` (`check_password_reuse`, `record_password`) were
   repointed from `created_at.desc()` to `seq.desc()` — ✅ verified.**
   `test_reuse_allows_password_beyond_generation_window` failed
   deterministically (same failure every run) against real Postgres 18 on
   wl-util-1, never against the DB-free unit suite. Root cause: Postgres's
   `now()`/`CURRENT_TIMESTAMP` — what `created_at`'s `server_default =
   func.now()` calls — returns the *start time of the enclosing
   transaction*, not per-statement execution time. The test's `db_session`
   fixture (`tests/conftest.py`) wraps the whole test in one transaction
   with a savepoint per call, so six sequential `record_password` calls all
   got a byte-identical `created_at`. `ORDER BY created_at DESC` over tied
   rows has no defined tiebreak; Postgres's actual (undocumented,
   implementation-specific) tie resolution for that query shape evidently
   preserved original insertion order rather than reversing it, so
   `record_password`'s retention-trim step deleted the newest row instead
   of the oldest — an old password stayed inside the "last N" reuse window
   indefinitely. Ruled out an off-by-one in the `LIMIT`/`OFFSET` arithmetic
   and settings-cache staleness (`password_history_generations`) before
   concluding this — both checked directly, neither was the cause. This is
   the first table in this codebase where sort order is load-bearing for a
   security control rather than just display, which is exactly why no
   existing table had ever needed a tiebreaker before. `id`
   (`uuid.uuid4()`, client-generated) can't serve as that tiebreaker either
   — a random UUID has no correlation with insertion order, so pairing it
   with `created_at` would make the query deterministic without making it
   correct. Fixed by adding a real monotonic `seq` column and repointing
   both queries at it; `id` stays the UUID primary key for consistency with
   every other table in this codebase, `created_at` stays for potential
   display/audit use but is no longer load-bearing. `SELECT COUNT(*) FROM
   password_history` against wl-util-1's live `wingrc` DB was checked (not
   assumed) before writing the migration: **0 rows** — the table had only
   just been created by 0019, with no application code path yet calling
   `record_password` outside test runs (which roll back their own inserts).
   Confirmed via `pytest tests/test_password_lifecycle.py -m integration -v`
   against real Postgres 18 on wl-util-1 — passing clean, 16/16.

   Same category as the session-fixation finding under I.6 and the
   `login_method`/`api_token.last_used_at` findings under I.4/I.6: a bug
   that a mock or an in-memory store would never have surfaced, caught only
   because the test ran against a real Postgres engine with real
   transaction/timestamp semantics rather than a stand-in. The project's
   own DB-required-for-integration-tests convention
   (`@pytest.mark.integration` + `WINGRC_TEST_DATABASE_URL`, see this doc's
   header note) exists precisely so this class of bug gets caught before
   deploy rather than in production.

**I.5 closed.** Full suite: 308/308 integration tests green on wl-util-1
(not just this file's 16). Browser smoke test against dev.wingrc.us
confirmed all four paths end to end: invite → enroll, admin-issued
reset-password → **verify** (not enroll — the exact client-side bug
deviations 1–2 exist to prevent), lockout status rendering the correct
"Locked until … (lockout #N)" message, and Unlock clearing it.

Not changed, considered and left alone: reset-password's token TTL reuses
`_INVITE_TTL_HOURS` (48h) rather than a shorter reset-specific window, and
there's no `login_method` guard rejecting `reset-password` for SSO accounts.
Neither is a correctness bug (an SSO user's `password_hash` is never checked
at login regardless), so neither was added — flagged here as a possible
follow-up rather than scope creep nobody asked for.

---

## I.6 — Backend hardening sweep

Small items, one branch.

- **Concurrent session cap — ✅ verified.** Confirmed prior behavior:
  `create_session` had no cap logic at all, so a user could hold unlimited
  concurrent sessions. Added `max_sessions_per_user: int = 0` to
  `config.py` (`WINGRC_MAX_SESSIONS_PER_USER`, default 0 = unlimited, per
  this doc). `create_session()` now calls `auth._enforce_session_cap()`
  before minting a new session, which revokes the oldest active sessions
  once the count would exceed the cap — self-healing if the cap is
  lowered after sessions already exceed it, rather than needing a
  one-time cleanup. `backend/tests/test_session_cap.py` (new): default
  stays unlimited, oldest-first eviction at a set cap, and self-healing
  after lowering the cap. Confirmed via `pytest tests/test_session_cap.py
  -m integration -v` against real Postgres 18 on wl-util-1 — passing
  clean.
- **Session fixation — ✅ verified.** First real Postgres run of
  `test_session_fixation.py` failed both tests. Root cause was a genuine
  gap in `local_login` (`routers/auth.py`), not the session-fixation
  property itself — **and a real pre-cutover RLS gap worth flagging on its
  own merits**: this is exactly the kind of bug the `wingrc_app` cutover
  would have surfaced in production (today's `wingrc` connection bypasses
  RLS unconditionally as a superuser, so it was invisible until a test
  actually ran under RLS enforcement). Caught and fixed here, ahead of
  that cutover, rather than after it:
  `app.current_org` was only ever `SET LOCAL` inside the *bad-password*
  branch, copied from that branch's own `user.org_id`. The success
  (correct-password) branch never set it at all — and neither did the
  `db.get(User, user_row.id)` read that runs *before* either branch, which
  is also RLS-gated. Under RLS-enforcing `wingrc_app` (this test harness's
  `SET ROLE wingrc_app` per request, matching the still-pending Phase 3
  cutover), that gap surfaced as either an invalid-UUID cast error or a
  silently-matched-zero-rows
  `StaleDataError` on the subsequent `clear_failed_login` UPDATE,
  depending on whatever `app.current_org` value happened to be left over
  on the pooled test connection from an earlier request — hence the two
  different error shapes for what was one bug. Fixed: `SET LOCAL
  app.current_org` now runs once, immediately after the initial user
  lookup, using `user_row.org_id` (already available from that lookup) —
  before the `db.get()` read and covering both branches, rather than being
  duplicated per-branch after the fact.
  Separately, `test_mfa_enroll_confirm_mints_fresh_session_and_clears_pending_cookies`
  failed consistently (not connection-state-dependent) with a flat 400
  from `/auth/set-password` — a test bug, not an app bug: the seeded
  invited user never got `invite_expires_at` set, and
  `auth.find_user_for_invite` requires `invite_expires_at > now()`
  (`NULL > now()` is `NULL`, not true, so the row was silently excluded).
  Fixed by seeding a real future expiry.
  Second wl-util-1 run (both fixes above applied) was down to one
  deterministic failure in both tests:
  `assert _is_cleared(verify_resp, "wingrc_mfa_pending")`. Diagnosis: a
  test-helper bug, not an app bug — `clear_state_cookie` was correctly
  clearing the cookie the whole time. Python's `http.cookies` module
  (which `Response.set_cookie` uses) renders an empty-string value as a
  literal quoted `""`, not a bare trailing `=` (`name=""; ...; Max-Age=0`,
  not `name=; ...; Max-Age=0`) — confirmed directly against
  `http.cookies.SimpleCookie` output, and confirmed a real (non-empty)
  cookie value is never quoted this way, so the fix can't false-positive
  on a legitimate value. `_is_cleared()` only checked the bare form.
  Fixed to accept either. Confirmed via
  `pytest tests/test_session_fixation.py -m integration -v` against real
  Postgres 18 on wl-util-1 — passing clean.
- **Login rate limit by IP — ✅ verified.** Confirmed no rate limiting
  existed anywhere in the codebase before this
  (only per-account lockout). This doc didn't specify a threshold/window;
  proposed and confirmed with Jarrod: 20 attempts / 15 minutes per source
  IP, fixed-window, in-memory (`auth._login_attempts`,
  `auth.check_login_rate_limit`) — viable without a shared store or new
  table because this deployment runs a single uvicorn process per instance
  (no `--workers`); resets on process restart, an accepted tradeoff for
  this control. Source IP resolved via `auth.get_client_ip()`: reads
  `X-Real-IP` first (`deploy/nginx/nginx.conf` sets this from nginx's own
  `$remote_addr`, not client-spoofable), falling back to
  `request.client.host` for direct-connection dev — necessary because
  uvicorn isn't run with `--proxy-headers`, so `request.client.host` alone
  would resolve to nginx's own address in the deployed topology, not the
  real client's. Wired into `POST /auth/login` only.
  `backend/tests/test_login_rate_limit.py` (new): under-limit still gets
  normal auth errors, over-limit gets 429, and — the exact scenario this
  item exists for — spraying a different, nonexistent account on every
  attempt from one IP still trips the limit even though no single
  account's own lockout counter ever climbs. Confirmed via
  `pytest tests/test_login_rate_limit.py -m integration -v` against real
  Postgres 18 on wl-util-1 — passing clean.
- **`login_method` coherence — ✅ verified.** First real Postgres run failed
  `test_local_login_rejects_non_local_login_method[entra]` at the seeding
  stage: `psycopg.errors.CheckViolation` on `ck_user_login_method`. Root
  cause was in the test, not the schema —
  `ck_user_login_method` allows `'sso'`, `'local'`, `'api'` (see `0015` and
  `0017`); `'entra'` was never a valid stored value. Entra ID is the
  identity *provider*, but `login_method` stores the generic `'sso'`
  bucket, not the provider name — the parametrize list should have been
  `["sso", "api"]`, not `["entra", "api"]`. Enforcement at the actual login
  boundary was already correct (`local_login` already checks
  `login_method != "local"` → 401 before touching the password); only the
  test's own fixture value was wrong. Fixed. Confirmed via
  `pytest tests/test_login_method_coherence.py -m integration -v` against
  real Postgres 18 on wl-util-1 — passing clean.
- **`api_token.last_used_at` never persists on GET-only requests — ✅
  verified.** Same root cause I.4 hit and fixed for
  `user_session.last_activity_at`: `_resolve_api_token`'s `UPDATE
  api_token SET last_used_at = ...` was a bare `db.execute()` with no
  `db.commit()`, and `get_session()`'s `finally: session.close()` rolls
  back anything uncommitted — so a Bearer-token request that never hits a
  mutating endpoint (a read-only integration, or any token minted at
  `c3pao_assessor`) never actually recorded `last_used_at`. Fixed with the
  same shape as `_resolve_session`'s activity heartbeat: commit
  immediately after the `UPDATE`, then re-issue `SET LOCAL
  app.current_org` since the commit ends the transaction it was scoped
  to — every RLS-gated query for the rest of the request (starting with
  the `db.get(User, row.user_id)` right after) depends on it.
  `backend/tests/test_api_token_last_used_at.py` (new): mints an API
  token, confirms `last_used_at` is `NULL`, authenticates a real request
  with it through the actual `get_current_user` → `_resolve_api_token`
  path (not the fixture bypass), confirms `last_used_at` is populated
  afterward. As anticipated, the test-harness limitation (session never
  truly commits mid-test) meant this needed the same kind of real,
  out-of-band confirmation I.4 required — done by hand via `curl` against
  `dev.wingrc.us` on wl-util-1: minted a self-issued token via `POST
  /orgs/{org_id}/api-tokens`, confirmed `last_used_at: null` via `GET
  /orgs/{org_id}/api-tokens`, authenticated a pure `GET
  /orgs/{org_id}/users` with `Authorization: Bearer <token>`, then
  re-checked the token list — `last_used_at` had flipped from `null` to
  `"2026-07-28T13:36:19.658168+00:00"`. Confirms the commit-and-re-scope
  fix persists correctly against real Postgres.
- **`0016_app_role.py`'s `downgrade()` blocked by dependent objects — resolved.**
  Downgrading `0016` failed with 32 objects still holding dependent grants
  on `wingrc_app`, blocking `DROP ROLE`. Confirmed via live queries against
  wl-util-1: all 32 objects (29 tables × 4 privileges, 2 schema `USAGE`
  grants, 1 default-ACL entry) matched `0016.upgrade()`'s grants exactly,
  grantor `wingrc`, no mismatch — nothing out-of-band, no unaccounted-for
  source. The actual cause was `downgrade()`'s original `DROP ROLE`
  statement failing on those dependent objects *before* its own preceding
  `REVOKE`/`ALTER DEFAULT PRIVILEGES` statements had actually cleared them,
  which rolled back the whole migration transaction and left 100% of the
  original grants intact — not a coverage gap in what was revoked, a
  transaction-rollback artifact. Fixed by replacing that
  `REVOKE`/`ALTER DEFAULT PRIVILEGES` sequence in `downgrade()` with `DROP
  OWNED BY wingrc_app` before `DROP ROLE IF EXISTS` — revokes every
  privilege the role holds on any object in one statement, so it isn't
  sensitive to the same ordering/coverage failure mode regardless of cause.
  `upgrade()` was left untouched since it's already applied in deployed
  history. **Verified end-to-end**: a full `alembic upgrade head` →
  `alembic downgrade -3` round-trip against a disposable Postgres 18
  database on wl-util-1 completed cleanly, landing at `0015_auth_users`
  with no dependency error — the exact failure mode that originally
  blocked this downgrade.

---

## I.7 — Frontend: admin surface

**Goal:** wire the nine user/token endpoints that currently have no UI.

**Current state:** `LoginPage.tsx` already covers local login, MFA verify, MFA
enrol, and backup-code display. `api.ts` has zero user-management or token
methods. `useAuth` exposes `user`, `isLoading`, `logout`, `refresh`.

### Changes

`frontend/src/api.ts` — add:
`listUsers`, `inviteUser`, `patchUser`, `deactivateUser`, `resetUserMfa`,
`unlockUser`, `resetUserPassword`, `listApiTokens`, `createApiToken`,
`revokeApiToken`.

`frontend/src/components/UsersPanel.tsx` (new)
- Table: display name, email, role, login method, active, MFA enrolled, locked.
- Invite dialog — role select, login method select. **The response contains the
  one-time invite token; display it in a copy-once panel with an explicit warning
  that it will not be shown again.** Do not log it to console.
- Row actions: change role, deactivate, unlock, reset MFA, reset password. Each
  destructive action confirms first. Role change confirms with explicit
  before → after text, since it is the highest-consequence action in the panel.

`frontend/src/components/ApiTokensPanel.tsx` (new)
- Table: name, role, created, expires, last used.
- Create dialog — name, role (constrained to ≤ current user's rank, mirroring the
  backend rule so the UI does not offer an option the API will reject), optional
  expiry.
- Same copy-once treatment for the minted token.
- Revoke with confirm.

Mount both under `OrgSettings.tsx`, visible only to `msp_admin` (and
`msp_engineer` for the token panel, matching the backend gates).

### Cross-cutting note — auth/session parameters need a settings store before they're GUI-editable

Several backend auth/session parameters landed in I.4/I.6 as env vars or
hardcoded module constants, matching the existing config.py pattern:

- `WINGRC_SESSION_IDLE_MINUTES` (I.4, `config.py` — `session_idle_minutes`)
- `WINGRC_MAX_SESSIONS_PER_USER` (I.6 — `max_sessions_per_user`)
- Login rate-limit threshold/window (I.6 — `auth._LOGIN_RATE_LIMIT` /
  `auth._LOGIN_RATE_WINDOW_SECONDS`; currently hardcoded constants, not even
  env-configurable)

This is intentional and correct for I.6 — matches how every other setting
in `config.py` works today, and nothing about how these are stored is being
changed as part of this note. But the roadmap (and common sense) expects
these to eventually be visible and editable from the frontend GUI, most
naturally as part of I.7's admin surface. **That's not a trivial "add a
form" task**: env vars and module constants are read once at process
startup (or, for the rate-limit constants, compiled in) — there is no
request-time code path that reads a live, per-org value for any of them,
and no table to write one to. Making any of these GUI-editable requires
first moving it from env-var/hardcoded to a database-backed settings store
(e.g. a `system_settings` table, or an `Organization`-scoped column
depending on whether the setting should be global or per-org) — a real
migration plus read-path changes in `auth.py`, `config.py` no longer being
the source of truth for that value, and probably a cache-invalidation
story so a saved change takes effect without a restart. Budget for that
migration cost explicitly when I.7 is scoped, rather than discovering it
mid-slice.

---

## I.8 — Frontend: role-aware rendering

**Goal:** the UI reflects what the backend permits.

**Stated plainly: this is UX, not security.** The I.2 backend gate is the control.
This slice exists so an assessor is not presented with controls that will 403.
(I.7 already wrote this exact framing into a comment in `OrgSettings.tsx`,
forward-referencing this slice — I.8 fulfills that reference rather than
introducing the principle.)

### Scope check against I.5/I.7 (done before implementation)

- **No overlap with I.7.** `UsersPanel`/`ApiTokensPanel`'s role-awareness
  (admin sub-panel visibility, assignable-role clamping in
  `ApiTokensPanel` via `lib/roles.ts`'s `ROLE_RANK`) is a different axis
  entirely — `msp_admin`/`msp_engineer` tiering, not `c3pao_assessor`
  read-only rendering. The `AssessmentBoard` subtree this slice targets
  currently has zero role plumbing into it.
- **The 11 listed components split into two differently-sized jobs, not
  one uniform one.** `ContactsPanel`, `ContactDrawer`, `OrgProfileForm`,
  `SystemDescriptionForm` sit under `OrgSettings`, which already receives
  `currentUserRole` from `App.tsx` (I.7) — these four just need it
  forwarded one more level. `AssessmentBoard`, `ControlDrawer`,
  `ObjectiveRow`, `EvidenceSection`, `EvidenceTasksPanel`, `ProductsPanel`,
  `ProductCard` sit under `AssessmentBoard`, which `App.tsx` currently
  renders with no user/role prop at all — this half establishes a brand
  new prop path from scratch.
- **`lib/roles.ts` is the right home** for the read-only-role set — it
  already mirrors `auth.py`'s `_ROLE_RANK` by explicit convention
  ("if the backend map ever changes, this one needs the matching edit");
  add a `READ_ONLY_ROLES` mirroring `auth.py`'s `_READ_ONLY_ROLES =
  frozenset({"c3pao_assessor"})` the same way, rather than inlining the
  set in `useAuth.ts` or duplicating it per-component.

### Design decision — prop threading, not React Context, for `canWrite`

Considered and rejected Context for the `AssessmentBoard` subtree. Two
reasons, both from the actual code rather than habit:

1. **Precedent already in this exact tree.** `org.id`/`assessment.id`
   thread as required props through the identical chain —
   `AssessmentBoard → FamilySection → ControlSection → ObjectiveRow`, plus
   `AssessmentBoard → ControlDrawer/EvidenceTasksPanel/ProductsPanel`
   directly — including through two pure-passthrough components
   (`FamilySection`, `ControlSection`) that don't use the values
   themselves. Introducing Context for `canWrite` alone would put two
   different plumbing mechanisms for equally cross-cutting values in the
   same tree.
2. **Required props catch the exact failure mode Context is being asked to
   prevent, and catch it earlier.** The concern: a component added later
   forgets to accept/forward the value and silently renders editable,
   failing only as a confusing 403 at click time. Under this project's
   `strict: true` tsconfig and `tsc -b` build gate, a **required**
   (non-optional) `canWrite: boolean` prop — declared the same way
   `orgId`/`assessmentId` already are on every component in this tree —
   makes that omission a **compile-time** error, not a runtime one.
   Context doesn't give this for free: `useContext` on a provider-less
   context silently returns `undefined` (or a default) unless a custom
   throwing hook is written on top of it, which is more code than "mark
   the prop required" costs, and still only fails at runtime.

Conclusion: thread `canWrite` as a required prop everywhere in the
`Changes` list below, matching the existing `orgId`/`assessmentId`
convention exactly. No context provider introduced.

### Changes
- `useAuth` — derive `canWrite` from `user.role`, exported alongside `user`.
- Thread through `AssessmentBoard`, `ControlDrawer`, `ObjectiveRow`,
  `EvidenceSection`, `EvidenceTasksPanel`, `ContactsPanel`, `ContactDrawer`,
  `ProductsPanel`, `ProductCard`, `OrgProfileForm`, `SystemDescriptionForm`.
- Read-only mode: disable inputs and hide mutate buttons rather than hiding whole
  panels. An assessor should see that evidence exists and read it; they simply
  cannot change it.
- Persistent banner when `!canWrite`: "Read-only access — assessor role."
- Keep the bundle export button enabled for assessors. It is a GET, it is gated,
  and it is the assessor's primary deliverable.

### Tests
Extend `frontend/src/lib/filters.test.ts` patterns — add a `permissions.test.ts`
covering the `canWrite` derivation for each of the four roles.

**I.8 implemented.** `useAuth` derives `canWrite`; `lib/roles.ts` adds
`READ_ONLY_ROLES` and an extracted pure `deriveCanWrite(role)` (mirrors the
`filters.ts` pattern of pure functions the hook itself can't be unit-tested
through) so `permissions.test.ts` can cover all four roles plus the
no-user case without mounting a component. `canWrite` threaded as a
required prop through all 11 listed components plus the two pure-passthrough
components already on that path (`FamilySection`, `ControlSection`) and
`OrgSettings`; read-only banner added in `App.tsx`; `.fieldset-reset`
(`display: contents`, native `disabled` cascade) added to `styles.css` for
the form-drawer cases (`ContactDrawer`, `OrgProfileForm`,
`SystemDescriptionForm`, `ControlDrawer`'s per-objective fields) so descendant
inputs are disabled without a `display` change to the layout. Not yet run
against the frontend toolchain (no local Node — this box only has the
backend Python env) or browser-smoke-tested; that verification is the next
step, same gap I.5 had before its "closed" line above.

---

## I.9 — Frontend: account self-service

- Change-password form in user settings — surfaces the 422 policy messages from
  the backend (length, pwned, reuse) as distinct field errors rather than a
  generic failure.
- Re-enrol MFA from settings, not only from the login flow.
- Regenerate backup codes.
- Show active sessions with last activity, and a "sign out everywhere" action.
  This depends on `last_activity_at` from I.4 and is the user-visible payoff for
  that column.

---

## Order of merge

```
I.1  audit + route guard harness      no migration
I.2  assessor read-only               no migration
I.3  token/role coherence             no migration
I.4  session idle timeout             0018
I.5  password lifecycle               0019
I.6  hardening sweep                  no migration (unless session cap)
I.7  frontend admin surface
I.8  frontend role-aware rendering
I.9  frontend self-service
```

I.1–I.3 are all no-migration and can land quickly. I.4 and I.5 are the two schema
changes; keep them in separate migrations rather than one combined revision so
either can be reverted independently.

---

## Deferred, tracked

- **I.2b** assessor per-assessment grants — only on real engagement need.
- **FIPS deployment profile** — separate roadmap item. Relevant checks already
  handled in auth: PBKDF2 over bcrypt/argon2, `usedforsecurity=False` on the HIBP
  SHA-1 lookup. The outstanding FIPS risk is elsewhere (boto3 Content-MD5), not in
  this slice.
- **SSO group→role mapping** — currently role is assigned locally at invite time
  even for Entra users. Mapping Entra groups to WinGRC roles is a later slice and
  should not block I.1–I.9.

---

## Standing constraints for every slice

- Small commits directly to `main`, push after each — the dev box pulls.
  (See the sequencing-rationale note above — this replaced "branch per
  slice" on 2026-08-02 to match what I.1–I.7 actually did.)
- `ruff check` clean before merge.
- DB-touching tests carry `@pytest.mark.integration`.
- Never log, echo, or persist a raw token, invite token, reset token, TOTP secret,
  or backup code. Hash at rest, show once in the response body, never in the audit
  log.
- Any new endpoint is covered by `test_route_guards.py` automatically; if it needs
  to be public, the allowlist edit should be visible in the diff and justified in
  the commit message.
