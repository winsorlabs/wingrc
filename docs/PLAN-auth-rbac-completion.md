# Plan — Auth/RBAC completion (roadmap item I) + frontend admin surface

**Status:** I.1 in progress · I.2–I.9 not started
**Baseline:** 0088757
**Scope:** close the gaps identified in the audit of item I, then land the frontend
surface those endpoints require.

Slices are numbered `I.1`–`I.9`. Each is a branch, each ends green, each is
independently shippable. Do not batch them.

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

---

## I.6 — Backend hardening sweep

Small items, one branch.

- **Concurrent session cap.** Optional; add `WINGRC_MAX_SESSIONS_PER_USER`
  (default 0 = unlimited). On `create_session`, revoke oldest beyond the cap.
- **Session fixation.** Confirm `create_session` mints a fresh token after MFA
  step-up and that the pre-MFA `wingrc_mfa_pending` state cookie is cleared on
  success. Add an explicit test — the behaviour is probably already correct, but
  it is not currently asserted.
- **Login rate limit by IP**, distinct from per-account lockout. Per-account
  lockout alone permits spraying one attempt each across many accounts from one
  source.
- **`login_method` coherence.** `test_invite_user_rejects_api_login_method`
  covers invite; assert `local_login` rejects a user whose `login_method` is
  `entra` or `api`.

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

---

## I.8 — Frontend: role-aware rendering

**Goal:** the UI reflects what the backend permits.

**Stated plainly: this is UX, not security.** The I.2 backend gate is the control.
This slice exists so an assessor is not presented with controls that will 403.

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

- Branch per slice, small commits, push after each — the dev box pulls.
- `ruff check` clean before merge.
- DB-touching tests carry `@pytest.mark.integration`.
- Never log, echo, or persist a raw token, invite token, reset token, TOTP secret,
  or backup code. Hash at rest, show once in the response body, never in the audit
  log.
- Any new endpoint is covered by `test_route_guards.py` automatically; if it needs
  to be public, the allowlist edit should be visible in the diff and justified in
  the commit message.
