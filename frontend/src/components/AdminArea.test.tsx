// @vitest-environment jsdom
//
// Coverage for AdminArea's own section nav (G.9): it now has two sections
// (Integrations, Tools) built on the same SideNavKit primitives SideNav.tsx
// uses, rather than a bare hardcoded single-button nav. Switching sections
// mounts the right panel and doesn't call the other section's endpoints.
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import { AdminArea } from "./AdminArea";

vi.mock("../api", () => ({
  api: {
    listIntegrations: vi.fn(),
    listToolsLibrary: vi.fn(),
  },
}));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("AdminArea", () => {
  it("defaults to Integrations and switching to Tools mounts ToolsLibraryPanel", async () => {
    vi.mocked(api.listIntegrations).mockResolvedValue([]);
    vi.mocked(api.listToolsLibrary).mockResolvedValue([]);

    render(<AdminArea canWrite={true} />);

    await screen.findByText(/Configuring a credential here/);
    expect(api.listToolsLibrary).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Tools" }));

    await screen.findByText("Baseline Library");
    expect(api.listToolsLibrary).toHaveBeenCalled();
  });

  it("does not render a disabled Users placeholder (slice B is not started)", () => {
    vi.mocked(api.listIntegrations).mockResolvedValue([]);

    render(<AdminArea canWrite={true} />);

    expect(screen.queryByText("Users")).toBeNull();
  });
});
