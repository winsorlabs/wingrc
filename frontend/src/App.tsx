import { useState } from "react";
import { AssessmentBoard } from "./components/AssessmentBoard";
import { InviteAcceptPage } from "./components/InviteAcceptPage";
import { LoginPage } from "./components/LoginPage";
import { OnboardingWizard } from "./components/OnboardingWizard";
import { OrgPicker } from "./components/OrgPicker";
import { OrgSettings } from "./components/OrgSettings";
import { useAuth } from "./hooks/useAuth";
import type { Assessment, Org } from "./types";

type Screen = "orgs" | "board" | "onboarding" | "settings";

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
  const [settingsReturnScreen, setSettingsReturnScreen] = useState<Screen>("orgs");
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
    setSettingsReturnScreen(screen);
    setScreen("settings");
  }

  function closeSettings() {
    setScreen(settingsReturnScreen);
  }

  function goBack() {
    setScreen("orgs");
  }

  const showGear = org !== null && screen !== "settings" && screen !== "orgs";

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
        <button
          className="header-logout"
          onClick={logout}
          title={`Sign out (${user.email})`}
          aria-label="Sign out"
        >
          ⏏
        </button>
      </header>

      {!canWrite && (
        <div className="read-only-banner">Read-only access — assessor role.</div>
      )}

      {screen === "orgs" && (
        <OrgPicker
          onEnterBoard={enterBoard}
          onEnterOnboarding={enterOnboarding}
          onOpenSettings={(o) => { setOrg(o); setSettingsReturnScreen("orgs"); setScreen("settings"); }}
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
    </>
  );
}
