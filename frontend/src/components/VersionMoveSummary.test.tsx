// @vitest-environment jsdom
//
// A baseline version move does not ask, so it tells afterwards (Jarrod,
// 2026-11-05): which objectives it set to needs_review and why, said once,
// above the cards the reload remounts.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import type { MoveVersionResult, MovedToReview, ProductRow } from "../types";
import { ProductsPanel } from "./ProductsPanel";
import { VersionMoveSummary } from "./VersionMoveSummary";

vi.mock("../api", () => ({
  ApiError: class ApiError extends Error {},
  api: { getProducts: vi.fn(), listProductVersions: vi.fn(), moveProductVersion: vi.fn() },
}));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function row(over: Partial<MovedToReview>): MovedToReview {
  return {
    control_state_id: "cs", control_id: "AC.L2-3.1.1", objective_key: "a", objective_text: "t",
    status_before: "pending_evidence", why: "mapping_changed", still_covered: false, ...over,
  };
}

function result(rows: MovedToReview[], tasks = 0): MoveVersionResult {
  return {
    controls_gained: 0, controls_lost: 0, controls_changed: 0, tasks_created: tasks,
    from_version: 1, to_version: 2, set_to_needs_review: rows,
  };
}

describe("VersionMoveSummary", () => {
  it("names each objective, what it was, and the reason the server gave", () => {
    render(
      <VersionMoveSummary
        productName="Datto RMM"
        result={result([
          row({ control_state_id: "1" }),
          row({ control_state_id: "2", control_id: "AC.L2-3.1.2", status_before: "met", why: "judged_newly_covered" }),
          row({ control_state_id: "3", control_id: "AU.L2-3.3.1", why: "no_longer_covered", still_covered: true }),
          row({ control_state_id: "4", control_id: "AU.L2-3.3.2", why: "no_longer_covered" }),
        ], 2)}
        onDismiss={vi.fn()}
      />,
    );
    const box = screen.getByRole("status");
    expect(box.textContent).toContain("Moved Datto RMM from v1 to v2. 4 objectives were set to Needs Review:");
    const items = screen.getAllByRole("listitem").map((li) => li.textContent);
    expect(items).toEqual([
      "AC.L2-3.1.1[a] was Pending Evidence. v2 changes how Datto RMM covers this control.",
      "AC.L2-3.1.2[a] was Met. v2 of Datto RMM now covers it, and a person had set it. A move does not ask, so it needs review.",
      "AU.L2-3.3.1[a] was Pending Evidence. v2 of Datto RMM no longer covers it. Another tool still does.",
      "AU.L2-3.3.2[a] was Pending Evidence. v2 of Datto RMM no longer covers it.",
    ]);
    expect(box.textContent).toContain("2 new evidence tasks were created.");
  });

  it("says one, and says none, rather than a count", () => {
    const { rerender } = render(<VersionMoveSummary productName="X" result={result([row({})])} onDismiss={vi.fn()} />);
    expect(screen.getByRole("status").textContent).toContain("One objective was set to Needs Review:");
    rerender(<VersionMoveSummary productName="X" result={result([])} onDismiss={vi.fn()} />);
    expect(screen.getByRole("status").textContent).toContain("No objective was set to Needs Review.");
    expect(screen.queryByRole("list")).toBeNull();
  });
});

describe("ProductsPanel after a version move", () => {
  const behind: ProductRow = {
    id: "p", key: "datto", name: "Datto RMM", provider: "Kaseya", category: "RMM", role: "RMM",
    is_active: true, activated_at: "2026-09-01T00:00:00Z", provider_satisfies_count: 1, shared_count: 0,
    customer_owns_count: 0, pinned_version_number: 1, current_version_number: 2, is_on_latest_version: false,
  };

  it("keeps the summary through the reload, until it is dismissed", async () => {
    vi.mocked(api.getProducts)
      .mockResolvedValueOnce([behind])
      .mockResolvedValueOnce([{ ...behind, pinned_version_number: 2, is_on_latest_version: true }]);
    vi.mocked(api.listProductVersions).mockResolvedValue([
      { id: "v2", version_number: 2, is_current: true } as never,
    ]);
    vi.mocked(api.moveProductVersion).mockResolvedValue(result([row({})]));
    // App's onActivated leaves the Tools panel, which would unmount the
    // summary before anyone read it (seen on the bench, 2026-11-05).
    const onActivated = vi.fn();
    render(
      <ProductsPanel orgId="o" assessmentId="a" canWrite onClose={vi.fn()} onActivated={onActivated} onDeactivated={vi.fn()} />,
    );
    fireEvent.click(await screen.findByRole("button", { name: "Move to v2" }));
    await waitFor(() => expect(api.getProducts).toHaveBeenCalledTimes(2));
    await screen.findByText(/· v2/);
    expect(screen.getByRole("status").textContent).toContain("One objective was set to Needs Review:");
    expect(onActivated).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(screen.queryByRole("status")).toBeNull();
  });
});
