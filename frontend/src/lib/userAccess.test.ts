import { describe, expect, it } from "vitest";
import type { UserAccess } from "../types";
import { deactivateConfirmation, reactivateConfirmation } from "./userAccess";

const FOUR: UserAccess = {
  memberships: [
    { org_id: "1", org_name: "Acme MSP", role: "msp_admin" },
    { org_id: "2", org_name: "Fasano Dev", role: "msp_admin" },
    { org_id: "3", org_name: "Test Customer A", role: "msp_admin" },
    { org_id: "4", org_name: "Winsorlabs", role: "msp_admin" },
  ],
  reactivates_to: "active",
};

describe("deactivateConfirmation", () => {
  it("counts and names every organization the account reaches", () => {
    const said = deactivateConfirmation("Rob", FOUR);
    expect(said).toContain("4 organizations: Acme MSP, Fasano Dev, Test Customer A, Winsorlabs");
    expect(said).toContain("Reactivate restores them");
  });

  it("says one organization in the singular", () => {
    const said = deactivateConfirmation("Rob", { ...FOUR, memberships: FOUR.memberships.slice(0, 1) });
    expect(said).toContain("1 organization: Acme MSP.");
  });

  it("names no count when the lookup failed, and still says it reaches every org", () => {
    const said = deactivateConfirmation("Rob", null);
    expect(said).toContain("every organization, not only this one");
    expect(said).not.toMatch(/\d/);
  });
});

describe("reactivateConfirmation", () => {
  it("says what it restores", () => {
    expect(reactivateConfirmation("Rob", FOUR, "local")).toBe(
      "Reactivate Rob? They can sign in again, with access to 4 organizations: Acme MSP, Fasano Dev, Test Customer A, Winsorlabs.",
    );
  });

  it("says an unfinished account returns to awaiting enrolment, as the server computed", () => {
    const said = reactivateConfirmation("Ivy", { ...FOUR, reactivates_to: "awaiting_enrolment" }, "local");
    expect(said).toContain("returns to Awaiting enrolment");
    expect(said).not.toContain("can sign in again");
  });

  it("names an SSO account's state as its first sign-in", () => {
    const said = reactivateConfirmation("Sam", { ...FOUR, reactivates_to: "awaiting_enrolment" }, "sso");
    expect(said).toContain("Awaiting first sign-in");
  });
});
