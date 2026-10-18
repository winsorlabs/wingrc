// @vitest-environment jsdom
//
// L.3: a bundle carries the lists the operator chose and no others -- none
// by default -- and the choice reaches the request as repeated list_id.
// L.4: lists linked as evidence here come first, and are still not ticked.
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api, bundleQuery } from "../api";
import type { LibraryListSummary, ListEvidenceLink } from "../types";
import { BundleListsPicker } from "./BundleListsPicker";

vi.mock("../api", async () => {
  const actual = await vi.importActual<typeof import("../api")>("../api");
  return { bundleQuery: actual.bundleQuery, api: { listLibrary: vi.fn(), assessmentListLinks: vi.fn() } };
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
    const { container } = render(<BundleListsPicker orgId="org" assessmentId="a1" selected={[]} onChange={onChange} />);
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

  it("puts lists linked as evidence first, says their drift, and ticks none of them", async () => {
    vi.mocked(api.listLibrary).mockResolvedValue([
      summary({ id: "a", title: "A Own", is_template: false }),
      summary({ id: "v", title: "Visitor Log", is_template: false, version_number: 3 }),
    ]);
    const link = (o: string, over: Partial<ListEvidenceLink> = {}): ListEvidenceLink => ({
      list_id: "v", list_key: "visitor-log", title: "Visitor Log", current_version_number: 3,
      linked_version_id: "v1", linked_version_number: 1, assessment_id: "a1", assessment_name: "FY26",
      control_state_id: o, control_key: "PE.L2-3.10.3", objective_key: o,
      drift: "List version 3 is available. This evidence stays on version 1 until someone moves it forward.",
      ...over,
    });
    vi.mocked(api.assessmentListLinks).mockResolvedValue([link("a"), link("b")]);
    const { container } = render(
      <BundleListsPicker orgId="org" assessmentId="a1" selected={[]} onChange={vi.fn()} />,
    );
    const details = container.querySelector("details")!;
    details.open = true;
    fireEvent(details, new Event("toggle"));

    await screen.findByText("evidence for 2 objectives");
    const boxes = screen.getAllByRole("checkbox");
    expect(boxes[0].closest("label")!.textContent).toContain("Visitor Log");
    expect(boxes.every((b) => !(b as HTMLInputElement).checked)).toBe(true);
    expect(api.assessmentListLinks).toHaveBeenCalledWith("org", "a1");
    expect(screen.getAllByText(/This evidence stays on version 1/)).toHaveLength(1);
  });
});
