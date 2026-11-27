import { useEffect, useState } from "react";
import { api } from "../api";
import type { DeploymentWarning } from "../types";

// The third alarm banner (backend/app/deployment_checks.py), on the TLS
// and snapshot banners' path and under the same rule (CLAUDE.md,
// "Banners"): msp_admin only, every screen, not dismissible, recomputed on
// load and hourly, and a line shows only when something is wrong and
// someone can fix it. A check the deployment cannot answer is never a
// line: the server leaves it out of the list.
//
// The server sends fields and /health's sentence; the sentence here is the
// banner's own, saying who acts and where the page is.

const REFRESH_MS = 60 * 60 * 1000;
const OPERATOR = "Whoever runs this server should fix it.";

function page(title: string): string {
  return `The operator's page is "${title}" in docs/deployment.md.`;
}

function storageAccountLine(w: DeploymentWarning): string {
  if (w.administers) {
    return (
      "The app reaches object storage with an account that administers it (MinIO's root, or an " +
      "admin policy): anyone holding the app's storage keys can make and remove accounts and read " +
      `every bucket. ${OPERATOR} ${page("Moving a deployment off MinIO's root")}`
    );
  }
  if (w.beyond_the_app && w.beyond_the_app.length > 0) {
    return (
      `The app's storage account is granted more than it uses: ${w.beyond_the_app.join(", ")}. ` +
      `${OPERATOR} ${page("Object storage account")}`
    );
  }
  const missing = (w.missing ?? []).join(", ");
  return (
    `The app's storage account lacks ${missing || "an action it uses"}, so some uploads, downloads ` +
    `or deletes fail. ${OPERATOR} ${page("Object storage account")}`
  );
}

function databaseRoleLine(w: DeploymentWarning): string {
  const role = w.role ?? "the app's role";
  const why = w.superuser
    ? `${role}, a superuser`
    : w.bypassrls
      ? `${role}, which has BYPASSRLS`
      : `${role}, which owns ${w.owner_of_rls_tables ?? 0} tables with row-level security`;
  return (
    `The app connects to the database as ${why}, so the database does not keep tenants apart; ` +
    `only the application's checks do. ${OPERATOR} ${page("Database roles: which credential does what")}`
  );
}

export function deploymentBannerLines(warnings: DeploymentWarning[]): string[] {
  return warnings.map((w) => {
    switch (w.key) {
      case "storage_unreachable":
        return (
          `The app cannot use its object storage: ${w.detail.replace(/^failed: /, "")} Uploads, ` +
          `downloads and logos fail until it is fixed. ${OPERATOR} ${page("Provisioning")}`
        );
      case "storage_account":
        return storageAccountLine(w);
      case "database_role":
        return databaseRoleLine(w);
      case "breached_password_list":
        return (
          "This build has no breached-password list, so where HIBP cannot be reached no new " +
          "password is checked against known breaches. It ships with a WinGRC release; the " +
          "server cannot add it."
        );
      default:
        // A key this build does not know: still something wrong, so say it.
        return w.detail;
    }
  });
}

export function DeploymentBanner() {
  const [warnings, setWarnings] = useState<DeploymentWarning[] | null>(null);

  useEffect(() => {
    let live = true;
    async function load() {
      try {
        const r = await api.getDeploymentWarnings();
        if (live) setWarnings(r.warnings);
      } catch {
        // No reading is not a verdict; the API being down shows elsewhere.
      }
    }
    load();
    const t = setInterval(load, REFRESH_MS);
    return () => {
      live = false;
      clearInterval(t);
    };
  }, []);

  const lines = warnings ? deploymentBannerLines(warnings) : [];
  if (lines.length === 0) return null;
  return (
    <div className="tls-expiry-banner" role="alert">
      {lines.map((l) => (
        <div key={l}>{l}</div>
      ))}
    </div>
  );
}
