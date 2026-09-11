import { useState } from "react";
import { IntegrationsPanel } from "./IntegrationsPanel";
import { SideNavCategory, SideNavItem, SideNavRoot } from "./SideNavKit";
import { ToolsLibraryPanel } from "./ToolsLibraryPanel";

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
// Tools (G.9, this slice) manages the baseline library itself -- import,
// publish/unpublish, documentation attachments -- and never writes
// OrgProduct. Users (slice B, deployment Users directory + org-access
// grant) is not started -- do not add a disabled placeholder entry for it
// here; that's already a standing mistake in SideNav's own Library
// category (see that file's comment) and isn't worth repeating.
type AdminSection = "integrations" | "tools";

interface Props {
  canWrite: boolean;
}

export function AdminArea({ canWrite }: Props) {
  const [section, setSection] = useState<AdminSection>("integrations");

  return (
    <div className="workspace-shell">
      <SideNavRoot>
        <SideNavCategory>
          <SideNavItem active={section === "integrations"} onClick={() => setSection("integrations")}>
            Integrations
          </SideNavItem>
        </SideNavCategory>
        <SideNavCategory>
          <SideNavItem active={section === "tools"} onClick={() => setSection("tools")}>
            Tools
          </SideNavItem>
        </SideNavCategory>
      </SideNavRoot>

      <div className="workspace-content">
        {section === "integrations" && <IntegrationsPanel canWrite={canWrite} />}
        {section === "tools" && <ToolsLibraryPanel />}
      </div>
    </div>
  );
}
