import { describe, expect, it } from "vitest";
import type { ControlStateRow } from "../types";
import { applyFilters, clearFilters, filtersActive, toggleSetItem } from "./filters";

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
    sourced_from_product_id: null,
    sourced_from_product_key: null,
    statement_status: null,
    evidence_count: 0,
    sprs_weight: 1,
    is_level_1: false,
    ...overrides,
  };
}

const NO_FILTER = clearFilters();

// ---------------------------------------------------------------------------
// No filters
// ---------------------------------------------------------------------------

it("no filters: returns all rows", () => {
  const rows = [row({ sprs_weight: 5 }), row({ sprs_weight: 1 })];
  expect(applyFilters(rows, NO_FILTER)).toHaveLength(2);
});

// ---------------------------------------------------------------------------
// L1 filter (single toggle)
// ---------------------------------------------------------------------------

it("l1Only: keeps only L1 rows", () => {
  const rows = [row({ is_level_1: true }), row({ is_level_1: false })];
  const result = applyFilters(rows, { ...NO_FILTER, l1Only: true });
  expect(result).toHaveLength(1);
  expect(result[0].is_level_1).toBe(true);
});

// ---------------------------------------------------------------------------
// Weight filter: OR within group
// ---------------------------------------------------------------------------

it("weight 5 only: excludes 3 and 1-pt rows", () => {
  const rows = [row({ sprs_weight: 5 }), row({ sprs_weight: 3 }), row({ sprs_weight: 1 })];
  const result = applyFilters(rows, { ...NO_FILTER, weights: new Set([5]) });
  expect(result).toHaveLength(1);
  expect(result[0].sprs_weight).toBe(5);
});

it("weight 5 + 3: OR — shows both, excludes 1-pt", () => {
  const rows = [row({ sprs_weight: 5 }), row({ sprs_weight: 3 }), row({ sprs_weight: 1 })];
  const result = applyFilters(rows, { ...NO_FILTER, weights: new Set([5, 3]) });
  expect(result).toHaveLength(2);
  expect(result.map((r) => r.sprs_weight)).toEqual(expect.arrayContaining([5, 3]));
});

// ---------------------------------------------------------------------------
// Status filter: OR within group
// ---------------------------------------------------------------------------

it("status not_met + partial: OR — shows both, excludes met", () => {
  const rows = [row({ status: "not_met" }), row({ status: "partial" }), row({ status: "met" })];
  const result = applyFilters(rows, { ...NO_FILTER, statuses: new Set(["not_met", "partial"]) });
  expect(result).toHaveLength(2);
  expect(result.every((r) => r.status !== "met")).toBe(true);
});

// ---------------------------------------------------------------------------
// Cross-group AND composition
// ---------------------------------------------------------------------------

it("(weight 5|3) AND not_met: excludes met rows and 1-pt rows", () => {
  const rows = [
    row({ sprs_weight: 5, status: "not_met" }),
    row({ sprs_weight: 5, status: "met" }),       // excluded: met
    row({ sprs_weight: 3, status: "not_met" }),
    row({ sprs_weight: 1, status: "not_met" }),   // excluded: weight 1
  ];
  const result = applyFilters(rows, {
    ...NO_FILTER,
    weights: new Set([5, 3]),
    statuses: new Set(["not_met"]),
  });
  expect(result).toHaveLength(2);
  result.forEach((r) => {
    expect([5, 3]).toContain(r.sprs_weight);
    expect(r.status).toBe("not_met");
  });
});

it("(weight 5|3) AND not_met AND customer_owns: three-way intersection", () => {
  const rows = [
    row({ sprs_weight: 5, status: "not_met", responsibility: "customer_owns" }),
    row({ sprs_weight: 5, status: "not_met", responsibility: "provider_satisfies" }), // wrong resp
    row({ sprs_weight: 3, status: "met",     responsibility: "customer_owns" }),       // wrong status
    row({ sprs_weight: 1, status: "not_met", responsibility: "customer_owns" }),       // wrong weight
  ];
  const result = applyFilters(rows, {
    ...NO_FILTER,
    weights: new Set([5, 3]),
    statuses: new Set(["not_met"]),
    resps: new Set(["customer_owns"]),
  });
  expect(result).toHaveLength(1);
  expect(result[0].sprs_weight).toBe(5);
  expect(result[0].status).toBe("not_met");
  expect(result[0].responsibility).toBe("customer_owns");
});

it("resp multi-select OR: customer + provider shows both, excludes shared", () => {
  const rows = [
    row({ responsibility: "customer_owns" }),
    row({ responsibility: "provider_satisfies" }),
    row({ responsibility: "shared" }),
  ];
  const result = applyFilters(rows, {
    ...NO_FILTER,
    resps: new Set(["customer_owns", "provider_satisfies"]),
  });
  expect(result).toHaveLength(2);
  expect(result.every((r) => r.responsibility !== "shared")).toBe(true);
});

// ---------------------------------------------------------------------------
// filtersActive and clearFilters
// ---------------------------------------------------------------------------

it("filtersActive: false when nothing selected", () => {
  expect(filtersActive(NO_FILTER)).toBe(false);
});

it("filtersActive: true with any selection", () => {
  expect(filtersActive({ ...NO_FILTER, weights: new Set([5]) })).toBe(true);
  expect(filtersActive({ ...NO_FILTER, l1Only: true })).toBe(true);
  expect(filtersActive({ ...NO_FILTER, statuses: new Set(["met"]) })).toBe(true);
});

it("clearFilters: resets all groups", () => {
  const cleared = clearFilters();
  expect(cleared.l1Only).toBe(false);
  expect(cleared.weights.size).toBe(0);
  expect(cleared.statuses.size).toBe(0);
  expect(cleared.resps.size).toBe(0);
  expect(filtersActive(cleared)).toBe(false);
});

// ---------------------------------------------------------------------------
// toggleSetItem
// ---------------------------------------------------------------------------

it("toggleSetItem: adds value when absent", () => {
  const result = toggleSetItem(new Set([5]), 3);
  expect(result.has(5)).toBe(true);
  expect(result.has(3)).toBe(true);
});

it("toggleSetItem: removes value when present", () => {
  const result = toggleSetItem(new Set([5, 3]), 5);
  expect(result.has(5)).toBe(false);
  expect(result.has(3)).toBe(true);
});
