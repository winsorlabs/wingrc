import { useEffect, useState } from "react";
import { api } from "../api";
import type { OnboardingStatus } from "../types";
import { ContactsPanel } from "./ContactsPanel";
import { OrgProfileForm } from "./OrgProfileForm";
import { SystemDescriptionForm } from "./SystemDescriptionForm";

type Tab = "profile" | "system" | "contacts";

interface Props {
  orgId: string;
  orgName: string;
  onClose: () => void;
  initialTab?: Tab;
}

export function OrgSettings({ orgId, orgName, onClose, initialTab = "profile" }: Props) {
  const [tab, setTab] = useState<Tab>(initialTab);
  const [status, setStatus] = useState<OnboardingStatus | null>(null);

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
        </div>
      </div>
    </div>
  );
}
