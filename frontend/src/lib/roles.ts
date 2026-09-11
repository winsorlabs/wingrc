// This file hand-mirrors three independent backend authorization axes.
// They are separate concepts, not tiers of one "permission level" — do not
// conflate them, and if a fourth axis shows up, give it its own constant
// rather than folding it into one of these:
//   - ROLE_RANK: relative seniority, used for clamping assignable roles
//     (e.g. ApiTokensPanel can't mint a token above the current user's rank).
//   - READ_ONLY_ROLES: which roles auth.py's require_write() blocks from
//     mutating anything.
//   - ORG_CREATOR_ROLES: which roles can create a *new* org (orgs.py's
//     inline require_role("msp_admin", "msp_engineer") on POST /orgs).
//     This is genuinely still a role fact — there's no existing org to
//     check membership against when creating one — unlike which orgs a
//     user can *see*, which is a per-user membership fact, not a role one
//     (ADR 0009 M.5/M.6: GET /orgs is membership-scoped for every role, so
//     OrgPicker branches on the response's length, not on role; see that
//     ADR's "Flagged" section for why MULTI_ORG_ROLES/canListOrgs — removed
//     here — was never really a role fact in the first place).
// Each has its own keep-in-lockstep caveat: no endpoint exposes any of these
// sets, so if the backend map changes, the matching constant here needs a
// manual edit.

// Renumbered for consultant_admin (migration 0034), slotted above
// msp_engineer/below msp_admin — mirrors backend/app/auth.py's _ROLE_RANK
// exactly. Rank alone does not imply route access; see canSeeIntegrations
// below and the per-route classification in the backend routers
// themselves for what consultant_admin can/cannot actually reach.
export const ROLE_RANK: Record<string, number> = {
  msp_admin: 5,
  consultant_admin: 4,
  msp_engineer: 3,
  customer_poc: 2,
  c3pao_assessor: 1,
};

export const ROLE_LABELS: Record<string, string> = {
  msp_admin: "MSP Admin",
  consultant_admin: "Consultant Admin",
  msp_engineer: "MSP Engineer",
  customer_poc: "Customer POC",
  c3pao_assessor: "C3PAO Assessor",
};

export const ALL_ROLES = Object.keys(ROLE_RANK);

// Mirrors backend/app/auth.py's _READ_ONLY_ROLES. UX only — require_write()
// on the backend is the actual control; see I.8 in
// docs/PLAN-auth-rbac-completion.md.
export const READ_ONLY_ROLES = new Set(["c3pao_assessor"]);

// Extracted for direct unit testing (see permissions.test.ts) — useAuth
// can't be exercised without mounting a component/mocking the API.
export function deriveCanWrite(role: string | null | undefined): boolean {
  return !!role && !READ_ONLY_ROLES.has(role);
}

// Mirrors backend/app/routers/orgs.py's inline
// require_role("msp_admin", "msp_engineer") on POST /orgs. UX only — that
// dependency is the actual control; this just tells OrgPicker whether to
// show the "create a new org" affordance. Unlike the old MULTI_ORG_ROLES,
// this does NOT decide whether the picker itself is shown — that's now a
// per-user fact (GET /orgs response length), not a role one.
export const ORG_CREATOR_ROLES = new Set(["msp_admin", "msp_engineer"]);

export function canCreateOrg(role: string | null | undefined): boolean {
  return !!role && ORG_CREATOR_ROLES.has(role);
}

// Mirrors routers/users.py's/routers/audit_log.py's role gates on the
// Security nav category's three sub-items (SideNav.tsx, G.1 — formerly
// OrgSettings.tsx's inline per-tab checks, extracted here so they're
// unit-testable the same way as every other axis in this file rather than
// living only inside a component).
export const API_TOKEN_ROLES = new Set(["msp_admin", "msp_engineer"]);

export function canSeeApiTokens(role: string | null | undefined): boolean {
  return !!role && API_TOKEN_ROLES.has(role);
}

// invite_user/patch_user (users.py) are gated to msp_admin only — no
// msp_engineer rank exception, unlike API tokens above.
export function canSeeUsers(role: string | null | undefined): boolean {
  return role === "msp_admin";
}

// Matches GET /orgs/{org_id}/audit-log's require_org_access("msp_admin")
// gate exactly (routers/audit_log.py).
export function canSeeAuditLog(role: string | null | undefined): boolean {
  return role === "msp_admin";
}

// Matches routers/integrations.py's router-wide
// require_role("msp_admin", "consultant_admin") (D.1 + migration 0034) —
// unlike Security's three sub-items, there's only one gate here since
// every route on that router, including GET, shares it. consultant_admin
// is included deliberately (compliance-data classification, not a
// security one — see that router's own docstring for the deployment-wide
// tension this leaves open, flagged rather than silently accepted).
export const INTEGRATIONS_ROLES = new Set(["msp_admin", "consultant_admin"]);

export function canSeeIntegrations(role: string | null | undefined): boolean {
  return !!role && INTEGRATIONS_ROLES.has(role);
}

// Matches routers/admin_products.py's router-wide
// require_role("msp_admin", "consultant_admin") (baseline-library
// management screen, G.9). Deliberately its own constant, not a reuse of
// INTEGRATIONS_ROLES even though the two sets are identical today — this
// file's opening comment is explicit that each authorization axis gets
// its own name, and two screens sharing a gate today is a coincidence,
// not a fact about the gate. See admin_products.py's own docstring for
// the open question of whether consultant_admin belongs here at all: a
// consultant engaged for one client could publish a baseline change that
// alters compliance conclusions for every other client on the
// deployment. Flagged there, not resolved — do not change this set to
// "fix" that without a decision from Jarrod.
export const TOOLS_LIBRARY_ROLES = new Set(["msp_admin", "consultant_admin"]);

export function canSeeToolsLibrary(role: string | null | undefined): boolean {
  return !!role && TOOLS_LIBRARY_ROLES.has(role);
}

// Whether the Security nav *category* itself should render at all — hiding
// an empty category is a nav-shell-specific concern the old per-tab-only
// gating never had to answer (OrgSettings always showed something, since
// Scope's tabs were never role-gated). Deliberately does NOT check
// canSeeIntegrations — Integrations moved out of the per-org side nav
// entirely (it was never org-scoped data; see routers/integrations.py's
// own docstring) into App.tsx's deployment-tier AdminArea, reachable from
// OrgPicker. Keeping this function's definition free of it, unchanged,
// still matters for the same reason it always did: a consultant_admin who
// can see Integrations still shouldn't be put in range of Security's
// other three (org-scoped, identity-administration) sub-items just
// because both checks happen to be true for them.
export function canSeeSecurity(role: string | null | undefined): boolean {
  return canSeeUsers(role) || canSeeApiTokens(role) || canSeeAuditLog(role);
}

// Matches routers/objectives.py's router-wide require_role("msp_admin")
// (migration 0032). msp_engineer was considered and rejected there —
// practitioner_notes has no org_id to scope an msp_engineer's write to,
// since editing it changes catalog content every org on this deployment
// sees. consultant_admin (migration 0034) was considered and rejected for
// the same deployment-wide reason -- deliberately NOT extended here the
// way it was for canSeeIntegrations above; see routers/objectives.py's
// own docstring for the distinction (no "Can" list entry named this
// explicitly, unlike integrations config).
export function canEditPractitionerNotes(role: string | null | undefined): boolean {
  return role === "msp_admin";
}
