import type { UserAccess } from "../types";

// What the Deactivate and Reactivate confirmations say. is_active and
// deactivated_at are on the user, not the membership, so the action taken
// from one org's Users tab reaches every org the account is a member of.
// Until 2026-11-20 the confirmation said only "Deactivate?", and
// deactivating from Acme MSP's tab removed access to four organizations.
//
// access is null when the lookup failed. The sentence then says what the
// rule guarantees (every organization) without naming any, rather than a
// count nobody computed.

function orgList(access: UserAccess): string {
  const names = access.memberships.map((m) => m.org_name);
  if (names.length === 0) return "no organizations";
  const noun = names.length === 1 ? "organization" : "organizations";
  return `${names.length} ${noun}: ${names.join(", ")}`;
}

export function deactivateConfirmation(name: string, access: UserAccess | null): string {
  if (access === null) {
    return `Deactivate ${name}? This signs them out and ends their access to every organization, not only this one. Which organizations could not be checked.`;
  }
  if (access.memberships.length === 0) {
    return `Deactivate ${name}? This signs them out. They have access to no organizations.`;
  }
  return `Deactivate ${name}? This signs them out and ends their access to ${orgList(access)}. The memberships are kept, so Reactivate restores them.`;
}

export function reactivateConfirmation(
  name: string,
  access: UserAccess | null,
  loginMethod: string,
): string {
  if (access === null) {
    return `Reactivate ${name}? This restores their access to every organization they are a member of. Which organizations could not be checked.`;
  }
  const reach = orgList(access);
  if (access.reactivates_to === "active") {
    return `Reactivate ${name}? They can sign in again, with access to ${reach}.`;
  }
  const state = loginMethod === "sso" ? "Awaiting first sign-in" : "Awaiting enrolment";
  return `Reactivate ${name}? They have not finished signing up, so the account returns to ${state}, and they get access to ${reach} once they do.`;
}
