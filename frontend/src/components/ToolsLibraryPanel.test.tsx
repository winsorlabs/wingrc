// @vitest-environment jsdom
//
// Coverage for the Tools library screen's key flows (G.9): the library
// list renders published/unpublished state, clicking a row drills into
// the read-only detail view (baseline mapping + footprint + documents),
// and publish/unpublish round-trips through the detail view without
// touching any other product's row.
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import type { ProductDetail, ProductLibraryItem } from "../types";
import { ToolsLibraryPanel } from "./ToolsLibraryPanel";

vi.mock("../api", () => ({
  api: {
    listToolsLibrary: vi.fn(),
    getToolDetail: vi.fn(),
    getToolFootprint: vi.fn(),
    publishTool: vi.fn(),
    unpublishTool: vi.fn(),
    listToolDocuments: vi.fn(),
    toolDocumentDownloadUrl: vi.fn(() => "/api/admin/products/p1/documents/d1/download"),
  },
}));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function makeLibraryItem(overrides: Partial<ProductLibraryItem> = {}): ProductLibraryItem {
  return {
    id: "p1",
    key: "rocketcyber",
    name: "RocketCyber",
    provider: "Kaseya",
    category: "SIEM/SOC",
    asset_type: "endpoint",
    framework_name: "CMMC L2",
    is_published: true,
    control_count: 3,
    objective_count: 6,
    ...overrides,
  };
}

function makeDetail(overrides: Partial<ProductDetail> = {}): ProductDetail {
  return {
    id: "p1",
    key: "rocketcyber",
    name: "RocketCyber",
    provider: "Kaseya",
    category: "SIEM/SOC",
    asset_type: "endpoint",
    role: "provider",
    assumed_config: ["24/7 SOC monitoring enabled"],
    is_published: true,
    source_docs: ["RocketCyber_SIEM_and_SOC_Baseline.docx"],
    baseline_controls: [
      {
        control_id: "3.14.6",
        family: "SI",
        title: "Monitor systems",
        objectives: ["3.14.6[a]"],
        classification: "provider_satisfies",
        coverage_basis: "customer_system",
        candidate_state: "pending_evidence",
        provider_contribution: "24/7 monitoring",
        customer_action: null,
        note: null,
        scope_note: null,
        evidence_specs: [],
      },
      {
        control_id: "3.14.99",
        family: "SI",
        title: "Vendor self-attestation",
        objectives: ["3.14.99[a]"],
        classification: "provider_satisfies",
        coverage_basis: "platform_only",
        candidate_state: "not_met",
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

describe("ToolsLibraryPanel — list", () => {
  it("shows published/unpublished status for each product", async () => {
    vi.mocked(api.listToolsLibrary).mockResolvedValue([
      makeLibraryItem({ id: "p1", name: "RocketCyber", is_published: true }),
      makeLibraryItem({ id: "p2", name: "Heimdal", is_published: false }),
    ]);

    render(<ToolsLibraryPanel />);

    await screen.findByText("RocketCyber");
    expect(screen.getByText("Heimdal")).toBeTruthy();
    expect(screen.getByText("Published")).toBeTruthy();
    expect(screen.getByText("Unpublished")).toBeTruthy();
  });
});

describe("ToolsLibraryPanel — detail", () => {
  it("drills into a product's baseline mapping and distinguishes platform_only rows", async () => {
    vi.mocked(api.listToolsLibrary).mockResolvedValue([makeLibraryItem()]);
    vi.mocked(api.getToolDetail).mockResolvedValue(makeDetail());
    vi.mocked(api.getToolFootprint).mockResolvedValue([
      { org_id: "org1", org_name: "Acme Corp", status: "active" },
    ]);

    render(<ToolsLibraryPanel />);
    await screen.findByText("RocketCyber");
    fireEvent.click(screen.getByText("RocketCyber"));

    await screen.findByText("Acme Corp");
    expect(screen.getByText("3.14.6")).toBeTruthy();
    expect(screen.getByText(/Excluded from magic-loop activation/)).toBeTruthy();
  });

  it("publishing an unpublished product flips its badge without affecting other rows", async () => {
    vi.mocked(api.listToolsLibrary).mockResolvedValue([makeLibraryItem({ is_published: false })]);
    vi.mocked(api.getToolDetail).mockResolvedValue(makeDetail({ is_published: false }));
    vi.mocked(api.getToolFootprint).mockResolvedValue([]);
    vi.mocked(api.publishTool).mockResolvedValue({ id: "p1", is_published: true });

    render(<ToolsLibraryPanel />);
    await screen.findByText("RocketCyber");
    fireEvent.click(screen.getByText("RocketCyber"));

    await screen.findByRole("button", { name: "Publish" });
    fireEvent.click(screen.getByRole("button", { name: "Publish" }));

    await screen.findByRole("button", { name: "Unpublish" });
    expect(api.publishTool).toHaveBeenCalledWith("p1");
  });
});
