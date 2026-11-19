import { describe, expect, it } from "vitest";
import {
  bannerItems,
  coverSummary,
  formatAssumedConfig,
  kindText,
  nonRowSentence,
  parseAssumedConfig,
  PROBLEM_TEXT,
  PRODUCT_PROBLEM_TEXT,
  rowIsWrong,
} from "./baselineReview";
import type { RowProblem } from "../types";

// Written out from backend/app/baseline_import.py's PROBLEM_KINDS, not read
// from it or from this module: a kind the backend sends and the screen has
// no sentence for would show as "Needs attention".
const ROW_KINDS = [
  "control_missing", "control_unknown",
  "classification_unset", "classification_invalid",
  "coverage_basis_unset", "coverage_basis_invalid",
  "objectives_malformed", "objective_unknown",
  "evidence_malformed", "evidence_on_customer_owns",
  "evidence_artifact_missing", "evidence_type_invalid",
];
const NON_ROW_KINDS = ["file_malformed", "row_malformed", "product_field_required", "product_key_invalid"];

const p = (row_index: number | null, kind: string, value: string | null = null): RowProblem => ({
  row_index, field: null, message: "controls[0] backend sentence", kind, value,
});

describe("problem sentences", () => {
  it.each(ROW_KINDS)("has a marker and a sentence for %s", (kind) => {
    expect(PROBLEM_TEXT[kind]).toBeDefined();
    expect(kindText(kind).marker).not.toBe("Needs attention");
  });

  it.each(NON_ROW_KINDS)("has a sentence for %s", (kind) => {
    expect(PRODUCT_PROBLEM_TEXT[kind]).toBeDefined();
  });

  it("only the two unanswered questions are questions; the rest are wrong", () => {
    const decide = ROW_KINDS.filter((k) => kindText(k).severity === "decide");
    expect(decide.sort()).toEqual(["classification_unset", "coverage_basis_unset"]);
  });

  it("writes the sentence from the value, never the backend's message", () => {
    expect(kindText("objective_unknown").detail("AC.L2-3.1.1[z]")).toBe("[z] is not an objective of AC.L2-3.1.1.");
    expect(nonRowSentence({ ...p(null, "product_field_required"), field: "product.provider" })).toBe("Vendor is required.");
  });
});

describe("banner", () => {
  it("counts rows, not problems, and puts questions before what is wrong", () => {
    const items = bannerItems([
      p(3, "control_unknown", "X"),
      p(0, "coverage_basis_unset"),
      p(1, "coverage_basis_unset"),
      p(1, "objective_unknown", "C[a]"),
      p(1, "objective_unknown", "C[b]"),
    ]);
    expect(items.map((i) => i.text)).toEqual([
      "2 rows need “On whose systems?” answered",
      "1 row has an unknown control",
      "1 row lists an objective its control does not have",
    ]);
    expect(items[2].rows).toEqual([1]);
  });

  it("tints a row only for something wrong", () => {
    const problems = [p(0, "coverage_basis_unset"), p(1, "control_unknown", "X")];
    expect(rowIsWrong(problems, 0)).toBe(false);
    expect(rowIsWrong(problems, 1)).toBe(true);
  });
});

describe("assumed configuration", () => {
  it("is one item per line, and a comma stays inside its item", () => {
    const items = ["Sources: Windows Event Log, M365, FortiGate", "Retention >= 365 days"];
    expect(parseAssumedConfig(formatAssumedConfig(items))).toEqual(items);
    expect(parseAssumedConfig("a, b\r\n\n  c  \n")).toEqual(["a, b", "c"]);
  });
});

describe("cover summary", () => {
  it("says how much of the control the row credits", () => {
    expect(coverSummary(["a", "b", "c"], ["a", "b", "c", "d"], 2)).toBe("3 of 4 objectives · 2 evidence items");
    expect(coverSummary(["a"], undefined, 1)).toBe("1 objective · 1 evidence item");
  });
});
