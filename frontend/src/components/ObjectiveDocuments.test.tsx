// @vitest-environment jsdom
//
// Item X: an objective's evidence offers the documents tagged with it -- a
// person attaches one -- and says, in its own words, when the version
// attached is superseded. These assert what the screen shows (item Z), from
// the start: L.10 found an attached item and an unattached one sharing a
// row, and this component must not repeat it.
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import type { DocumentEvidenceCandidate } from "../types";
import { ObjectiveDocuments } from "./ObjectiveDocuments";

vi.mock("../api", () => ({
  api: {
    stateDocuments: vi.fn(), linkDocument: vi.fn(), moveDocumentForward: vi.fn(), unlinkDocument: vi.fn(),
  },
}));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const SUPERSEDED =
  "Version 1, attached here, is superseded: version 2 is the approved one, and version 1 may no longer be in force. This evidence stays on version 1 until someone moves it forward.";

function cand(over: Partial<DocumentEvidenceCandidate>): DocumentEvidenceCandidate {
  return {
    document_id: "d", doc_id: "AC-POL-001", title: "Access Control Policy", approved_version_id: "v2",
    approved_version_number: 2, offered: true, not_offered: null, linked_version_id: null,
    linked_version_number: null, linked_superseded: false, evidence_id: null, superseded: null, ...over,
  };
}

function mount(rows: DocumentEvidenceCandidate[], canWrite = true) {
  vi.mocked(api.stateDocuments).mockResolvedValue(rows);
  const onCount = vi.fn();
  render(<ObjectiveDocuments orgId="o" assessmentId="a" controlStateId="cs" canWrite={canWrite} onCountChange={onCount} />);
  return onCount;
}

describe("ObjectiveDocuments", () => {
  it("offers an approved document and attaches it only when asked", async () => {
    const onCount = mount([cand({})]);
    const offer = await screen.findByText(/is approved, tagged with this objective, and not attached/);
    expect(offer.textContent).toContain("AC-POL-001 — Access Control Policy (version 2)");
    expect(api.linkDocument).not.toHaveBeenCalled();
    expect(onCount).toHaveBeenLastCalledWith(0);
    vi.mocked(api.linkDocument).mockResolvedValue([cand({ linked_version_id: "v2", linked_version_number: 2 })]);
    fireEvent.click(screen.getByRole("button", { name: "Attach version 2" }));
    const attached = await screen.findByRole("list", { name: "Documents attached as evidence" });
    expect(within(attached).getByText("version 2")).toBeTruthy();
    expect(api.linkDocument).toHaveBeenCalledWith("o", "a", "cs", "d");
    expect(onCount).toHaveBeenLastCalledWith(1);
  });

  it("says why a document is not offered, instead of leaving it out", async () => {
    mount([cand({
      approved_version_id: null, approved_version_number: null, offered: false,
      not_offered: "no version of it has been approved yet. Publish one first",
    })]);
    await screen.findByText(
      "AC-POL-001 — Access Control Policy is tagged with this objective but is not attached, and cannot be: no version of it has been approved yet. Publish one first.",
    );
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("says a superseded version is superseded, and moves it forward only when asked", async () => {
    mount([cand({ linked_version_id: "v1", linked_version_number: 1, linked_superseded: true, superseded: SUPERSEDED })]);
    await screen.findByText(SUPERSEDED);
    const attached = screen.getByRole("list", { name: "Documents attached as evidence" });
    expect(within(attached).getByText("superseded")).toBeTruthy();
    expect(within(attached).getByText("version 1")).toBeTruthy();
    expect(SUPERSEDED).not.toMatch(/is available/);
    vi.mocked(api.moveDocumentForward).mockResolvedValue([cand({ linked_version_id: "v2", linked_version_number: 2 })]);
    fireEvent.click(screen.getByRole("button", { name: "Move to version 2" }));
    await screen.findByText("version 2");
    expect(screen.queryByText(SUPERSEDED)).toBeNull();
    expect(screen.queryByText("superseded")).toBeNull();
  });

  it("does not offer a move forward the server would refuse, and says why", async () => {
    mount([cand({
      linked_version_id: "v1", linked_version_number: 1, linked_superseded: true, superseded: SUPERSEDED,
      offered: false, not_offered: "it is no longer tagged with this objective",
    })]);
    await screen.findByText(SUPERSEDED);
    expect(screen.queryByRole("button", { name: /Move to/ })).toBeNull();
    expect(screen.getByText("It cannot be moved forward: it is no longer tagged with this objective.")).toBeTruthy();
  });

  it("shows a read-only role the attachment and the superseded notice, with nothing to change them", async () => {
    mount([
      cand({ linked_version_id: "v1", linked_version_number: 1, linked_superseded: true, superseded: SUPERSEDED }),
      cand({ document_id: "e", doc_id: "IR-PLAN-001", title: "Incident Response Plan" }),
    ], false);
    await screen.findByText(SUPERSEDED);
    expect(screen.queryAllByRole("button")).toHaveLength(0);
  });

  it("never draws a document that is not attached the way it draws an attached one", async () => {
    mount([
      cand({ document_id: "att", doc_id: "AC-POL-001", linked_version_id: "v2", linked_version_number: 2 }),
      cand({ document_id: "off", doc_id: "IR-PLAN-001", title: "Incident Response Plan" }),
      cand({
        document_id: "no", doc_id: "PE-PROC-001", title: "Visitor Procedure", approved_version_id: null,
        approved_version_number: null, offered: false, not_offered: "no version of it has been approved yet. Publish one first",
      }),
    ]);
    const attached = await screen.findByRole("list", { name: "Documents attached as evidence" });
    expect(within(attached).getByText("AC-POL-001 — Access Control Policy")).toBeTruthy();
    expect(within(attached).queryByText(/IR-PLAN-001|PE-PROC-001/)).toBeNull();
    expect(screen.getAllByText("Document")).toHaveLength(1);
    expect(screen.getAllByRole("button", { name: /^Remove/ })).toHaveLength(1);

    const others = screen.getByRole("list", { name: "Documents not attached" });
    for (const name of [/IR-PLAN-001/, /PE-PROC-001/]) {
      const row = within(others).getByText(name);
      expect(row.textContent).toContain("not attached");
      expect(row.closest(".ev-item")).toBeNull();
    }
  });

  it("with only an unapproved document, shows nothing that reads as attached", async () => {
    mount([cand({ approved_version_id: null, approved_version_number: null, offered: false, not_offered: "x" })]);
    await screen.findByRole("list", { name: "Documents not attached" });
    expect(screen.queryByRole("list", { name: "Documents attached as evidence" })).toBeNull();
    expect(screen.queryByText("Document")).toBeNull();
    expect(document.querySelector(".ev-lists .ev-item")).toBeNull();
  });

  it("shows nothing when no document is tagged with this objective", async () => {
    const onCount = mount([]);
    await vi.waitFor(() => expect(onCount).toHaveBeenCalledWith(0));
    expect(screen.queryByLabelText("Documents for this objective")).toBeNull();
  });
});
