// @vitest-environment jsdom
//
// L.3: a bundle carries the lists the operator chose and no others -- none
// by default -- and the choice reaches the request as repeated list_id.
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api, bundleQuery } from "../api";
import type { LibraryListSummary } from "../types";
import { BundleListsPicker } from "./BundleListsPicker";

vi.mock("../api", async () => {
  const actual = await vi.importActual<typeof import("../api")>("../api");
  return { bundleQuery: actual.bundleQuery, api: { listLibrary: vi.fn() } };
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function summary(over: Partial<LibraryListSummary>): LibraryListSummary {
  return {
    id: "x", list_key: "x", title: "x", is_template: true, source_ref: null, projection_view_id: null,
    control_keys: [], version_number: 1, column_count: 2, row_count: 1, updated_at: "2026-10-14T00:00:00Z",
    saved_by: null, cause: "first_import", merged_from: [], has_changelog: false, example_rows: 0,
    adopted_from: null, adopted_copies: 0, ...over,
  };
}

describe("bundleQuery", () => {
  it("is empty for no lists, and repeats list_id otherwise", () => {
    expect(bundleQuery([])).toBe("");
    expect(bundleQuery(["a", "b"])).toBe("?list_id=a&list_id=b");
  });
});

describe("BundleListsPicker", () => {
  it("starts with nothing chosen and lists the org's own lists before templates", async () => {
    vi.mocked(api.listLibrary).mockResolvedValue([
      summary({ id: "t", title: "A Template", is_template: true }),
      summary({ id: "k", title: "Z Kept", is_template: false, version_number: 3 }),
    ]);
    const onChange = vi.fn();
    const { container } = render(<BundleListsPicker orgId="org" selected={[]} onChange={onChange} />);
    expect(screen.getByText("Lists in bundle (0)")).toBeTruthy();
    const details = container.querySelector("details")!;
    details.open = true;
    fireEvent(details, new Event("toggle"));

    const boxes = await screen.findAllByRole("checkbox");
    expect(boxes.map((b) => b.closest("label")!.textContent)).toEqual([
      expect.stringContaining("Z Kept"),
      expect.stringContaining("A Template"),
    ]);
    expect(boxes.every((b) => !(b as HTMLInputElement).checked)).toBe(true);
    expect(screen.getByText("template")).toBeTruthy();
    expect(screen.getByText(/rows are examples, not/)).toBeTruthy();
    fireEvent.click(boxes[0]);
    expect(onChange).toHaveBeenCalledWith(["k"]);
  });
});
