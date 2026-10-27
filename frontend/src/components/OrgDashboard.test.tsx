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
import { render, screen, waitFor, fireEvent, cleanup } from "@testing-library/react";
import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import type { Assessment, DashboardData, EnvironmentData, ScopeListWidgetData } from "../types";
import { OrgDashboard } from "./OrgDashboard";

vi.mock("../api", () => ({
  api: {
    getAssessments: vi.fn(),
    getDashboard: vi.fn(),
    // msp_admin's assessment view mounts RecentActivityCard (Q.0 made
    // the G.4 tests run as msp_admin), so this needs a resolved page too.
    listAuditLog: vi.fn().mockResolvedValue({ items: [], total: 0, offset: 0, limit: 10 }),
    // Org-scoped, fetched unconditionally by SprsSubmissionCard on every
    // render -- defaulted here (not per-test) so the many existing tests
    // that don't care about this widget don't all need updating just to
    // avoid an unmocked-call rejection.
    getCurrentSprsSubmission: vi.fn().mockResolvedValue(null),
    getEnvironment: vi.fn(),
    getScopeFiltered: vi.fn().mockResolvedValue([]),
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
    submitted_at: null,
    closed_at: null,
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
    raci_load: { msp_count: 0, customer_count: 0, other_count: 0, by_contact: [] },
    document_reviews: {
      overdue_count: 0,
      due_soon_count: 0,
      never_approved_count: 0,
      items: [],
    },
    open_evidence_tasks: [],
    open_evidence_tasks_count: 0,
  };
}

function emptyList(overrides: Partial<ScopeListWidgetData> = {}): ScopeListWidgetData {
  return { entity_type: "device", status: "active", in_boundary: true, total: 0, cap: 50, rows: [], ...overrides };
}

function makeEnvironment(overrides: Partial<EnvironmentData> = {}): EnvironmentData {
  return {
    org: {
      name: "Acme Corp",
      cage_code: null,
      uei: null,
      address_line1: null,
      address_line2: null,
      city: null,
      state_or_province: null,
      postal_code: null,
      country: null,
      phone_primary: null,
      website: null,
      missing_ssp_fields: ["cage_code", "uei"],
    },
    assessments: [],
    devices_in_boundary: emptyList(),
    people_in_boundary: emptyList({ entity_type: "person" }),
    pending_approval: emptyList({ entity_type: null, status: "pending_approval", in_boundary: null }),
    devices_out_of_boundary: 0,
    people_out_of_boundary: 0,
    document_reviews: { overdue_count: 0, due_soon_count: 0, never_approved_count: 0, items: [] },
    ...overrides,
  };
}

// Minimal harness mimicking App.tsx's own prop-lifting contract: holds the
// currently-selected assessment in state, passes its id down, updates it
// when OrgDashboard reports a switch via onSwitchAssessment. Exercises the
// real switch-and-refetch cycle without dragging in App.tsx's auth/login
// machinery, which is unrelated to what these tests check.
//
// Q.0: the role is a prop now, because it picks the default view. The G.4
// tests below ran as customer_poc, whose default became the environment
// view; they pass msp_admin so they still exercise the assessment view
// they were written for (the switcher behaves the same in both).
function Harness({
  initial,
  role = "msp_admin",
  onNavigate = () => {},
}: {
  initial: Assessment | null;
  role?: string;
  onNavigate?: (target: unknown) => void;
}) {
  const [assessment, setAssessment] = useState<Assessment | null>(initial);
  return (
    <OrgDashboard
      orgId="org1"
      assessmentId={assessment?.id ?? null}
      currentUserRole={role}
      onSwitchAssessment={setAssessment}
      onNavigate={onNavigate}
    />
  );
}

describe("OrgDashboard assessment switcher (G.4)", () => {
  // Neither this file nor vite.config.ts registers @testing-library/react's
  // auto-cleanup or a mock-clearing hook (no setupFiles, no clearMocks) —
  // without this, every test after the first inherits the prior test's
  // still-mounted DOM (screen queries aren't scoped per-render) and the
  // prior test's spy call history (mockResolvedValue replaces the
  // implementation but never clears .mock.calls). Scoped to this file
  // rather than vite.config.ts's global test block, same reasoning as the
  // @vitest-environment jsdom comment above.
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

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

describe("OrgDashboard views (Q.0)", () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  beforeEach(() => {
    vi.mocked(api.getDashboard).mockResolvedValue(makeDashboardData());
    vi.mocked(api.getEnvironment).mockResolvedValue(makeEnvironment());
  });

  it("an engineer lands on the environment view, an admin on the assessment view", async () => {
    const a1 = makeAssessment();
    vi.mocked(api.getAssessments).mockResolvedValue([a1]);

    render(<Harness initial={a1} role="msp_engineer" />);
    expect(await screen.findByText("Devices in boundary")).toBeTruthy();
    expect(screen.queryByText("Family Completion")).toBeNull();
    cleanup();

    vi.mocked(api.getEnvironment).mockClear();
    render(<Harness initial={a1} role="msp_admin" />);
    expect(await screen.findByText("Family Completion")).toBeTruthy();
    expect(screen.queryByText("Devices in boundary")).toBeNull();
    expect(api.getEnvironment).not.toHaveBeenCalled();
  });

  it("either view is one click away for any role", async () => {
    const a1 = makeAssessment();
    vi.mocked(api.getAssessments).mockResolvedValue([a1]);
    render(<Harness initial={a1} role="msp_admin" />);
    await screen.findByText("Family Completion");
    fireEvent.click(screen.getByRole("button", { name: "Environment" }));
    expect(await screen.findByText("Devices in boundary")).toBeTruthy();
  });

  it("shows the server's total, not the length of the capped rows", async () => {
    const a1 = makeAssessment();
    vi.mocked(api.getAssessments).mockResolvedValue([a1]);
    vi.mocked(api.getEnvironment).mockResolvedValue(
      makeEnvironment({
        devices_in_boundary: emptyList({
          total: 120,
          cap: 2,
          rows: [
            { id: "d1", entity_type: "device", natural_key: "D1", display_name: "ws01", sub_label: null, scope_category: null, source: "manual" },
            { id: "d2", entity_type: "device", natural_key: "D2", display_name: "ws02", sub_label: null, scope_category: null, source: "manual" },
          ],
        }),
      }),
    );
    render(<Harness initial={a1} role="msp_engineer" />);
    expect(await screen.findByText("Showing the first 2 of 120.", { exact: false })).toBeTruthy();
    expect(screen.getByLabelText("Devices in boundary, 120")).toBeTruthy();
  });

  it("renders for an org with no assessment and an empty profile, and invites completion", async () => {
    vi.mocked(api.getAssessments).mockResolvedValue([]);
    const onNavigate = vi.fn();
    render(<Harness initial={null} role="msp_engineer" onNavigate={onNavigate} />);
    expect(await screen.findByText("Acme Corp")).toBeTruthy();
    expect(api.getDashboard).not.toHaveBeenCalled();
    expect(screen.getByText("No assessment yet, so no score.", { exact: false })).toBeTruthy();
    expect(screen.getByText("No assessment yet.")).toBeTruthy();
    expect(screen.getAllByText("Missing")).toHaveLength(2);
    expect(screen.getByText("Evidence tasks appear once an assessment exists.", { exact: false })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Complete the org profile" }));
    expect(onNavigate).toHaveBeenCalledWith({ category: "scope", scopeTab: "profile" });
  });

  it("an org with no assessment opens on the environment view even for an admin", async () => {
    vi.mocked(api.getAssessments).mockResolvedValue([]);
    render(<Harness initial={null} role="msp_admin" />);
    expect(await screen.findByText("Devices in boundary")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Assessment" }));
    expect(await screen.findByText("No assessments yet", { exact: false })).toBeTruthy();
  });

  it("says outside the boundary, never active, and draws no radar with nothing to plot", async () => {
    const a1 = makeAssessment();
    vi.mocked(api.getAssessments).mockResolvedValue([a1]);
    vi.mocked(api.getEnvironment).mockResolvedValue(makeEnvironment({ devices_out_of_boundary: 3 }));
    const { container } = render(<Harness initial={a1} role="msp_engineer" />);
    expect(await screen.findByRole("button", { name: "3 devices outside the boundary" })).toBeTruthy();
    expect(screen.queryByText(/active device/)).toBeNull();
    expect(container.querySelector(".radar-chart")).toBeNull();
    expect(screen.getByText("No controls in this assessment yet.")).toBeTruthy();
  });

  it("titles the task list for what it holds and says what it leaves out", async () => {
    const a1 = makeAssessment();
    vi.mocked(api.getAssessments).mockResolvedValue([a1]);
    render(<Harness initial={a1} role="msp_engineer" />);
    expect(await screen.findByText("Document reviews and open evidence tasks")).toBeTruthy();
    expect(screen.getByText("Not included here: POA&M items and periodic review cycles.", { exact: false })).toBeTruthy();
  });

  it("renders the audit log quick link only for a role its endpoint serves", async () => {
    const a1 = makeAssessment();
    vi.mocked(api.getAssessments).mockResolvedValue([a1]);
    render(<Harness initial={a1} role="msp_engineer" />);
    await screen.findByText("Devices in boundary");
    expect(screen.queryByRole("button", { name: "Audit log" })).toBeNull();
    cleanup();
    render(<Harness initial={a1} role="msp_admin" />);
    await screen.findByText("Family Completion");
    fireEvent.click(screen.getByRole("button", { name: "Environment" }));
    expect(await screen.findByRole("button", { name: "Audit log" })).toBeTruthy();
  });
});
