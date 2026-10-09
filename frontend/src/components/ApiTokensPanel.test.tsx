// @vitest-environment jsdom
//
// A token issued outside the user's home org can never authenticate (the
// backend now refuses to mint one), so the panel offers creation only in the
// home org and says why everywhere else.
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import { ApiTokensPanel } from "./ApiTokensPanel";

vi.mock("../api", () => ({
  api: { listApiTokens: vi.fn(), createApiToken: vi.fn(), revokeApiToken: vi.fn() },
}));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("ApiTokensPanel", () => {
  it("offers token creation in the home org", async () => {
    vi.mocked(api.listApiTokens).mockResolvedValue([]);
    render(<ApiTokensPanel orgId="home" homeOrgId="home" currentUserRole="msp_engineer" />);
    expect(await screen.findByRole("button", { name: "+ Create Token" })).toBeTruthy();
  });

  it("does not offer it in another org, and says why", async () => {
    vi.mocked(api.listApiTokens).mockResolvedValue([]);
    render(<ApiTokensPanel orgId="client" homeOrgId="home" currentUserRole="msp_engineer" />);
    expect((await screen.findByRole("note")).textContent).toMatch(/home organization/);
    expect(screen.queryByRole("button", { name: "+ Create Token" })).toBeNull();
  });
});
