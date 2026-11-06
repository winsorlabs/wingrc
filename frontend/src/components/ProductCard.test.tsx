// @vitest-environment jsdom
//
// Deactivating a product asks, optionally, what replaces it (Jarrod,
// 2026-10-19). "Replaced by X" exists only when a person names X here; the
// product never infers it, and blank is a real answer.
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, api } from "../api";
import type { ActivationPreview, JudgedObjective, ProductRow } from "../types";
import { ProductCard } from "./ProductCard";

vi.mock("../api", () => ({
  ApiError: class ApiError extends Error {
    status: number;
    constructor(message: string, status: number) {
      super(message);
      this.status = status;
    }
  },
  api: {
    activateProduct: vi.fn(), previewActivation: vi.fn(), deactivateProduct: vi.fn(),
    listProductVersions: vi.fn(), moveProductVersion: vi.fn(),
  },
}));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function product(over: Partial<ProductRow>): ProductRow {
  return {
    id: "p", key: "heimdal", name: "Heimdal", provider: "Heimdal", category: "EDR", role: "Endpoint",
    is_active: true, activated_at: "2026-09-01T00:00:00Z", provider_satisfies_count: 3, shared_count: 0,
    customer_owns_count: 0, pinned_version_number: 1, current_version_number: 1, is_on_latest_version: true,
    ...over,
  };
}

function mount() {
  const onDeactivated = vi.fn();
  vi.mocked(api.deactivateProduct).mockResolvedValue({ controls_flagged: 1, tasks_archived: 0, evidence_links_archived: 1 });
  render(
    <ProductCard
      product={product({})}
      others={[product({ id: "q", key: "defender", name: "Defender" })]}
      orgId="o" assessmentId="a" canWrite onActivated={vi.fn()} onDeactivated={onDeactivated}
    />,
  );
  return onDeactivated;
}

describe("ProductCard deactivation", () => {
  it("asks before deactivating, and sends no replacement when none is named", async () => {
    const onDeactivated = mount();
    fireEvent.click(screen.getByRole("button", { name: "Deactivate" }));
    expect(api.deactivateProduct).not.toHaveBeenCalled();
    const form = screen.getByRole("group", { name: "Deactivate Heimdal" });
    expect((within(form).getByRole("combobox") as HTMLSelectElement).value).toBe("");
    fireEvent.click(within(form).getByRole("button", { name: "Deactivate" }));
    await vi.waitFor(() => expect(onDeactivated).toHaveBeenCalled());
    expect(api.deactivateProduct).toHaveBeenCalledWith("o", "a", "p", null);
  });

  it("sends the replacement the person names", async () => {
    const onDeactivated = mount();
    fireEvent.click(screen.getByRole("button", { name: "Deactivate" }));
    const form = screen.getByRole("group", { name: "Deactivate Heimdal" });
    expect(within(form).queryByRole("option", { name: "Heimdal" })).toBeNull();
    fireEvent.change(within(form).getByRole("combobox"), { target: { value: "q" } });
    fireEvent.click(within(form).getByRole("button", { name: "Deactivate" }));
    await vi.waitFor(() => expect(onDeactivated).toHaveBeenCalled());
    expect(api.deactivateProduct).toHaveBeenCalledWith("o", "a", "p", "q");
  });
});

// Activation asks before it changes a judgement (Jarrod, 2026-11-03). The
// preview is the server's: which objectives a person judged, the default
// for each, and the score for the choices sent.
function judged(over: Partial<JudgedObjective>): JudgedObjective {
  return {
    control_state_id: "cs1", control_id: "AC.L2-3.1.2", objective_key: "a",
    objective_text: "Transactions are limited.", status: "met", default: "needs_review",
    choice: "needs_review", becomes: "needs_review", practice_deducts_now: false, ...over,
  };
}

function preview(objectives: JudgedObjective[], now = 104, after = 101, starting: string[] = []): ActivationPreview {
  return { sprs_now: now, sprs_after: after, objectives, practices_starting: starting };
}

function mountInactive() {
  const onActivated = vi.fn();
  vi.mocked(api.activateProduct).mockResolvedValue({ objectives_updated: 3, tasks_created: 1, judged: [] });
  render(
    <ProductCard
      product={product({ is_active: false, activated_at: null })}
      others={[]}
      orgId="o" assessmentId="a" canWrite onActivated={onActivated} onDeactivated={vi.fn()}
    />,
  );
  return onActivated;
}

describe("ProductCard activation", () => {
  it("activates at once when nothing a person judged would change", async () => {
    vi.mocked(api.previewActivation).mockResolvedValue(preview([], 101, 101));
    const onActivated = mountInactive();
    fireEvent.click(screen.getByRole("button", { name: "Activate" }));
    await waitFor(() => expect(onActivated).toHaveBeenCalled());
    expect(api.activateProduct).toHaveBeenCalledWith("o", "a", "p", []);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("asks on one screen, with each default, and Cancel activates nothing", async () => {
    vi.mocked(api.previewActivation).mockResolvedValue(
      preview([
        judged({}),
        judged({ control_state_id: "cs2", objective_key: "b", status: "not_applicable", default: "keep",
                 choice: "keep", becomes: "not_applicable" }),
      ], 104, 101, ["AC.L2-3.1.2"]),
    );
    const onActivated = mountInactive();
    fireEvent.click(screen.getByRole("button", { name: "Activate" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByRole("heading").textContent).toBe(
      "Activating Heimdal would change 2 objectives you judged",
    );
    expect((within(dialog).getByLabelText("What AC.L2-3.1.2[a] becomes") as HTMLSelectElement).value)
      .toBe("needs_review");
    expect((within(dialog).getByLabelText("What AC.L2-3.1.2[b] becomes") as HTMLSelectElement).value)
      .toBe("keep");
    expect(within(dialog).getByText("SPRS goes from 104 to 101 (down 3): AC.L2-3.1.2 would score as not met.")).toBeTruthy();
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(api.activateProduct).not.toHaveBeenCalled();
    expect(onActivated).not.toHaveBeenCalled();
  });

  it("Escape is a cancel", async () => {
    vi.mocked(api.previewActivation).mockResolvedValue(preview([judged({})]));
    mountInactive();
    fireEvent.click(screen.getByRole("button", { name: "Activate" }));
    await screen.findByRole("dialog");
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(api.activateProduct).not.toHaveBeenCalled();
  });

  it("names a single objective in the singular", async () => {
    vi.mocked(api.previewActivation).mockResolvedValue(preview([judged({})]));
    mountInactive();
    fireEvent.click(screen.getByRole("button", { name: "Activate" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByRole("heading").textContent).toBe(
      "Activating Heimdal would change an objective you judged",
    );
  });

  // The two screenshots of 2026-11-03 read side by side: one downgrade cost
  // 3 points, three cost none. The reason is per practice, and the server's.
  it("says why the score stays: the practices already score as not met", async () => {
    vi.mocked(api.previewActivation).mockResolvedValue(
      preview([
        judged({ control_state_id: "1", control_id: "AU.L2-3.3.1", practice_deducts_now: true }),
        judged({ control_state_id: "2", control_id: "AU.L2-3.3.1", objective_key: "b", practice_deducts_now: true }),
        judged({ control_state_id: "3", control_id: "AU.L2-3.3.2", status: "partial", practice_deducts_now: true }),
        judged({ control_state_id: "4", control_id: "IR.L2-3.6.1", status: "inherited", default: "keep",
                 choice: "keep", becomes: "inherited", practice_deducts_now: true }),
      ], -201, -201),
    );
    mountInactive();
    fireEvent.click(screen.getByRole("button", { name: "Activate" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(
      "SPRS stays at -201: AU.L2-3.3.1 and AU.L2-3.3.2 already score as not met.",
    )).toBeTruthy();
  });

  it("names what costs points and what already did, when both happen", async () => {
    vi.mocked(api.previewActivation).mockResolvedValue(
      preview([
        judged({ control_state_id: "1", control_id: "AU.L2-3.3.1", practice_deducts_now: true }),
        judged({ control_state_id: "2", control_id: "MA.L2-3.7.1" }),
      ], -201, -204, ["MA.L2-3.7.1"]),
    );
    mountInactive();
    fireEvent.click(screen.getByRole("button", { name: "Activate" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(
      "SPRS goes from -201 to -204 (down 3): MA.L2-3.7.1 would score as not met. AU.L2-3.3.1 already does.",
    )).toBeTruthy();
  });

  it("asks the server for the score of a new choice, and waits for it before activating", async () => {
    vi.mocked(api.previewActivation).mockResolvedValueOnce(preview([judged({})]));
    let answer: (p: ActivationPreview) => void = () => {};
    vi.mocked(api.previewActivation).mockImplementationOnce(
      () => new Promise((resolve) => { answer = resolve; }),
    );
    const onActivated = mountInactive();
    fireEvent.click(screen.getByRole("button", { name: "Activate" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText("What AC.L2-3.1.2[a] becomes"), { target: { value: "keep" } });
    expect(api.previewActivation).toHaveBeenLastCalledWith("o", "a", "p", [{ control_state_id: "cs1", choice: "keep" }]);
    expect(within(dialog).getByText("Working out the score for these choices…")).toBeTruthy();
    expect(within(dialog).getByRole("button", { name: "Activate Heimdal" })).toHaveProperty("disabled", true);
    answer(preview([judged({ choice: "keep", becomes: "met" })], 104, 104));
    await within(dialog).findByText("SPRS stays at 104.");
    fireEvent.click(within(dialog).getByRole("button", { name: "Activate Heimdal" }));
    await waitFor(() => expect(onActivated).toHaveBeenCalled());
    expect(api.activateProduct).toHaveBeenCalledWith("o", "a", "p", [
      { control_state_id: "cs1", shown_status: "met", choice: "keep" },
    ]);
  });

  it("when what was shown has changed, says so and asks again; nothing activates", async () => {
    vi.mocked(api.previewActivation)
      .mockResolvedValueOnce(preview([judged({})]))
      .mockResolvedValueOnce(preview([judged({ status: "inherited", default: "keep", choice: "keep",
                                               becomes: "inherited" })], 104, 104));
    vi.mocked(api.activateProduct).mockRejectedValueOnce(
      new ApiError("An objective you were asked about has changed. Nothing was activated.", 409),
    );
    const onActivated = vi.fn();
    render(
      <ProductCard
        product={product({ is_active: false, activated_at: null })}
        others={[]}
        orgId="o" assessmentId="a" canWrite onActivated={onActivated} onDeactivated={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Activate" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "Activate Heimdal" }));
    await within(dialog).findByText(/Nothing was activated/);
    await waitFor(() =>
      expect((within(dialog).getByLabelText("What AC.L2-3.1.2[a] becomes") as HTMLSelectElement).value).toBe("keep"),
    );
    expect(onActivated).not.toHaveBeenCalled();
  });
});
