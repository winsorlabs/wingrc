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

export const ROLE_RANK: Record<string, number> = {
  msp_admin: 4,
  msp_engineer: 3,
  customer_poc: 2,
  c3pao_assessor: 1,
};

export const ROLE_LABELS: Record<string, string> = {
  msp_admin: "MSP Admin",
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

// Whether the Security nav *category* itself should render at all — hiding
// an empty category is a nav-shell-specific concern the old per-tab-only
// gating never had to answer (OrgSettings always showed something, since
// Scope's tabs were never role-gated).
export function canSeeSecurity(role: string | null | undefined): boolean {
  return canSeeUsers(role) || canSeeApiTokens(role) || canSeeAuditLog(role);
}
