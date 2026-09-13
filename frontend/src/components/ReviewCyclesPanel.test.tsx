// @vitest-environment jsdom
//
// Coverage for the periodic review & attestation panel: the empty state,
// the MSP-only "Open a cycle now" button gated by role, and the attest
// flow using the authenticated caller's own identity (no name field is
// ever submitted from this UI -- see api.ts's attestReviewCycle).
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import type { ReviewCycle, ReviewCycleDetail } from "../types";
import { ReviewCyclesPanel } from "./ReviewCyclesPanel";

vi.mock("../api", () => ({
  api: {
    listReviewCycles: vi.fn(),
    getReviewCycle: vi.fn(),
    openReviewCycle: vi.fn(),
    attestReviewCycle: vi.fn(),
    flagReviewCycleItem: vi.fn(),
    resolveReviewCycleFlag: vi.fn(),
  },
}));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function makeCycle(overrides: Partial<ReviewCycle> = {}): ReviewCycle {
  return {
    id: "c1",
    org_id: "org1",
    opened_at: "2026-09-01T00:00:00Z",
    due_at: "2026-09-22T00:00:00Z",
    closed_at: null,
    status: "open",
    cadence_months: 6,
    opened_by: "scheduler",
    ...overrides,
  };
}

function makeDetail(overrides: Partial<ReviewCycleDetail> = {}): ReviewCycleDetail {
  return {
    ...makeCycle(),
    items: [
      { id: "i1", subject_type: "user", natural_key: "jane.doe", scope_category: "CUI Asset", attributes: {} },
    ],
    reviewers: [
      {
        id: "r1", user_id: "u1", reviewer_name: "Jane Filer", reviewer_email: "jane@example.com",
        reviewer_side: "client", status: "requested", requested_at: "2026-09-01T00:00:00Z",
        viewed_at: null, attested_at: null, comment: null,
      },
    ],
    flags: [],
    ...overrides,
  };
}

describe("ReviewCyclesPanel — list", () => {
  it("shows the empty state when no cycles exist", async () => {
    vi.mocked(api.listReviewCycles).mockResolvedValue([]);
    render(<ReviewCyclesPanel orgId="org1" currentUserId="u1" currentUserRole="msp_admin" />);
    await screen.findByText("No review cycles yet.");
  });

  it("an msp_admin sees Open a cycle now; a customer_poc does not", async () => {
    vi.mocked(api.listReviewCycles).mockResolvedValue([]);
    render(<ReviewCyclesPanel orgId="org1" currentUserId="u1" currentUserRole="msp_admin" />);
    await screen.findByText("No review cycles yet.");
    expect(screen.getByRole("button", { name: "Open a cycle now" })).toBeTruthy();

    cleanup();
    render(<ReviewCyclesPanel orgId="org1" currentUserId="u2" currentUserRole="customer_poc" />);
    await screen.findByText("No review cycles yet.");
    expect(screen.queryByRole("button", { name: "Open a cycle now" })).toBeNull();
  });

  it("opening a cycle calls the API and reloads the list", async () => {
    vi.mocked(api.listReviewCycles).mockResolvedValue([]);
    vi.mocked(api.openReviewCycle).mockResolvedValue(makeCycle());

    render(<ReviewCyclesPanel orgId="org1" currentUserId="u1" currentUserRole="msp_admin" />);
    await screen.findByText("No review cycles yet.");

    vi.mocked(api.listReviewCycles).mockResolvedValue([makeCycle()]);
    fireEvent.click(screen.getByRole("button", { name: "Open a cycle now" }));

    await screen.findByText("open");
    expect(api.openReviewCycle).toHaveBeenCalledWith("org1");
  });
});

describe("ReviewCyclesPanel — detail & attest", () => {
  it("drills into a cycle and shows its items/reviewers", async () => {
    vi.mocked(api.listReviewCycles).mockResolvedValue([makeCycle()]);
    vi.mocked(api.getReviewCycle).mockResolvedValue(makeDetail());

    render(<ReviewCyclesPanel orgId="org1" currentUserId="u1" currentUserRole="msp_admin" />);
    await screen.findByText("open");
    fireEvent.click(screen.getByText("open"));

    await screen.findByText("jane.doe");
    expect(screen.getByText("Jane Filer")).toBeTruthy();
  });

  it("the current reviewer can attest, and no name field is ever sent", async () => {
    vi.mocked(api.listReviewCycles).mockResolvedValue([makeCycle()]);
    vi.mocked(api.getReviewCycle).mockResolvedValue(makeDetail());
    vi.mocked(api.attestReviewCycle).mockResolvedValue({
      id: "r1", user_id: "u1", reviewer_name: "Jane Filer", reviewer_email: "jane@example.com",
      reviewer_side: "client", status: "attested", requested_at: "2026-09-01T00:00:00Z",
      viewed_at: "2026-09-01T00:00:00Z", attested_at: "2026-09-02T00:00:00Z", comment: null,
    });

    render(<ReviewCyclesPanel orgId="org1" currentUserId="u1" currentUserRole="customer_poc" />);
    await screen.findByText("open");
    fireEvent.click(screen.getByText("open"));

    await screen.findByRole("button", { name: "I have reviewed this list" });
    fireEvent.click(screen.getByRole("button", { name: "I have reviewed this list" }));

    // attestReviewCycle's own signature (orgId, cycleId, comment) carries no
    // name/identity argument at all -- the backend derives it from the
    // session, which this call shape makes structurally impossible to bypass.
    await waitFor(() =>
      expect(api.attestReviewCycle).toHaveBeenCalledWith("org1", "c1", undefined)
    );
  });

  it("does not offer to attest once the caller has already attested", async () => {
    vi.mocked(api.listReviewCycles).mockResolvedValue([makeCycle()]);
    vi.mocked(api.getReviewCycle).mockResolvedValue(
      makeDetail({
        reviewers: [
          {
            id: "r1", user_id: "u1", reviewer_name: "Jane Filer", reviewer_email: "jane@example.com",
            reviewer_side: "client", status: "attested", requested_at: "2026-09-01T00:00:00Z",
            viewed_at: "2026-09-01T00:00:00Z", attested_at: "2026-09-02T00:00:00Z", comment: null,
          },
        ],
      })
    );

    render(<ReviewCyclesPanel orgId="org1" currentUserId="u1" currentUserRole="customer_poc" />);
    await screen.findByText("open");
    fireEvent.click(screen.getByText("open"));

    await screen.findByText("jane.doe");
    expect(screen.queryByRole("button", { name: "I have reviewed this list" })).toBeNull();
  });
});
