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
