// @vitest-environment jsdom
//
// Responsibility, set by a person: the RACI suggestion is shown, never
// applied; only a click writes, and it says it came from the suggestion.
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import type { RaciAssignmentRow } from "../types";
import { ResponsibilityControl, provenanceLine } from "./ResponsibilityControl";

vi.mock("../api", async (orig) => {
  const mod = await orig<typeof import("../api")>();
  return { ...mod, api: { ...mod.api, setResponsibility: vi.fn() } };
});

afterEach(cleanup);
beforeEach(() => {
  vi.mocked(api.setResponsibility).mockReset();
  vi.mocked(api.setResponsibility).mockResolvedValue({
    id: "cs", responsibility: "provider_satisfies", responsibility_set_by: "Pat", responsibility_set_at: "2026-11-18T00:00:00Z",
  });
});

const r = (letter: string, affiliation: string) =>
  ({ raci_letter: letter, contact_affiliation: affiliation, control_state_id: "cs" }) as unknown as RaciAssignmentRow;

function mount(assignments: RaciAssignmentRow[], onChanged = vi.fn()) {
  render(
    <ResponsibilityControl orgId="o" assessmentId="a" controlStateId="cs" responsibility="customer_owns"
      setBy={null} setAt={null} assignments={assignments} canWrite onChanged={onChanged} />,
  );
  return onChanged;
}

describe("the responsibility control", () => {
  it("shows the RACI suggestion and writes nothing until it is clicked", async () => {
    const onChanged = mount([r("R", "msp")]);
    expect(screen.getByText("You've made an MSP contact Responsible here. Set responsibility to Provider?")).toBeTruthy();
    expect(api.setResponsibility).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Set to Provider" }));
    await vi.waitFor(() => expect(onChanged).toHaveBeenCalled());
    expect(api.setResponsibility).toHaveBeenCalledWith("o", "a", "cs", "provider_satisfies", "raci_suggestion");
  });

  it("dismissing writes nothing", () => {
    mount([r("R", "msp")]);
    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(screen.queryByText(/Set responsibility to Provider/)).toBeNull();
    expect(api.setResponsibility).not.toHaveBeenCalled();
  });

  it("offers no suggestion when RACI agrees with the value", () => {
    mount([r("R", "customer")]);
    expect(screen.queryByText(/Set responsibility to/)).toBeNull();
  });

  it("a hand change is saved as manual", async () => {
    mount([]);
    fireEvent.change(screen.getByLabelText("Responsibility"), { target: { value: "external_system" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await vi.waitFor(() => expect(api.setResponsibility).toHaveBeenCalledWith("o", "a", "cs", "external_system", "manual"));
  });

  it("says who set it, or that nobody did", () => {
    expect(provenanceLine(null, null)).toBe("Not set by a person: the default, or the tools covering this objective.");
    expect(provenanceLine("Pat", null)).toBe("Set by Pat.");
  });
});
