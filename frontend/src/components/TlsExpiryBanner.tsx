import { useEffect, useState } from "react";
import { api } from "../api";
import type { TlsCert, TlsExpiry } from "../types";

// The only reminder that the certificate needs renewing: renewal is manual
// (DNS-01 by hand, deploy/tls/renew.sh), and the July certificate lapsed
// with nothing saying so. msp_admin only, on every screen, until it is
// fixed -- including when the check itself has stopped running.

const REFRESH_MS = 60 * 60 * 1000;

function day(iso: string): string {
  return iso.slice(0, 10);
}

function certLine(c: TlsCert): string | null {
  if (c.verdict === "expiring" && c.not_after) {
    const days = c.days_left === 1 ? "1 day" : `${c.days_left} days`;
    return `The TLS certificate for ${c.name} expires in ${days}, on ${day(c.not_after)}.`;
  }
  if (c.verdict === "expired" && c.not_after) {
    return `The TLS certificate for ${c.name} expired on ${day(c.not_after)}.`;
  }
  if (c.verdict === "unreadable") {
    return `Could not read the TLS certificate served for ${c.name}: ${c.error ?? "no certificate"}.`;
  }
  return null;
}

export function tlsBannerLines(s: TlsExpiry): string[] {
  if (!s.configured || !s.attention) return [];
  const lines = s.certs.map(certLine).filter((l): l is string => l !== null);
  if (s.certs.some((c) => c.verdict === "expiring" || c.verdict === "expired")) {
    lines.push("Renew it on the host with deploy/tls/renew.sh.");
  }
  if (s.stale) {
    lines.push(
      s.checked_at
        ? `The certificate check has not run since ${day(s.checked_at)}; the worker may be stopped.`
        : "The certificate check has never run; the worker may be stopped."
    );
  }
  return lines;
}

export function TlsExpiryBanner() {
  const [status, setStatus] = useState<TlsExpiry | null>(null);

  useEffect(() => {
    let live = true;
    async function load() {
      try {
        const s = await api.getTlsExpiry();
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

  const lines = status ? tlsBannerLines(status) : [];
  if (lines.length === 0) return null;
  return (
    <div className="tls-expiry-banner" role="alert">
      {lines.map((l) => (
        <div key={l}>{l}</div>
      ))}
    </div>
  );
}
