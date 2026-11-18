import { describe, expect, it } from "vitest";
import { askHeading } from "./ActivationAsk";

describe("the activation dialog's heading names what the person did", () => {
  it("statuses only, as before", () => {
    expect(askHeading("Heimdal", 1, 0)).toBe("Activating Heimdal would change an objective you judged");
    expect(askHeading("Heimdal", 2, 0)).toBe("Activating Heimdal would change 2 objectives you judged");
  });
  it("responsibility only: never 'judged'", () => {
    expect(askHeading("Heimdal", 0, 1)).toBe("Activating Heimdal would change a responsibility you set");
    expect(askHeading("Heimdal", 0, 3)).toBe("Activating Heimdal would change 3 responsibilities you set");
  });
  it("both", () => {
    expect(askHeading("Heimdal", 1, 1)).toBe(
      "Activating Heimdal would change an objective you judged and a responsibility you set");
    expect(askHeading("Heimdal", 2, 3)).toBe(
      "Activating Heimdal would change 2 objectives you judged and 3 responsibilities you set");
  });
});
