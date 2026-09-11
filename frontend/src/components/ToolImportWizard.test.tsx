// @vitest-environment jsdom
//
// Coverage for the baseline import wizard's dry-run -> review -> apply
// discipline (G.9 §3e/§4): an invalid file reports every problem and
// blocks Apply; a valid re-import for a product with active tenants shows
// the affected-org warning before anything is written.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import type { BaselineImportPreview } from "../types";
import { ToolImportWizard } from "./ToolImportWizard";

vi.mock("../api", () => ({
  api: {
    dryRunBaselineImport: vi.fn(),
    applyBaselineImport: vi.fn(),
  },
}));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function selectFile(input: HTMLElement, name = "baseline.yaml") {
  const file = new File(["key: value"], name, { type: "application/x-yaml" });
  fireEvent.change(input, { target: { files: [file] } });
}

function makePreview(overrides: Partial<BaselineImportPreview> = {}): BaselineImportPreview {
  return {
    problems: [],
    product_key: "rocketcyber",
    product_is_new: false,
    product_name: "RocketCyber",
    control_changes: [
      { control_id: "3.14.6", change_type: "changed", classification: "provider_satisfies", coverage_basis: "customer_system", field_diffs: {} },
    ],
    affected_org_count: 0,
    affected_org_names: [],
    ...overrides,
  };
}

describe("ToolImportWizard — invalid file", () => {
  it("reports every problem at once and disables Apply", async () => {
    vi.mocked(api.dryRunBaselineImport).mockResolvedValue(
      makePreview({
        problems: [
          "Unknown control id: 3.99.99",
          "Evidence spec on a customer_owns row for 3.14.6",
          "candidate_state 'bogus' is not valid",
        ],
      })
    );

    render(<ToolImportWizard onClose={vi.fn()} onApplied={vi.fn()} />);
    selectFile(document.querySelector("input[type=file]")!);
    fireEvent.click(screen.getByRole("button", { name: /Preview Import/ }));

    await screen.findByText(/3 problems/);
    expect(screen.getByText(/Unknown control id/)).toBeTruthy();
    expect(screen.getByText(/Evidence spec on a customer_owns row/)).toBeTruthy();
    expect(screen.getByText(/candidate_state 'bogus'/)).toBeTruthy();
    expect(screen.getByRole("button", { name: /Apply Import/ })).toHaveProperty("disabled", true);
    expect(api.applyBaselineImport).not.toHaveBeenCalled();
  });
});

describe("ToolImportWizard — valid re-import with active tenants", () => {
  it("warns with the affected-org count before apply, then applies", async () => {
    vi.mocked(api.dryRunBaselineImport).mockResolvedValue(
      makePreview({ affected_org_count: 2, affected_org_names: ["Acme Corp", "Widgets Inc"] })
    );
    vi.mocked(api.applyBaselineImport).mockResolvedValue({
      product_id: "p1",
      product_key: "rocketcyber",
      baseline_controls: 1,
      evidence_specs: 0,
    });

    render(<ToolImportWizard onClose={vi.fn()} onApplied={vi.fn()} />);
    selectFile(document.querySelector("input[type=file]")!);
    fireEvent.click(screen.getByRole("button", { name: /Preview Import/ }));

    await screen.findByText(/2 orgs already have this product/);
    expect(screen.getByText(/Acme Corp, Widgets Inc/)).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: /Apply Import/ }));

    await waitFor(() => expect(api.applyBaselineImport).toHaveBeenCalled());
    await screen.findByText(/Imported 1 baseline control/);
  });
});
