// The readiness bars: three segments per objective, Implementation | RACI |
// Evidence (docs/roadmap.md, "The readiness bars"). Each reads the
// control-states payload and decides nothing: no status, no SPRS.
//
// The same rule holds for every objective, whatever its responsibility
// (Jarrod, 2026-11-17). A provider-satisfied objective still needs a
// statement, an owner and evidence to read good. That is why `readiness`
// takes no `responsibility`, and no `status` either: `needs_review` has its
// own badge, and the bars add no second warning for it.

import type { ControlStateRow } from "../types";

export type SegmentState = "good" | "partial" | "empty";

export interface Segment {
  key: "implementation" | "raci" | "evidence";
  short: string;
  name: string;
  state: SegmentState;
  detail: string;
}

// Red: no statement. Yellow: a draft. Green: reviewed or approved. A status
// the vocabulary does not have never reads green.
export function implementationState(statementStatus: string | null): SegmentState {
  if (statementStatus === "reviewed" || statementStatus === "approved") return "good";
  if (statementStatus === "draft") return "partial";
  return "empty";
}

// Green: R and A are both held. Yellow: some letter is held, but R or A is
// missing. Red: none.
export function raciState(letters: string[]): SegmentState {
  if (letters.includes("R") && letters.includes("A")) return "good";
  if (letters.length > 0) return "partial";
  return "empty";
}

// Two states, not three: at least one live link, or none. Archived links
// are shown on the objective and never counted (`evidence_count`, 2026-10-19).
export function evidenceState(liveLinks: number): SegmentState {
  return liveLinks > 0 ? "good" : "empty";
}

function raciDetail(letters: string[]): string {
  if (letters.length === 0) return "nobody assigned";
  const missing = ["R", "A"].filter((l) => !letters.includes(l));
  const held = `${letters.join(", ")} held`;
  return missing.length === 0 ? held : `${held}; no ${missing.join(" or ")}`;
}

export function readiness(
  row: Pick<ControlStateRow, "statement_status" | "raci_letters" | "evidence_count">
): Segment[] {
  return [
    {
      key: "implementation",
      short: "Impl",
      name: "Implementation statement",
      state: implementationState(row.statement_status),
      detail: row.statement_status ?? "none",
    },
    {
      key: "raci",
      short: "RACI",
      name: "RACI",
      state: raciState(row.raci_letters),
      detail: raciDetail(row.raci_letters),
    },
    {
      key: "evidence",
      short: "Evid",
      name: "Evidence",
      state: evidenceState(row.evidence_count),
      detail:
        row.evidence_count === 0
          ? "no live evidence"
          : `${row.evidence_count} attached`,
    },
  ];
}

export const STATE_WORD: Record<SegmentState, string> = {
  good: "complete",
  partial: "partial",
  empty: "empty",
};
