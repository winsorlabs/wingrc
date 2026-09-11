import { useState } from "react";
import { IntegrationsPanel } from "./IntegrationsPanel";

// Deployment-tier administration -- distinct from an org's own Settings
// (OrgProfileForm etc.) and from a user's own account settings
// (AccountSettings). Reachable from OrgPicker (the pre-org screen), not
// nested inside any org's side nav, because the data here isn't org-scoped
// at all (see IntegrationsPanel/routers/integrations.py). App.tsx's own
// "admin" screen state is the mount point; this component is the shell
// inside it.
//
// Integrations is the only section today. Built as a shell with its own
// small section nav (not a single hardcoded panel) so future
// deployment-tier admin surfaces land here rather than each inventing a
// new top-level screen -- ROADMAP.md/docs/PLAN-gui-restructure.md's G.9
// (baseline-library import) and G.11 (pre-org access-grant screen) are
// both already documented as belonging to this same tier. Do not add
// disabled placeholder entries for those here before they're built --
// same reasoning as SideNav's Library category, which is already a
// standing example of how that ages.
type AdminSection = "integrations";

interface Props {
  canWrite: boolean;
}

export function AdminArea({ canWrite }: Props) {
  const [section, setSection] = useState<AdminSection>("integrations");

  return (
    <div className="workspace-shell">
      <nav className="side-nav">
        <div className="side-nav-category">
          <button
            className={`side-nav-item${section === "integrations" ? " active" : ""}`}
            onClick={() => setSection("integrations")}
          >
            Integrations
          </button>
        </div>
      </nav>

      <div className="workspace-content">
        {section === "integrations" && <IntegrationsPanel canWrite={canWrite} />}
      </div>
    </div>
  );
}
