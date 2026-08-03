import { useEffect, useState } from "react";
import { api } from "../api";
import type { OnboardingStatus } from "../types";
import { ApiTokensPanel } from "./ApiTokensPanel";
import { AuditLogPanel } from "./AuditLogPanel";
import { ContactsPanel } from "./ContactsPanel";
import { OrgProfileForm } from "./OrgProfileForm";
import { SystemDescriptionForm } from "./SystemDescriptionForm";
import { UsersPanel } from "./UsersPanel";

type Tab = "profile" | "system" | "contacts" | "api-tokens" | "users" | "audit-log";

// Matches the backend's require_org_access("msp_admin", "msp_engineer")
// gate on the /api-tokens endpoints. This is UX only, not a security
// boundary — the real enforcement is server-side (see I.8's own framing
// for the same distinction on read-only rendering).
const API_TOKEN_ROLES = new Set(["msp_admin", "msp_engineer"]);

interface Props {
  orgId: string;
  orgName: string;
  currentUserId: string;
  currentUserRole: string;
  canWrite: boolean;
  onClose: () => void;
  initialTab?: Tab;
}

export function OrgSettings({
  orgId,
  orgName,
  currentUserId,
  currentUserRole,
  canWrite,
  onClose,
  initialTab = "profile",
}: Props) {
  const [tab, setTab] = useState<Tab>(initialTab);
  const [status, setStatus] = useState<OnboardingStatus | null>(null);
  const canSeeApiTokens = API_TOKEN_ROLES.has(currentUserRole);
  // invite_user/patch_user are gated to msp_admin only (no msp_engineer
  // rank exception, unlike API tokens) — see routers/users.py.
  const canSeeUsers = currentUserRole === "msp_admin";
  // Matches GET /orgs/{org_id}/audit-log's require_org_access("msp_admin")
  // gate exactly (routers/audit_log.py) — this is UX only, the server-side
  // check is the real boundary.
  const canSeeAuditLog = currentUserRole === "msp_admin";

  function loadStatus() {
    api.getOnboardingStatus(orgId).then(setStatus).catch(() => {});
  }

  useEffect(() => {
    loadStatus();
  }, [orgId]);

  function indicator(complete: boolean) {
    return <span className={`completion-dot${complete ? " complete" : ""}`}>{complete ? "✓" : "○"}</span>;
  }

  return (
    <div className="settings-shell">
      <div className="settings-header">
        <div className="settings-title">
          <span className="settings-org-name">{orgName}</span>
          <span className="settings-title-sep">·</span>
          <span>Settings</span>
        </div>
        <button className="drawer-close" onClick={onClose} aria-label="Close settings">×</button>
      </div>

      <div className="settings-layout">
        <nav className="settings-nav">
          <button
            className={`settings-nav-item${tab === "profile" ? " active" : ""}`}
            onClick={() => setTab("profile")}
          >
            {status && indicator(status.profile.complete)}
            Org Profile
          </button>
          <button
            className={`settings-nav-item${tab === "system" ? " active" : ""}`}
            onClick={() => setTab("system")}
          >
            {status && indicator(status.system_description.complete)}
            System Description
          </button>
          <button
            className={`settings-nav-item${tab === "contacts" ? " active" : ""}`}
            onClick={() => setTab("contacts")}
          >
            {status && indicator(status.personnel.complete)}
            Personnel &amp; Contacts
          </button>
          {canSeeApiTokens && (
            <button
              className={`settings-nav-item${tab === "api-tokens" ? " active" : ""}`}
              onClick={() => setTab("api-tokens")}
            >
              API Tokens
            </button>
          )}
          {canSeeUsers && (
            <button
              className={`settings-nav-item${tab === "users" ? " active" : ""}`}
              onClick={() => setTab("users")}
            >
              Users
            </button>
          )}
          {canSeeAuditLog && (
            <button
              className={`settings-nav-item${tab === "audit-log" ? " active" : ""}`}
              onClick={() => setTab("audit-log")}
            >
              Audit Log
            </button>
          )}
        </nav>

        <div className="settings-content">
          {tab === "profile" && (
            <OrgProfileForm orgId={orgId} canWrite={canWrite} onSaved={loadStatus} />
          )}
          {tab === "system" && (
            <SystemDescriptionForm orgId={orgId} canWrite={canWrite} onSaved={loadStatus} />
          )}
          {tab === "contacts" && (
            <ContactsPanel orgId={orgId} canWrite={canWrite} onChanged={loadStatus} />
          )}
          {tab === "api-tokens" && canSeeApiTokens && (
            <ApiTokensPanel orgId={orgId} currentUserRole={currentUserRole} />
          )}
          {tab === "users" && canSeeUsers && (
            <UsersPanel orgId={orgId} currentUserId={currentUserId} />
          )}
          {tab === "audit-log" && canSeeAuditLog && <AuditLogPanel orgId={orgId} />}
        </div>
      </div>
    </div>
  );
}
