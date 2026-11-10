/**
 * The SPRS rule as the assessment board draws it, live from every row
 * (never the filtered view). The server's rule is
 * backend/app/assessment.py's SATISFYING_STATUSES, and that comment has
 * the authority: since 2026-11-10 not_applicable is satisfying, by
 * 32 CFR 170.24(b)(3) ("an assessment objective assessed as N/A is
 * equivalent to the same assessment objective being assessed as MET").
 * This is its copy; sprs.test.ts names the statuses, so a change to one
 * side without the other turns a test red.
 */
import type { ControlStateRow } from "../types";

export const SATISFYING_STATUSES: ReadonlySet<string> = new Set(["met", "inherited", "not_applicable"]);

/** A practice keeps its points only when every objective is satisfying. */
export function practiceSatisfied(statuses: string[]): boolean {
  return statuses.every((s) => SATISFYING_STATUSES.has(s));
}

function byControl(rows: ControlStateRow[]): Map<string, { weight: number; statuses: string[] }> {
  const controls = new Map<string, { weight: number; statuses: string[] }>();
  for (const row of rows) {
    const c = controls.get(row.control_id) ?? { weight: row.sprs_weight, statuses: [] };
    c.statuses.push(row.status);
    controls.set(row.control_id, c);
  }
  return controls;
}

export function computeSprsLive(rows: ControlStateRow[]): number {
  let deductions = 0;
  for (const { weight, statuses } of byControl(rows).values()) {
    if (!practiceSatisfied(statuses)) deductions += weight;
  }
  return 110 - deductions;
}

/** Practices of one weight, and how many keep their points. */
export function computeTierSummary(rows: ControlStateRow[], weight: number): { met: number; total: number } {
  let met = 0;
  let total = 0;
  for (const c of byControl(rows).values()) {
    if (c.weight !== weight) continue;
    total++;
    if (practiceSatisfied(c.statuses)) met++;
  }
  return { met, total };
}
