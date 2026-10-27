import { describe, expect, it } from "vitest";
import { defaultDashboardView } from "./dashboardView";
import { ALL_ROLES } from "./roles";

describe("defaultDashboardView", () => {
  it("decides every role explicitly", () => {
    expect(Object.fromEntries(ALL_ROLES.map((r) => [r, defaultDashboardView(r)]))).toEqual({
      msp_admin: "assessment",
      consultant_admin: "assessment",
      msp_engineer: "environment",
      customer_poc: "environment",
      c3pao_assessor: "assessment",
    });
  });

  it("falls back to the assessment view for an unknown role", () => {
    expect(defaultDashboardView("somebody_new")).toBe("assessment");
    expect(defaultDashboardView(null)).toBe("assessment");
  });
});
