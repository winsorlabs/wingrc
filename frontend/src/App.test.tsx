// @vitest-environment jsdom
//
// Coverage for moving Integrations out of the per-org side nav and into a
// new deployment-tier "admin" screen state (App.tsx), reachable from
// OrgPicker via a header button gated by canSeeIntegrations — not a route,
// since this codebase has no router (see App.tsx's own Screen type
// comment). Only the pieces this move actually touches are exercised
// here: the button's visibility by role, that clicking it mounts the new
// AdminArea (which renders IntegrationsPanel), and that the breadcrumb
// link returns to the org picker. Not a general App.tsx test suite.
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import { api } from "./api";
import { useAuth } from "./hooks/useAuth";
import type { AuthUser } from "./types";

vi.mock("./api", () => ({
  api: {
    getOrgs: vi.fn(),
    getFrameworks: vi.fn(),
    getAssessments: vi.fn(),
    listIntegrations: vi.fn(),
  },
  getCachedAssessmentId: vi.fn(),
  setCachedAssessmentId: vi.fn(),
}));

vi.mock("./hooks/useAuth", () => ({
  useAuth: vi.fn(),
}));

function makeUser(overrides: Partial<AuthUser> = {}): AuthUser {
  return {
    id: "u1",
    org_id: "org1",
    email: "user@example.com",
    display_name: "Test User",
    role: "msp_admin",
    login_method: "local",
    mfa_enrolled: false,
    ...overrides,
  };
}

function mockAuth(user: AuthUser | null) {
  vi.mocked(useAuth).mockReturnValue({
    user,
    isLoading: false,
    canWrite: true,
    logout: vi.fn(),
    refresh: vi.fn(),
  });
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("App — deployment-tier Administration entry point", () => {
  it("an msp_admin sees the Administration button and it opens AdminArea (Integrations)", async () => {
    mockAuth(makeUser({ role: "msp_admin" }));
    vi.mocked(api.getOrgs).mockResolvedValue([]);
    vi.mocked(api.getFrameworks).mockResolvedValue([]);
    vi.mocked(api.listIntegrations).mockResolvedValue([]);

    render(<App />);

    const adminButton = await screen.findByRole("button", { name: /Administration/ });
    fireEvent.click(adminButton);

    await screen.findByText("Integrations");
    expect(api.listIntegrations).toHaveBeenCalled();
  });

  it("a customer_poc does not see the Administration button at all", async () => {
    mockAuth(makeUser({ role: "customer_poc" }));
    vi.mocked(api.getOrgs).mockResolvedValue([]);
    vi.mocked(api.getFrameworks).mockResolvedValue([]);

    render(<App />);

    // Wait for the picker's own load to settle before asserting absence.
    await screen.findByText(/No organization is available/);
    expect(screen.queryByRole("button", { name: /Administration/ })).toBeNull();
  });

  it("the Administration breadcrumb link returns to the org picker", async () => {
    mockAuth(makeUser({ role: "msp_admin" }));
    vi.mocked(api.getOrgs).mockResolvedValue([]);
    vi.mocked(api.getFrameworks).mockResolvedValue([]);
    vi.mocked(api.listIntegrations).mockResolvedValue([]);

    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: /Administration/ }));
    await screen.findByText("Integrations");

    // The header's own "Administration" button is gone once screen==="admin"
    // (showAdminButton is scoped to screen==="orgs"), so the breadcrumb's
    // text is unambiguous here.
    fireEvent.click(screen.getByText("Administration"));
    // msp_admin can create orgs, so the picker's full two-column view
    // renders (not the single-org fast path) -- see OrgPicker's own
    // showPicker logic.
    await screen.findByText("Organizations");
  });
});
