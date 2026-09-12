// @vitest-environment jsdom
//
// Coverage for the read-only Scheduled Jobs admin panel (job scheduler,
// D.3's second infrastructure prerequisite): renders a job that has never
// run, a succeeded run, and a failed run (with its error surfaced), and
// the empty-registry state.
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import type { ScheduledJob } from "../types";
import { ScheduledJobsPanel } from "./ScheduledJobsPanel";

vi.mock("../api", () => ({
  api: {
    listScheduledJobs: vi.fn(),
  },
}));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function makeJob(overrides: Partial<ScheduledJob> = {}): ScheduledJob {
  return {
    job_name: "expire_stale_invites",
    interval_seconds: 3600,
    last_run: null,
    next_due_at: null,
    ...overrides,
  };
}

describe("ScheduledJobsPanel", () => {
  it("shows a never-run job as due now, with no error column", async () => {
    vi.mocked(api.listScheduledJobs).mockResolvedValue([makeJob()]);

    render(<ScheduledJobsPanel />);

    await screen.findByText("expire_stale_invites");
    expect(screen.getByText("Never run")).toBeTruthy();
    expect(screen.getByText("Due now")).toBeTruthy();
    expect(screen.getByText("never run")).toBeTruthy();
  });

  it("shows a succeeded run's outcome without an error", async () => {
    vi.mocked(api.listScheduledJobs).mockResolvedValue([
      makeJob({
        last_run: {
          started_at: "2026-09-12T10:00:00Z",
          finished_at: "2026-09-12T10:00:01Z",
          status: "succeeded",
          error: null,
          result: { expired_count: 3 },
          worker_id: "worker-1:123",
        },
        next_due_at: "2026-09-12T11:00:00Z",
      }),
    ]);

    render(<ScheduledJobsPanel />);

    await screen.findByText("succeeded");
    expect(screen.queryByText(/Orphaned|terminated/)).toBeNull();
  });

  it("surfaces the error for a failed run", async () => {
    vi.mocked(api.listScheduledJobs).mockResolvedValue([
      makeJob({
        last_run: {
          started_at: "2026-09-12T10:00:00Z",
          finished_at: "2026-09-12T10:00:01Z",
          status: "failed",
          error: "Orphaned: worker process terminated without completing this run.",
          result: null,
          worker_id: "worker-1:123",
        },
        next_due_at: "2026-09-12T11:00:00Z",
      }),
    ]);

    render(<ScheduledJobsPanel />);

    await screen.findByText("failed");
    expect(
      screen.getByText("Orphaned: worker process terminated without completing this run.")
    ).toBeTruthy();
  });

  it("shows the empty state when no jobs are registered", async () => {
    vi.mocked(api.listScheduledJobs).mockResolvedValue([]);

    render(<ScheduledJobsPanel />);

    await screen.findByText("No jobs registered.");
  });

  it("surfaces a fetch error", async () => {
    vi.mocked(api.listScheduledJobs).mockRejectedValue(new Error("network down"));

    render(<ScheduledJobsPanel />);

    await screen.findByText("network down");
  });
});
