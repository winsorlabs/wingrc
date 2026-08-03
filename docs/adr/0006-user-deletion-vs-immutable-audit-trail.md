# 6. User deletion vs. the immutable audit trail

Date: 2026-07-30
Status: Accepted — implemented 2026-08-03, see
docs/PLAN-auth-rbac-completion.md's "User deletion (ADR 0006)" section

## Context

The I.7 admin surface (UsersPanel, `docs/PLAN-auth-rbac-completion.md`) gives
MSP admins a users list. `DELETE /orgs/{org_id}/users/{user_id}`
(`backend/app/routers/users.py:235`, `deactivate_user`) already exists behind
that HTTP verb — but despite the method name, it does not delete the row. It
sets `is_active = False`, revokes sessions, and logs `user.deactivate`. That is
the only "removal" affordance in the product today, and it is fully
reversible (an admin can flip `is_active` back with `PATCH`).

Requests for a *permanent* delete keep surfacing anyway — most recently to
clean up throwaway smoke-test accounts by hand via raw SQL against wl-util-1,
which is exactly the kind of one-off, ungoverned operation a real admin
feature should replace.

The obstacle is that `audit_log` is deliberately append-only:
`test_audit_service_has_no_mutating_paths` (`backend/tests/test_deactivation.py:514`)
asserts `audit.py` contains no `session.update()`/`session.delete()` path, and
`audit.py`'s module docstring documents a pending DB-level hardening step
(`REVOKE UPDATE, DELETE ON audit_log FROM wingrc`, also noted in migration
`0010_deactivation_and_audit.py`). For a CMMC assessment platform, `audit_log`
is not incidental logging — it is the non-repudiation record a C3PAO assessor
relies on ("who confirmed this control met, and when"). A feature that lets an
admin quietly erase that record is a compliance platform destroying its own
compliance evidence.

**What actually references `user.id`**, checked directly against
`backend/app/models.py` rather than assumed:

| Table | Relationship | Deleting the user row |
|---|---|---|
| `user_session.user_id` | FK, `ON DELETE CASCADE` | rows vanish automatically |
| `mfa_backup_code.user_id` | FK, `ON DELETE CASCADE` | rows vanish automatically |
| `api_token.user_id` | FK, `ON DELETE CASCADE` | rows vanish automatically |
| `audit_log.entity_id` (`entity_type='user'`) | **not a DB FK** — polymorphic text/UUID column | dangling reference, no constraint violation |
| `audit_log.actor` | **not a DB FK** — free-text column, usually `str(user.id)` | dangling reference, no constraint violation |
| `control_state_history` | no user reference at all | unaffected |
| `raci_assignment` | FK to `contact.id`, not `user.id` | unaffected |

The three cascading tables are pure auth mechanics (sessions, MFA codes,
tokens) — none of them carry compliance narrative, so cascading those is
uncontroversial. `audit_log` is the only table where deletion is a real
question, and it has no FK forcing an answer either way — the constraint
here is architectural intent, not the schema.

**Update (2026-08-03, at implementation time):** `password_history.user_id`
(migration `0019`, landed under I.5 the same day this ADR was written, but
after this table was drafted) is a fourth `FK, ON DELETE CASCADE` row that
belongs in the table above — it just didn't exist yet when the table was
written. It shares the identical shape and reasoning as the other three
(pure auth mechanics — password hashes, not compliance narrative), so the
implementation treats it exactly the same way: cascaded automatically by
the hard-delete path, and explicitly deleted alongside the other three in
the anonymize path. Noted here rather than silently folded into the
original table so the table's own text doesn't go stale next to a
"three cascading tables" sentence that a future schema addition could
just as easily make wrong again — check `models.py` for `ForeignKey("user.id"`
at implementation time rather than trusting this list's count.

One more fact that bounds every option below: `user.invite`'s `after_value`
already stores the invited email address verbatim at invite time
(`routers/users.py:112`, `{"email": body.email, ...}`), and other events
(`user.role_change`, `user.activation_change`) store role/status, not PII, in
`before_value`/`after_value`. So a copy of the user's email already lives
permanently in `audit_log` the moment they're invited, independent of
whatever happens to the `user` row later.

## Options considered

**A. Cascade-delete into `audit_log`.** Delete or `entity_id IS NULL` the rows
that reference the user. Simplest to implement, but it requires mutating an
append-only table that has a test and a documented DB-hardening step
specifically guarding against that. It destroys the "who did what, when"
record an assessor needs, and for events like `user.role_change` it can erase
the only evidence a privilege escalation was reviewed and by whom. **Rejected.**

**B. Null out the actor reference.** Keep the `audit_log` row (action, entity,
timestamp, before/after) but blank `actor`/`entity_id` so the event survives
without pointing at a deleted person. This still requires an `UPDATE` against
`audit_log` — the exact operation `test_audit_service_has_no_mutating_paths`
exists to catch — for a benefit (partial anonymization) that a non-mutating
approach (option C) achieves without touching the table at all. **Rejected**,
same reason as A, just less destructive.

**C. Soft-delete / anonymize the `user` row; never touch `audit_log`.** Keep
the row (so `id` stays a valid, resolvable reference), scrub the PII columns
(`email`, `display_name`, `entra_oid`, `totp_secret`, `password_hash`), cascade
the three auth tables as normal, and leave every `audit_log` row byte-for-byte
untouched. `audit_log.actor`/`entity_id` still resolve to a real row — just one
that no longer discloses who the person was outside the historical event text
already committed to `audit_log`. Zero conflict with the append-only
invariant. **This is the mechanism for any user with audit history.**

**D. Block deletion entirely if the user has any historical activity; force
deactivation as the permanent end-state.** This is what `DELETE
/users/{user_id}` already does today, for *every* user, unconditionally. It's
the safest possible default and needs no new code — but it also means the
platform has no way to ever actually remove a mistakenly-invited or
zero-activity account short of a DBA running raw SQL, which is the exact gap
that prompted this ADR. Keeping it as a blanket rule (never allow real
deletion) is too blunt; keeping it as the *fallback when there's history to
protect* is exactly right.

## Decision

Adopt a two-tier model. "History" means: any `audit_log` row where `actor =
str(user.id)` OR (`entity_type = 'user'` AND `entity_id = user.id`).

1. **User has audit history (the common case) → anonymize (option C), not
   delete.** Revoke sessions; cascade-delete `user_session`, `mfa_backup_code`,
   `api_token` (nothing compliance-relevant lives there); scrub `email` to a
   deterministic, collision-free placeholder (`deleted-{user.id}@wingrc.invalid`
   — satisfies `uq_user_org_email`), `display_name` to `"Deleted user"`, and
   null `entra_oid`/`totp_secret`/`password_hash`. Mark the row permanently
   inert in a way distinguishable from ordinary deactivation (a
   `deleted_at` timestamp, not reuse of `is_active` — an admin can reactivate
   a deactivated user, but never an anonymized one; the UI needs to tell those
   apart). `audit_log` is never written to by an `UPDATE`/`DELETE` — the
   delete action itself gets one new, forward-only `user.delete` event, and
   its `before_value`/`after_value` must carry *only* non-PII (e.g.
   `{"anonymized": true}`), never the pre-scrub email/name, or the delete
   operation would re-inject the PII it's trying to remove.

2. **User has zero audit history → real `DELETE FROM "user"` is permitted.**
   This covers exactly the case that prompted this ADR: an invited-by-mistake
   or throwaway smoke-test account that never did anything logged. Gate it
   with the history check above; if any `audit_log` row references the user,
   reject with 409 and point the admin at path 1. This turns today's manual
   "identify test users, delete children, delete the row" SQL into a governed,
   auditable admin action instead of ad hoc DBA work.

Existing `DELETE /users/{user_id}` (`deactivate_user`) is unchanged and
remains the reversible, non-permanent action — it is not what "delete" means
in this ADR. The new permanent action is additive.

## Consequences

- Schema: add a nullable `deleted_at TIMESTAMPTZ` (or equivalent) to `user`,
  distinct from `is_active`, so the UI and any future logic can tell
  "deactivated, reversible" apart from "anonymized, permanent." No change to
  `audit_log`.
- The pre-delete history check is one indexed query
  (`SELECT 1 FROM audit_log WHERE actor = :uid OR (entity_type='user' AND
  entity_id = :uid) LIMIT 1`) — cheap, and it's the same shape query used to
  hand-verify the smoke-test cleanup earlier this session.
- WinGRC cannot honestly claim full "right to erasure" for a user's PII: the
  email captured in `user.invite`'s `after_value` at invite time survives
  anonymization by design, because `audit_log` is exempt from mutation on
  principle, not as an oversight. Any customer-facing privacy language must
  scope "delete" to the user record, not to historical audit metadata.
- Frontend (UsersPanel, already shipped in I.7): needs a second, clearly
  separated action from the existing deactivate toggle — copy should make the
  irreversibility and PII-scrub explicit, and disable/hide it with an
  explanatory state when the zero-history hard-delete path isn't available for
  that user.
- Precedent set here (never mutate `audit_log`; anonymize the referencing row
  instead) should extend to `password_history` when it lands under I.5 — that
  table doesn't exist yet, but will face the identical tension the moment a
  "delete user" feature needs to reason about it.
- No endpoint or UI work starts from this ADR alone; it exists to settle the
  approach before I.8/I.9-adjacent work touches user deletion.
