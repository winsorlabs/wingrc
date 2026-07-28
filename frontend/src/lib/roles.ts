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
