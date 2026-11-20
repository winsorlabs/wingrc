import type { UserRow } from "../types";

// Inactive means one of two things, and an administrator acts on them
// differently: offboarded (deactivated_at, which only Reactivate undoes) or
// still to finish signing up (an invitee, or an account whose MFA was reset).
// Reactivating an account that never finished returns it to the second, so
// "Active" always has an authenticator behind it.
export function accountStatus(
  u: Pick<UserRow, "is_active" | "deactivated_at" | "login_method">,
): string {
  if (u.is_active) return "Active";
  if (u.deactivated_at) return "Deactivated";
  return u.login_method === "sso" ? "Awaiting first sign-in" : "Awaiting enrolment";
}
