import { describe, expect, it } from "vitest";
import { ALL_ROLES, canCreateOrg, deriveCanWrite } from "./roles";

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

describe("canCreateOrg", () => {
  // ADR 0009 M.5/M.6: which orgs a user can *see* is a per-user membership
  // fact now (GET /orgs's response length), not a role one — the old
  // canListOrgs/MULTI_ORG_ROLES this replaced no longer exists. Creating a
  // *new* org is still genuinely role-gated (there's no existing org to
  // check membership against), so that part of the old test still applies,
  // just renamed to match orgs.py's POST /orgs gate specifically.
  it("msp_admin: can create orgs", () => {
    expect(canCreateOrg("msp_admin")).toBe(true);
  });

  it("msp_engineer: can create orgs", () => {
    expect(canCreateOrg("msp_engineer")).toBe(true);
  });

  it("customer_poc: cannot create orgs", () => {
    expect(canCreateOrg("customer_poc")).toBe(false);
  });

  it("c3pao_assessor: cannot create orgs", () => {
    expect(canCreateOrg("c3pao_assessor")).toBe(false);
  });

  it("no user (null/undefined role): defaults closed, not open", () => {
    expect(canCreateOrg(null)).toBe(false);
    expect(canCreateOrg(undefined)).toBe(false);
  });

  it("covers every known role — fails loudly if a role is added without an explicit expectation above", () => {
    const expected: Record<string, boolean> = {
      msp_admin: true,
      msp_engineer: true,
      customer_poc: false,
      c3pao_assessor: false,
    };
    expect(ALL_ROLES.sort()).toEqual(Object.keys(expected).sort());
    for (const role of ALL_ROLES) {
      expect(canCreateOrg(role)).toBe(expected[role]);
    }
  });
});
