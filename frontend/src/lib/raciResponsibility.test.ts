import { describe, expect, it } from "vitest";
import { responsibilitySuggestionText, suggestedResponsibility } from "./raci";

const a = (raci_letter: string, contact_affiliation: string) => ({ raci_letter, contact_affiliation });

describe("the RACI suggestion, Jarrod's mapping, each case written out", () => {
  it("MSP Responsible -> provider; both -> shared; customer only -> customer", () => {
    expect(suggestedResponsibility([a("R", "msp")])).toBe("provider_satisfies");
    expect(suggestedResponsibility([a("R", "msp"), a("R", "customer")])).toBe("shared");
    expect(suggestedResponsibility([a("R", "customer")])).toBe("customer_owns");
  });

  it("suggests nothing without a Responsible msp or customer contact", () => {
    expect(suggestedResponsibility([])).toBeNull();
    expect(suggestedResponsibility([a("A", "msp"), a("C", "customer"), a("I", "msp")])).toBeNull();
    expect(suggestedResponsibility([a("R", "mssp"), a("R", "government"), a("R", "other")])).toBeNull();
  });

  it("never suggests external_system, which has no RACI shape", () => {
    const shapes = [[a("R", "msp")], [a("R", "customer")], [a("R", "msp"), a("R", "customer")]];
    expect(shapes.map(suggestedResponsibility)).not.toContain("external_system");
  });

  it("asks in words", () => {
    expect(responsibilitySuggestionText([a("R", "msp")], "provider_satisfies"))
      .toBe("You've made an MSP contact Responsible here. Set responsibility to Provider?");
    expect(responsibilitySuggestionText([a("R", "msp"), a("R", "customer")], "shared"))
      .toBe("You've made an MSP contact and a customer contact Responsible here. Set responsibility to Shared?");
  });
});
