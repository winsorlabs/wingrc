// The board's copy of the SPRS rule (backend/app/assessment.py's
// SATISFYING_STATUSES). Statuses are named here, not read from the set
// under test. Since 2026-11-10 not_applicable is satisfying: 32 CFR
// 170.24(b)(3).
import { describe, expect, it } from "vitest";
import type { ControlStateRow } from "../types";
import { computeSprsLive, computeTierSummary, practiceSatisfied, SATISFYING_STATUSES } from "./sprs";

function row(control_id: string, sprs_weight: number, status: string): ControlStateRow {
  return { control_id, sprs_weight, status } as ControlStateRow;
}

const ALL = ["met", "not_met", "partial", "pending_evidence", "needs_review", "not_applicable", "inherited"];

describe("SPRS on the board", () => {
  it("satisfies a practice only with met, inherited or not_applicable", () => {
    expect([...SATISFYING_STATUSES].sort()).toEqual(["inherited", "met", "not_applicable"]);
    for (const s of ALL) {
      expect(practiceSatisfied([s]), s).toBe(["met", "inherited", "not_applicable"].includes(s));
    }
  });

  it("scores a practice with an N/A objective and a met one as met", () => {
    expect(computeSprsLive([row("P5", 5, "not_applicable"), row("P5", 5, "met")])).toBe(110);
  });

  it("scores a practice whose objectives are all N/A as met", () => {
    expect(computeSprsLive([row("P5", 5, "not_applicable"), row("P5", 5, "not_applicable")])).toBe(110);
  });

  it("deducts the whole weight when one objective is not met, N/A beside it or not", () => {
    const rows = [
      row("P5", 5, "not_applicable"), row("P5", 5, "met"), row("P5", 5, "not_met"),
      row("P3", 3, "not_applicable"),
    ];
    expect(computeSprsLive(rows)).toBe(105);
    expect(computeTierSummary(rows, 5)).toEqual({ met: 0, total: 1 });
    expect(computeTierSummary(rows, 3)).toEqual({ met: 1, total: 1 });
  });
});
