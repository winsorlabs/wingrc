// @vitest-environment jsdom
//
// The first component-rendering test in this codebase — every other
// frontend test (filters.test.ts, permissions.test.ts, radarChart.test.ts)
// tests a pure function in lib/, deliberately avoiding DOM/rendering
// infrastructure. This one exists because the specific claim it verifies
// ("switching the assessment dropdown actually re-fetches the dashboard,
// not just changes a label") lives entirely in a useEffect reacting to a
// prop change — there's no pure function to extract it into. Confirmed
// adding jsdom + @testing-library/react for this with the user before
// adding them, per the same "flag new dependencies" standard as G.3's
// hand-rolled radar chart decision. Scoped to just this file via the
// @vitest-environment comment above, not vite.config.ts's global
// test.environment (left as "node" for every other test).
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { useState } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import type { Assessment, DashboardData } from "../types";
import { OrgDashboard } from "./OrgDashboard";

vi.mock("../api", () => ({
  api: {
    getAssessments: vi.fn(),
    getDashboard: vi.fn(),
    listAuditLog: vi.fn(),
  },
}));

function makeAssessment(overrides: Partial<Assessment> = {}): Assessment {
  return {
    id: "a1",
    org_id: "org1",
    framework_id: "fw1",
    name: "First",
    assessment_type: "self",
    status: "in_progress",
    started_at: "2026-01-01T00:00:00Z",
    sprs_score: 100,
    last_activity_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

function makeDashboardData(): DashboardData {
  return {
    family_heatmap: [],
    sprs: { current_score: 100, trajectory: [] },
    statement_progress: { draft: 0, reviewed: 0, approved: 0, not_started: 0 },
    evidence_expiring: [],
    needs_review: [],
    needs_review_count: 0,
    blocked_objectives: [],
    blocked_objectives_count: 0,
    raci_open_tasks: [],
    poam_summary: { open: 0, on_track: 0, delayed: 0, completed: 0, cancelled: 0 },
  };
}

// Minimal harness mimicking App.tsx's own prop-lifting contract: holds the
// currently-selected assessment in state, passes its id down, updates it
// when OrgDashboard reports a switch via onSwitchAssessment. Exercises the
// real switch-and-refetch cycle without dragging in App.tsx's auth/login
// machinery, which is unrelated to what these tests check.
function Harness({ initial }: { initial: Assessment | null }) {
  const [assessment, setAssessment] = useState<Assessment | null>(initial);
  return (
    <OrgDashboard
      orgId="org1"
      assessmentId={assessment?.id ?? null}
      currentUserRole="customer_poc"
      onSwitchAssessment={setAssessment}
    />
  );
}

describe("OrgDashboard assessment switcher (G.4)", () => {
  beforeEach(() => {
    vi.mocked(api.getDashboard).mockResolvedValue(makeDashboardData());
  });

  it("switching the dropdown re-fetches the dashboard for the newly selected assessment, not just relabels", async () => {
    const a1 = makeAssessment({ id: "a1", name: "First" });
    const a2 = makeAssessment({ id: "a2", name: "Second" });
    vi.mocked(api.getAssessments).mockResolvedValue([a1, a2]);

    render(<Harness initial={a1} />);

    await waitFor(() => expect(api.getDashboard).toHaveBeenCalledWith("org1", "a1"));

    const select = await screen.findByLabelText("Assessment");
    fireEvent.change(select, { target: { value: "a2" } });

    await waitFor(() => expect(api.getDashboard).toHaveBeenCalledWith("org1", "a2"));
    expect(api.getDashboard).toHaveBeenCalledTimes(2);
  });

  it("auto-selects the most recently active assessment when nothing was pre-selected", async () => {
    const older = makeAssessment({ id: "a1", name: "Older", last_activity_at: "2026-01-01T00:00:00Z" });
    const newer = makeAssessment({ id: "a2", name: "Newer", last_activity_at: "2026-02-01T00:00:00Z" });
    vi.mocked(api.getAssessments).mockResolvedValue([older, newer]);

    render(<Harness initial={null} />);

    await waitFor(() => expect(api.getDashboard).toHaveBeenCalledWith("org1", "a2"));
    expect(api.getDashboard).not.toHaveBeenCalledWith("org1", "a1");
  });

  it("does not override an already-selected assessment, even if a different one is more recently active", async () => {
    const selected = makeAssessment({ id: "a1", name: "Explicitly selected", last_activity_at: "2026-01-01T00:00:00Z" });
    const fresher = makeAssessment({ id: "a2", name: "Actually fresher", last_activity_at: "2026-02-01T00:00:00Z" });
    vi.mocked(api.getAssessments).mockResolvedValue([selected, fresher]);

    render(<Harness initial={selected} />);

    await waitFor(() => expect(api.getDashboard).toHaveBeenCalledWith("org1", "a1"));
    expect(api.getDashboard).not.toHaveBeenCalledWith("org1", "a2");
  });

  it("hides the switcher entirely when the org has only one assessment", async () => {
    const a1 = makeAssessment({ id: "a1", name: "Only One" });
    vi.mocked(api.getAssessments).mockResolvedValue([a1]);

    render(<Harness initial={a1} />);

    await waitFor(() => expect(api.getDashboard).toHaveBeenCalledWith("org1", "a1"));
    expect(screen.queryByLabelText("Assessment")).toBeNull();
  });
});
