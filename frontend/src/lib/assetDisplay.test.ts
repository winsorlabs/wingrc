import { describe, expect, it } from "vitest";
import { assetDisplayName } from "./assetDisplay";

describe("assetDisplayName", () => {
  it("uses display_name when set", () => {
    expect(assetDisplayName({ display_name: "Jarrods Desktop" }, "SN-123")).toBe(
      "Jarrods Desktop"
    );
  });

  it("falls back to natural_key when display_name is unset", () => {
    expect(assetDisplayName({}, "SN-123")).toBe("SN-123");
  });

  it("falls back to natural_key when display_name is blank/whitespace", () => {
    expect(assetDisplayName({ display_name: "   " }, "SN-123")).toBe("SN-123");
  });

  it("falls back to natural_key when display_name is not a string", () => {
    expect(assetDisplayName({ display_name: null }, "SN-123")).toBe("SN-123");
  });
});
