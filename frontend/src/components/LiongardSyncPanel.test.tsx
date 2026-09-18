// @vitest-environment jsdom
//
// Coverage for the asset approval panel (D.3 second half): the empty
// state, sync-now, drilling into a result's pending changes, and the
// approve/reject flow -- rejecting requires a non-empty reason (mirrors
// the backend's own validation).
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import type { LiongardSyncResultDetail, LiongardSyncResultRow } from "../types";
import { LiongardSyncPanel } from "./LiongardSyncPanel";

vi.mock("../api", () => ({
  api: {
    listLiongardSyncResults: vi.fn(),
    getLiongardSyncResult: vi.fn(),
    syncLiongardNow: vi.fn(),
    approveLiongardChange: vi.fn(),
    rejectLiongardChange: vi.fn(),
  },
}));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function makeResult(overrides: Partial<LiongardSyncResultRow> = {}): LiongardSyncResultRow {
  return {
    id: "s1",
    org_id: "org1",
    liongard_environment_id: 8815,
    liongard_environment_name: "Acme Corp",
    pulled_at: "2026-09-18T00:00:00Z",
    status: "pending_review",
    summary: { new: 1, changed: 0, missing: 0, unchanged: 0 },
    warnings: [],
    created_at: "2026-09-18T00:00:00Z",
    ...overrides,
  };
}

function makeDetail(overrides: Partial<LiongardSyncResultDetail> = {}): LiongardSyncResultDetail {
  return {
    ...makeResult(),
    changes: [
      {
        id: "c1",
        change_type: "new",
        entity_type: "device",
        natural_key: "SN-ABC123",
        field_diffs: {},
        incoming: {
          entity_type: "device",
          natural_key: "SN-ABC123",
          attributes: { display_name: "Jarrods Laptop" },
          scope_category: null,
          status: "pending_approval",
          in_boundary: true,
          source: "liongard",
          source_ref: "liongard:environment=8815",
        },
        warnings: [],
        resolution: "pending",
        resolved_at: null,
        resolved_by: null,
      },
    ],
    checklist_products: [{ product_key: "fenixpyre", product_name: "FenixPyre" }],
    ...overrides,
  };
}

describe("LiongardSyncPanel — list", () => {
  it("shows the empty state when no syncs exist", async () => {
    vi.mocked(api.listLiongardSyncResults).mockResolvedValue([]);
    render(<LiongardSyncPanel orgId="org1" />);
    await screen.findByText(/No syncs yet/);
  });

  it("clicking Sync now triggers a sync and opens the new result", async () => {
    vi.mocked(api.listLiongardSyncResults).mockResolvedValue([]);
    vi.mocked(api.syncLiongardNow).mockResolvedValue(makeResult());
    vi.mocked(api.getLiongardSyncResult).mockResolvedValue(makeDetail());

    render(<LiongardSyncPanel orgId="org1" />);
    await screen.findByText(/No syncs yet/);
    fireEvent.click(screen.getByRole("button", { name: "Sync now" }));

    await waitFor(() => expect(api.syncLiongardNow).toHaveBeenCalledWith("org1"));
    await screen.findByText(/Jarrods Laptop/);
  });

  it("lists prior syncs with their status and counts", async () => {
    vi.mocked(api.listLiongardSyncResults).mockResolvedValue([makeResult({ status: "reviewed" })]);
    render(<LiongardSyncPanel orgId="org1" />);
    await screen.findByText("reviewed");
  });
});

describe("LiongardSyncPanel — approve / reject", () => {
  it("approving sends checklist confirmations and refreshes the detail", async () => {
    vi.mocked(api.listLiongardSyncResults).mockResolvedValue([makeResult()]);
    vi.mocked(api.getLiongardSyncResult).mockResolvedValue(makeDetail());
    vi.mocked(api.approveLiongardChange).mockResolvedValue({
      id: "a1", scope_entity_id: "e1", decision: "approved",
      decided_by_name: "Jarrod", decided_at: "2026-09-18T00:00:00Z", rejection_reason: null,
    });

    render(<LiongardSyncPanel orgId="org1" />);
    fireEvent.click(await screen.findByText("pending_review"));
    await screen.findByText(/Jarrods Laptop/);

    fireEvent.click(screen.getByText("FenixPyre"));
    fireEvent.click(screen.getByRole("button", { name: "Approve" }));

    await waitFor(() =>
      expect(api.approveLiongardChange).toHaveBeenCalledWith("org1", "s1", "c1", { fenixpyre: true })
    );
  });

  it("reject requires a non-empty reason before it can be submitted", async () => {
    vi.mocked(api.listLiongardSyncResults).mockResolvedValue([makeResult()]);
    vi.mocked(api.getLiongardSyncResult).mockResolvedValue(makeDetail());

    render(<LiongardSyncPanel orgId="org1" />);
    fireEvent.click(await screen.findByText("pending_review"));
    await screen.findByText(/Jarrods Laptop/);

    fireEvent.click(screen.getByRole("button", { name: "Reject" }));
    const confirmButton = screen.getByRole("button", { name: "Confirm reject" });
    expect(confirmButton).toHaveProperty("disabled", true);

    fireEvent.change(screen.getByPlaceholderText(/Reason for rejecting/), {
      target: { value: "Personal device." },
    });
    expect(confirmButton).toHaveProperty("disabled", false);
  });
});
