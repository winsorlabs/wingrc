// @vitest-environment jsdom
//
// The Deactivate and Reactivate confirmations say which organizations the
// action reaches before it is confirmed (2026-11-20). Deactivation is on
// the user, so from one org's Users tab it ends access to all of them.
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import type { UserRow } from "../types";
import { UsersPanel } from "./UsersPanel";

vi.mock("../api", () => ({
  ApiError: class ApiError extends Error {},
  api: {
    listUsers: vi.fn(),
    getUserAccess: vi.fn(),
    deactivateUser: vi.fn(),
    patchUser: vi.fn(),
  },
}));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function makeUser(overrides: Partial<UserRow> = {}): UserRow {
  return {
    id: "u1",
    org_id: "org1",
    contact_id: null,
    email: "rob@example.com",
    display_name: "Rob Tester",
    role: "msp_admin",
    login_method: "local",
    is_active: true,
    mfa_enrolled: true,
    requires_admin_reset: false,
    locked_until: null,
    lockout_count: 0,
    last_login_at: null,
    created_at: "2026-11-01T00:00:00Z",
    deleted_at: null,
    deactivated_at: null,
    ...overrides,
  };
}

const ACCESS = {
  memberships: [
    { org_id: "org1", org_name: "Acme MSP", role: "msp_admin" },
    { org_id: "org2", org_name: "Fasano Dev", role: "msp_admin" },
  ],
  reactivates_to: "active" as const,
};

describe("UsersPanel confirmations", () => {
  it("Deactivate names every organization before it can be confirmed", async () => {
    vi.mocked(api.listUsers).mockResolvedValue([makeUser()]);
    vi.mocked(api.getUserAccess).mockResolvedValue(ACCESS);
    render(<UsersPanel orgId="org1" currentUserId="me" />);

    fireEvent.click(await screen.findByRole("button", { name: "Deactivate" }));
    expect(api.getUserAccess).toHaveBeenCalledWith("org1", "u1");
    expect(await screen.findByText(/2 organizations: Acme MSP, Fasano Dev/)).toBeTruthy();
    expect(api.deactivateUser).not.toHaveBeenCalled();
  });

  it("Reactivate asks first and says what it restores", async () => {
    vi.mocked(api.listUsers).mockResolvedValue([
      makeUser({ is_active: false, deactivated_at: "2026-11-20T10:00:00Z" }),
    ]);
    vi.mocked(api.getUserAccess).mockResolvedValue(ACCESS);
    render(<UsersPanel orgId="org1" currentUserId="me" />);

    fireEvent.click(await screen.findByRole("button", { name: "Reactivate" }));
    expect(api.patchUser).not.toHaveBeenCalled();
    expect(
      await screen.findByText(/They can sign in again, with access to 2 organizations: Acme MSP, Fasano Dev/),
    ).toBeTruthy();
  });
});
