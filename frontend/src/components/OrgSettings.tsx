import { useEffect, useState } from "react";
import { api } from "../api";
import type { OnboardingStatus } from "../types";
import { ApiTokensPanel } from "./ApiTokensPanel";
import { ContactsPanel } from "./ContactsPanel";
import { OrgProfileForm } from "./OrgProfileForm";
import { SystemDescriptionForm } from "./SystemDescriptionForm";

type Tab = "profile" | "system" | "contacts" | "api-tokens";

// Matches the backend's require_org_access("msp_admin", "msp_engineer")
// gate on the /api-tokens endpoints. This is UX only, not a security
// boundary — the real enforcement is server-side (see I.8's own framing
// for the same distinction on read-only rendering).
const API_TOKEN_ROLES = new Set(["msp_admin", "msp_engineer"]);

interface Props {
  orgId: string;
  orgName: string;
  currentUserRole: string;
  onClose: () => void;
  initialTab?: Tab;
}

export function OrgSettings({ orgId, orgName, currentUserRole, onClose, initialTab = "profile" }: Props) {
  const [tab, setTab] = useState<Tab>(initialTab);
  const [status, setStatus] = useState<OnboardingStatus | null>(null);
  const canSeeApiTokens = API_TOKEN_ROLES.has(currentUserRole);

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
        </nav>

        <div className="settings-content">
          {tab === "profile" && (
            <OrgProfileForm orgId={orgId} onSaved={loadStatus} />
          )}
          {tab === "system" && (
            <SystemDescriptionForm orgId={orgId} onSaved={loadStatus} />
          )}
          {tab === "contacts" && (
            <ContactsPanel orgId={orgId} onChanged={loadStatus} />
          )}
          {tab === "api-tokens" && canSeeApiTokens && (
            <ApiTokensPanel orgId={orgId} currentUserRole={currentUserRole} />
          )}
        </div>
      </div>
    </div>
  );
}
