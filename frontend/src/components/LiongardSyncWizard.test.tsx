// @vitest-environment jsdom
//
// Coverage for the D.2 Liongard sync wizard: no-mapping-yet shows the
// environment picker, saving a mapping transitions to the ready-to-sync
// view, syncing shows the review diff (reusing ScopeChangeDiffTable) plus
// any pull-level warnings, and apply calls the same applyWorkbookImport
// endpoint the workbook import wizard uses (routers/scope.py's own
// docstring explains why that's the correct reuse, not a bug).
import { render, screen, fireEvent, waitFor, cleanup } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import type { DryRunResult, LiongardEnvironmentMapping, LiongardEnvironmentOption } from "../types";
import { LiongardSyncWizard } from "./LiongardSyncWizard";

vi.mock("../api", () => ({
  api: {
    getLiongardEnvironmentMapping: vi.fn(),
    listLiongardEnvironments: vi.fn(),
    setLiongardEnvironmentMapping: vi.fn(),
    unmapLiongardEnvironment: vi.fn(),
    liongardSyncDryRun: vi.fn(),
    applyWorkbookImport: vi.fn(),
  },
}));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function makeMapping(overrides: Partial<LiongardEnvironmentMapping> = {}): LiongardEnvironmentMapping {
  return {
    liongard_environment_id: 8815,
    liongard_environment_name: "Acme Corp",
    updated_at: "2026-09-11T00:00:00Z",
    liongard_sourced_scope_count: 0,
    ...overrides,
  };
}

function makeDryRun(overrides: Partial<DryRunResult> = {}): DryRunResult {
  return {
    summary: { new: 1, changed: 0, missing: 0, unchanged: 0 },
    warnings: [],
    pull_status: [
      { entity_label: "devices", total_found: 1, inventory_count: 1, message: "1 devices in Inventory, 1 new or changed." },
      { entity_label: "identities", total_found: 0, inventory_count: 0, message: "Liongard returned no identities for this Environment." },
    ],
    changes: [
      {
        change_type: "new",
        entity_type: "device",
        natural_key: "SN-123",
        field_diffs: {},
        incoming: {
          scope_category: null,
          status: "active",
          in_boundary: true,
          source: "liongard",
          source_ref: "liongard:environment=8815 (Acme Corp):pulled_at=2026-09-11",
          attributes: { make_oem: "Apple" },
        },
        warnings: [],
      },
    ],
    ...overrides,
  };
}

describe("LiongardSyncWizard — no mapping yet", () => {
  it("shows the environment picker and saves a chosen mapping", async () => {
    vi.mocked(api.getLiongardEnvironmentMapping).mockResolvedValue(null);
    const options: LiongardEnvironmentOption[] = [
      { id: 8815, name: "Acme Corp" },
      { id: 42, name: "Other Client" },
    ];
    vi.mocked(api.listLiongardEnvironments).mockResolvedValue(options);
    vi.mocked(api.setLiongardEnvironmentMapping).mockResolvedValue(makeMapping());

    render(<LiongardSyncWizard orgId="org1" onClose={vi.fn()} onApplied={vi.fn()} />);

    await screen.findByText(/isn't mapped to a Liongard Environment/);
    fireEvent.click(screen.getByRole("button", { name: /Load Liongard Environments/ }));

    await screen.findByText("Other Client");
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "8815" } });
    fireEvent.click(screen.getByRole("button", { name: /Save Environment/ }));

    await waitFor(() =>
      expect(api.setLiongardEnvironmentMapping).toHaveBeenCalledWith("org1", 8815)
    );
    await screen.findByText(/Mapped to Liongard Environment/);
    expect(screen.getByText("Acme Corp")).toBeTruthy();
  });

  it("surfaces a real error when Liongard isn't configured at all", async () => {
    vi.mocked(api.getLiongardEnvironmentMapping).mockResolvedValue(null);
    vi.mocked(api.listLiongardEnvironments).mockRejectedValue(
      new Error("Liongard isn't configured yet -- add a credential on the Integrations page first.")
    );

    render(<LiongardSyncWizard orgId="org1" onClose={vi.fn()} onApplied={vi.fn()} />);
    await screen.findByText(/isn't mapped to a Liongard Environment/);
    fireEvent.click(screen.getByRole("button", { name: /Load Liongard Environments/ }));

    await screen.findByText(/isn't configured yet/);
  });
});

describe("LiongardSyncWizard — mapped, sync + apply", () => {
  it("syncs, shows the diff and pull-level warnings, then applies via the shared endpoint", async () => {
    vi.mocked(api.getLiongardEnvironmentMapping).mockResolvedValue(makeMapping());
    const dryRun = makeDryRun({ warnings: ["Liongard device record has neither SerialNumber nor Hostname -- skipped."] });
    vi.mocked(api.liongardSyncDryRun).mockResolvedValue(dryRun);
    vi.mocked(api.applyWorkbookImport).mockResolvedValue({ applied: 1 });

    render(<LiongardSyncWizard orgId="org1" onClose={vi.fn()} onApplied={vi.fn()} />);

    await screen.findByText(/Mapped to Liongard Environment/);
    fireEvent.click(screen.getByRole("button", { name: /Sync Now/ }));

    await screen.findByText("SN-123");
    expect(screen.getByText(/neither SerialNumber nor Hostname/)).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: /Apply 1 Change/ }));

    await waitFor(() =>
      expect(api.applyWorkbookImport).toHaveBeenCalledWith("org1", dryRun.changes)
    );
    await screen.findByText(/Applied 1 change/);
  });

  it("excluding the only row disables Apply", async () => {
    vi.mocked(api.getLiongardEnvironmentMapping).mockResolvedValue(makeMapping());
    vi.mocked(api.liongardSyncDryRun).mockResolvedValue(makeDryRun());

    render(<LiongardSyncWizard orgId="org1" onClose={vi.fn()} onApplied={vi.fn()} />);
    await screen.findByText(/Mapped to Liongard Environment/);
    fireEvent.click(screen.getByRole("button", { name: /Sync Now/ }));

    await screen.findByText("SN-123");
    fireEvent.click(screen.getByRole("checkbox"));

    expect(screen.getByRole("button", { name: /Apply 0 Changes/ })).toHaveProperty("disabled", true);
  });

  it("a no-op sync (matches Liongard already) shows nothing to apply", async () => {
    vi.mocked(api.getLiongardEnvironmentMapping).mockResolvedValue(makeMapping());
    vi.mocked(api.liongardSyncDryRun).mockResolvedValue(
      makeDryRun({
        changes: [],
        summary: { new: 0, changed: 0, missing: 0, unchanged: 4 },
        pull_status: [
          { entity_label: "devices", total_found: 4, inventory_count: 4, message: "4 devices compared against scope -- no changes." },
          { entity_label: "identities", total_found: 0, inventory_count: 0, message: "Liongard returned no identities for this Environment." },
        ],
      })
    );

    render(<LiongardSyncWizard orgId="org1" onClose={vi.fn()} onApplied={vi.fn()} />);
    await screen.findByText(/Mapped to Liongard Environment/);
    fireEvent.click(screen.getByRole("button", { name: /Sync Now/ }));

    await screen.findByText(/no changes/);
  });

  it("a pull that found records but none are Inventory-state yet names the remedy", async () => {
    vi.mocked(api.getLiongardEnvironmentMapping).mockResolvedValue(makeMapping());
    vi.mocked(api.liongardSyncDryRun).mockResolvedValue(
      makeDryRun({
        changes: [],
        summary: { new: 0, changed: 0, missing: 0, unchanged: 0 },
        pull_status: [
          {
            entity_label: "devices",
            total_found: 197,
            inventory_count: 0,
            message:
              "Liongard returned 197 devices, but none are in Inventory state yet -- promote them from Discovery to Inventory in Liongard before WinGRC will include them.",
          },
          { entity_label: "identities", total_found: 0, inventory_count: 0, message: "Liongard returned no identities for this Environment." },
        ],
      })
    );

    render(<LiongardSyncWizard orgId="org1" onClose={vi.fn()} onApplied={vi.fn()} />);
    await screen.findByText(/Mapped to Liongard Environment/);
    fireEvent.click(screen.getByRole("button", { name: /Sync Now/ }));

    await screen.findByText(/promote them from Discovery to Inventory/);
  });
});

describe("LiongardSyncWizard — unmap", () => {
  it("confirms before unmapping and surfaces the orphaned scope count on success", async () => {
    vi.mocked(api.getLiongardEnvironmentMapping).mockResolvedValue(
      makeMapping({ liongard_sourced_scope_count: 3 })
    );
    vi.mocked(api.unmapLiongardEnvironment).mockResolvedValue({
      liongard_environment_id: 8815,
      liongard_environment_name: "Acme Corp",
      orphaned_scope_entity_count: 3,
    });

    render(<LiongardSyncWizard orgId="org1" onClose={vi.fn()} onApplied={vi.fn()} />);
    await screen.findByText(/Mapped to Liongard Environment/);

    fireEvent.click(screen.getByRole("button", { name: "Unmap" }));
    await screen.findByText(/3 scope entities already imported from it will remain in scope/);

    fireEvent.click(screen.getByRole("button", { name: /Yes, unmap/ }));

    await waitFor(() => expect(api.unmapLiongardEnvironment).toHaveBeenCalledWith("org1"));
    await screen.findByText(/isn't mapped to a Liongard Environment yet/);
    expect(
      screen.getByText(/3 scope entities previously imported from it remain in scope, now managed manually/)
    ).toBeTruthy();
  });

  it("cancelling the confirm leaves the mapping untouched", async () => {
    vi.mocked(api.getLiongardEnvironmentMapping).mockResolvedValue(makeMapping());

    render(<LiongardSyncWizard orgId="org1" onClose={vi.fn()} onApplied={vi.fn()} />);
    await screen.findByText(/Mapped to Liongard Environment/);

    fireEvent.click(screen.getByRole("button", { name: "Unmap" }));
    await screen.findByText(/Unmap this Environment/);
    fireEvent.click(screen.getByRole("button", { name: "Keep mapped" }));

    expect(screen.queryByText(/Unmap this Environment/)).toBeNull();
    expect(api.unmapLiongardEnvironment).not.toHaveBeenCalled();
    expect(screen.getByText(/Mapped to Liongard Environment/)).toBeTruthy();
  });

  it("a zero-count unmap says plainly that nothing was left behind", async () => {
    vi.mocked(api.getLiongardEnvironmentMapping).mockResolvedValue(
      makeMapping({ liongard_sourced_scope_count: 0 })
    );

    render(<LiongardSyncWizard orgId="org1" onClose={vi.fn()} onApplied={vi.fn()} />);
    await screen.findByText(/Mapped to Liongard Environment/);
    fireEvent.click(screen.getByRole("button", { name: "Unmap" }));

    await screen.findByText(/No scope entities have been imported from it yet/);
  });

  it("surfaces a real unmap error rather than failing silently", async () => {
    vi.mocked(api.getLiongardEnvironmentMapping).mockResolvedValue(makeMapping());
    vi.mocked(api.unmapLiongardEnvironment).mockRejectedValue(new Error("This org has no Liongard Environment mapping to remove."));

    render(<LiongardSyncWizard orgId="org1" onClose={vi.fn()} onApplied={vi.fn()} />);
    await screen.findByText(/Mapped to Liongard Environment/);
    fireEvent.click(screen.getByRole("button", { name: "Unmap" }));
    fireEvent.click(screen.getByRole("button", { name: /Yes, unmap/ }));

    await screen.findByText("This org has no Liongard Environment mapping to remove.");
    // Still mapped -- an error must not silently drop the mapping from state.
    expect(screen.getByText(/Mapped to Liongard Environment/)).toBeTruthy();
  });
});
