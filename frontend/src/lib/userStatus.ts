import type { UserRow } from "../types";

// Inactive means one of two things, and an administrator acts on them
// differently: offboarded (deactivated_at, which only Reactivate undoes) or
// still to enrol (an invitee, or an account whose MFA was reset).
export function accountStatus(u: Pick<UserRow, "is_active" | "deactivated_at">): string {
  if (u.is_active) return "Active";
  if (u.deactivated_at) return "Deactivated";
  return "Awaiting enrolment";
}
