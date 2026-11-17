// Each segment's rule, its cases written out here rather than read from the
// implementation (CLAUDE.md, "A test naming a rule's cases writes them out
// itself"). docs/roadmap.md, "The readiness bars".
import { describe, expect, it } from "vitest";
import type { ControlStateRow } from "../types";
import { evidenceState, implementationState, raciState, readiness } from "./readiness";

function row(overrides: Partial<ControlStateRow> = {}): ControlStateRow {
  return {
    id: "x",
    objective_id: "o",
    control_id: "AC.L2-3.1.1",
    control_db_id: "d",
    family: "AC",
    control_title: "Test",
    objective_key: "a",
    objective_text: "Test objective",
    status: "not_met",
    responsibility: "customer_owns",
    contributors: [],
    statement_status: null,
    evidence_count: 0,
    raci_letters: [],
    sprs_weight: 1,
    is_level_1: false,
    ...overrides,
  };
}

const states = (r: ControlStateRow) => readiness(r).map((s) => s.state);

describe("Implementation", () => {
  it.each([
    [null, "empty"],
    ["draft", "partial"],
    ["reviewed", "good"],
    ["approved", "good"],
  ] as const)("%s reads %s", (status, expected) => {
    expect(implementationState(status)).toBe(expected);
  });

  it("a status outside the vocabulary never reads good (the database refuses one today)", () => {
    expect(implementationState("rejected")).toBe("empty");
    expect(implementationState("")).toBe("empty");
  });
});

describe("RACI", () => {
  it.each([
    [[], "empty"],
    [["R"], "partial"],
    [["A"], "partial"],
    [["C"], "partial"],
    [["I"], "partial"],
    [["C", "I"], "partial"],
    [["C", "I", "R"], "partial"],
    [["A", "C", "I"], "partial"],
    [["A", "R"], "good"],
    [["A", "C", "I", "R"], "good"],
  ] as const)("%j reads %s", (letters, expected) => {
    expect(raciState([...letters])).toBe(expected);
  });
});

describe("Evidence", () => {
  it.each([
    [0, "empty"],
    [1, "good"],
    [7, "good"],
  ] as const)("%i live link(s) read %s", (n, expected) => {
    expect(evidenceState(n)).toBe(expected);
  });

  it("has no partial state: no count reads partial", () => {
    for (let n = 0; n <= 50; n++) expect(evidenceState(n)).not.toBe("partial");
  });
});

describe("the whole bar", () => {
  it("reads Implementation | RACI | Evidence, in that order", () => {
    expect(readiness(row()).map((s) => s.key)).toEqual(["implementation", "raci", "evidence"]);
  });

  it("all three complete", () => {
    expect(
      states(row({ statement_status: "approved", raci_letters: ["A", "R"], evidence_count: 2 }))
    ).toEqual(["good", "good", "good"]);
  });

  it("all three empty", () => {
    expect(states(row())).toEqual(["empty", "empty", "empty"]);
  });

  // Jarrod, 2026-11-17: the same rule for every responsibility.
  it("a provider-satisfied objective with no statement reads empty on Implementation", () => {
    expect(
      states(row({ responsibility: "provider_satisfies", raci_letters: ["A", "R"], evidence_count: 1 }))
    ).toEqual(["empty", "good", "good"]);
  });

  it.each(["provider_satisfies", "shared", "customer_owns", "inherited"])(
    "responsibility %s changes nothing",
    (responsibility) => {
      const base = { statement_status: "draft", raci_letters: ["R"], evidence_count: 0 };
      expect(states(row({ ...base, responsibility }))).toEqual(["partial", "partial", "empty"]);
    }
  );

  it("needs_review is not a bar state: the badge says it, the bars do not", () => {
    const full = { statement_status: "reviewed", raci_letters: ["A", "R"], evidence_count: 1 };
    expect(states(row({ ...full, status: "needs_review" }))).toEqual(["good", "good", "good"]);
  });
});
