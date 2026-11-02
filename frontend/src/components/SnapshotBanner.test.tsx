// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import type { SnapshotStatus } from "../types";
import { SnapshotBanner, snapshotBannerLines } from "./SnapshotBanner";

vi.mock("../api", () => ({ api: { getSnapshotStatus: vi.fn() } }));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function status(over: Partial<SnapshotStatus> = {}): SnapshotStatus {
  return {
    status: "ok",
    attention: false,
    max_age_hours: 50,
    newest: "20261101T214424Z",
    age_hours: 3.2,
    count: 2,
    detail: null,
    ...over,
  };
}

describe("snapshotBannerLines", () => {
  it("says nothing when fresh, or when snapshots are not configured", () => {
    expect(snapshotBannerLines(status())).toEqual([]);
    expect(snapshotBannerLines(status({ status: "not configured", attention: false }))).toEqual([]);
  });

  it("names the snapshot, its age and the limit when stale", () => {
    expect(snapshotBannerLines(status({ status: "stale", attention: true, age_hours: 61.7 }))).toEqual([
      "The newest local snapshot, 20261101T214424Z, is 61 hours old (limit 50); the snapshot job may have stopped. " +
        'See docs/deployment.md, "Local snapshots".',
    ]);
  });

  it("says when there is none, when the clock is wrong, and when it cannot read", () => {
    expect(snapshotBannerLines(status({ status: "none", attention: true, newest: null }))[0]).toContain(
      "There is no local snapshot"
    );
    expect(snapshotBannerLines(status({ status: "invalid", attention: true }))[0]).toContain("dated in the future");
    expect(
      snapshotBannerLines(status({ status: "unreadable", attention: true, detail: "/snapshots: PermissionError" }))
    ).toEqual(["The local snapshot directory cannot be read: /snapshots: PermissionError."]);
  });
});

describe("SnapshotBanner", () => {
  it("shows an alert when the status needs attention", async () => {
    vi.mocked(api.getSnapshotStatus).mockResolvedValue(status({ status: "stale", attention: true, age_hours: 72 }));
    render(<SnapshotBanner />);
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("is 72 hours old (limit 50)");
  });

  it("renders nothing when all is well, or when the status cannot be fetched", async () => {
    vi.mocked(api.getSnapshotStatus).mockResolvedValue(status());
    const { container, unmount } = render(<SnapshotBanner />);
    await vi.waitFor(() => expect(api.getSnapshotStatus).toHaveBeenCalled());
    expect(container.innerHTML).toBe("");
    unmount();

    vi.mocked(api.getSnapshotStatus).mockRejectedValue(new Error("down"));
    const second = render(<SnapshotBanner />);
    await vi.waitFor(() => expect(api.getSnapshotStatus).toHaveBeenCalledTimes(2));
    expect(second.container.innerHTML).toBe("");
  });
});
