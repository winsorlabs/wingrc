// @vitest-environment jsdom
//
// Coverage for AdminArea's own section nav: sections (Integrations, Tools,
// Users, Scheduled Jobs) built on the same SideNavKit primitives SideNav.tsx
// uses. Switching sections mounts the right panel and doesn't call the
// other sections' endpoints. Users and Scheduled Jobs are both gated to
// msp_admin only, unlike Integrations/Tools which also admit
// consultant_admin -- those are the two sections here that need the
// caller's role to decide whether to render their nav entry at all.
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import { AdminArea } from "./AdminArea";

vi.mock("../api", () => ({
  api: {
    listIntegrations: vi.fn(),
    listToolsLibrary: vi.fn(),
    listUserDirectory: vi.fn(),
    listScheduledJobs: vi.fn(),
    getOrgs: vi.fn(),
    getMspOrg: vi.fn(),
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

    render(<AdminArea canWrite={true} currentUserRole="msp_admin" />);

    await screen.findByText(/Configuring a credential here/);
    expect(api.listToolsLibrary).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Tools" }));

    await screen.findByText("Baseline Library");
    expect(api.listToolsLibrary).toHaveBeenCalled();
  });

  it("an msp_admin sees Users and switching to it mounts UserDirectoryPanel", async () => {
    vi.mocked(api.listIntegrations).mockResolvedValue([]);
    vi.mocked(api.listUserDirectory).mockResolvedValue([]);
    vi.mocked(api.getOrgs).mockResolvedValue([]);
    vi.mocked(api.getMspOrg).mockResolvedValue(null);

    render(<AdminArea canWrite={true} currentUserRole="msp_admin" />);
    await screen.findByText(/Configuring a credential here/);

    fireEvent.click(screen.getByRole("button", { name: "Users" }));

    await screen.findByText("User Directory");
    expect(api.listUserDirectory).toHaveBeenCalled();
  });

  it("a consultant_admin (sees Integrations/Tools) does not see a Users entry", async () => {
    vi.mocked(api.listIntegrations).mockResolvedValue([]);

    render(<AdminArea canWrite={true} currentUserRole="consultant_admin" />);
    await screen.findByText(/Configuring a credential here/);

    expect(screen.queryByRole("button", { name: "Users" })).toBeNull();
    expect(api.listUserDirectory).not.toHaveBeenCalled();
  });

  it("an msp_admin sees Scheduled Jobs and switching to it mounts ScheduledJobsPanel", async () => {
    vi.mocked(api.listIntegrations).mockResolvedValue([]);
    vi.mocked(api.listScheduledJobs).mockResolvedValue([]);

    render(<AdminArea canWrite={true} currentUserRole="msp_admin" />);
    await screen.findByText(/Configuring a credential here/);

    fireEvent.click(screen.getByRole("button", { name: "Scheduled Jobs" }));

    await screen.findByText("No jobs registered.");
    expect(api.listScheduledJobs).toHaveBeenCalled();
  });

  it("a consultant_admin does not see a Scheduled Jobs entry", async () => {
    vi.mocked(api.listIntegrations).mockResolvedValue([]);

    render(<AdminArea canWrite={true} currentUserRole="consultant_admin" />);
    await screen.findByText(/Configuring a credential here/);

    expect(screen.queryByRole("button", { name: "Scheduled Jobs" })).toBeNull();
    expect(api.listScheduledJobs).not.toHaveBeenCalled();
  });
});
