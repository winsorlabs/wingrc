import { describe, expect, it } from "vitest";
import {
  ALL_ROLES,
  canCreateOrg,
  canEditPractitionerNotes,
  canSeeApiTokens,
  canSeeAuditLog,
  canSeeIntegrations,
  canSeeSecurity,
  canSeeUsers,
  deriveCanWrite,
} from "./roles";

describe("deriveCanWrite", () => {
  it("msp_admin: can write", () => {
    expect(deriveCanWrite("msp_admin")).toBe(true);
  });

  it("consultant_admin: can write", () => {
    expect(deriveCanWrite("consultant_admin")).toBe(true);
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
      consultant_admin: true,
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

  // Migration 0034: deliberately excluded — org creation auto-provisions
  // every msp_admin/msp_engineer into the new org (org_membership.py's
  // _AUTO_PROVISION_ROLES, which consultant_admin is NOT in), so a
  // consultant_admin caller would create an org it then has no membership
  // in and can never reach again. See routers/orgs.py's create_org() gate
  // comment for the full reasoning.
  it("consultant_admin: cannot create orgs", () => {
    expect(canCreateOrg("consultant_admin")).toBe(false);
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
      consultant_admin: false,
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

// G.1: SideNav.tsx's Security category, and the three sub-items under it —
// extracted from what was previously inline in OrgSettings.tsx (untested
// there too; now unit-tested the same way as every other axis in this file).
describe("canSeeUsers / canSeeApiTokens / canSeeAuditLog / canSeeSecurity", () => {
  const expectedUsers: Record<string, boolean> = {
    msp_admin: true,
    consultant_admin: false,
    msp_engineer: false,
    customer_poc: false,
    c3pao_assessor: false,
  };
  const expectedApiTokens: Record<string, boolean> = {
    msp_admin: true,
    consultant_admin: false,
    msp_engineer: true,
    customer_poc: false,
    c3pao_assessor: false,
  };
  // Migration 0034: consultant_admin is deliberately excluded — decided
  // and documented in routers/audit_log.py's own docstring. The log is
  // one undifferentiated stream that also carries identity/security
  // events (user.role_change, api_token.create, credential rotation,
  // IP addresses), not just compliance-data entries, so granting it
  // would disclose security-relevant history regardless of read-only
  // access.
  const expectedAuditLog: Record<string, boolean> = {
    msp_admin: true,
    consultant_admin: false,
    msp_engineer: false,
    customer_poc: false,
    c3pao_assessor: false,
  };

  it("covers every known role for each sub-item — fails loudly if a role is added without an explicit expectation above", () => {
    expect(ALL_ROLES.sort()).toEqual(Object.keys(expectedUsers).sort());
    for (const role of ALL_ROLES) {
      expect(canSeeUsers(role)).toBe(expectedUsers[role]);
      expect(canSeeApiTokens(role)).toBe(expectedApiTokens[role]);
      expect(canSeeAuditLog(role)).toBe(expectedAuditLog[role]);
    }
  });

  it("no user (null/undefined role): every check defaults closed, not open", () => {
    expect(canSeeUsers(null)).toBe(false);
    expect(canSeeUsers(undefined)).toBe(false);
    expect(canSeeApiTokens(null)).toBe(false);
    expect(canSeeApiTokens(undefined)).toBe(false);
    expect(canSeeAuditLog(null)).toBe(false);
    expect(canSeeAuditLog(undefined)).toBe(false);
    expect(canSeeSecurity(null)).toBe(false);
    expect(canSeeSecurity(undefined)).toBe(false);
  });

  it("canSeeSecurity is true whenever any one sub-item is visible", () => {
    // msp_admin: all three. msp_engineer: only API tokens. Both must show
    // the category itself.
    expect(canSeeSecurity("msp_admin")).toBe(true);
    expect(canSeeSecurity("msp_engineer")).toBe(true);
  });

  it("canSeeSecurity is false when every sub-item is hidden — the category itself must not render an empty room", () => {
    expect(canSeeSecurity("customer_poc")).toBe(false);
    expect(canSeeSecurity("c3pao_assessor")).toBe(false);
    // consultant_admin sees none of Users/API Tokens/Audit Log — the
    // Security nav category itself must not render for it either, even
    // though it sees Integrations (a separate, non-Security nav entry —
    // see canSeeIntegrations below).
    expect(canSeeSecurity("consultant_admin")).toBe(false);
  });
});

// Migration 0034: matches routers/integrations.py's router-wide
// require_role("msp_admin", "consultant_admin") exactly. Integrations is
// its own top-level nav entry (SideNav.tsx's showIntegrations), not part
// of the Security category above — a consultant_admin seeing this entry
// and 403ing on click would be a bad experience and look broken, which is
// exactly what this test guards against.
describe("canSeeIntegrations", () => {
  const expected: Record<string, boolean> = {
    msp_admin: true,
    consultant_admin: true,
    msp_engineer: false,
    customer_poc: false,
    c3pao_assessor: false,
  };

  it("covers every known role — fails loudly if a role is added without an explicit expectation above", () => {
    expect(ALL_ROLES.sort()).toEqual(Object.keys(expected).sort());
    for (const role of ALL_ROLES) {
      expect(canSeeIntegrations(role)).toBe(expected[role]);
    }
  });

  it("no user (null/undefined role): defaults closed, not open", () => {
    expect(canSeeIntegrations(null)).toBe(false);
    expect(canSeeIntegrations(undefined)).toBe(false);
  });
});

// Migration 0032: matches routers/objectives.py's router-wide
// require_role("msp_admin") exactly -- msp_admin only, no msp_engineer
// exception (practitioner_notes has no org_id to scope a write to), and
// no c3pao_assessor exception (that role stays permanently read-only,
// deliberately not carved out here — see that router's own docstring).
// consultant_admin (migration 0034) was considered and rejected for the
// same deployment-wide-scope reason, deliberately NOT extended the way
// canSeeIntegrations above was.
describe("canEditPractitionerNotes", () => {
  const expected: Record<string, boolean> = {
    msp_admin: true,
    consultant_admin: false,
    msp_engineer: false,
    customer_poc: false,
    c3pao_assessor: false,
  };

  it("covers every known role — fails loudly if a role is added without an explicit expectation above", () => {
    expect(ALL_ROLES.sort()).toEqual(Object.keys(expected).sort());
    for (const role of ALL_ROLES) {
      expect(canEditPractitionerNotes(role)).toBe(expected[role]);
    }
  });

  it("no user (null/undefined role): defaults closed, not open", () => {
    expect(canEditPractitionerNotes(null)).toBe(false);
    expect(canEditPractitionerNotes(undefined)).toBe(false);
  });
});
