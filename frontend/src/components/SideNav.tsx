import { canSeeApiTokens, canSeeAuditLog, canSeeSecurity, canSeeUsers } from "../lib/roles";
import type { OnboardingStatus } from "../types";

export type NavCategory = "dashboard" | "scope" | "assessments" | "tools" | "library" | "security";
export type ScopeTab = "profile" | "system" | "contacts" | "assets" | "roles";
export type SecurityTab = "users" | "api-tokens" | "audit-log";
export type SystemDescriptionSection = "network_diagram" | "data_flow_diagram";

interface Props {
  category: NavCategory;
  onSelectCategory: (c: NavCategory) => void;
  scopeTab: ScopeTab;
  onSelectScopeTab: (t: ScopeTab) => void;
  // Network Diagram / Data Flow Diagram don't have their own ScopeTab — per
  // G.6 they live inside the System Description editor, not separate pages
  // (see docs/PLAN-gui-restructure.md G.6). Clicking either nav entry routes
  // to the "system" tab and asks it to scroll to/highlight this section.
  onFocusSystemSection: (s: SystemDescriptionSection) => void;
  securityTab: SecurityTab;
  onSelectSecurityTab: (t: SecurityTab) => void;
  currentUserRole: string;
  status: OnboardingStatus | null;
}

export function SideNav({
  category,
  onSelectCategory,
  scopeTab,
  onSelectScopeTab,
  onFocusSystemSection,
  securityTab,
  onSelectSecurityTab,
  currentUserRole,
  status,
}: Props) {
  const showApiTokens = canSeeApiTokens(currentUserRole);
  const showUsers = canSeeUsers(currentUserRole);
  const showAuditLog = canSeeAuditLog(currentUserRole);
  const showSecurity = canSeeSecurity(currentUserRole);

  function indicator(complete: boolean) {
    return <span className={`completion-dot${complete ? " complete" : ""}`}>{complete ? "✓" : "○"}</span>;
  }

  function categoryClass(c: NavCategory) {
    return `side-nav-item${category === c ? " active" : ""}`;
  }

  function scopeSubClass(t: ScopeTab) {
    return `side-nav-subitem${category === "scope" && scopeTab === t ? " active" : ""}`;
  }

  function securitySubClass(t: SecurityTab) {
    return `side-nav-subitem${category === "security" && securityTab === t ? " active" : ""}`;
  }

  return (
    <nav className="side-nav">
      <div className="side-nav-category">
        <button className={categoryClass("dashboard")} onClick={() => onSelectCategory("dashboard")}>
          Dashboard
        </button>
      </div>

      <div className="side-nav-category">
        <button className={categoryClass("scope")} onClick={() => onSelectCategory("scope")}>
          Scope
        </button>
        {category === "scope" && (
          <div className="side-nav-subitems">
            <button className={scopeSubClass("profile")} onClick={() => onSelectScopeTab("profile")}>
              {status && indicator(status.profile.complete)}
              Org Profile
            </button>
            <button className={scopeSubClass("system")} onClick={() => onSelectScopeTab("system")}>
              {status && indicator(status.system_description.complete)}
              System Description
            </button>
            <button className={scopeSubClass("contacts")} onClick={() => onSelectScopeTab("contacts")}>
              {status && indicator(status.personnel.complete)}
              Personnel &amp; Contacts
            </button>
            <button className={scopeSubClass("assets")} onClick={() => onSelectScopeTab("assets")}>
              Assets
            </button>
            {/* G.6: no separate pages — the diagrams live inside the System
                Description editor. These entries route there and ask it to
                scroll to/highlight the relevant section, so they light up
                together with "System Description" rather than tracking
                their own active state. */}
            <button className={scopeSubClass("system")} onClick={() => onFocusSystemSection("network_diagram")}>
              Network Diagram
            </button>
            <button className={scopeSubClass("system")} onClick={() => onFocusSystemSection("data_flow_diagram")}>
              Data Flow Diagram
            </button>
            <button className={scopeSubClass("roles")} onClick={() => onSelectScopeTab("roles")}>
              Roles
            </button>
          </div>
        )}
      </div>

      <div className="side-nav-category">
        <button className={categoryClass("assessments")} onClick={() => onSelectCategory("assessments")}>
          Assessments
        </button>
      </div>

      <div className="side-nav-category">
        <button className={categoryClass("tools")} onClick={() => onSelectCategory("tools")}>
          Tools
        </button>
      </div>

      <div className="side-nav-category">
        <button className={categoryClass("library")} onClick={() => onSelectCategory("library")}>
          Library
        </button>
        {category === "library" && (
          <div className="side-nav-subitems">
            {/* None of these are built yet (G.10) except Lists' backend
                export logic, which has no frontend wrapper either. */}
            <button className="side-nav-subitem side-nav-subitem--placeholder" disabled>Lists</button>
            <button className="side-nav-subitem side-nav-subitem--placeholder" disabled>Baselines</button>
            <button className="side-nav-subitem side-nav-subitem--placeholder" disabled>Plans</button>
            <button className="side-nav-subitem side-nav-subitem--placeholder" disabled>Policies</button>
            <button className="side-nav-subitem side-nav-subitem--placeholder" disabled>Procedures</button>
          </div>
        )}
      </div>

      {showSecurity && (
        <div className="side-nav-category">
          <button className={categoryClass("security")} onClick={() => onSelectCategory("security")}>
            Security
          </button>
          {category === "security" && (
            <div className="side-nav-subitems">
              {showUsers && (
                <button className={securitySubClass("users")} onClick={() => onSelectSecurityTab("users")}>
                  Users
                </button>
              )}
              {showApiTokens && (
                <button
                  className={securitySubClass("api-tokens")}
                  onClick={() => onSelectSecurityTab("api-tokens")}
                >
                  API Tokens
                </button>
              )}
              {showAuditLog && (
                <button
                  className={securitySubClass("audit-log")}
                  onClick={() => onSelectSecurityTab("audit-log")}
                >
                  Audit Log
                </button>
              )}
            </div>
          )}
        </div>
      )}
    </nav>
  );
}
