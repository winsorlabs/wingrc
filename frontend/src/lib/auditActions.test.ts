import { describe, expect, it } from "vitest";
import { OFFBOARDED_ATTEMPT, actionNote } from "./auditActions";

describe("actionNote", () => {
  it("marks an offboarded account's refused sign-in", () => {
    expect(
      actionNote({
        action: "auth.login.refused_deactivated",
        context: { reason: "deactivated", method: "local" },
      }),
    ).toBe(OFFBOARDED_ATTEMPT);
  });

  it("marks a row written before the action existed the same way, by its reason", () => {
    expect(
      actionNote({ action: "auth.login.failed", context: { reason: "deactivated", method: "local" } }),
    ).toBe(OFFBOARDED_ATTEMPT);
  });

  it("tells a mistyped password apart from it", () => {
    expect(actionNote({ action: "auth.login.failed", context: { reason: "bad_password" } })).toBe(
      "Wrong password",
    );
  });

  it("shows an unknown reason as written rather than nothing", () => {
    expect(actionNote({ action: "auth.login.failed", context: { reason: "something_new" } })).toBe(
      "something_new",
    );
  });

  it("adds nothing to other actions", () => {
    expect(actionNote({ action: "user.deactivate", context: { reason: "deactivated" } })).toBeNull();
  });
});
