import type { Contact } from "../types";

// Shared between RolesPanel.tsx (the full matrix/bulk-assign page) and
// RaciSection.tsx (the inline per-objective control in the assessment
// board's drawer, G.7 Part 2) -- one suggestion rule, not two copies that
// can drift apart.

export const RACI_LETTERS = ["R", "A", "C", "I"] as const;
export const RACI_LETTER_LABELS: Record<string, string> = {
  R: "Responsible", A: "Accountable", C: "Consulted", I: "Informed",
};

export type Affiliation = "msp" | "customer";

// ControlState.responsibility -> which side of the MSP/customer line
// usually owns it. Suggestion only, per G.7 ("candidates, never
// auto-met" applied to RACI too) -- see Contact model's own docstring in
// backend/app/models.py for the same mapping stated as design intent.
// Not the same field the plan doc names (BaselineControl.responsibility) --
// see raci.py's module docstring for why control_state.responsibility is
// used instead: it's the already-resolved per-objective value, available
// here for free from endpoints this app already calls (control-states for
// RolesPanel, statements for RaciSection), rather than a second lookup
// back through BaselineControl.
export function suggestedAffiliation(responsibility: string): Affiliation {
  return responsibility === "provider_satisfies" || responsibility === "shared" ? "msp" : "customer";
}

// Contacts of the suggested affiliation first, so the picker's default
// option is the suggestion -- still nothing is written until the user
// explicitly submits the form.
export function sortedForSuggestion(contacts: Contact[], affiliation: Affiliation): Contact[] {
  return [...contacts].sort((a, b) => {
    const aMatch = a.affiliation === affiliation ? 0 : 1;
    const bMatch = b.affiliation === affiliation ? 0 : 1;
    if (aMatch !== bMatch) return aMatch - bMatch;
    return a.name.localeCompare(b.name);
  });
}

// The other direction: RACI -> a responsibility to suggest (Jarrod's
// mapping, 2026-11-18). Suggestions point both ways; only a person writes
// (docs/roadmap.md, "Responsibility had no human write path"). Deriving it
// automatically would make the two fields feed each other, and would be a
// person's assignment silently rewriting an assessor-facing claim.
//   MSP contact(s) Responsible only        -> provider_satisfies
//   MSP and customer contacts Responsible  -> shared
//   customer contact(s) Responsible only   -> customer_owns
// Only "R" counts, and only the msp and customer affiliations: an mssp,
// government or other contact Responsible suggests nothing, because the
// mapping does not say what it means. external_system has no RACI shape;
// it is set by hand only.
export const RESPONSIBILITY_LABELS: Record<string, string> = {
  provider_satisfies: "Provider",
  shared: "Shared",
  customer_owns: "Customer",
  external_system: "External system",
};

export function suggestedResponsibility(
  assignments: { raci_letter: string; contact_affiliation: string }[],
): string | null {
  const r = assignments.filter((a) => a.raci_letter === "R");
  const msp = r.some((a) => a.contact_affiliation === "msp");
  const customer = r.some((a) => a.contact_affiliation === "customer");
  if (msp && customer) return "shared";
  if (msp) return "provider_satisfies";
  if (customer) return "customer_owns";
  return null;
}

export function responsibilitySuggestionText(
  assignments: { raci_letter: string; contact_affiliation: string }[],
  suggested: string,
): string {
  const r = assignments.filter((a) => a.raci_letter === "R");
  const msp = r.some((a) => a.contact_affiliation === "msp");
  const customer = r.some((a) => a.contact_affiliation === "customer");
  const who = msp && customer ? "an MSP contact and a customer contact" : msp ? "an MSP contact" : "a customer contact";
  return `You've made ${who} Responsible here. Set responsibility to ${RESPONSIBILITY_LABELS[suggested]}?`;
}
