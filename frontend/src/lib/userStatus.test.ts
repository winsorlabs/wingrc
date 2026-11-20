import { describe, expect, it } from "vitest";
import { accountStatus } from "./userStatus";

describe("accountStatus", () => {
  it("reads an active account as active", () => {
    expect(accountStatus({ is_active: true, deactivated_at: null })).toBe("Active");
  });

  it("reads an offboarded account as deactivated", () => {
    expect(accountStatus({ is_active: false, deactivated_at: "2026-11-20T10:00:00Z" })).toBe(
      "Deactivated",
    );
  });

  it("reads an invitee or an MFA reset as awaiting enrolment, not deactivated", () => {
    expect(accountStatus({ is_active: false, deactivated_at: null })).toBe("Awaiting enrolment");
  });
});
