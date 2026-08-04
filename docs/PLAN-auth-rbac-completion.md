# Plan — Auth/RBAC completion (roadmap item I) + frontend admin surface

**Status:** I.1 ✅ merged · I.2 ✅ merged · I.3 ✅ merged · I.4 ✅ merged · I.5 ✅ closed (5 deviations — see I.5; 308/308 integration tests green on wl-util-1, browser smoke test confirmed) · I.6 ✅ merged (all 6 items) · I.7 ✅ merged (users + API tokens admin panels, invite-redemption page) · I.8 implemented pending commit/review (see I.8) · I.9 not started · **User deletion (ADR 0006) implemented, out-of-band — see that section below, not part of I.1–I.9** · **Audit log viewer implemented, out-of-band — see that section below, not part of I.1–I.9**
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

## User deletion (ADR 0006) — implemented, out-of-band

**Not part of the I.1–I.9 numbering.** This work implements
`docs/adr/0006-user-deletion-vs-immutable-audit-trail.md`, which was proposed
independently of this plan (it settles a design question — permanent
deletion vs. the append-only `audit_log` — not a slice this plan ever
scoped). It's recorded here only because it touches the same `users.py`
router and `UsersPanel.tsx` surface I.7/I.8 built, and a future reader
diffing this plan against `git log` should not conclude it was silently
folded into I.8's role-aware-rendering scope. It wasn't — I.8 is UI
read-only enforcement for `c3pao_assessor`; this is a new, permanent,
`msp_admin`-gated mutation with no relationship to role-based rendering.

**Adopts the ADR's two-tier model exactly:**
- `POST /orgs/{org_id}/users/{user_id}/delete` — hard `DELETE FROM "user"`,
  permitted only when the target is already inactive (deactivate-first gate,
  enforced server-side, not just hidden in the UI) and has zero `audit_log`
  rows referencing it (`actor = user_id` OR `entity_type='user' AND
  entity_id=user_id`). Cascades via the existing `ON DELETE CASCADE` FKs on
  `user_session`, `mfa_backup_code`, `api_token` — and `password_history`,
  which didn't exist when the ADR was written (I.5 landed it the same day)
  but shares the identical shape the ADR describes for the other three: pure
  auth mechanics, no compliance narrative. Blocked with 409 when history
  exists; the 409 detail is the exact "N audit log entries... Anonymize
  instead?" message, so the UI never has to duplicate that copy.
- `POST /orgs/{org_id}/users/{user_id}/anonymize` — the fallback for any
  user with history. Scrubs `email`/`display_name`/`entra_oid`/
  `totp_secret`/`password_hash`, sets `mfa_enrolled=False`, explicitly
  deletes that same four-table set (the parent row survives here, so the FK
  cascade never fires), and sets the new `user.deleted_at` column
  (migration `0021_user_deleted_at`). `audit_log` is never touched — the
  action inserts one new `user.anonymize` row via the existing `log_event()`,
  `after_value={"anonymized": true}` only, no PII per the ADR's explicit
  warning against re-injecting what it just scrubbed.
- `deleted_at` is permanent and distinct from `is_active`: `patch_user`
  now 409s on any attempt to set `is_active=True` where `deleted_at` is set,
  so an anonymized account can never be reactivated the way an ordinarily
  deactivated one can.
- Both endpoints require `msp_admin` (`require_org_access("msp_admin")`,
  matching every other destructive user action in this router) and refuse
  to act on the caller's own account, matching `deactivate_user`'s existing
  self-check.

**Frontend (`UsersPanel.tsx`):** "Delete" only renders on an inactive,
non-self row (the same deactivate-first gate, mirrored in the UI). Inline
confirm matches the existing deactivate/reset-MFA pattern. If the server
409s, the row switches to a *second*, visually distinct confirm — the
server's own message plus an "Anonymize" button — rather than silently
retrying as anonymize; the admin must click that second button to choose
it. An anonymized row (`deleted_at` set) renders a status badge and no
further row actions, and its role `<select>` is disabled.

**Tests:** `backend/tests/test_user_deletion.py` (18 cases) — active-user
gate on both endpoints, self-protection, history-blocks-delete (singular
and plural count wording, and the actor-not-just-entity case), zero-history
delete cascades all four tables, the delete action's own audit row carries
no PII, double-anonymize and double-delete rejection, anonymize scrubs PII
while leaving the pre-existing audit row byte-for-byte unchanged, anonymize
cascades the same four tables, reactivation-after-anonymize 409s, and
non-admin/assessor 403 on both endpoints.

**Verification status:** `ruff check` clean; migration chain verified to
resolve to a single head (`0021_user_deleted_at`) via Alembic's
`ScriptDirectory` offline (no DB needed for that check); all 18 new tests
collect cleanly under pytest. Not yet run against a real Postgres — this
box has no reachable database — so `pytest tests/test_user_deletion.py -m
integration -v` on wl-util-1 (after `git pull`) is the outstanding step,
same gap I.5/I.8 had before their own "closed"/"implemented" lines above.
Frontend not yet `tsc -b`'d or browser-smoke-tested for the same reason
(no local Node on this box, per I.8's note).

---

## Audit log viewer — implemented, out-of-band

**Not part of the I.1–I.9 numbering**, same footing as the ADR 0006 section
above — this is a new read-only surface over the existing `audit_log`
table, not a role-rendering or auth-lifecycle slice this plan ever scoped.
Recorded here for the same reason: so a future reader diffing this plan
against `git log` doesn't conclude it was folded into I.8 or I.9.

**Schema check done before writing the endpoint** (per the actual ask):
`audit_log` (`backend/app/models.py`) had exactly `id`, `org_id`, `actor`,
`actor_type`, `action`, `entity_type`, `entity_id`, `before_value`,
`after_value`, `context`, `created_at` — no IP column anywhere, confirmed
by grep before writing any code. `actor` is free text, not an FK (per ADR
0006's own finding) — the viewer shows it as the raw stored string; it does
not attempt to resolve it to a user display name, since a safe resolution
would need to distinguish real UUIDs from values like `"system"`/`"api"`
and wasn't asked for. Flagged as a possible follow-up, not built.

**IP capture (migration `0022_audit_log_ip_address`):** adds
`audit_log.ip_address VARCHAR(45)`, nullable, plus `(org_id, created_at)`
and `(org_id, ip_address)` indexes. Populated by `audit.log_event()` from a
`ContextVar` (`audit._current_ip`) that `main.py`'s new
`_stamp_audit_ip` middleware sets once per request via
`auth.get_client_ip(request)` — the exact same X-Real-IP-aware resolver
the I.6 login rate limiter already uses, not a second extraction path.
This was the only way to populate the column without threading a
`Request`/IP argument through all ~40 existing `log_event()` call sites
across 6 routers and `engine.py` (several of which have no `Request` in
scope at all) — out of proportion to what this feature asked for.
`tests/test_audit_log.py::test_real_request_captures_client_ip_via_middleware`
proves the ContextVar-through-middleware mechanism actually works for this
codebase's sync `def` endpoints end to end (a real `TestClient` request
with a custom `X-Real-IP` header, checked against the resulting row),
rather than trusting the anyio-threadpool-context-propagation reasoning
on its own.

**Is `get_client_ip` trustworthy for this, given nginx is the only thing in
front of the app?** Yes, for the topology that's actually deployed today:
`deploy/nginx/nginx.conf` sets `X-Real-IP` unconditionally from its own
`$remote_addr`, which a client cannot override by sending its own
`X-Real-IP` header through nginx. **Caveat, checked against
`docs/azure-container-apps-deployment-plan.md`:** that draft (not yet
executed) plan puts Container Apps' own ingress in front of nginx for TLS
termination — in that topology nginx's `$remote_addr` would be the
ingress's own hop, not the real client, unless nginx there is reconfigured
to trust a forwarded-for header from that ingress instead. Not a live bug
today; flagged so it isn't rediscovered as a surprise when that plan
executes.

**Rows predating this migration have `ip_address = NULL` and cannot be
backfilled** — the address was simply never captured. Decision, stated
explicitly rather than left implicit: when the IP filter is active, NULL
rows are excluded (plain SQL `ILIKE` semantics — NULL never matches), and
the UI shows a persistent hint while the filter is non-empty explaining
that omission. Without the filter, NULL-IP rows still appear in the list,
rendered as "Unknown" rather than blank — the row is real and its absence
of an IP is itself informative, so it must never look like a display bug.

**Endpoint:** `GET /orgs/{org_id}/audit-log` (`backend/app/routers/audit_log.py`),
`require_org_access("msp_admin")` — read-only, no other HTTP method on this
router and none planned; the table itself is append-only by design.
Params: `offset`/`limit` (default 50, capped 200), `action` (exact match),
`actor` (substring), `ip_address` (substring, NULL-excludes as above),
`start`/`end` (inclusive `created_at` bounds). Sort `created_at DESC, id
DESC` — the `id` tiebreaker is pagination-stability only, not a claim about
real ordering; same-transaction timestamp ties are real here (see the
`password_history` seq-column history under I.5's deviation 5 for the same
root cause turning up in different data).

**Frontend:** new "Audit Log" tab under `OrgSettings`, gated the same way
`UsersPanel`'s tab is (`currentUserRole === "msp_admin"`, UX mirror of the
server-side gate only). Date-range/action/actor/IP filters, newest-first
paginated table, an expandable row for the JSON before/after/context
values. Action filter is a free-text input with an autocomplete `<datalist>`
of currently-known actions (non-enforced, drifts stale harmlessly if
`audit.py`'s vocabulary changes — unlike `lib/roles.ts`'s `READ_ONLY_ROLES`,
which mirrors a real enforcement boundary and would drift unsafely).

**Tests:** `backend/tests/test_audit_log.py` (15 cases) — non-`msp_admin`
403 (all three other roles, not just the I.2 assessor case), org-scoping,
newest-first ordering + pagination, each filter in isolation, filters
combined with AND, the NULL-never-matches-active-filter behavior in both
directions (excluded when filtering, visible as "Unknown" when not), the
real-request-through-middleware IP capture proof above, and a direct
`log_event()` call outside any request producing a NULL IP (the expected,
non-buggy default for every existing non-HTTP call site).

**Crash-safety check on the no-request-context path, done explicitly
before commit** (seed scripts, catalog seeding, CLI invocations, any future
background job calling `log_event()` with no ambient HTTP request —
`_current_ip.get()` must degrade to NULL, never raise, since a crash in the
audit writer would be a worse failure mode than the missing IP itself
already is): `_current_ip` is declared `ContextVar("_current_ip",
default=None)` (`audit.py`), so `.get()` cannot raise `LookupError` even
when `.set()` was never called anywhere in the process — confirmed by
direct execution against this box's Python (no DB, no ASGI app): a bare
`ContextVar(default=None).get()` with no prior `.set()` returns `None`; a
`.set()` performed inside a spawned `asyncio.Task` (the same shape as one
HTTP request's middleware) does not leak into the parent context once that
task completes, matching the isolation between one request's IP-stamping
and any later, unrelated direct call in the same process; and the real
`audit.log_event()` function called directly against a bare stub session
object (no DB) returns `ip_address=None` with no exception.
`test_log_event_outside_any_request_never_raises_no_db_required` (new,
deliberately **not** `@pytest.mark.integration` — it needs no fixtures and
no DB) encodes this as a real, currently-passing test rather than only an
integration test gated on wl-util-1 reachability; ran it locally on this
box just now — passes.

**Verification status:** `ruff check` clean; migration chain verified to
resolve to a single head (`0022_audit_log_ip_address`) via Alembic's
`ScriptDirectory` offline; `test_route_guards.py`'s deny-by-default harness
passes without any allowlist edit (the new route's `require_org_access`
dependency satisfies it automatically); all 15 new tests (14 integration +
1 DB-free) plus the full 472-test suite collect cleanly; all 85 DB-free
tests pass, including the new crash-safety one above. Everything else
(the 14 `@pytest.mark.integration` cases) not yet run against a real
Postgres, and frontend not yet `tsc -b`'d/browser-tested — same outstanding
step as the ADR 0006 section above, for the same reason (no DB/Node
reachable from this box). wl-util-1, after `git pull`:
`pytest tests/test_audit_log.py -m integration -v`.

**Fixed after first real-Postgres run (339/340 passed):**
`test_filter_by_date_range` built its query with an f-string
(`f"...?start={start}"`) instead of encoding it. `datetime.isoformat()`'s
`+00:00` UTC offset contains a literal `+`, and in a raw, unencoded query
string a literal `+` decodes as a space (`application/x-www-form-urlencoded`
semantics) — the backend received `... 00:00` instead of `...+00:00`,
Pydantic rejected it, and the endpoint 422'd. The test never checked
`status_code` before reading `r.json()["total"]`, so the failure surfaced
as a confusing `KeyError: 'total'` on the 422 error body instead of the
real 422. Confirmed the whole causal chain by direct execution (not
assumed): `urllib.parse.parse_qs` on the naive URL reproduces the exact
space-corrupted value; `pydantic.TypeAdapter(datetime)` (pydantic 2.13.4,
what the endpoint actually validates against) rejects that corrupted value
and accepts the correct one; `httpx.Request(..., params={"start": start})`
— what the fix now uses — round-trips the original value exactly, verified
by encoding then re-parsing it back to the identical string. Fixed by
switching to `params=` (httpx dict, correctly percent-encodes) and adding
`assert r.status_code == 200` to every request in this file that reads
`.json()`, not just the one that broke — the whole point being that a test
reading a key off an unvalidated response body will keep hiding errors
like this one.

**Frontend checked, not assumed fine:** `frontend/src/api.ts`'s
`listAuditLog` already builds its query with `URLSearchParams` +
`.set()`/`.toString()`, not string interpolation — the UI's date filter was
never affected by this. `URLSearchParams.toString()` percent-encodes a
literal `+` as `%2B` per the WHATWG URL Standard's
`application/x-www-form-urlencoded` serializer (a fixed byte-safelist, `+`
not included) — this box has no Node/JS runtime to execute that literally,
so confidence rests on the spec being deterministic (not
implementation/browser-varying) plus direct execution of the identical
encoding class via Python's `urlencode`/`httpx.QueryParams`, both of which
implement the same named standard for the same reason. No frontend test
added for this specifically — it would be testing a spec-guaranteed Web
API's built-in encoding behavior, not application logic that this
codebase's changes could regress.

**Follow-up: GUID identity resolution (2026-08-04).** Two rendering gaps
closed, no schema change (no new migration — `User.display_name`/`email`/
`deleted_at` already existed):

- `actor` was always a raw GUID (or the literal `"system"`) in the UI, and
  `entity_id` was a raw GUID for every `entity_type` including `"user"` —
  Jarrod noticed a `user.deactivate` row didn't show *which* user got
  deactivated, which is exactly the `entity_id` case, not the `actor` one.
  Both are resolved the same way now, since both are GUIDs pointing at the
  same `user` table.
- **Resolution is read-time only, never written back.** `audit_log` gained
  no columns; `routers/audit_log.py`'s `_resolve_identities` runs one batch
  `SELECT ... WHERE id IN (...)` per request (all actor GUIDs that parse as
  UUIDs, plus all `entity_id`s where `entity_type == "user"`, unioned into
  one query) and joins in memory — `test_identity_resolution_batches_into_one_query`
  asserts exactly one `FROM "user"` statement fires via a
  `before_cursor_execute` listener on the real engine, not just that the
  result is correct, since an N+1 regression here would still pass a
  correctness-only test.
- **Three-way fallback (ADR 0006), identical for actor and entity:**
  `"active"` → `display_name (email)`; `"anonymized"` (row exists,
  `deleted_at` set) → a fixed **"Anonymized user"** label, never the
  scrubbed placeholder display_name/email even though those happen to
  already read innocuously (`"Deleted user"` / `deleted-{id}@wingrc.invalid`)
  — the API returns `display_name: null, email: null` for this status so
  the frontend can't accidentally leak the placeholder even if it changes
  later; `"deleted"` (no row at all — the hard-delete path) → a distinct
  **"Deleted user"** label, so "the row was scrubbed but survives" and "the
  row is completely gone" never look identical to an admin reading the
  log, even though both are legitimate ADR 0006 outcomes rather than bugs.
  The raw GUID is always included and always rendered (a secondary line
  under the name, `.contact-sub`, full value in a `title` tooltip) — never
  replaced, per the requirement that the durable record stay visible.
- Filtering behavior is unchanged: the `actor` query param still matches
  against the raw stored string (GUID or `"system"`), not the resolved
  name/email — same as `before_value`/`after_value`, the filter operates on
  the durable record.
- Column rename: "Actor" → "User" (table header only; the filter input
  above it is still labeled "Actor" since it filters the raw field, not the
  resolved name).
- 9 new backend tests: all three fallback statuses for both actor and
  entity_id (6), the `"system"`-literal-not-resolved case, the
  entity_type-isn't-`"user"`-not-resolved case, and the batch-query-count
  proof above. Full suite: 481 tests collected, all 85 DB-free tests still
  pass. The 9 new cases are `@pytest.mark.integration` — same outstanding
  wl-util-1 step as the rest of this section.

**Immediate follow-up:** the "User" column (resolved names) and the
"Actor" filter (raw stored field) had a naming mismatch — nothing told an
admin that typing a display name into the Actor filter would silently
return zero rows. Fixed at the point of use, not by changing what the
filter queries (matching resolved/mutable names instead of the immutable
stored record would be the wrong behavior for an audit trail, not just a
UX nit): the Actor field's placeholder now reads "GUID or 'system' — not
name/email" and gained a persistent `.field-hint` — "Matches the stored
ID, not the resolved name shown in the User column." Also switched
`.audit-log-filters`'s `align-items` from `end` to `start`, since the
Actor field is now taller than its siblings (label + input + hint vs. just
label + input) and `end` would have pulled its input down out of line with
the rest of the filter row.

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
