// @vitest-environment jsdom
//
// Coverage for the acceptance-record display on the asset itself
// (2026-09-23): AssetApproval already stores who approved/rejected an
// asset and when -- this is display only, no new state. See
// routers/scope.py:get_scope_entity_approvals for the backend half.
import { render, screen, fireEvent, waitFor, cleanup } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import type { AssetApproval, ScopeEntity } from "../types";
import { AssetDrawer } from "./AssetDrawer";

vi.mock("../api", () => ({
  api: {
    getContacts: vi.fn(),
    getScopeEntityApprovals: vi.fn(),
    patchScopeEntity: vi.fn(),
    createScopeEntity: vi.fn(),
    deleteScopeEntity: vi.fn(),
  },
}));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function makeAsset(overrides: Partial<ScopeEntity> = {}): ScopeEntity {
  return {
    id: "asset-1",
    entity_type: "device",
    natural_key: "SN-WL-LT26",
    scope_category: null,
    status: "active",
    in_boundary: true,
    source: "liongard",
    source_ref: "liongard:environment=8815 (Acme Corp):pulled_at=2026-09-22",
    attributes: {},
    ...overrides,
  };
}

function makeApproval(overrides: Partial<AssetApproval> = {}): AssetApproval {
  return {
    id: "approval-1",
    decision: "approved",
    decided_by_name: "Jane Reviewer",
    decided_at: "2026-09-22T18:30:00Z",
    rejection_reason: null,
    checklist: [],
    ...overrides,
  };
}

describe("AssetDrawer — acceptance record", () => {
  it("shows nothing when the asset has no approval record", async () => {
    vi.mocked(api.getContacts).mockResolvedValue([]);
    vi.mocked(api.getScopeEntityApprovals).mockResolvedValue([]);

    render(
      <AssetDrawer
        orgId="org1"
        asset={makeAsset()}
        canWrite={false}
        onClose={vi.fn()}
        onSaved={vi.fn()}
      />
    );

    await screen.findByText("Edit Asset");
    await waitFor(() => expect(api.getScopeEntityApprovals).toHaveBeenCalledWith("org1", "asset-1"));
    expect(screen.queryByText("Approval")).toBeNull();
  });

  it("does not fetch approvals for a brand-new asset", () => {
    vi.mocked(api.getContacts).mockResolvedValue([]);

    render(
      <AssetDrawer orgId="org1" asset={null} canWrite={true} onClose={vi.fn()} onSaved={vi.fn()} />
    );

    expect(api.getScopeEntityApprovals).not.toHaveBeenCalled();
  });

  it("shows the approver's name and timestamp for an approved asset", async () => {
    vi.mocked(api.getContacts).mockResolvedValue([]);
    vi.mocked(api.getScopeEntityApprovals).mockResolvedValue([makeApproval()]);

    render(
      <AssetDrawer
        orgId="org1"
        asset={makeAsset()}
        canWrite={false}
        onClose={vi.fn()}
        onSaved={vi.fn()}
      />
    );

    await screen.findByText("Approved");
    expect(screen.getByText(/Jane Reviewer/)).toBeTruthy();
  });

  it("shows the rejection reason for a rejected asset", async () => {
    vi.mocked(api.getContacts).mockResolvedValue([]);
    vi.mocked(api.getScopeEntityApprovals).mockResolvedValue([
      makeApproval({
        decision: "rejected",
        rejection_reason: "Personal device, not authorized for CUI.",
      }),
    ]);

    render(
      <AssetDrawer
        orgId="org1"
        asset={makeAsset()}
        canWrite={false}
        onClose={vi.fn()}
        onSaved={vi.fn()}
      />
    );

    await screen.findByText("Rejected");
    expect(screen.getByText("Personal device, not authorized for CUI.")).toBeTruthy();
  });

  it("keeps the checklist snapshot hidden until asked for", async () => {
    vi.mocked(api.getContacts).mockResolvedValue([]);
    vi.mocked(api.getScopeEntityApprovals).mockResolvedValue([
      makeApproval({
        checklist: [
          { product_key: "rocketcyber", product_name: "RocketCyber", confirmed: true },
          { product_key: "datto-rmm", product_name: "Datto RMM", confirmed: false },
        ],
      }),
    ]);

    render(
      <AssetDrawer
        orgId="org1"
        asset={makeAsset()}
        canWrite={false}
        onClose={vi.fn()}
        onSaved={vi.fn()}
      />
    );

    await screen.findByText("Approved");
    expect(screen.queryByText(/RocketCyber/)).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: /Show checklist/ }));
    expect(screen.getByText(/RocketCyber/)).toBeTruthy();
    expect(screen.getByText(/Datto RMM/)).toBeTruthy();
  });

  it("shows only the most recent decision until history is expanded, and never hides the earlier one", async () => {
    vi.mocked(api.getContacts).mockResolvedValue([]);
    vi.mocked(api.getScopeEntityApprovals).mockResolvedValue([
      makeApproval({ id: "a2", decision: "approved", decided_by_name: "Later Reviewer" }),
      makeApproval({
        id: "a1", decision: "rejected", decided_by_name: "Earlier Reviewer",
        rejection_reason: "Not yet.",
      }),
    ]);

    render(
      <AssetDrawer
        orgId="org1"
        asset={makeAsset()}
        canWrite={false}
        onClose={vi.fn()}
        onSaved={vi.fn()}
      />
    );

    await screen.findByText(/Later Reviewer/);
    expect(screen.queryByText(/Earlier Reviewer/)).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: /View full history/ }));
    expect(screen.getByText(/Earlier Reviewer/)).toBeTruthy();
    expect(screen.getByText("Not yet.")).toBeTruthy();
  });
});

describe("AssetDrawer — Hostname (2026-09-24)", () => {
  it("shows the Hostname field, read-only, alongside Display Name", async () => {
    vi.mocked(api.getContacts).mockResolvedValue([]);
    vi.mocked(api.getScopeEntityApprovals).mockResolvedValue([]);

    render(
      <AssetDrawer
        orgId="org1"
        asset={makeAsset({ attributes: { display_name: "Jarrods Desktop", hostname: "WL-DT26" } })}
        canWrite={true}
        onClose={vi.fn()}
        onSaved={vi.fn()}
      />
    );

    await screen.findByText("Hostname");
    expect(screen.getByText("WL-DT26")).toBeTruthy();
    // Read-only: no text input for it, unlike Display Name.
    expect(screen.queryByDisplayValue("WL-DT26")).toBeNull();
  });

  it("hides the Hostname field entirely when unset", async () => {
    vi.mocked(api.getContacts).mockResolvedValue([]);
    vi.mocked(api.getScopeEntityApprovals).mockResolvedValue([]);

    render(
      <AssetDrawer
        orgId="org1"
        asset={makeAsset()}
        canWrite={true}
        onClose={vi.fn()}
        onSaved={vi.fn()}
      />
    );

    await screen.findByText("Edit Asset");
    expect(screen.queryByText("Hostname")).toBeNull();
  });
});
