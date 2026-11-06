import { useEffect, useState } from "react";
import { api } from "../api";
import type { SnapshotStatus } from "../types";

// The alarm for local snapshots (item S), on the TLS banner's path: a
// cron that stops leaves nothing behind to say so, and /health is read
// only at a deploy. msp_admin only, on every screen, until a snapshot
// newer than the limit exists. The banner's style is the TLS banner's.

const REFRESH_MS = 60 * 60 * 1000;

// msp_admin need not be the operator, or have the repo: the banner says
// who acts, and names the operator's page last (Jarrod, 2026-11-05).
const OPERATOR_PAGE = "The operator's page is \"Local snapshots\" in docs/deployment.md.";

export function snapshotBannerLines(s: SnapshotStatus): string[] {
  if (!s.attention) return [];
  switch (s.status) {
    case "stale":
      return [
        `The newest local snapshot, ${s.newest}, is ${Math.floor(s.age_hours ?? 0)} hours old ` +
          `(limit ${s.max_age_hours}); the snapshot job may have stopped. ` +
          `Whoever runs this server should check it. ${OPERATOR_PAGE}`,
      ];
    case "none":
      return [
        "There is no local snapshot of this deployment. Whoever runs this server should set up " +
          `or check the snapshot job. ${OPERATOR_PAGE}`,
      ];
    case "invalid":
      return ["The newest local snapshot is dated in the future; check the host's clock."];
    default:
      return [`The local snapshot directory cannot be read: ${s.detail ?? s.status}.`];
  }
}

export function SnapshotBanner() {
  const [status, setStatus] = useState<SnapshotStatus | null>(null);

  useEffect(() => {
    let live = true;
    async function load() {
      try {
        const s = await api.getSnapshotStatus();
        if (live) setStatus(s);
      } catch {
        // No status is not a verdict; the API being down shows elsewhere.
      }
    }
    load();
    const t = setInterval(load, REFRESH_MS);
    return () => {
      live = false;
      clearInterval(t);
    };
  }, []);

  const lines = status ? snapshotBannerLines(status) : [];
  if (lines.length === 0) return null;
  return (
    <div className="tls-expiry-banner" role="alert">
      {lines.map((l) => (
        <div key={l}>{l}</div>
      ))}
    </div>
  );
}
