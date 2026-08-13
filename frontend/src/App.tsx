import { useState } from "react";
import { AccountSettings } from "./components/AccountSettings";
import { AssessmentBoard } from "./components/AssessmentBoard";
import { InviteAcceptPage } from "./components/InviteAcceptPage";
import { LoginPage } from "./components/LoginPage";
import { OnboardingWizard } from "./components/OnboardingWizard";
import { OrgPicker } from "./components/OrgPicker";
import { OrgSettings } from "./components/OrgSettings";
import { useAuth } from "./hooks/useAuth";
import type { Assessment, Org } from "./types";

type Screen = "orgs" | "board" | "onboarding" | "settings" | "account";

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
  // Shared by both drawer overlays (org settings, account settings) — each
  // remembers where to return the user when closed.
  const [drawerReturnScreen, setDrawerReturnScreen] = useState<Screen>("orgs");
  const [preAuthScreen, setPreAuthScreen] = useState<PreAuthScreen>("login");

  function enterBoard(o: Org, a: Assessment) {
    setOrg(o);
    setAssessment(a);
    setScreen("board");
  }

  function enterOnboarding(o: Org) {
    setOrg(o);
    setScreen("onboarding");
  }

  function openSettings() {
    setDrawerReturnScreen(screen);
    setScreen("settings");
  }

  function closeSettings() {
    setScreen(drawerReturnScreen);
  }

  function openAccount() {
    setDrawerReturnScreen(screen);
    setScreen("account");
  }

  function closeAccount() {
    setScreen(drawerReturnScreen);
  }

  function goBack() {
    setScreen("orgs");
  }

  const showGear = org !== null && screen !== "settings" && screen !== "orgs";
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
        {screen === "board" && org && assessment && (
          <nav className="breadcrumb">
            <span>›</span>
            <a onClick={goBack}>{org.name}</a>
            <span>›</span>
            <span>{assessment.name}</span>
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
          {showGear && (
            <button
              className="header-gear"
              onClick={openSettings}
              aria-label="Org settings"
              title="Org settings"
            >
              ⚙
            </button>
          )}
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
          onEnterBoard={enterBoard}
          onEnterOnboarding={enterOnboarding}
          onOpenSettings={(o) => { setOrg(o); setDrawerReturnScreen("orgs"); setScreen("settings"); }}
        />
      )}
      {screen === "board" && org && assessment && (
        <AssessmentBoard org={org} assessment={assessment} canWrite={canWrite} />
      )}
      {screen === "onboarding" && org && (
        <OnboardingWizard
          orgId={org.id}
          orgName={org.name}
          onClose={goBack}
        />
      )}
      {screen === "settings" && org && (
        <OrgSettings
          orgId={org.id}
          orgName={org.name}
          currentUserId={user.id}
          currentUserRole={user.role}
          canWrite={canWrite}
          onClose={closeSettings}
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
