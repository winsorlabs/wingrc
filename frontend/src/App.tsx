import { useState } from "react";
import { AssessmentBoard } from "./components/AssessmentBoard";
import { OnboardingWizard } from "./components/OnboardingWizard";
import { OrgPicker } from "./components/OrgPicker";
import { OrgSettings } from "./components/OrgSettings";
import type { Assessment, Org } from "./types";

type Screen = "orgs" | "board" | "onboarding" | "settings";

export function App() {
  const [screen, setScreen] = useState<Screen>("orgs");
  const [org, setOrg] = useState<Org | null>(null);
  const [assessment, setAssessment] = useState<Assessment | null>(null);
  const [settingsReturnScreen, setSettingsReturnScreen] = useState<Screen>("orgs");

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
      </header>

      {screen === "orgs" && (
        <OrgPicker
          onEnterBoard={enterBoard}
          onEnterOnboarding={enterOnboarding}
          onOpenSettings={(o) => { setOrg(o); setSettingsReturnScreen("orgs"); setScreen("settings"); }}
        />
      )}
      {screen === "board" && org && assessment && (
        <AssessmentBoard org={org} assessment={assessment} />
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
          onClose={closeSettings}
        />
      )}
    </>
  );
}
