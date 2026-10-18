// @vitest-environment jsdom
//
// L.4: an objective's evidence offers the lists tagged with its practice --
// a person attaches one -- says why a list is not offered, and says when a
// linked list has moved on, with moving forward a deliberate act.
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import type { ListEvidenceCandidate } from "../types";
import { ObjectiveLists } from "./ObjectiveLists";

vi.mock("../api", () => ({
  api: { stateLists: vi.fn(), linkList: vi.fn(), moveListForward: vi.fn(), unlinkList: vi.fn() },
}));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function cand(over: Partial<ListEvidenceCandidate>): ListEvidenceCandidate {
  return {
    list_id: "l", list_key: "visitor-log", title: "Visitor Log", current_version_id: "v3",
    current_version_number: 3, offered: true, not_offered: null, linked_version_id: null,
    linked_version_number: null, evidence_id: null, drift: null, ...over,
  };
}

function mount(rows: ListEvidenceCandidate[], canWrite = true) {
  vi.mocked(api.stateLists).mockResolvedValue(rows);
  const onCount = vi.fn();
  render(<ObjectiveLists orgId="o" assessmentId="a" controlStateId="cs" canWrite={canWrite} onCountChange={onCount} />);
  return onCount;
}

describe("ObjectiveLists", () => {
  it("offers a populated list and attaches it only when asked", async () => {
    const onCount = mount([cand({})]);
    const attach = await screen.findByRole("button", { name: "Attach version 3" });
    expect(api.linkList).not.toHaveBeenCalled();
    expect(onCount).toHaveBeenLastCalledWith(0);
    vi.mocked(api.linkList).mockResolvedValue([cand({ linked_version_id: "v3", linked_version_number: 3 })]);
    fireEvent.click(attach);
    await screen.findByText("linked at version 3");
    expect(api.linkList).toHaveBeenCalledWith("o", "a", "cs", "l");
    expect(onCount).toHaveBeenLastCalledWith(1);
  });

  it("says why a list is not offered, instead of leaving it out", async () => {
    mount([cand({ offered: false, not_offered: "still showing template examples (2 of 2 rows)" })]);
    await screen.findByText("Not offered as evidence: still showing template examples (2 of 2 rows).");
    expect(screen.queryByRole("button", { name: /Attach/ })).toBeNull();
  });

  it("says the list moved on, and moves the link forward only when asked", async () => {
    const drift = "List version 3 is available. This evidence stays on version 1 until someone moves it forward.";
    mount([cand({ linked_version_id: "v1", linked_version_number: 1, drift })]);
    await screen.findByText(drift);
    expect(screen.getByText("linked at version 1")).toBeTruthy();
    vi.mocked(api.moveListForward).mockResolvedValue([cand({ linked_version_id: "v3", linked_version_number: 3 })]);
    fireEvent.click(screen.getByRole("button", { name: "Move to version 3" }));
    await screen.findByText("linked at version 3");
    expect(screen.queryByText(drift)).toBeNull();
  });

  it("shows a read-only role the links and drift, with nothing to change them", async () => {
    const drift = "List version 3 is available. This evidence stays on version 1 until someone moves it forward.";
    mount([cand({ linked_version_id: "v1", linked_version_number: 1, drift }), cand({ list_id: "m", title: "Other" })], false);
    await screen.findByText(drift);
    expect(screen.queryAllByRole("button")).toHaveLength(0);
  });

  it("shows nothing when no list serves this practice", async () => {
    const onCount = mount([]);
    await vi.waitFor(() => expect(onCount).toHaveBeenCalledWith(0));
    expect(screen.queryByLabelText("Lists for this practice")).toBeNull();
  });
});
