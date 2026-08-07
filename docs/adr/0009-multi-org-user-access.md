# 9. Multi-org user access

Date: 2026-08-07
Status: Accepted — implementation in progress. M.1 (schema), M.2
(auto-provisioning), M.3 (`User.org_id` → `home_org_id` rename), and M.4
(`require_org_access`/`get_current_user` enforcement cutover — the fix
for the defect this ADR documents) are landed pending live wl-util-1
verification (create a second org, open it, complete OnboardingWizard,
confirm scoping). M.5 (`GET /orgs`/`OrgPicker` reshape) not started.

**Severity: this documents a functional defect in already-shipped
behavior, not groundwork for unbuilt features.** An msp_admin cannot open
any org but their own today — not even one they just created — which
breaks the MSP-serves-many-clients premise the product is built around
(`CLAUDE.md`, "What WinGRC is": "select the security tools a tenant
runs..."; the entire five-layer model assumes an MSP working inside many
client orgs). This is not a gap in a not-yet-built feature to be triaged
behind roadmap "Planned" work — it's a bug in already-merged I.1–I.9 auth
work, confirmed end-to-end below. Settling the access model here is a
prerequisite for the fix, and nav/dashboard work is a secondary reason to
do this, not the primary one. See `docs/roadmap.md`'s "Known defects"
section for the tracking entry.

## Context

### Current state, verified against code and tests — not assumed

**`User` is single-org, full stop.** `org_id` is a `NOT NULL` FK to
`organization.id` (`models.py:1107-1109`), one row per person. There is no
membership table, no notion of a "home" org distinct from an "only" org.
`UserSession.org_id` (`models.py:1169`) is explicitly documented as
"denormalized from `user.org_id`."

**`require_org_access()` gates on strict equality, no role exemption —
confirmed in the source, not summarized:**

```python
# auth.py:604-625
def require_org_access(*roles: str):
    def _check(org_id: uuid.UUID, current_user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if current_user.org_id != org_id:
            raise HTTPException(status_code=403, detail="Cross-org access denied")
        if roles and current_user.role not in roles:
            raise HTTPException(status_code=403, detail=f"Requires one of: {', '.join(roles)}")
        return current_user
    return _check
```

There is no `if current_user.role in {"msp_admin", ...}: return current_user`
early-out anywhere. This is applied to every org-scoped route in
`assessments.py`, `evidence.py`, `contacts.py`, `bundle.py`, `orgs.py`
(profile/system-description/onboarding-status), and `users.py`.

**`GET /orgs` / `POST /orgs` are role-gated, not org-gated** — they have no
`org_id` in their own path, so they use a separate dependency,
`require_role("msp_admin", "msp_engineer")` (`orgs.py:227-255`), with no
membership check of any kind.

**Test evidence that these two facts combine into a dead end.**
`test_org_access_guard.py` seeds a second org and asserts, using a
`fake_msp_admin` identity, flat 403 against it on every router: `assessments`
(`test_assessments_get_cross_org_403`), `evidence`
(`test_evidence_list_cross_org_403`), `contacts`
(`test_contacts_list_cross_org_403`), `orgs` profile
(`test_orgs_profile_get_cross_org_403`), and `bundle`
(`test_bundle_cross_org_403`) — every one passing today, all with `role =
"msp_admin"`. Separately, `test_create_org_allowed_for_msp_roles` proves an
msp_admin can create a new org (201). No test anywhere combines the two —
create an org, then access it — because it cannot succeed: `require_org_access`
would 403 the creator against the org they just made, identically to every
other cross-org case above.

**Traced end to end, not just at the API boundary.** `invite_user`
(`POST /orgs/{org_id}/users`, `users.py:73-78`) requires
`Depends(require_org_access("msp_admin"))` — the caller must *already*
belong to `org_id` as `msp_admin` before they can invite anyone into it.
`create_org` (`orgs.py:233-245`) inserts a bare `Organization` row with no
owner, no membership, no link back to whoever created it. So a freshly
created org has no path by which anyone — including its creator — can ever
invite the first user into it through the API. The only account-creation
path that doesn't run into this circularity is `app/manage.py`'s
`bootstrap-admin` CLI command, explicitly documented as "first-boot
bootstrap," a one-time manual operation, not a per-customer onboarding
mechanism.

**Confirmed against the actual frontend flow.** `OrgPicker`'s "Add"
button → `api.createOrg()` → `App.tsx`'s `onEnterOnboarding` →
`OnboardingWizard`, whose first action is `api.getOnboardingStatus(orgId)`
→ `GET /orgs/{org_id}/onboarding-status`, gated by
`Depends(require_org_access())` (`orgs.py:456-459`). For any org beyond the
msp_admin's own, this 403s immediately — the very first screen of the
onboarding flow that `OrgPicker`'s own "Add" button exists to launch.

**Net finding: today, an msp_admin can *see* every org (`GET /orgs`) and
*create* new ones, but can only ever successfully open, configure, or work
inside the one org their own `User.org_id` happens to point at.** This is
not a deliberate design choice recorded anywhere — the plan doc
(`docs/PLAN-auth-rbac-completion.md`) never discusses cross-org MSP access
as an open question the way it does assessor per-assessment scoping (I.2).
It reads as a side effect: `require_org_access` was built (I.1-era) to close
unauthorized cross-org access — correctly — from a pure isolation lens,
without the "MSP staff need legitimate cross-org access as their actual job"
requirement being in view at the time. `test_org_access_guard.py`'s own
docstring frames its purpose purely as "an authenticated user from Org A
must not be able to read or mutate Org B's data" — true and necessary, but
incomplete once the same deployment's MSP staff are Org A and need
legitimate access to Org B, C, D.

Classified plainly: this is a **functional defect** in the product's core
multi-tenant premise, present in already-merged code, not a design gap in
work that hasn't shipped yet. It should be triaged and prioritized as a
bug fix, not queued behind "Planned" roadmap features.

**"MSP org" vs. "customer org" is not a structural distinction today.**
`Organization` (`models.py:58-86`) has no `org_type`, no `is_msp` flag, no
parent/child relationship, nothing. Every org row is identical in shape.
"MSP-ness" exists only as an emergent property of which users (by role)
happen to have their fixed `org_id` pointing at a given org — and per ADR
0005, there is exactly one MSP per deployment, so in practice exactly one
org row is "the MSP's own," established once by `bootstrap-admin` and never
marked as such anywhere in the schema.

**RLS is uniform, single-value, and — this matters for the design below —
already correctly scoped for a per-request active org, not a per-user
static one.** Every RLS-protected table, across every migration that adds
one (`0001`, `0002`'s `_enable_rls` helper applied to "every table with a
direct org_id column," `0015`, `0019`), uses the identical pattern:

```sql
CREATE POLICY {table}_tenant_isolation ON {table}
USING (org_id = current_setting('app.current_org', true)::uuid)
```

This is a single scalar comparison against one GUC, set per-request via
`SET LOCAL app.current_org = ...`. It says nothing about *how many* orgs a
user may cause that GUC to be set to across different requests — it only
constrains what's visible *within* whichever value is set for *this*
request. That's the right invariant to keep: a request should still act
within exactly one org's data at a time even for a multi-org user. What
needs to change is upstream — the authorization check that decides which
values a given user is *allowed* to cause `app.current_org` to take.

**Session-resolution already runs org-independent for identity, but
org-fixed for RLS-setting — and that's the one real structural wrinkle.**
`auth.find_user_for_login(oid, email)` (`0015_auth_users.py:198-208`) looks
up a user by `entra_oid`/`email` with **no `org_id` parameter at all** —
identity resolution at login is already global, not org-scoped. But
`auth.resolve_session(token_hash)` (`0015_auth_users.py:183-195`) returns
`(user_id, org_id, expires_at)` where `org_id` comes from
`user_session.org_id` — and `_resolve_session` (`auth.py`) uses that value
to `SET LOCAL app.current_org` immediately, before any route's own `org_id`
path parameter has even been parsed. Today this is harmless because there
is only one possible value. Under multi-org it stops being harmless: the
session-resolution step doesn't yet know which org a *specific* request
targets, so it cannot correctly pre-set `app.current_org` for that request's
business-data reads (assessments, evidence, contacts, ...). But it does need
*some* `app.current_org` value set immediately, because `user`,
`user_session`, `mfa_backup_code`, `api_token`, and `password_history` are
themselves RLS-protected by the same pattern — including the read of the
caller's *own* `User` row that `get_current_user` and every I.9 self-service
endpoint performs. These are two genuinely different notions of "org" that
happen to be the same value today only because of the single-org
constraint:

1. **The user's home/account org** — governs RLS on the account-mechanics
   tables (`user`, `user_session`, `mfa_backup_code`, `api_token`,
   `password_history`). Needed immediately at session resolution,
   independent of whatever business org a specific request is about.
2. **The per-request target org** — governs RLS on business/assessment
   tables (`assessment`, `evidence`, `contact`, `control_state`, ...),
   resolved from the URL path's `org_id` once membership is checked.

Conflating these was invisible under single-org and becomes a real design
decision under multi-org (see Design below).

## Options considered — membership shape

**A. Many-to-many `org_membership` table** (`user_id`, `org_id`, `role`,
one row per grant). `User.org_id`/`User.role` stop being authoritative for
access; a person's accessible orgs and their role in each come entirely
from their membership rows.

**B. Role-implied access** — no membership table; `require_org_access`
grows an early-out: if `current_user.role in {"msp_admin", "msp_engineer"}`,
skip the org-equality check entirely. MSP roles reach every org in the
deployment by role alone.

**C. Something else** — e.g. a `parent_org_id` on `Organization` marking
which orgs "belong to" the MSP, with access derived from that relationship
rather than either a membership table or a role check.

**Option B is coherent, not naive, given ADR 0005** — "per-MSP instance,
not shared SaaS" guarantees exactly one MSP per deployment, so "MSP role ⇒
every org in this deployment" isn't actually a data-shape assumption, it's
a true structural fact of the topology this product already commits to.
It's also the cheapest possible change: one `if` in `require_org_access`,
no migration, no new table. But it can only ever express one thing — "all
MSP roles see all orgs, uniformly" — and cannot express the plausible case
raised for this ADR: an `msp_engineer` on one customer and something more
restricted on another (staffing tiers by account, not just by person).
Option B has no way to grow into that without becoming Option A anyway.

**Option C doesn't actually solve the problem it looks like it solves.** A
`parent_org_id`-style relationship encodes "org B belongs to MSP org A" —
but access is a *user* fact ("can Alice see org B"), not an *org* fact
("does org B belong to the MSP"). Every MSP-role user would still need
identical access to every "child" org, which is Option B's exact
expressiveness ceiling, just recorded on the wrong table. It also
reintroduces the "which org is *the* MSP org" structural flag this ADR's
Context section already established the schema doesn't have and — per the
Decision below — doesn't need to gain.

## Options considered — role scope

**Global** (today's shape): one `role` value per `User`, applies
everywhere they have access.

**Per-membership**: role travels with the grant, not the person. The same
human can be `msp_admin` on one org's membership row and `msp_engineer` (or
even `c3pao_assessor`) on another's.

Per-membership is the only one of the two that can express the scenario
this ADR was asked to consider — account-tiered staffing (senior engineer
gets admin-level access on a large enterprise client, the same person gets
narrower access shadowing on a smaller one) is a plain, ordinary MSP
staffing pattern, not a hypothetical. One flagged caveat, stated plainly
rather than waved through: the specific example given —
`msp_engineer`/`c3pao_assessor` on the *same person* — is a separation-of-
duties smell in real compliance practice (the party implementing controls
shouldn't also be the party attesting to them), and the product may want to
discourage or block that specific combination later. That's a policy
decision for whoever owns role assignment, not a reason to reject
per-membership role scope generally — the mechanism should allow it (many
real, unobjectionable combinations exist), even if a future guard chooses
to flag or block that one pairing specifically.

Per-membership role scope also quietly resolves the "MSP vs. customer org"
distinction this ADR was asked to address: it's not a fact about the org at
all, and doesn't need a new column on `Organization`. It's a fact about
what role a given *membership* carries. An org doesn't need to declare
itself a customer org — it just accumulates memberships, most carrying
`msp_admin`/`msp_engineer` (because the MSP serves it) and typically one or
a few carrying `customer_poc` (the client's own staff). "MSP-ness" was
already living in `role`; per-membership scope just stops requiring it to
also live redundantly on a single fixed `User.org_id`.

## Decision

**Adopt Option A (many-to-many `org_membership`), with per-membership role,
plus one deliberate ergonomic rule layered on top: MSP-role memberships are
auto-provisioned, not manually granted per org.**

New table:

```sql
CREATE TABLE org_membership (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID NOT NULL REFERENCES "user"(id) ON DELETE CASCADE,
    org_id      UUID NOT NULL REFERENCES organization(id) ON DELETE CASCADE,
    role        VARCHAR(40) NOT NULL,  -- same CHECK constraint values as today's user.role
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (user_id, org_id)
);
```

Auto-provisioning rule (application logic, not a DB trigger — keeps it
visible and testable rather than implicit): whenever a new `Organization`
is created, insert an `org_membership` row for every existing
`msp_admin`/`msp_engineer` user. Whenever a new user is invited with role
`msp_admin`/`msp_engineer`, insert a membership row for every existing
org. `customer_poc` and `c3pao_assessor` memberships are always explicit,
one grant at a time — matching their inherently narrow, client-specific
scope, and leaving room for the exact "assessor on two engagements over
time, but not automatically on every client" case ADR 0005's topology
makes plausible (a C3PAO firm assessing more than one of this MSP's
clients, in different years, within the same deployment).

This gets Option B's ergonomic property (an msp_admin never has to be
manually re-granted access to every new customer) without needing a
structural "is this the MSP org" flag anywhere — the auto-provisioning
rule fires off *role*, exactly like every other role-based decision in this
codebase already does, not off a new org-level marker.

**Why not B alone:** it's cheaper today but a dead end for the plausible
per-org role variance this ADR was explicitly asked to evaluate, and this
codebase is ~22 migrations in — retrofitting a membership table onto more
entrenched single-org assumptions later is a strictly worse time to do this
than now, before nav/dashboard work adds more code that assumes
`current_user.org_id` is a fixed scalar.

## Boundary: auto-provisioning depends on ADR 0005 by name

Auto-provisioning MSP-role memberships across *every* org in the
deployment is only sound because **ADR 0005** guarantees exactly one MSP
per deployment ("per-MSP instance, not shared multi-tenant SaaS... Each
deployment continues to support multiple internal organizations... an MSP
and the client organizations they serve collaborating within one shared,
self-hosted platform"). This ADR's Decision leans on that guarantee
directly: "every org in the deployment" is treated as synonymous with
"every org this one MSP serves" *only because* ADR 0005 makes those the
same set by construction. This dependency needs to be named, not left
implicit the way "MSP org vs. customer org" was left implicit in the
schema before this ADR — the exact kind of silent assumption this whole
investigation exists to stop making.

**What breaks if that guarantee is ever relaxed.** If a future deployment
model change allowed a single deployment to host more than one unrelated
MSP (reintroducing the shared multi-tenant SaaS shape ADR 0005 explicitly
rejected), this ADR's auto-provisioning rule would silently grant MSP-A's
staff membership access to MSP-B's customers' orgs — and vice versa — the
moment any `msp_admin`/`msp_engineer` is invited or any org is created, with
no additional check standing in the way. That is precisely the cross-tenant
CUI-adjacent exposure ADR 0005 was written to prevent in the first place
(its Context section: evidence uploads are "an uncontrolled upload surface"
and cross-tenant exposure "lands on the platform operator... exactly the
scenario DFARS 252.204-7012's FedRAMP-Moderate-equivalent requirement...
exists to prevent"). Multi-MSP support is not a natural extension of this
ADR's model — it would require re-scoping auto-provisioning to a real
"MSP tenant" concept this ADR deliberately avoids introducing, and revisiting
every RLS policy's implicit "one deployment, one blast radius" assumption,
not just this ADR's membership table.

**Enforce structurally, but cheaply — not merely documented, and not a
heavyweight subsystem.** ADR 0005 itself places the actual isolation
boundary at the infrastructure layer ("enforced by separate infrastructure
— separate databases, object storage, and network boundaries"), and no
application-layer check can fully substitute for that; a genuinely
misconfigured shared database is outside what any query inside it can
detect or prevent. But a cheap, real, fail-loud anchor is still worth
having, for a narrower and more likely failure mode: a future contributor
relaxing ADR 0005 without realizing this ADR's auto-provisioning rule
depends on it by name. Add a **singleton `deployment_settings` table**:

```sql
CREATE TABLE deployment_settings (
    id          SMALLINT PRIMARY KEY DEFAULT 1 CHECK (id = 1),  -- enforced singleton
    msp_org_id  UUID NOT NULL REFERENCES organization(id),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

Populated once by `manage.py bootstrap-admin` at first run, alongside the
org and admin user it already creates. No API endpoint ever writes to it —
changing `msp_org_id` after bootstrap is a deliberate DBA/migration action,
never a runtime one. This is **not** consulted by `require_org_access` or
any access-control check at request time — access control stays exactly
what Decision above describes, membership rows only, so this doesn't
reintroduce the org-level "is this the MSP org" flag this ADR's Design
section elsewhere argues access control doesn't need. Its only job is
integrity, not authorization: it's the one place "this deployment serves
one MSP, anchored at this org" is written down as a real constraint rather
than an assumption living only in ADR 0005's prose and this ADR's Decision
section. Concretely, it fails loudly in the way that matters most: a
future multi-MSP pivot cannot ship without a contributor deliberately
finding, reading, and redesigning this table and the auto-provisioning
logic that would need to stop trusting it — it cannot be silently
outgrown the way an undocumented assumption could be.

## Design: `require_org_access`, RLS, and active-org selection

**`require_org_access` becomes a membership lookup, not an equality
check:**

```python
def require_org_access(*roles: str):
    def _check(org_id: uuid.UUID, current_user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        membership = get_membership(current_user.id, org_id)  # SELECT ... WHERE user_id=, org_id=
        if membership is None:
            raise HTTPException(status_code=403, detail="Cross-org access denied")
        if roles and membership.role not in roles:
            raise HTTPException(status_code=403, detail=f"Requires one of: {', '.join(roles)}")
        db.execute(text("SET LOCAL app.current_org = :org_id"), {"org_id": org_id})
        return current_user_with_org_role(current_user, org_id, membership.role)
    return _check
```

**Correction (2026-08-08, found during M.2's wl-util-1 verification —
see the "System-level cross-org operations" subsection below for the
full incident): the original claim here — "RLS policies do not change" —
is true only for ordinary per-org business requests, and was incomplete
as written.** For a request that legitimately acts on exactly one org
(the case this paragraph was written about), the RLS policies themselves
— `org_id = current_setting('app.current_org')` — really do stay
unchanged; what changes is who's allowed to cause that GUC to hold a
given value. That part held up. What this section failed to consider at
all: **system-level operations that must read or write across more than
one org within a single request** (M.2's auto-provisioning; M.5's
`GET /orgs` reshape, below) cannot be expressed as "one org per request"
at any GUC value — RLS's single-org-per-request model is the wrong shape
for them by construction, not something a smarter GUC value fixes. Those
need a different, explicit mechanism — see below — and the first attempt
at one of them (M.2, implemented without reading this ADR's own
forward-flagged note in `OrgMembership`'s docstring) shipped without it
and crashed on real Postgres.

**`get_current_user` can no longer resolve `org_id`/`role` — those become
contextual to which org a specific request targets, not properties of
identity alone.** This is the one real structural change.
`get_current_user` should resolve pure identity only — `id`, `email`,
`display_name`, `login_method`, `mfa_enrolled` — and immediately
`SET LOCAL app.current_org` to the user's **home org** (a field that must
be retained, see Migration below) so the account-mechanics tables (`user`,
`user_session`, `mfa_backup_code`, `api_token`, `password_history`) resolve
correctly regardless of which business org, if any, the request later
turns out to target. `require_org_access` then does the second,
route-specific `SET LOCAL` once the path's `org_id` and membership are
known — for the account tables this is a no-op re-set to the same GUC name
but a business-table-relevant value; for routes with no `org_id` in their
path at all (`/auth/me`, `/auth/change-password`, `GET /orgs`, every I.9
self-service endpoint) it never happens, which is correct — those aren't
org-scoped operations and shouldn't need to be.

**How the active org is selected and carried across requests: it already
is, via the URL — no new mechanism needed.** Every org-scoped route already
carries `org_id` in its path (`/orgs/{org_id}/assessments/...`). There is
no server-side "current active org" session state to introduce, no
"switch org" endpoint, nothing new for the frontend to persist beyond what
`OrgPicker` already does (pick an org, then every subsequent call embeds
its id in the URL). This is a case where the existing design already
anticipated the right shape; it just needs the authorization check
underneath it to stop assuming one fixed value.

**`GET /orgs` changes shape, and gets simpler, not more complex.** Instead
of `require_role("msp_admin", "msp_engineer")` gating a query that returns
every `Organization` row, it becomes: authenticated, return every org the
caller has a membership row for. No role check needed at the endpoint level
at all — a `customer_poc` naturally gets back a list of exactly one org
(their only membership), an MSP user gets back everything they've been
auto-provisioned into. This is a strict simplification of the current
special-cased gate, not an added one. **This read is cross-org against
`org_membership`** (the whole point is seeing every membership row for
the caller, not just whichever one matches `app.current_org`) — it needs
the same SECURITY DEFINER treatment as M.2's auto-provisioning, below,
not a plain ORM query. Flagged now so M.5 doesn't repeat M.2's mistake.

### System-level cross-org operations need a different mechanism than per-request RLS

**Incident, M.2 (2026-08-08):** `provision_new_org_memberships()`'s read
of every existing `msp_admin`/`msp_engineer` user — by design, a query
that must see across every org, not just one — was implemented as a plain
`select(User.id, User.role).where(...)`, which runs under `user`'s RLS
policy (`org_id = current_setting('app.current_org', true)::uuid`,
migration `0015`) like any other query. Two failure modes resulted,
both symptoms of the same root design error, not two separate bugs:

- When `app.current_org` happened to be an empty string rather than a
  valid UUID at that point in the request (an order-dependent artifact of
  Postgres's `RESET` behavior on a never-declared custom GUC — see the
  incident writeup in the M.2 commit history for the full mechanism),
  the cast `''::uuid` raised `DataError: invalid input syntax for type
  uuid: ""` and the request 500'd.
- When `app.current_org` was validly set to the *caller's own* org, the
  query silently returned only that one org's MSP users instead of every
  MSP user in the deployment — RLS did exactly what it's supposed to do
  for a normal request, which is precisely wrong for this one.

Neither failure mode is fixable by setting the GUC "more correctly" —
there is no single value of `app.current_org` that makes "every org's
MSP users" a one-org-scoped query. The operation needs to bypass RLS
deliberately and narrowly, not work around it by chance.

**Fix: SECURITY DEFINER SQL functions, matching the existing
`auth.resolve_session`/`auth.find_user_for_login`/`auth.resolve_api_token`
precedent (migration `0015`) exactly** — the same tool this codebase
already uses for "must read/write before this request's own org context
applies." Considered and rejected: running the operation under the
RLS-bypassing owner role (reintroduces the exact blast radius the
`wingrc_app` cutover exists to shrink, for a request path that should
stay ordinary-privileged everywhere except this one narrow call); looping
`SET LOCAL app.current_org = <target>` once per org (works without ever
bypassing RLS, but mutates request-scoped state repeatedly and needs
careful save/restore so nothing *after* the loop inherits the wrong org
— fragile, and O(N) round trips for what should be one query).

```sql
CREATE FUNCTION auth.msp_role_users()
RETURNS TABLE (id UUID, role VARCHAR)
SECURITY DEFINER SET search_path = public, pg_catalog
LANGUAGE sql STABLE AS $$
    SELECT id, role FROM public."user" WHERE role IN ('msp_admin', 'msp_engineer');
$$;

CREATE FUNCTION auth.grant_org_membership(p_user_id UUID, p_org_id UUID, p_role VARCHAR)
RETURNS UUID
SECURITY DEFINER SET search_path = public, pg_catalog
LANGUAGE sql AS $$
    INSERT INTO public.org_membership (id, user_id, org_id, role)
    VALUES (gen_random_uuid(), p_user_id, p_org_id, p_role)
    ON CONFLICT (user_id, org_id) DO NOTHING
    RETURNING id;
$$;
```

`grant_org_membership`'s `ON CONFLICT DO NOTHING` replaces a Python-side
existence check that would otherwise be a second RLS-affected read
needing the same bypass — inside a SECURITY DEFINER SQL boundary
function this is the natural tool, same as migrations already use raw
SQL freely; it is not a reach for a new idiom in *application* code,
where this codebase's established dedup pattern (query first, check in
Python — see `engine.py`'s evidence-task fan-out) still governs.

**This is now the standing pattern for every future cross-org system
operation**, not just M.2's — `GET /orgs`'s reshape (above) is the next
one and needs the equivalent `auth.my_org_memberships(p_user_id)`.
Ordinary per-org business requests are unaffected and keep working
exactly as the rest of this Design section describes: `app.current_org`
set once per request from the path's `org_id`, RLS policies unchanged,
no bypass anywhere in that path.

**Not resolved here, flagged for a deliberate decision later:** migration
`0015`/`0019`'s policies (`user`, `user_session`, `mfa_backup_code`,
`api_token`, `password_history`) lack the `NULLIF(current_setting(...),
'')` guard that migration `0002`'s later template added. Adding it would
turn the empty-string crash above into a silent zero-rows result
instead — arguably worse for a security boundary, since this bug was
only caught *because* it failed loudly. Whether "fail loud" or "fail
empty" is the right default for an RLS policy hitting an unexpectedly
unset GUC is a real question this ADR doesn't resolve, not an
inconsistency to silently paper over in one direction.

**`UserSession.org_id` needs a settled, deliberate meaning, not a silent
repurpose.** Recommend: keep it as "org active when this session was
created" for display purposes only (I.9's Active Sessions list could show
it later) — never treat it as authoritative for `app.current_org` on any
request after the first. `auth.resolve_session()` should stop returning
`org_id` for RLS-setting purposes; `get_current_user` sets
`app.current_org` from the user's home org (see above), not from the
session row.

## Migration path for existing single-org users

1. New migration adds `org_membership` and the `deployment_settings`
   singleton (both schemas above). Backfill `deployment_settings` with the
   org created by whichever `bootstrap-admin` run stood up this deployment
   — identifiable today as the org attached to the earliest-created
   `msp_admin` user, since nothing currently marks it explicitly.
2. Backfill: `INSERT INTO org_membership (user_id, org_id, role) SELECT id, org_id, role FROM "user"` —
   one membership per existing user, identical access to today, zero
   behavior change on deploy.
3. Backfill pass 2, msp roles only: for every existing `msp_admin`/
   `msp_engineer`, insert membership rows into every *other* existing org
   in the deployment. This is the step that actually fixes the dead end
   documented in Context — every current MSP user becomes able to open
   every existing customer org the moment this migration lands, with no
   manual re-grant.
4. `User.org_id` → rename to `User.home_org_id`, kept permanently (not a
   throwaway migration artifact) — needed for session-resolution's
   immediate `app.current_org` set (Design, above) and as the audit-log
   anchor for account-level events that aren't scoped to any business org
   (see Audit log, below). `User.role` is removed from the `user` table
   entirely once call sites are migrated — a column that looks
   authoritative but isn't invites exactly the kind of drift this
   codebase's own `lib/roles.ts` comments already warn about for its
   hand-mirrored constants. Dropped in a **separate, later migration**
   after `org_membership` has been live and verified, per this plan's own
   standing convention of small, independently-shippable, always-green
   commits — not bundled into the introducing migration.
5. `uq_user_org_email` (`org_id`, `email`) → becomes a plain global
   `UNIQUE(email)`, since `org_id` is no longer identity-distinguishing on
   `User`. This also closes a latent, currently-unexercised ambiguity:
   `auth.find_user_for_login` already looks up by email/`entra_oid` with no
   `org_id` filter (`0015_auth_users.py:198-208`) — under today's schema,
   two `User` rows with the same email in different orgs would make that
   lookup's `LIMIT 1` resolve arbitrarily. A global unique constraint
   removes the possibility outright, as a side benefit of the migration
   rather than a new bug being introduced by it.
6. `invite_user` (`POST /orgs/{org_id}/users`) stops writing `role` onto
   the `User` row and instead inserts (or upserts) an `org_membership` row
   for `(new_user_id, org_id, body.role)`. Re-inviting an existing user
   (identified by email) into a *second* org becomes a real, supported
   operation for the first time — today `invite_user` can only ever create
   a brand-new `User` row, there's no path to grant an existing person
   access to another org at all.

## Impact on the audit log

**No change needed to `AuditLog.org_id` or the resolution mechanism —
confirmed by reading `log_event()`'s actual call sites, not assumed.**
`log_event()` takes `org_id` as a required keyword argument
(`audit.py:86-99`), and every call site passes the *resource's* org — the
path's `org_id` in admin/business routes, not `current_user.org_id` — so
this already generalizes correctly to a multi-org actor without any code
change: an msp_admin editing org B's contacts already logs `org_id=B`
(from the path), never their own identity's org. The one place this ADR's
own new endpoints (I.9 self-service: change-password, MFA reenroll, backup
codes, session revoke) pass `org_id=current_user.org_id` needs revisiting —
see below.

**Actor identity resolution is unaffected, and this is a real point in
favor of Option A over any per-org-row-per-user alternative.** `actor`
stores `str(user.id)` (`audit.py`), resolved at read time to a `User` row
by id (the "GUID identity resolution" work from this session,
`routers/audit_log.py`). Under Option A there remains exactly one `User`
row, one stable `id`, per human, regardless of how many orgs they can
access — "who did this" stays continuous across every org they've ever
touched. A design that instead gave a multi-org user a *separate* `User`
row per org (never proposed above, but worth naming as a rejected shape)
would fragment that continuity: the same person's actions in org A and org
B would resolve to different, unrelated actor ids, breaking exactly the
"who confirmed this control met, and when" guarantee ADR 0006 built the
whole anonymize-don't-delete model to protect.

**Account-level events need a stable org anchor that isn't "whichever org
happens to be in the URL," because several of them have no org in the URL
at all.** `auth.login`, `auth.logout`, `auth.mfa.enrolled`,
`auth.password_change`, `auth.mfa.reenrolled`,
`auth.mfa.backup_codes_regenerated`, `auth.sessions.revoke_all` (all in
`routers/auth.py`, several added this session under I.9) currently log
`org_id=current_user.org_id` or `org_id=user.org_id` — correct today only
because that value is unambiguous. Under multi-org, a login event isn't "in"
any particular customer org yet; recommend these continue to log against
the user's **home org** (`User.home_org_id`, retained per Migration above)
rather than `NULL`. `NULL` would make these events invisible in every org's
own audit-log viewer (`GET /orgs/{org_id}/audit-log` filters
`WHERE org_id = :org_id`) — a compliance product silently dropping
identity-lifecycle events from every audit trail is a worse outcome than
anchoring them to a slightly-approximate-but-real org.

## Flagged: I.1–I.9 work that needs revisiting under this model

- **`CurrentUser` dataclass** (`auth.py`) — `org_id`/`role` currently
  resolved once, at `get_current_user` time. Needs splitting: identity-only
  at `get_current_user`, org+role resolved per-request by
  `require_org_access` once the path's `org_id` is known (Design, above).
  Every direct `CurrentUser(...)` construction in tests — six files, per
  this session's own `mfa_enrolled` addition (`conftest.py`,
  `test_api_tokens.py`, `test_assessor_readonly.py`, `test_onboarding.py`,
  `test_org_access_guard.py`, plus `test_account_self_service.py`) — will
  need another mechanical pass. Worth a shared test-fixture builder at that
  point rather than six more manual edits next time a field changes.
- **`test_org_access_guard.py`** — its entire cross-org-403 suite currently
  asserts flat 403 for *any* `org_id != current_user.org_id`, regardless of
  role. Every one of those tests needs rewriting to the real invariant:
  403 *without* a membership row, 200 *with* one — not 403 unconditionally.
  This is the largest single test-file rewrite this migration implies.
- **`require_role("msp_admin", "msp_engineer")` on `GET /orgs`/`POST
  /orgs`** (`orgs.py:227,251`) — `POST /orgs` (create) still makes sense
  gated by role alone (anyone with an MSP role can create a new customer
  org; there's no "org" to check membership against yet). `GET /orgs`
  changes shape entirely per Design above — role gate replaced by "return
  my memberships."
- **`OrgPicker.tsx` / `lib/roles.ts`'s `MULTI_ORG_ROLES`/`canListOrgs`**
  (this session's own org-picker landing-gap fix) — currently a hard,
  role-keyed binary: MSP roles get the two-card picker, everyone else gets
  auto-selected into their one fixed org. Under this ADR's model, *any*
  role can have more than one membership (a `c3pao_assessor` across two
  engagements is the explicit example this ADR's Decision keeps open). The
  branch needs to key off "does this user have more than one
  `org_membership` row" — a per-user fact from `GET /orgs`'s response
  length — not a per-role constant. `MULTI_ORG_ROLES` as a concept goes
  away; nothing replaces it as a *role* set, because it was never really a
  role fact in the first place.
- **I.9's self-service audit calls** — `org_id=current_user.org_id` in
  `change_password`, `mfa_reenroll`/`_confirm`, `regenerate_backup_codes`,
  `revoke_all_sessions` (`routers/auth.py`) need to read
  `current_user.home_org_id` once that field exists, not whatever
  `CurrentUser.org_id` ends up meaning post-split (Audit log, above).
- **`OrgSettings.tsx`'s per-tab role gates** (`canSeeUsers`,
  `canSeeAuditLog`, `API_TOKEN_ROLES`, all keyed on `currentUserRole`) —
  **no change needed.** These are already scoped to "role in the context of
  the one org currently open in Settings," which is exactly what a
  per-membership role becomes — same shape, sourced from the active
  membership instead of a global field. Called out explicitly so this
  migration isn't assumed to touch more than it does.
- **`invite_user`** (`users.py`) — needs the re-invite-into-a-second-org
  path described in Migration step 6; today it can only create new
  identities, never grant an existing one broader access.

## Consequences

- Two new tables (`org_membership`, `deployment_settings`), one new
  migration for them, one later migration to drop `User.role` — not a
  single big-bang change, matching this codebase's standing "small
  commits, always green" discipline.
- Fixes a real, currently-shipped **defect**: MSP staff cannot open any
  org but their own today, confirmed above with exact code and test
  citations. This is not a speculative future need — it's a bug in
  already-merged behavior, and should be prioritized as one.
- The auto-provisioning rule this ADR recommends is only sound under ADR
  0005's single-MSP-per-deployment guarantee. `deployment_settings`
  records that dependency as a real, singleton-enforced constraint rather
  than leaving it as prose two ADRs have to agree on by convention — any
  future multi-MSP deployment model has to deliberately confront and
  redesign it, not silently outgrow it.
- `GET /orgs` gets simpler (membership lookup replaces a role special
  case); `require_org_access` gets one more query (membership lookup
  instead of a field comparison) on every org-scoped request — negligible
  cost, indexed by `(user_id, org_id)`.
- Nothing about RLS policy definitions changes; every existing
  `{table}_tenant_isolation` policy stays exactly as written.
- No new frontend session-state mechanism — the URL already carries
  active-org selection.
- Real migration cost is concentrated in two places: the
  `test_org_access_guard.py` rewrite, and the `CurrentUser` identity/role
  split rippling through every test file that constructs one directly.
  Both are known and bounded, not open-ended.
- Blocks nothing already shipped — every existing single-org user's access
  is preserved byte-for-byte by the backfill in Migration step 2, and step
  3 only *adds* access (existing MSP users gain reach into other existing
  orgs), never removes any.
