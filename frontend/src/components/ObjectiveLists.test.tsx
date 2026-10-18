// @vitest-environment jsdom
//
// L.4: an objective's evidence offers the lists tagged with its practice --
// a person attaches one -- says why a list is not offered, and says when a
// linked list has moved on, with moving forward a deliberate act.
//
// L.10: these assert what the screen shows, not what the API returned. The
// L.4 tests passed while the screen drew an unfilled list exactly like an
// attached one, and on live that read as attached.
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
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
    await screen.findByText(
      "Visitor Log (version 3) is tagged with this practice but is not attached, and cannot be: still showing template examples (2 of 2 rows).",
    );
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

  it("never draws a list that is not attached the way it draws an attached one", async () => {
    // The live case of 2026-10-18 beside an attached list and an offered one.
    mount([
      cand({ list_id: "att", title: "Badge Register", linked_version_id: "v3", linked_version_number: 3 }),
      cand({ list_id: "off", title: "Visitor Log" }),
      cand({
        list_id: "no", title: "Physical Access Device Register", current_version_number: 1,
        offered: false, not_offered: "still showing template examples (2 of 2 rows)",
      }),
    ]);
    const attached = await screen.findByRole("list", { name: "Lists attached as evidence" });
    expect(within(attached).getByText("Badge Register")).toBeTruthy();
    expect(within(attached).queryByText(/Visitor Log|Physical Access/)).toBeNull();
    expect(screen.getAllByText("List")).toHaveLength(1);

    const others = screen.getByRole("list", { name: "Lists not attached" });
    const unfilled = within(others).getByText(/Physical Access Device Register/);
    expect(unfilled.textContent).toContain("is not attached");
    expect(unfilled.closest(".ev-item")).toBeNull();
    expect(within(others).getByText(/Visitor Log/).closest(".ev-item")).toBeNull();
  });

  it("with only an unfilled list, shows nothing that reads as attached", async () => {
    mount([cand({ offered: false, not_offered: "still showing template examples (2 of 2 rows)" })]);
    await screen.findByRole("list", { name: "Lists not attached" });
    expect(screen.queryByRole("list", { name: "Lists attached as evidence" })).toBeNull();
    expect(screen.queryByText("List")).toBeNull();
    expect(document.querySelector(".ev-lists .ev-item")).toBeNull();
  });

  it("shows nothing when no list serves this practice", async () => {
    const onCount = mount([]);
    await vi.waitFor(() => expect(onCount).toHaveBeenCalledWith(0));
    expect(screen.queryByLabelText("Lists for this practice")).toBeNull();
  });
});
