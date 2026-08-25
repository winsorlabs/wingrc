// @vitest-environment jsdom
//
// Regression coverage for the cached-assessment auto-resume bug: OrgPicker's
// selectOrg() used to call onEnterBoard() unconditionally whenever
// getCachedAssessmentId() found a hit, even when the user had explicitly
// navigated back to the picker — bouncing them straight back into the board
// and making the assessment list / "Start New Assessment" unreachable for
// any org that had ever been opened. Fixed via the skipAutoResume prop
// (App.tsx passes org !== null — true once an org has already been open
// this session). See App.tsx's and OrgPicker.tsx's own comments at the prop
// definition/call site for the full incident context.
//
// Same isolation setup as OrgDashboard.test.tsx (jsdom scoped to this file,
// afterEach(cleanup) + vi.clearAllMocks() — neither this file nor
// vite.config.ts's global test block registers either automatically).
import { render, screen, waitFor, fireEvent, cleanup } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api, getCachedAssessmentId } from "../api";
import type { Assessment, AuthUser, Org } from "../types";
import { OrgPicker } from "./OrgPicker";

vi.mock("../api", () => ({
  api: {
    getOrgs: vi.fn(),
    getFrameworks: vi.fn(),
    getAssessments: vi.fn(),
    createOrg: vi.fn(),
    createAssessment: vi.fn(),
  },
  getCachedAssessmentId: vi.fn(),
  setCachedAssessmentId: vi.fn(),
}));

function makeUser(overrides: Partial<AuthUser> = {}): AuthUser {
  return {
    id: "u1",
    org_id: "org1",
    email: "jarrod@example.com",
    display_name: "Jarrod",
    role: "customer_poc",
    login_method: "local",
    mfa_enrolled: false,
    ...overrides,
  };
}

function makeOrg(overrides: Partial<Org> = {}): Org {
  return { id: "org1", name: "Acme", created_at: "2026-01-01T00:00:00Z", ...overrides };
}

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

describe("OrgPicker cached-assessment auto-resume", () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  beforeEach(() => {
    vi.mocked(api.getFrameworks).mockResolvedValue([]);
  });

  it("fresh login (skipAutoResume unset) auto-resumes into the cached assessment for a single-org role", async () => {
    const org = makeOrg();
    const cached = makeAssessment({ id: "a1", name: "Cached" });
    vi.mocked(api.getOrgs).mockResolvedValue([org]);
    vi.mocked(api.getAssessments).mockResolvedValue([cached]);
    vi.mocked(getCachedAssessmentId).mockReturnValue("a1");
    const onEnterBoard = vi.fn();

    render(
      <OrgPicker
        currentUser={makeUser({ role: "customer_poc" })}
        canWrite={true}
        onEnterBoard={onEnterBoard}
        onEnterOnboarding={vi.fn()}
        onOpenSettings={vi.fn()}
      />
    );

    await waitFor(() => expect(onEnterBoard).toHaveBeenCalledWith(org, cached));
  });

  it("explicit return to the picker (skipAutoResume=true) shows the real list instead of bouncing back", async () => {
    const org = makeOrg();
    const cached = makeAssessment({ id: "a1", name: "Cached" });
    vi.mocked(api.getOrgs).mockResolvedValue([org]);
    vi.mocked(api.getAssessments).mockResolvedValue([cached]);
    vi.mocked(getCachedAssessmentId).mockReturnValue("a1");
    const onEnterBoard = vi.fn();

    render(
      <OrgPicker
        currentUser={makeUser({ role: "customer_poc" })}
        canWrite={true}
        skipAutoResume={true}
        onEnterBoard={onEnterBoard}
        onEnterOnboarding={vi.fn()}
        onOpenSettings={vi.fn()}
      />
    );

    await screen.findByText("Cached");
    expect(onEnterBoard).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: /Start New Assessment/ })).toBeTruthy();
  });

  it("hides the multi-org branch's Start New Assessment button for a role without canWrite", async () => {
    const orgA = makeOrg({ id: "orgA", name: "Org A" });
    const orgB = makeOrg({ id: "orgB", name: "Org B" });
    vi.mocked(api.getOrgs).mockResolvedValue([orgA, orgB]);
    vi.mocked(api.getAssessments).mockResolvedValue([]);
    vi.mocked(getCachedAssessmentId).mockReturnValue(null);

    render(
      <OrgPicker
        currentUser={makeUser({ role: "c3pao_assessor" })}
        canWrite={false}
        onEnterBoard={vi.fn()}
        onEnterOnboarding={vi.fn()}
        onOpenSettings={vi.fn()}
      />
    );

    fireEvent.click(await screen.findByText("Org A"));

    await screen.findByText("No assessments yet.");
    expect(screen.queryByRole("button", { name: /Start New Assessment/ })).toBeNull();
  });
});
