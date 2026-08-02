// Mirrors backend/app/auth.py's _ROLE_RANK exactly. No endpoint exposes
// this ranking, so it has to be kept in lockstep by hand — if the backend
// map ever changes, this one needs the matching edit.
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

// Mirrors backend/app/auth.py's _READ_ONLY_ROLES exactly (same
// keep-in-lockstep caveat as ROLE_RANK above). UX only — require_write()
// on the backend is the actual control; see I.8 in
// docs/PLAN-auth-rbac-completion.md.
export const READ_ONLY_ROLES = new Set(["c3pao_assessor"]);

// Extracted for direct unit testing (see permissions.test.ts) — useAuth
// can't be exercised without mounting a component/mocking the API.
export function deriveCanWrite(role: string | null | undefined): boolean {
  return !!role && !READ_ONLY_ROLES.has(role);
}
