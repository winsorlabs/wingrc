// @vitest-environment jsdom
//
// Coverage for the baseline import wizard's dry-run -> review -> apply
// discipline (G.9 §3e/§4): an invalid file reports every problem and
// blocks Apply; a valid re-import for a product with active tenants shows
// the affected-org warning before anything is written. Documents mode's
// per-control review table is covered separately below: a missing
// coverage_basis renders as a flagged dropdown, picking a value and
// re-checking clears the flag and unblocks Apply.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import type { BaselineControlDraft, BaselineImportPreview, ProductDetail, ProductMetaDraft } from "../types";
import { ToolImportWizard } from "./ToolImportWizard";

vi.mock("../api", () => ({
  api: {
    dryRunBaselineImport: vi.fn(),
    applyBaselineImport: vi.fn(),
    ingestBaselineFromDocuments: vi.fn(),
    previewStructuredBaselineImport: vi.fn(),
    uploadToolDocument: vi.fn(),
    getToolDetail: vi.fn(),
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
    row_problems: [],
    disclaim_flags: [],
    ...overrides,
  };
}

function makeProduct(overrides: Partial<ProductMetaDraft> = {}): ProductMetaDraft {
  return {
    key: "newtool",
    name: "New Tool",
    provider: "Acme",
    category: "ESP",
    asset_type: "SPA",
    framework: "NIST 800-171 Rev 2 / CMMC L2",
    role: "Does a thing.",
    assumed_config: [],
    source_docs: ["crm.pdf"],
    ai_generated_at: null,
    ai_generated_model: null,
    ...overrides,
  };
}

function makeControlRow(overrides: Partial<BaselineControlDraft> = {}): BaselineControlDraft {
  return {
    row_index: 0,
    control: ["AC.L2-3.1.1"],
    classification: "provider_satisfies",
    coverage_basis: null,
    candidate_state: "pending_evidence",
    objectives: ["a"],
    provider_contribution: null,
    customer_action: null,
    evidence: [],
    note: null,
    scope_note: null,
    ...overrides,
  };
}

function makeProductDetail(overrides: Partial<ProductDetail> = {}): ProductDetail {
  return {
    id: "p1",
    key: "rocketcyber",
    name: "RocketCyber",
    provider: "Kaseya",
    category: "ESP",
    asset_type: "SPA",
    role: "Managed SIEM + SOC",
    assumed_config: [],
    is_published: true,
    source_docs: [],
    ai_generated_at: null,
    ai_generated_model: null,
    baseline_controls: [
      {
        control_id: "AC.L2-3.1.1",
        family: "AC",
        title: "Limit system access",
        objectives: ["a"],
        classification: "provider_satisfies",
        coverage_basis: "customer_system",
        candidate_state: "pending_evidence",
        provider_contribution: null,
        customer_action: null,
        note: null,
        scope_note: null,
        evidence_specs: [],
      },
    ],
    documents: [],
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

describe("ToolImportWizard — generate from vendor documents (per-control table)", () => {
  it("flags a missing coverage_basis on its row, and clears after picking a value and re-checking", async () => {
    vi.mocked(api.ingestBaselineFromDocuments).mockResolvedValue({
      yaml: "product:\n  key: newtool\ncontrols:\n  - control: AC.L2-3.1.1\n",
      preview: makePreview({
        problems: ["controls[0].coverage_basis must be explicitly set to one of ..."],
        product_is_new: true,
        product_key: "newtool",
        product_name: "New Tool",
        control_changes: [],
        row_problems: [
          {
            row_index: 0,
            field: "coverage_basis",
            message: "controls[0].coverage_basis must be explicitly set to one of ...",
          },
        ],
      }),
      product: makeProduct(),
      controls: [makeControlRow()],
    });

    render(<ToolImportWizard onClose={vi.fn()} onApplied={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: /Generate from vendor documents/ }));

    fireEvent.change(screen.getByLabelText(/Product key/), { target: { value: "newtool" } });
    const fileInput = document.querySelector("input[type=file]")!;
    const doc = new File(["fake pdf"], "crm.pdf", { type: "application/pdf" });
    fireEvent.change(fileInput, { target: { files: [doc] } });

    fireEvent.click(screen.getByRole("button", { name: /Generate Baseline/ }));

    await waitFor(() => expect(api.ingestBaselineFromDocuments).toHaveBeenCalledWith([doc], "newtool"));
    expect(screen.getByRole("button", { name: /Apply Import/ })).toHaveProperty("disabled", true);

    const coverageSelect = screen.getByRole("combobox", { name: "Coverage basis for row 0" });
    expect(coverageSelect.className).toContain("field-needs-decision");

    // Reviewer picks a value via the dropdown, then re-checks -- no YAML
    // text editing, no page reload of the row.
    vi.mocked(api.previewStructuredBaselineImport).mockResolvedValue({
      yaml: "product:\n  key: newtool\ncontrols:\n  - control: AC.L2-3.1.1\n    coverage_basis: customer_system\n",
      preview: makePreview({ product_is_new: true, product_key: "newtool", product_name: "New Tool" }),
      product: makeProduct(),
      controls: [makeControlRow({ coverage_basis: "customer_system" })],
    });

    fireEvent.change(coverageSelect, { target: { value: "customer_system" } });
    fireEvent.click(screen.getByRole("button", { name: /Re-check/ }));

    await waitFor(() =>
      expect(api.previewStructuredBaselineImport).toHaveBeenCalledWith(
        expect.objectContaining({
          controls: [expect.objectContaining({ coverage_basis: "customer_system" })],
        })
      )
    );
    await waitFor(() =>
      expect(screen.getByRole("combobox", { name: "Coverage basis for row 0" }).className).not.toContain(
        "field-needs-decision"
      )
    );
    expect(screen.getByRole("button", { name: /Apply Import/ })).toHaveProperty("disabled", false);
  });

  it("blocks Apply while a row is edited but not yet re-checked", async () => {
    vi.mocked(api.ingestBaselineFromDocuments).mockResolvedValue({
      yaml: "product:\n  key: newtool\ncontrols:\n  - control: AC.L2-3.1.1\n    coverage_basis: customer_system\n",
      preview: makePreview({ product_is_new: true, product_key: "newtool", product_name: "New Tool", control_changes: [] }),
      product: makeProduct(),
      controls: [makeControlRow({ coverage_basis: "customer_system" })],
    });

    render(<ToolImportWizard onClose={vi.fn()} onApplied={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: /Generate from vendor documents/ }));
    fireEvent.change(screen.getByLabelText(/Product key/), { target: { value: "newtool" } });
    const doc = new File(["fake pdf"], "crm.pdf", { type: "application/pdf" });
    fireEvent.change(document.querySelector("input[type=file]")!, { target: { files: [doc] } });
    fireEvent.click(screen.getByRole("button", { name: /Generate Baseline/ }));

    await waitFor(() => expect(api.ingestBaselineFromDocuments).toHaveBeenCalled());
    expect(screen.getByRole("button", { name: /Apply Import/ })).toHaveProperty("disabled", false);

    // Editing a field after the last successful check must re-block Apply
    // -- otherwise Apply could submit a `yaml` that no longer matches
    // what's on screen.
    fireEvent.change(screen.getByRole("combobox", { name: "Candidate state for row 0" }), {
      target: { value: "not_satisfied_by_product" },
    });
    expect(screen.getByRole("button", { name: /Apply Import/ })).toHaveProperty("disabled", true);
  });

  it("attaches the original source documents as ProductDocuments after apply", async () => {
    vi.mocked(api.ingestBaselineFromDocuments).mockResolvedValue({
      yaml: "product:\n  key: newtool\ncontrols: []\n",
      preview: makePreview({ product_is_new: true, product_key: "newtool", control_changes: [] }),
      product: makeProduct(),
      controls: [],
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

describe("ToolImportWizard — editing an existing product's mapping", () => {
  it("loads pre-populated and pre-validated, with the key field read-only", async () => {
    vi.mocked(api.getToolDetail).mockResolvedValue(makeProductDetail());
    vi.mocked(api.previewStructuredBaselineImport).mockResolvedValue({
      yaml: "product:\n  key: rocketcyber\ncontrols:\n  - control: AC.L2-3.1.1\n",
      preview: makePreview({ product_key: "rocketcyber", product_name: "RocketCyber" }),
      product: makeProduct({ key: "rocketcyber", name: "RocketCyber" }),
      controls: [makeControlRow({ coverage_basis: "customer_system" })],
    });

    render(<ToolImportWizard editProductId="p1" onClose={vi.fn()} onApplied={vi.fn()} />);

    expect(await screen.findByText("Edit Baseline Mapping")).toBeTruthy();
    await waitFor(() => expect(api.getToolDetail).toHaveBeenCalledWith("p1"));
    await waitFor(() => expect(api.previewStructuredBaselineImport).toHaveBeenCalled());

    // Landed straight in the table -- no chooser screen, no separate
    // "Preview"/"Generate" click needed.
    expect(await screen.findByRole("combobox", { name: "Classification for row 0" })).toBeTruthy();
    const keyInput = screen.getByDisplayValue("rocketcyber") as HTMLInputElement;
    expect(keyInput.disabled).toBe(true);
    expect(screen.getByRole("button", { name: /Save Changes/ })).toHaveProperty("disabled", false);
  });

  it("a newly added blank row survives Re-check with its own flag, instead of vanishing", async () => {
    vi.mocked(api.getToolDetail).mockResolvedValue(makeProductDetail());
    vi.mocked(api.previewStructuredBaselineImport).mockResolvedValueOnce({
      yaml: "product:\n  key: rocketcyber\ncontrols:\n  - control: AC.L2-3.1.1\n",
      preview: makePreview({ product_key: "rocketcyber", product_name: "RocketCyber" }),
      product: makeProduct({ key: "rocketcyber", name: "RocketCyber" }),
      controls: [makeControlRow({ coverage_basis: "customer_system" })],
    });

    render(<ToolImportWizard editProductId="p1" onClose={vi.fn()} onApplied={vi.fn()} />);
    await waitFor(() => expect(api.previewStructuredBaselineImport).toHaveBeenCalledTimes(1));

    fireEvent.click(await screen.findByRole("button", { name: /\+ Add control/ }));

    // The second (Re-check) call reflects the blank row surviving the
    // round trip, with its own row_problem attached.
    vi.mocked(api.previewStructuredBaselineImport).mockResolvedValueOnce({
      yaml: "product:\n  key: rocketcyber\ncontrols:\n  - control: AC.L2-3.1.1\n  - control: ''\n",
      preview: makePreview({
        problems: ["controls[1].control must be a control id or a list of them."],
        product_key: "rocketcyber",
        product_name: "RocketCyber",
        row_problems: [
          {
            row_index: 1,
            field: "control",
            message: "controls[1].control must be a control id or a list of them.",
          },
        ],
      }),
      product: makeProduct({ key: "rocketcyber", name: "RocketCyber" }),
      controls: [
        makeControlRow({ coverage_basis: "customer_system" }),
        makeControlRow({ row_index: 1, control: [], classification: null, coverage_basis: null, objectives: [] }),
      ],
    });
    fireEvent.click(screen.getByRole("button", { name: /Re-check/ }));

    await waitFor(() => expect(api.previewStructuredBaselineImport).toHaveBeenCalledTimes(2));
    const secondCallArg = vi.mocked(api.previewStructuredBaselineImport).mock.calls[1][0];
    expect(secondCallArg.controls).toHaveLength(2);

    // The blank row is still rendered (row 1), flagged, not dropped.
    expect(screen.getByRole("combobox", { name: "Classification for row 0" })).toBeTruthy();
    const row1Control = screen.getByRole("textbox", { name: "Control(s) for row 1" });
    expect(row1Control.className).toContain("field-needs-decision");
  });

  it("shows a disclaim flag as advisory (amber), distinct from a red needs-decision flag, and never blocks Apply", async () => {
    vi.mocked(api.ingestBaselineFromDocuments).mockResolvedValue({
      yaml: "product:\n  key: newtool\ncontrols:\n  - control: AC.L2-3.1.1\n    classification: shared\n    coverage_basis: customer_system\n",
      preview: makePreview({
        product_is_new: true,
        product_key: "newtool",
        product_name: "New Tool",
        control_changes: [],
        disclaim_flags: [
          {
            row_index: 0,
            message:
              "This entry's own supporting text reads as a disclaim (vendor doesn't cover this), but it's classified 'shared'.",
          },
        ],
      }),
      product: makeProduct(),
      controls: [
        makeControlRow({
          coverage_basis: "customer_system",
          note: "Kaseya explicitly states it does not implement or enforce this control.",
        }),
      ],
    });

    render(<ToolImportWizard onClose={vi.fn()} onApplied={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: /Generate from vendor documents/ }));
    fireEvent.change(screen.getByLabelText(/Product key/), { target: { value: "newtool" } });
    const doc = new File(["fake pdf"], "crm.pdf", { type: "application/pdf" });
    fireEvent.change(document.querySelector("input[type=file]")!, { target: { files: [doc] } });
    fireEvent.click(screen.getByRole("button", { name: /Generate Baseline/ }));

    await waitFor(() => expect(api.ingestBaselineFromDocuments).toHaveBeenCalled());

    const clsSelect = screen.getByRole("combobox", { name: "Classification for row 0" });
    expect(clsSelect.className).toContain("field-disclaim-flag");
    expect(clsSelect.className).not.toContain("field-needs-decision");
    expect(screen.getByText(/reads as a disclaim/)).toBeTruthy();
    // No coverage_basis problem here (it's set), no row_problems at all --
    // an advisory-only flag must never block Apply.
    expect(screen.getByRole("button", { name: /Apply Import/ })).toHaveProperty("disabled", false);
  });
});
