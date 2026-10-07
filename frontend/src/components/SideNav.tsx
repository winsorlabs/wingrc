import { canSeeApiTokens, canSeeAuditLog, canSeeSecurity, canSeeUsers } from "../lib/roles";
import { SideNavCategory, SideNavItem, SideNavRoot, SideNavSubitem, SideNavSubitems } from "./SideNavKit";
import type { OnboardingStatus } from "../types";

// No "integrations" category here (moved out, not renamed) -- Integrations
// was never actually org-scoped data (routers/integrations.py carries no
// org_id on any route; IntegrationConnection is deployment-wide, one
// credential per MSP instance). Rendering it inside this per-org side nav
// let an admin working in one client's org clear or replace a credential
// every other client on the deployment depends on. It now lives in
// App.tsx's deployment-tier AdminArea, reachable from OrgPicker -- see
// AdminArea.tsx and App.tsx's own "admin" screen state. Same reasoning
// keeps "tools" here as *activation only* (ProductsPanel) while the
// baseline *library* management screen lives in AdminArea's own "tools"
// section -- two screens sharing an English name, not the same data.
export type NavCategory =
  | "dashboard"
  | "scope"
  | "assessments"
  | "tools"
  | "library"
  | "security";
export type ScopeTab =
  | "profile"
  | "system"
  | "contacts"
  | "assets"
  | "sprs"
  | "review-cycles"
  | "liongard-sync"
  | "lists";
// RACI is assessment-scoped data (docs/PLAN-gui-restructure.md G.7's
// 2026-09-09 move note) — "board" is the existing per-control assessment
// view, "roles" is the Roles/RACI matrix, both live under Assessments now.
export type AssessmentsTab = "board" | "roles";
export type SecurityTab = "users" | "api-tokens" | "audit-log";
// Library is the org's document library (roadmap N.1/N.2). "all" is every
// document; the rest are doc_type filters.
//
// "baseline" is a doc_type filter like the others: an org-level baseline
// (e.g. a Windows 11 Workstation Baseline -- headings, tool inventory,
// change management, review cadence, tied to CM.L2-3.4.1/3.4.2) is a
// narrative document. It is NOT the product baseline library, which is
// reference data about what a vendor's product covers and lives in
// AdminArea as "Product Baselines". An earlier version of this comment
// said the nav's Baselines meant that library; a real org baseline
// document showed otherwise, and the AdminArea feature was renamed so the
// two stop sharing a word.
//
// Lists is still not a doc_type filter here, despite `list` being one:
// the CMMC lists are views over the scope graph (CLAUDE.md), so they live
// at Scope > Lists, and the Library entry only points there.
export type LibraryTab = "all" | "policy" | "procedure" | "plan" | "baseline";
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
  assessmentsTab: AssessmentsTab;
  onSelectAssessmentsTab: (t: AssessmentsTab) => void;
  securityTab: SecurityTab;
  onSelectSecurityTab: (t: SecurityTab) => void;
  libraryTab: LibraryTab;
  onSelectLibraryTab: (t: LibraryTab) => void;
  currentUserRole: string;
  status: OnboardingStatus | null;
}

export function SideNav({
  category,
  onSelectCategory,
  scopeTab,
  onSelectScopeTab,
  onFocusSystemSection,
  assessmentsTab,
  onSelectAssessmentsTab,
  securityTab,
  onSelectSecurityTab,
  libraryTab,
  onSelectLibraryTab,
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

  return (
    <SideNavRoot>
      <SideNavCategory>
        <SideNavItem active={category === "dashboard"} onClick={() => onSelectCategory("dashboard")}>
          Dashboard
        </SideNavItem>
      </SideNavCategory>

      <SideNavCategory>
        <SideNavItem active={category === "scope"} onClick={() => onSelectCategory("scope")}>
          Scope
        </SideNavItem>
        {category === "scope" && (
          <SideNavSubitems>
            <SideNavSubitem active={scopeTab === "profile"} onClick={() => onSelectScopeTab("profile")}>
              {status && indicator(status.profile.complete)}
              Org Profile
            </SideNavSubitem>
            <SideNavSubitem active={scopeTab === "system"} onClick={() => onSelectScopeTab("system")}>
              {status && indicator(status.system_description.complete)}
              System Description
            </SideNavSubitem>
            <SideNavSubitem active={scopeTab === "contacts"} onClick={() => onSelectScopeTab("contacts")}>
              {status && indicator(status.personnel.complete)}
              Personnel &amp; Contacts
            </SideNavSubitem>
            <SideNavSubitem active={scopeTab === "assets"} onClick={() => onSelectScopeTab("assets")}>
              Assets
            </SideNavSubitem>
            {/* No completion indicator here, unlike the three above --
                "we have never filed with SPRS" is a legitimate state
                (a first-time assessment), not an incomplete one; see
                SprsSubmissionsPanel's own docstring. */}
            <SideNavSubitem active={scopeTab === "sprs"} onClick={() => onSelectScopeTab("sprs")}>
              SPRS Submissions
            </SideNavSubitem>
            {/* Reachable by every org member, including customer_poc --
                the client's own attestation is the point. See
                ReviewCyclesPanel's own docstring. */}
            <SideNavSubitem
              active={scopeTab === "review-cycles"}
              onClick={() => onSelectScopeTab("review-cycles")}
            >
              Periodic Review
            </SideNavSubitem>
            {/* D.3 second half: daily Liongard sync + asset/user onboarding
                approval. Reachable by every org member with write access,
                same as review-cycles above -- the org's Security Officer/
                IT contact is exactly who approves here. */}
            <SideNavSubitem
              active={scopeTab === "liongard-sync"}
              onClick={() => onSelectScopeTab("liongard-sync")}
            >
              Asset Approvals
            </SideNavSubitem>
            <SideNavSubitem active={scopeTab === "lists"} onClick={() => onSelectScopeTab("lists")}>
              Lists
            </SideNavSubitem>
            {/* G.6: no separate pages — the diagrams live inside the System
                Description editor. These entries route there and ask it to
                scroll to/highlight the relevant section, so they light up
                together with "System Description" rather than tracking
                their own active state. */}
            <SideNavSubitem active={scopeTab === "system"} onClick={() => onFocusSystemSection("network_diagram")}>
              Network Diagram
            </SideNavSubitem>
            <SideNavSubitem active={scopeTab === "system"} onClick={() => onFocusSystemSection("data_flow_diagram")}>
              Data Flow Diagram
            </SideNavSubitem>
          </SideNavSubitems>
        )}
      </SideNavCategory>

      <SideNavCategory>
        <SideNavItem active={category === "assessments"} onClick={() => onSelectCategory("assessments")}>
          Assessments
        </SideNavItem>
        {category === "assessments" && (
          <SideNavSubitems>
            <SideNavSubitem active={assessmentsTab === "board"} onClick={() => onSelectAssessmentsTab("board")}>
              Assessment Board
            </SideNavSubitem>
            <SideNavSubitem active={assessmentsTab === "roles"} onClick={() => onSelectAssessmentsTab("roles")}>
              Roles
            </SideNavSubitem>
          </SideNavSubitems>
        )}
      </SideNavCategory>

      <SideNavCategory>
        <SideNavItem active={category === "tools"} onClick={() => onSelectCategory("tools")}>
          Tools
        </SideNavItem>
      </SideNavCategory>

      <SideNavCategory>
        <SideNavItem active={category === "library"} onClick={() => onSelectCategory("library")}>
          Library
        </SideNavItem>
        {category === "library" && (
          <SideNavSubitems>
            <SideNavSubitem
              active={libraryTab === "all"}
              onClick={() => onSelectLibraryTab("all")}
            >
              All Documents
            </SideNavSubitem>
            <SideNavSubitem
              active={libraryTab === "policy"}
              onClick={() => onSelectLibraryTab("policy")}
            >
              Policies
            </SideNavSubitem>
            <SideNavSubitem
              active={libraryTab === "procedure"}
              onClick={() => onSelectLibraryTab("procedure")}
            >
              Procedures
            </SideNavSubitem>
            <SideNavSubitem
              active={libraryTab === "plan"}
              onClick={() => onSelectLibraryTab("plan")}
            >
              Plans
            </SideNavSubitem>
            <SideNavSubitem
              active={libraryTab === "baseline"}
              onClick={() => onSelectLibraryTab("baseline")}
            >
              Baselines
            </SideNavSubitem>
            {/* Not a document filter -- see LibraryTab's comment. Goes to
                Scope > Lists, and says so before the click does. */}
            <SideNavSubitem
              active={false}
              title="Opens Scope › Lists — the CMMC lists (3.1.1a–c, External Services) are views over the scope graph, not documents"
              onClick={() => {
                onSelectCategory("scope");
                onSelectScopeTab("lists");
              }}
            >
              Lists ↗
            </SideNavSubitem>
          </SideNavSubitems>
        )}
      </SideNavCategory>

      {showSecurity && (
        <SideNavCategory>
          <SideNavItem active={category === "security"} onClick={() => onSelectCategory("security")}>
            Security
          </SideNavItem>
          {category === "security" && (
            <SideNavSubitems>
              {showUsers && (
                <SideNavSubitem active={securityTab === "users"} onClick={() => onSelectSecurityTab("users")}>
                  Users
                </SideNavSubitem>
              )}
              {showApiTokens && (
                <SideNavSubitem
                  active={securityTab === "api-tokens"}
                  onClick={() => onSelectSecurityTab("api-tokens")}
                >
                  API Tokens
                </SideNavSubitem>
              )}
              {showAuditLog && (
                <SideNavSubitem
                  active={securityTab === "audit-log"}
                  onClick={() => onSelectSecurityTab("audit-log")}
                >
                  Audit Log
                </SideNavSubitem>
              )}
            </SideNavSubitems>
          )}
        </SideNavCategory>
      )}
    </SideNavRoot>
  );
}
