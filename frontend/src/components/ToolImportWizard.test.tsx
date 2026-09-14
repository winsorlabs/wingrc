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
    ingestBaselineFromDocuments: vi.fn(),
    uploadToolDocument: vi.fn(),
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

describe("ToolImportWizard — generate from vendor documents", () => {
  it("generates an editable draft, flags missing coverage_basis, and re-checks after edit", async () => {
    vi.mocked(api.ingestBaselineFromDocuments).mockResolvedValue({
      yaml: "product:\n  key: newtool\ncontrols:\n  - control: AC.L2-3.1.1\n",
      preview: makePreview({
        problems: ["controls[0].coverage_basis must be explicitly set to one of ..."],
        product_is_new: true,
        product_key: "newtool",
        product_name: "New Tool",
        control_changes: [],
      }),
    });

    render(<ToolImportWizard onClose={vi.fn()} onApplied={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: /Generate from vendor documents/ }));

    fireEvent.change(screen.getByLabelText(/Product key/), { target: { value: "newtool" } });
    const fileInput = document.querySelector("input[type=file]")!;
    const doc = new File(["fake pdf"], "crm.pdf", { type: "application/pdf" });
    fireEvent.change(fileInput, { target: { files: [doc] } });

    fireEvent.click(screen.getByRole("button", { name: /Generate Baseline/ }));

    await waitFor(() => expect(api.ingestBaselineFromDocuments).toHaveBeenCalledWith([doc], "newtool"));
    await screen.findByText(/coverage_basis must be explicitly set/);
    expect(screen.getByRole("button", { name: /Apply Import/ })).toHaveProperty("disabled", true);

    // Reviewer edits the draft to add coverage_basis, then re-checks.
    vi.mocked(api.dryRunBaselineImport).mockResolvedValue(
      makePreview({ product_is_new: true, product_key: "newtool", product_name: "New Tool" })
    );
    const textarea = screen.getByRole("textbox", { name: /Generated baseline/ });
    fireEvent.change(textarea, {
      target: { value: "product:\n  key: newtool\ncontrols:\n  - control: AC.L2-3.1.1\n    coverage_basis: customer_system\n" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Re-check/ }));

    await waitFor(() => expect(api.dryRunBaselineImport).toHaveBeenCalled());
    expect(screen.getByRole("button", { name: /Apply Import/ })).toHaveProperty("disabled", false);
  });

  it("attaches the original source documents as ProductDocuments after apply", async () => {
    vi.mocked(api.ingestBaselineFromDocuments).mockResolvedValue({
      yaml: "product:\n  key: newtool\ncontrols: []\n",
      preview: makePreview({ product_is_new: true, product_key: "newtool", control_changes: [] }),
    });
    vi.mocked(api.applyBaselineImport).mockResolvedValue({
      product_id: "p-new",
      product_key: "newtool",
      baseline_controls: 0,
      evidence_specs: 0,
    });

    render(<ToolImportWizard onClose={vi.fn()} onApplied={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: /Generate from vendor documents/ }));
    fireEvent.change(screen.getByLabelText(/Product key/), { target: { value: "newtool" } });
    const doc = new File(["fake pdf"], "crm.pdf", { type: "application/pdf" });
    fireEvent.change(document.querySelector("input[type=file]")!, { target: { files: [doc] } });
    fireEvent.click(screen.getByRole("button", { name: /Generate Baseline/ }));

    await waitFor(() => expect(api.ingestBaselineFromDocuments).toHaveBeenCalled());
    fireEvent.click(screen.getByRole("button", { name: /Apply Import/ }));

    await waitFor(() => expect(api.applyBaselineImport).toHaveBeenCalled());
    await waitFor(() =>
      expect(api.uploadToolDocument).toHaveBeenCalledWith("p-new", doc, { title: "crm.pdf", kind: "other" })
    );
  });
});
