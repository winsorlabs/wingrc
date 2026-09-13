// @vitest-environment jsdom
//
// Coverage for the SPRS submission record-keeping panel: the empty state
// (no submission yet, never framed as an error or an incomplete state),
// the current/history split, recording a new submission, and voiding one
// without it disappearing from history.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import type { Contact, SprsSubmission } from "../types";
import { SprsSubmissionsPanel } from "./SprsSubmissionsPanel";

vi.mock("../api", () => ({
  api: {
    listSprsSubmissions: vi.fn(),
    getContacts: vi.fn(),
    recordSprsSubmission: vi.fn(),
    voidSprsSubmission: vi.fn(),
  },
}));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function makeContact(overrides: Partial<Contact> = {}): Contact {
  return {
    id: "c1",
    org_id: "org1",
    name: "Jane Filer",
    email: "jane@example.com",
    affiliation: "customer",
    phone: null,
    role_title: null,
    contract_ref: null,
    notes: null,
    documentation_roles: [],
    ...overrides,
  };
}

function makeSubmission(overrides: Partial<SprsSubmission> = {}): SprsSubmission {
  return {
    id: "s1",
    org_id: "org1",
    assessment_id: null,
    score: 95,
    submitted_date: "2026-03-01",
    submitted_by_contact_id: "c1",
    submitted_by_name: "Jane Filer",
    submitted_by_email: "jane@example.com",
    note: null,
    created_by: "u1",
    created_at: "2026-03-01T00:00:00Z",
    voided_at: null,
    voided_reason: null,
    ...overrides,
  };
}

describe("SprsSubmissionsPanel — empty state", () => {
  it("shows a plain empty state, not an error, when nothing is on file", async () => {
    vi.mocked(api.listSprsSubmissions).mockResolvedValue([]);
    vi.mocked(api.getContacts).mockResolvedValue([]);

    render(<SprsSubmissionsPanel orgId="org1" canWrite={true} />);

    await screen.findByText("No SPRS submission on file for this org yet.");
    expect(screen.queryByText(/error/i)).toBeNull();
  });
});

describe("SprsSubmissionsPanel — history", () => {
  it("shows the current submission and marks older ones superseded", async () => {
    vi.mocked(api.listSprsSubmissions).mockResolvedValue([
      makeSubmission({ id: "newer", submitted_date: "2026-03-01", score: 95 }),
      makeSubmission({ id: "older", submitted_date: "2025-03-01", score: 80 }),
    ]);
    vi.mocked(api.getContacts).mockResolvedValue([makeContact()]);

    render(<SprsSubmissionsPanel orgId="org1" canWrite={true} />);

    await screen.findByText("Current submission");
    expect(screen.getByText("Current")).toBeTruthy();
    expect(screen.getByText("Superseded")).toBeTruthy();
  });

  it("shows a voided submission's badge without hiding it from history", async () => {
    vi.mocked(api.listSprsSubmissions).mockResolvedValue([
      makeSubmission({ id: "s1", voided_at: "2026-04-01T00:00:00Z", voided_reason: "typo" }),
    ]);
    vi.mocked(api.getContacts).mockResolvedValue([]);

    render(<SprsSubmissionsPanel orgId="org1" canWrite={true} />);

    await screen.findByText("Voided");
    expect(screen.getByText("No SPRS submission on file for this org yet.")).toBeTruthy();
  });
});

describe("SprsSubmissionsPanel — recording", () => {
  it("records a new submission and reloads the history", async () => {
    vi.mocked(api.listSprsSubmissions).mockResolvedValue([]);
    vi.mocked(api.getContacts).mockResolvedValue([makeContact()]);
    vi.mocked(api.recordSprsSubmission).mockResolvedValue(makeSubmission());

    render(<SprsSubmissionsPanel orgId="org1" canWrite={true} />);
    await screen.findByText("No SPRS submission on file for this org yet.");

    fireEvent.click(screen.getByRole("button", { name: "Record a submission" }));
    await screen.findByText("Record an SPRS submission");

    fireEvent.change(screen.getByLabelText("Score"), { target: { value: "95" } });
    fireEvent.change(screen.getByLabelText("Date submitted"), { target: { value: "2026-03-01" } });
    fireEvent.change(screen.getByLabelText("Submitted by"), { target: { value: "c1" } });

    vi.mocked(api.listSprsSubmissions).mockResolvedValue([makeSubmission()]);
    fireEvent.click(screen.getByRole("button", { name: "Record submission" }));

    await waitFor(() =>
      expect(api.recordSprsSubmission).toHaveBeenCalledWith("org1", {
        score: 95,
        submitted_date: "2026-03-01",
        submitted_by_contact_id: "c1",
        note: undefined,
      })
    );
    await screen.findByText("Current submission");
  });

  it("does not show the record button for a read-only caller", async () => {
    vi.mocked(api.listSprsSubmissions).mockResolvedValue([]);
    vi.mocked(api.getContacts).mockResolvedValue([]);

    render(<SprsSubmissionsPanel orgId="org1" canWrite={false} />);
    await screen.findByText("No SPRS submission on file for this org yet.");

    expect(screen.queryByRole("button", { name: "Record a submission" })).toBeNull();
  });
});

describe("SprsSubmissionsPanel — voiding", () => {
  it("voids a submission and the row survives with a Voided badge", async () => {
    vi.mocked(api.listSprsSubmissions).mockResolvedValue([makeSubmission()]);
    vi.mocked(api.getContacts).mockResolvedValue([makeContact()]);
    vi.mocked(api.voidSprsSubmission).mockResolvedValue(
      makeSubmission({ voided_at: "2026-04-01T00:00:00Z", voided_reason: "wrong score" })
    );

    render(<SprsSubmissionsPanel orgId="org1" canWrite={true} />);
    await screen.findByText("Current submission");

    fireEvent.click(screen.getByRole("button", { name: "Void" }));
    await screen.findByText("Void this submission?");
    fireEvent.change(screen.getByLabelText("Reason"), { target: { value: "wrong score" } });

    vi.mocked(api.listSprsSubmissions).mockResolvedValue([
      makeSubmission({ voided_at: "2026-04-01T00:00:00Z", voided_reason: "wrong score" }),
    ]);
    fireEvent.click(screen.getByRole("button", { name: "Void submission" }));

    await waitFor(() =>
      expect(api.voidSprsSubmission).toHaveBeenCalledWith("org1", "s1", "wrong score")
    );
    await screen.findByText("Voided");
  });
});
