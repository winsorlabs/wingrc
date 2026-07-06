import { useState } from "react";
import { AssessmentBoard } from "./components/AssessmentBoard";
import { OrgPicker } from "./components/OrgPicker";
import type { Assessment, Org } from "./types";

type Screen = "orgs" | "board";

export function App() {
  const [screen, setScreen] = useState<Screen>("orgs");
  const [org, setOrg] = useState<Org | null>(null);
  const [assessment, setAssessment] = useState<Assessment | null>(null);

  function enterBoard(o: Org, a: Assessment) {
    setOrg(o);
    setAssessment(a);
    setScreen("board");
  }

  function goBack() {
    setScreen("orgs");
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
      </header>

      {screen === "orgs" && <OrgPicker onEnterBoard={enterBoard} />}
      {screen === "board" && org && assessment && (
        <AssessmentBoard org={org} assessment={assessment} />
      )}
    </>
  );
}
