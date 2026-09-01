import { useEffect, useState } from "react";
import { api } from "./api";
import { AccountSettings } from "./components/AccountSettings";
import { ApiTokensPanel } from "./components/ApiTokensPanel";
import { AssessmentBoard } from "./components/AssessmentBoard";
import { AssetsPanel } from "./components/AssetsPanel";
import { AuditLogPanel } from "./components/AuditLogPanel";
import { ContactsPanel } from "./components/ContactsPanel";
import { InviteAcceptPage } from "./components/InviteAcceptPage";
import { LoginPage } from "./components/LoginPage";
import { OnboardingWizard } from "./components/OnboardingWizard";
import { OrgDashboard } from "./components/OrgDashboard";
import { OrgPicker } from "./components/OrgPicker";
import { OrgProfileForm } from "./components/OrgProfileForm";
import { ProductsPanel } from "./components/ProductsPanel";
import type { NavCategory, ScopeTab, SecurityTab } from "./components/SideNav";
import { SideNav } from "./components/SideNav";
import { SystemDescriptionForm } from "./components/SystemDescriptionForm";
import { UsersPanel } from "./components/UsersPanel";
import { useAuth } from "./hooks/useAuth";
import { canSeeApiTokens, canSeeAuditLog, canSeeUsers } from "./lib/roles";
import type { Assessment, OnboardingStatus, Org } from "./types";

type Screen = "orgs" | "nav" | "onboarding" | "account";

// Separate from Screen (which only makes sense once `user` exists): this
// picks between the two pre-auth pages. There's no router in this codebase
// (see InviteAcceptPage's own comment) — both screen state machines follow
// the same pattern deliberately, just scoped to different lifecycles.
type PreAuthScreen = "login" | "accept-invite";

export function App() {
  const { user, isLoading, canWrite, logout, refresh } = useAuth();
  const [screen, setScreen] = useState<Screen>("orgs");
  const [org, setOrg] = useState<Org | null>(null);
  const [assessment, setAssessment] = useState<Assessment | null>(null);
  // "nav" screen's own state — G.1's side nav (docs/PLAN-gui-restructure.md).
  // Each category keeps its own last-selected sub-tab independently (switch
  // to Security then back to Scope and you're still on whichever Scope tab
  // you left), mirroring the old OrgSettings drawer's per-drawer `tab` state
  // one level higher, now persistent instead of open-close.
  const [navCategory, setNavCategory] = useState<NavCategory>("assessments");
  const [scopeTab, setScopeTab] = useState<ScopeTab>("profile");
  const [securityTab, setSecurityTab] = useState<SecurityTab>("users");
  const [onboardingStatus, setOnboardingStatus] = useState<OnboardingStatus | null>(null);
  const [preAuthScreen, setPreAuthScreen] = useState<PreAuthScreen>("login");

  function loadOnboardingStatus(orgId: string) {
    api.getOnboardingStatus(orgId).then(setOnboardingStatus).catch(() => {});
  }

  useEffect(() => {
    if (org && screen === "nav") loadOnboardingStatus(org.id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [org?.id, screen]);

  function enterBoard(o: Org, a: Assessment) {
    setOrg(o);
    setAssessment(a);
    // Dashboard, not Assessments, is the landing view once org+assessment
    // are both resolved (G.3) — Assessments still shows the existing
    // control-state board, just no longer the first thing you see.
    setNavCategory("dashboard");
    setScreen("nav");
  }

  function enterOnboarding(o: Org) {
    setOrg(o);
    setScreen("onboarding");
  }

  function openAccount() {
    setScreen("account");
  }

  function closeAccount() {
    setScreen(org ? "nav" : "orgs");
  }

  function goBack() {
    setScreen("orgs");
  }

  const showAccountButton = screen !== "account";

  if (isLoading) return <div className="app-loading">Loading…</div>;
  if (!user) {
    return preAuthScreen === "accept-invite"
      ? (
        <InviteAcceptPage
          onAuthenticated={refresh}
          onCancel={() => setPreAuthScreen("login")}
        />
      )
      : (
        <LoginPage
          onAuthenticated={refresh}
          onWantInvite={() => setPreAuthScreen("accept-invite")}
        />
      );
  }

  return (
    <>
      <header className="app-header">
        <h1>WinGRC</h1>
        {screen === "nav" && org && (
          <nav className="breadcrumb">
            <span>›</span>
            <a onClick={goBack}>{org.name}</a>
            {(navCategory === "dashboard" || navCategory === "assessments") && assessment && (
              <>
                <span>›</span>
                <span>{assessment.name}</span>
              </>
            )}
          </nav>
        )}
        {screen === "onboarding" && org && (
          <nav className="breadcrumb">
            <span>›</span>
            <a onClick={goBack}>{org.name}</a>
            <span>›</span>
            <span>Setup</span>
          </nav>
        )}
        <div className="header-icons">
          {showAccountButton && (
            <button
              className="header-gear"
              onClick={openAccount}
              aria-label="My account"
              title={`My account (${user.email})`}
            >
              👤
            </button>
          )}
          <button
            className="header-logout"
            onClick={logout}
            title={`Sign out (${user.email})`}
            aria-label="Sign out"
          >
            ⏏
          </button>
        </div>
      </header>

      {!canWrite && (
        <div className="read-only-banner">Read-only access — assessor role.</div>
      )}

      {screen === "orgs" && (
        <OrgPicker
          currentUser={user}
          canWrite={canWrite}
          // True once we've already had an org open this session — i.e.
          // the user explicitly navigated back to the picker (breadcrumb
          // click) rather than this being the fresh-login landing. Without
          // this, OrgPicker's cached-assessment auto-resume (see its own
          // comment) fires again on every return trip and bounces the user
          // straight back into the board they just left, making the
          // assessment list / "Start New Assessment" screen unreachable
          // for any org that's ever been opened. Pre-existing bug, not
          // introduced by G.1 — the breadcrumb "go back" path is unchanged
          // from before G.1's nav restructure; only the reachable
          // destination (the picker's own auto-resume) was broken.
          skipAutoResume={org !== null}
          onEnterBoard={enterBoard}
          onEnterOnboarding={enterOnboarding}
          onOpenSettings={(o) => {
            // Reset assessment explicitly: this path sets org without ever
            // selecting an assessment, and a stale one from a previously
            // open org would otherwise let AssessmentBoard render with a
            // mismatched org/assessment pairing if the user then clicks
            // "Assessments" in the side nav.
            setOrg(o);
            setAssessment(null);
            setNavCategory("scope");
            setScreen("nav");
          }}
        />
      )}

      {screen === "nav" && org && (
        <div className="workspace-shell">
          <SideNav
            category={navCategory}
            onSelectCategory={setNavCategory}
            scopeTab={scopeTab}
            onSelectScopeTab={setScopeTab}
            securityTab={securityTab}
            onSelectSecurityTab={setSecurityTab}
            currentUserRole={user.role}
            status={onboardingStatus}
          />

          {navCategory === "dashboard" && (
            <OrgDashboard
              orgId={org.id}
              assessmentId={assessment?.id ?? null}
              currentUserRole={user.role}
              onSwitchAssessment={setAssessment}
            />
          )}

          {navCategory === "scope" && (
            <div className="workspace-content">
              {scopeTab === "profile" && (
                <OrgProfileForm orgId={org.id} canWrite={canWrite} onSaved={() => loadOnboardingStatus(org.id)} />
              )}
              {scopeTab === "system" && (
                <SystemDescriptionForm
                  orgId={org.id}
                  canWrite={canWrite}
                  onSaved={() => loadOnboardingStatus(org.id)}
                />
              )}
              {scopeTab === "contacts" && (
                <ContactsPanel orgId={org.id} canWrite={canWrite} onChanged={() => loadOnboardingStatus(org.id)} />
              )}
              {scopeTab === "assets" && (
                <AssetsPanel orgId={org.id} canWrite={canWrite} />
              )}
            </div>
          )}

          {navCategory === "assessments" && (
            assessment ? (
              <AssessmentBoard org={org} assessment={assessment} canWrite={canWrite} />
            ) : (
              <div className="workspace-content">
                <div className="empty">
                  No assessment selected — go back to the org picker to choose one.
                </div>
              </div>
            )
          )}

          {navCategory === "tools" && (
            assessment ? (
              <ProductsPanel
                orgId={org.id}
                assessmentId={assessment.id}
                canWrite={canWrite}
                onClose={() => setNavCategory("assessments")}
                onActivated={() => setNavCategory("assessments")}
                onDeactivated={() => setNavCategory("assessments")}
              />
            ) : (
              <div className="workspace-content">
                <div className="empty">
                  No assessment selected — go back to the org picker to choose one.
                </div>
              </div>
            )
          )}

          {navCategory === "library" && (
            <div className="workspace-content">
              <div className="empty">Library isn't built yet (docs/PLAN-gui-restructure.md G.10).</div>
            </div>
          )}

          {navCategory === "security" && (
            <div className="workspace-content">
              {/* Re-checks role here, not just in SideNav: securityTab
                  defaults to "users" regardless of role (React hooks can't
                  initialize state from `user.role` conditionally — `user`
                  isn't guaranteed non-null yet at the useState call site).
                  Without this, an msp_engineer (sees Security via API
                  Tokens, not Users) landing on the category for the first
                  time would render UsersPanel anyway and hit an immediate
                  403 from its own fetch. */}
              {securityTab === "users" && canSeeUsers(user.role) && (
                <UsersPanel orgId={org.id} currentUserId={user.id} />
              )}
              {securityTab === "api-tokens" && canSeeApiTokens(user.role) && (
                <ApiTokensPanel orgId={org.id} currentUserRole={user.role} />
              )}
              {securityTab === "audit-log" && canSeeAuditLog(user.role) && (
                <AuditLogPanel orgId={org.id} />
              )}
            </div>
          )}
        </div>
      )}

      {screen === "onboarding" && org && (
        <OnboardingWizard
          orgId={org.id}
          orgName={org.name}
          onClose={goBack}
        />
      )}
      {screen === "account" && (
        <AccountSettings
          user={user}
          onClose={closeAccount}
          onSignedOutEverywhere={refresh}
        />
      )}
    </>
  );
}
