// @vitest-environment jsdom
//
// Coverage for the assets table's Name column (2026-09-17): shows the
// display name when a device has one, falls back to the natural key when
// it doesn't (workbook/manual assets, unchanged from before this slice).
import { render, screen, cleanup } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import type { ScopeEntity } from "../types";
import { AssetsPanel } from "./AssetsPanel";

vi.mock("../api", () => ({
  api: {
    getScope: vi.fn(),
  },
}));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function makeDevice(overrides: Partial<ScopeEntity> = {}): ScopeEntity {
  return {
    id: "d1",
    entity_type: "device",
    natural_key: "SN-DT26",
    scope_category: null,
    status: "active",
    in_boundary: true,
    source: "liongard",
    source_ref: "liongard:environment=5912 (WinsorLabs):pulled_at=2026-09-17",
    attributes: {},
    ...overrides,
  };
}

describe("AssetsPanel — Name column", () => {
  it("shows the display name with the natural key alongside it when set", async () => {
    vi.mocked(api.getScope).mockImplementation((_orgId: string, entityType?: string) =>
      Promise.resolve(
        entityType === "device"
          ? [makeDevice({ attributes: { display_name: "Jarrods Desktop" } })]
          : []
      )
    );

    render(<AssetsPanel orgId="org1" canWrite={false} />);

    await screen.findByText("Jarrods Desktop");
    expect(screen.getByText("SN-DT26")).toBeTruthy();
  });

  it("falls back to the natural key alone when no display name is set", async () => {
    vi.mocked(api.getScope).mockImplementation((_orgId: string, entityType?: string) =>
      Promise.resolve(entityType === "device" ? [makeDevice()] : [])
    );

    render(<AssetsPanel orgId="org1" canWrite={false} />);

    await screen.findByText("SN-DT26");
    expect(screen.getAllByText("SN-DT26")).toHaveLength(1);
  });
});
