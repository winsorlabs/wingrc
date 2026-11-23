// @vitest-environment jsdom
//
// Coverage for the deployment-wide user directory's key flows (ADR 0009
// M.7/M.8, G.11): the directory renders cross-org membership per user,
// granting access calls the real endpoint with the picked org/role,
// revoking asks for confirmation first, and a fresh deployment with no
// MSP org designated shows a clear prompt instead of erroring or hiding
// the invite action silently.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import type { Org, UserDirectoryEntry } from "../types";
import { UserDirectoryPanel } from "./UserDirectoryPanel";

vi.mock("../api", () => ({
  api: {
    listUserDirectory: vi.fn(),
    getOrgs: vi.fn(),
    getMspOrg: vi.fn(),
    grantMembership: vi.fn(),
    revokeMembership: vi.fn(),
    inviteUser: vi.fn(),
  },
}));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function makeUser(overrides: Partial<UserDirectoryEntry> = {}): UserDirectoryEntry {
  return {
    id: "u1",
    email: "alice@example.com",
    display_name: "Alice Admin",
    home_org_id: "org1",
    home_org_name: "Acme Corp",
    deleted_at: null,
    is_active: true,
    deactivated_at: null,
    login_method: "local",
    memberships: [{ org_id: "org1", org_name: "Acme Corp", role: "msp_admin" }],
    ...overrides,
  };
}

const ORGS: Org[] = [
  { id: "org1", name: "Acme Corp", created_at: "2026-01-01T00:00:00Z" },
  { id: "org2", name: "Widgets Inc", created_at: "2026-01-01T00:00:00Z" },
];

describe("UserDirectoryPanel — directory", () => {
  it("shows every user's cross-org membership, not a bare role column", async () => {
    vi.mocked(api.listUserDirectory).mockResolvedValue([
      makeUser({
        id: "u1",
        display_name: "Alice Admin",
        memberships: [
          { org_id: "org1", org_name: "Acme Corp", role: "msp_admin" },
          { org_id: "org2", org_name: "Widgets Inc", role: "msp_engineer" },
        ],
      }),
    ]);
    vi.mocked(api.getOrgs).mockResolvedValue(ORGS);
    vi.mocked(api.getMspOrg).mockResolvedValue(null);

    render(<UserDirectoryPanel />);

    await screen.findByText("Alice Admin");
    expect(screen.getByText(/Acme Corp: MSP Admin/)).toBeTruthy();
    expect(screen.getByText(/Widgets Inc: MSP Engineer/)).toBeTruthy();
  });

  it("says which accounts can use the access they hold, and offers a grant only to one that can use it", async () => {
    const access = [
      { org_id: "org1", org_name: "Acme Corp", role: "msp_admin" },
      { org_id: "org2", org_name: "Widgets Inc", role: "msp_admin" },
    ];
    vi.mocked(api.listUserDirectory).mockResolvedValue([
      makeUser({ id: "u1", display_name: "Alice Active", memberships: access }),
      makeUser({
        id: "u2",
        display_name: "Rob Offboarded",
        is_active: false,
        deactivated_at: "2026-11-20T10:00:00Z",
        memberships: access,
      }),
      makeUser({ id: "u3", display_name: "Ivy Invited", is_active: false, memberships: access }),
      makeUser({
        id: "u4",
        display_name: "Anon",
        is_active: false,
        deactivated_at: "2026-11-01T10:00:00Z",
        deleted_at: "2026-11-02T10:00:00Z",
        memberships: access,
      }),
    ]);
    vi.mocked(api.getOrgs).mockResolvedValue(ORGS);
    vi.mocked(api.getMspOrg).mockResolvedValue(null);

    render(<UserDirectoryPanel />);

    const text = async (name: string) =>
      ((await screen.findByText(name)).closest("tr") as HTMLElement).textContent ?? "";

    expect(await text("Alice Active")).toContain("Active");
    expect(await text("Alice Active")).not.toContain("Cannot sign in");
    expect(await text("Rob Offboarded")).toContain("Deactivated");
    expect(await text("Rob Offboarded")).toContain("Cannot sign in while deactivated");
    expect(await text("Ivy Invited")).toContain("Awaiting enrolment");
    expect(await text("Ivy Invited")).toContain("Not usable until they finish signing up");
    expect(await text("Anon")).toContain("Anonymized");
    expect(await text("Anon")).not.toContain("Deactivated");
    expect(await text("Anon")).not.toContain("Grant access");
    expect(await text("Rob Offboarded")).not.toContain("Grant access");
    expect(await text("Rob Offboarded")).toContain("Reactivate before granting access.");
    expect(await text("Ivy Invited")).toContain("Grant access");
    expect(await text("Alice Active")).toContain("Grant access");
  });

  it("a fresh deployment with no MSP org designated shows a clear prompt, not an error", async () => {
    vi.mocked(api.listUserDirectory).mockResolvedValue([makeUser()]);
    vi.mocked(api.getOrgs).mockResolvedValue(ORGS);
    vi.mocked(api.getMspOrg).mockResolvedValue(null);

    render(<UserDirectoryPanel />);

    await screen.findByText(/No MSP org designated yet/);
    expect(screen.queryByRole("button", { name: /\+ Invite to/ })).toBeNull();
  });
});

describe("UserDirectoryPanel — grant", () => {
  it("grants access to the picked org/role and reloads the directory", async () => {
    vi.mocked(api.listUserDirectory).mockResolvedValue([makeUser()]);
    vi.mocked(api.getOrgs).mockResolvedValue(ORGS);
    vi.mocked(api.getMspOrg).mockResolvedValue(null);
    vi.mocked(api.grantMembership).mockResolvedValue({
      user_id: "u1", org_id: "org2", role: "customer_poc", granted: true,
    });

    render(<UserDirectoryPanel />);
    await screen.findByText("Alice Admin");

    fireEvent.click(screen.getByRole("button", { name: "Grant access…" }));
    await screen.findByText("Grant access — Alice Admin");

    const [orgSelect, roleSelect] = screen.getAllByRole("combobox");
    fireEvent.change(orgSelect, { target: { value: "org2" } });
    fireEvent.change(roleSelect, { target: { value: "customer_poc" } });
    fireEvent.click(screen.getByRole("button", { name: "Grant" }));

    await waitFor(() =>
      expect(api.grantMembership).toHaveBeenCalledWith("org2", { user_id: "u1", role: "customer_poc" })
    );
    await waitFor(() => expect(api.listUserDirectory).toHaveBeenCalledTimes(2));
  });
});

describe("UserDirectoryPanel — revoke", () => {
  it("asks for confirmation before revoking", async () => {
    vi.mocked(api.listUserDirectory).mockResolvedValue([makeUser()]);
    vi.mocked(api.getOrgs).mockResolvedValue(ORGS);
    vi.mocked(api.getMspOrg).mockResolvedValue(null);
    vi.mocked(api.revokeMembership).mockResolvedValue(undefined);

    render(<UserDirectoryPanel />);
    await screen.findByText("Alice Admin");

    fireEvent.click(screen.getByTitle("Revoke access to Acme Corp"));
    await screen.findByText("Revoke access?");
    expect(api.revokeMembership).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Revoke" }));

    await waitFor(() => expect(api.revokeMembership).toHaveBeenCalledWith("org1", "u1"));
  });
});

describe("UserDirectoryPanel — invite into the designated MSP org", () => {
  it("invites a new MSP user into the designated org", async () => {
    vi.mocked(api.listUserDirectory).mockResolvedValue([makeUser()]);
    vi.mocked(api.getOrgs).mockResolvedValue(ORGS);
    vi.mocked(api.getMspOrg).mockResolvedValue({ org_id: "org1", org_name: "Acme Corp" });
    vi.mocked(api.inviteUser).mockResolvedValue({
      id: "u2",
      email: "new@example.com",
      display_name: "New Engineer",
      role: "msp_engineer",
      login_method: "local",
      is_active: false,
      invite_token: "raw-token-value",
      invite_expires_at: "2026-09-20T00:00:00Z",
      email_sent: false,
      email_error: "Email link cannot be built: WINGRC_PUBLIC_URL is not configured.",
    });

    render(<UserDirectoryPanel />);
    await screen.findByText("Alice Admin");

    fireEvent.click(screen.getByRole("button", { name: "+ Invite to Acme Corp" }));
    await screen.findByText("Invite to Acme Corp");

    const [emailInput, nameInput] = screen.getAllByRole("textbox");
    fireEvent.change(emailInput, { target: { value: "new@example.com" } });
    fireEvent.change(nameInput, { target: { value: "New Engineer" } });
    fireEvent.click(screen.getByRole("button", { name: "Invite" }));

    await waitFor(() =>
      expect(api.inviteUser).toHaveBeenCalledWith("org1", {
        email: "new@example.com",
        display_name: "New Engineer",
        role: "msp_engineer",
        login_method: "local",
      })
    );
    await screen.findByText("raw-token-value");
  });
});
