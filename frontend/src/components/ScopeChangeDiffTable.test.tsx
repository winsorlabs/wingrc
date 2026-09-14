// @vitest-environment jsdom
//
// Coverage for the Name column's display-name-vs-natural-key rendering
// (2026-09-17): the review diff must show a device's display name (when
// its incoming attributes carry one) with the natural key visible
// alongside it, not the natural key standing in as the name -- the exact
// bug this slice exists to fix. See lib/assetDisplay.ts for the shared
// fallback logic this table (and AssetsPanel) both use.
import { render, screen, cleanup } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ScopeChange } from "../types";
import { ScopeChangeDiffTable } from "./ScopeChangeDiffTable";

afterEach(() => cleanup());

function makeChange(overrides: Partial<ScopeChange> = {}): ScopeChange {
  return {
    change_type: "new",
    entity_type: "device",
    natural_key: "SN-DT26",
    field_diffs: {},
    incoming: {
      scope_category: null,
      status: "active",
      in_boundary: true,
      source: "liongard",
      source_ref: "liongard:environment=5912 (WinsorLabs):pulled_at=2026-09-17",
      attributes: {},
    },
    warnings: [],
    ...overrides,
  };
}

describe("ScopeChangeDiffTable — display name", () => {
  it("shows the display name as the primary text with the natural key alongside it", () => {
    const change = makeChange({
      incoming: {
        ...makeChange().incoming!,
        attributes: { display_name: "Jarrods Desktop" },
      },
    });
    render(<ScopeChangeDiffTable changes={[change]} excluded={new Set()} onToggle={vi.fn()} />);

    expect(screen.getByText("Jarrods Desktop")).toBeTruthy();
    expect(screen.getByText("SN-DT26")).toBeTruthy();
  });

  it("shows only the natural key once when no display name is set (workbook/manual assets)", () => {
    const change = makeChange({ incoming: { ...makeChange().incoming!, attributes: {} } });
    render(<ScopeChangeDiffTable changes={[change]} excluded={new Set()} onToggle={vi.fn()} />);

    // Exactly one occurrence -- not duplicated into both the name and the
    // natural-key sub-line, which would be redundant noise.
    expect(screen.getAllByText("SN-DT26")).toHaveLength(1);
  });

  it("a missing row (no incoming attributes at all) still shows its natural key", () => {
    const change = makeChange({ change_type: "missing", incoming: null });
    render(<ScopeChangeDiffTable changes={[change]} excluded={new Set()} onToggle={vi.fn()} />);

    expect(screen.getByText("SN-DT26")).toBeTruthy();
  });
});
