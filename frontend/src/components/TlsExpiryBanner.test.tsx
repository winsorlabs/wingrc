// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import type { TlsCert, TlsExpiry } from "../types";
import { TlsExpiryBanner, tlsBannerLines } from "./TlsExpiryBanner";

vi.mock("../api", () => ({ api: { getTlsExpiry: vi.fn() } }));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function cert(over: Partial<TlsCert> = {}): TlsCert {
  return {
    name: "dev.wingrc.us",
    not_after: "2027-01-15T16:00:00Z",
    days_left: 90,
    verdict: "ok",
    error: null,
    ...over,
  };
}

function status(over: Partial<TlsExpiry> = {}): TlsExpiry {
  return {
    configured: true,
    attention: false,
    checked_at: "2026-10-17T12:00:00Z",
    stale: false,
    window_days: 30,
    certs: [cert(), cert({ name: "storage.dev.wingrc.us" })],
    ...over,
  };
}

describe("tlsBannerLines", () => {
  it("says nothing when nothing needs doing", () => {
    expect(tlsBannerLines(status())).toEqual([]);
    expect(tlsBannerLines(status({ configured: false, attention: true }))).toEqual([]);
  });

  it("names the certificate, the days and the date, and how to renew", () => {
    const lines = tlsBannerLines(status({
      attention: true,
      certs: [cert(), cert({ name: "storage.dev.wingrc.us", verdict: "expiring", days_left: 12, not_after: "2026-10-29T00:04:38Z" })],
    }));
    expect(lines).toEqual([
      "The TLS certificate for storage.dev.wingrc.us expires in 12 days, on 2026-10-29.",
      "Renew it on the host with deploy/tls/renew.sh.",
    ]);
  });

  it("says when it expired, and when it could not read one", () => {
    const lines = tlsBannerLines(status({
      attention: true,
      certs: [
        cert({ verdict: "expired", days_left: -3, not_after: "2026-10-15T00:04:38Z" }),
        cert({ name: "storage.dev.wingrc.us", verdict: "unreadable", not_after: null, days_left: null, error: "TimeoutError: timed out" }),
      ],
    }));
    expect(lines).toEqual([
      "The TLS certificate for dev.wingrc.us expired on 2026-10-15.",
      "Could not read the TLS certificate served for storage.dev.wingrc.us: TimeoutError: timed out.",
      "Renew it on the host with deploy/tls/renew.sh.",
    ]);
  });

  it("says when the check itself has stopped, even if its last reading was fine", () => {
    expect(tlsBannerLines(status({ attention: true, stale: true }))).toEqual([
      "The certificate check has not run since 2026-10-17; the worker may be stopped.",
    ]);
    expect(tlsBannerLines(status({ attention: true, stale: true, checked_at: null, certs: [] }))).toEqual([
      "The certificate check has never run; the worker may be stopped.",
    ]);
  });
});

describe("TlsExpiryBanner", () => {
  it("shows an alert when the status needs attention", async () => {
    vi.mocked(api.getTlsExpiry).mockResolvedValue(status({
      attention: true,
      certs: [cert({ verdict: "expiring", days_left: 1, not_after: "2026-10-18T00:00:00Z" })],
    }));
    render(<TlsExpiryBanner />);
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("expires in 1 day, on 2026-10-18");
  });

  it("renders nothing when all is well, or when the status cannot be fetched", async () => {
    vi.mocked(api.getTlsExpiry).mockResolvedValue(status());
    const { container, unmount } = render(<TlsExpiryBanner />);
    await vi.waitFor(() => expect(api.getTlsExpiry).toHaveBeenCalled());
    expect(container.innerHTML).toBe("");
    unmount();

    vi.mocked(api.getTlsExpiry).mockRejectedValue(new Error("down"));
    const second = render(<TlsExpiryBanner />);
    await vi.waitFor(() => expect(api.getTlsExpiry).toHaveBeenCalledTimes(2));
    expect(second.container.innerHTML).toBe("");
  });
});
