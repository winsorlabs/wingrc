// The SSP import review screen's wording and per-row state, kept pure so
// vitest holds it. The server decides what a row may be accepted onto
// (ssp_import_session.py); nothing here widens that.
import type { SspImportFamily, SspImportRow, SspImportView, SspMatchKind } from "../types";

export function familyProgress(f: SspImportFamily): string {
  return `${f.key}: ${f.decided} of ${f.total} decided`;
}

export function overallProgress(v: SspImportView): string {
  const families = v.families.length;
  const s = v.session;
  return [
    `${s.decided} of ${s.rows} rows decided`,
    `${v.families_complete} of ${families} ${families === 1 ? "family" : "families"} complete`,
    `${s.written} ${s.written === 1 ? "draft" : "drafts"} written`,
  ].join(" · ");
}

export const KIND_LABELS: Record<SspMatchKind, string> = {
  exact: "Exact match",
  ambiguous: "Same text as more than one objective: choose one",
  family_mismatch: "Exact text, but under another family's heading: choose one",
  no_family: "Exact text, but no family heading above it: choose one",
  unmatched: "No objective's text matches",
};

export type RowState = "pending" | "conflict" | "cannot_accept" | "written" | "rejected";

/** What the row asks of the person now. `conflict` is read from the
 * server's view of the objective's statement, taken when the screen last
 * loaded; the server checks again when the row is decided. */
export function rowState(r: SspImportRow): RowState {
  if (r.decision === "accepted" || r.decision === "edited") return "written";
  if (r.decision === "rejected") return "rejected";
  if (r.acceptable_onto.length === 0) return "cannot_accept";
  if (r.existing) return "conflict";
  return "pending";
}

/** The family to open on: the first with an undecided row, so coming back
 * to the screen lands where the work is. */
export function startingFamily(v: SspImportView): string | null {
  const open = v.families.find((f) => f.decided < f.total);
  return (open ?? v.families[0])?.key ?? null;
}

export function decidedLine(r: SspImportRow): string {
  const who = r.decided_by ? ` by ${r.decided_by}` : "";
  const onto = r.decided_ref ?? r.objective_ref ?? "";
  if (r.decision === "accepted") {
    return `Accepted onto ${onto}${who}. Draft written${r.resolution === "replaced" ? ", replacing the statement that was there" : ""}.`;
  }
  if (r.decision === "edited") {
    return `Edited and accepted onto ${onto}${who}. Draft written${r.resolution === "replaced" ? ", replacing the statement that was there" : ""}.`;
  }
  if (r.resolution === "kept_existing") return `Rejected${who}: the statement already on ${onto} was kept.`;
  return `Rejected${who}. Nothing was written.`;
}

export function shortHash(sha256: string): string {
  return sha256.slice(0, 12);
}
