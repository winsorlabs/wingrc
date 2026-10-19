// @vitest-environment jsdom
//
// Deactivating a product asks, optionally, what replaces it (Jarrod,
// 2026-10-19). "Replaced by X" exists only when a person names X here; the
// product never infers it, and blank is a real answer.
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import type { ProductRow } from "../types";
import { ProductCard } from "./ProductCard";

vi.mock("../api", () => ({
  api: { activateProduct: vi.fn(), deactivateProduct: vi.fn(), listProductVersions: vi.fn(), moveProductVersion: vi.fn() },
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
