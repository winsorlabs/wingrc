import { useState } from "react";
import { canSeeUserDirectory } from "../lib/roles";
import { IntegrationsPanel } from "./IntegrationsPanel";
import { SideNavCategory, SideNavItem, SideNavRoot } from "./SideNavKit";
import { ToolsLibraryPanel } from "./ToolsLibraryPanel";
import { UserDirectoryPanel } from "./UserDirectoryPanel";

// Email (SMTP, D.3's outbound-email prerequisite) is structurally a
// connector -- same ConnectorSpec/IntegrationConnection shape, same
// /integrations/* endpoints -- but not a data source the way Liongard is:
// it carries no compliance content by design (email_service.py's own
// docstring) and configures an outbound comms channel instead of feeding
// scope. Listing it under "Integrations" next to Liongard would read
// oddly (a reader scanning that section for "what feeds compliance data"
// would trip over a connector that feeds nothing). Given its own section
// instead, backed by the exact same IntegrationsPanel component filtered
// to `kind="notification"` -- one generic component, two mount points,
// not two panels to keep in sync.

// Deployment-tier administration -- distinct from an org's own Settings
// (OrgProfileForm etc.) and from a user's own account settings
// (AccountSettings). Reachable from OrgPicker (the pre-org screen), not
// nested inside any org's side nav, because the data here isn't org-scoped
// at all (see IntegrationsPanel/routers/integrations.py). App.tsx's own
// "admin" screen state is the mount point; this component is the shell
// inside it. Top bar is context only (the "WinGRC › Administration"
// breadcrumb in App.tsx plus account/logout) -- no menu items live there.
//
// Uses the same SideNavKit primitives as the per-org SideNav.tsx so the
// two navs are visually/behaviorally identical by construction, not by
// two copies of class-name strings that can drift.
//
// Tools (G.9) manages the baseline library itself -- import, publish/
// unpublish, documentation attachments -- and never writes OrgProduct.
// Users (G.11, this slice) is the deployment-wide user directory +
// org-access grant/revoke (ADR 0009 M.7/M.8). Named "Users," not
// "Security" -- unlike the tenant nav's Security category (Users/API
// Tokens/Audit Log), this section holds exactly one thing, and calling a
// one-item section "Security" overpromises. Gated to msp_admin only,
// unlike Integrations/Tools which also admit consultant_admin -- this is
// identity administration across every client on the deployment, not
// compliance-data configuration; see lib/roles.ts's canSeeUserDirectory
// for the full reasoning. That's why this section, alone of the three,
// needs the caller's role passed in to decide whether to render its nav
// entry at all.
type AdminSection = "integrations" | "email" | "tools" | "users";

interface Props {
  canWrite: boolean;
  currentUserRole: string;
}

export function AdminArea({ canWrite, currentUserRole }: Props) {
  const [section, setSection] = useState<AdminSection>("integrations");
  const showUsers = canSeeUserDirectory(currentUserRole);

  return (
    <div className="workspace-shell">
      <SideNavRoot>
        <SideNavCategory>
          <SideNavItem active={section === "integrations"} onClick={() => setSection("integrations")}>
            Integrations
          </SideNavItem>
        </SideNavCategory>
        <SideNavCategory>
          <SideNavItem active={section === "email"} onClick={() => setSection("email")}>
            Email
          </SideNavItem>
        </SideNavCategory>
        <SideNavCategory>
          <SideNavItem active={section === "tools"} onClick={() => setSection("tools")}>
            Tools
          </SideNavItem>
        </SideNavCategory>
        {showUsers && (
          <SideNavCategory>
            <SideNavItem active={section === "users"} onClick={() => setSection("users")}>
              Users
            </SideNavItem>
          </SideNavCategory>
        )}
      </SideNavRoot>

      <div className="workspace-content">
        {section === "integrations" && <IntegrationsPanel canWrite={canWrite} kind="data_source" />}
        {section === "email" && <IntegrationsPanel canWrite={canWrite} kind="notification" />}
        {section === "tools" && <ToolsLibraryPanel />}
        {section === "users" && showUsers && <UserDirectoryPanel />}
      </div>
    </div>
  );
}
