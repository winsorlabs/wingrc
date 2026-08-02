import { describe, expect, it } from "vitest";
import { ALL_ROLES, deriveCanWrite } from "./roles";

describe("deriveCanWrite", () => {
  it("msp_admin: can write", () => {
    expect(deriveCanWrite("msp_admin")).toBe(true);
  });

  it("msp_engineer: can write", () => {
    expect(deriveCanWrite("msp_engineer")).toBe(true);
  });

  it("customer_poc: can write", () => {
    expect(deriveCanWrite("customer_poc")).toBe(true);
  });

  it("c3pao_assessor: read-only", () => {
    expect(deriveCanWrite("c3pao_assessor")).toBe(false);
  });

  it("no user (null/undefined role): defaults closed, not open", () => {
    expect(deriveCanWrite(null)).toBe(false);
    expect(deriveCanWrite(undefined)).toBe(false);
  });

  it("covers every known role — fails loudly if a role is added without an explicit expectation above", () => {
    const expected: Record<string, boolean> = {
      msp_admin: true,
      msp_engineer: true,
      customer_poc: true,
      c3pao_assessor: false,
    };
    expect(ALL_ROLES.sort()).toEqual(Object.keys(expected).sort());
    for (const role of ALL_ROLES) {
      expect(deriveCanWrite(role)).toBe(expected[role]);
    }
  });
});
