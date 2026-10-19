// @vitest-environment jsdom
//
// An objective's evidence section tells the truth about what is attached,
// what used to be, and what happened to it (2026-10-19). These assert what
// the screen tells a person (item Z): the shape of each state, and that
// every sentence stating a result is driven by what the server counted.
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import type {
  ArchivedEvidence,
  ArchivedEvidenceSection,
  DocumentEvidenceCandidate,
  EvidenceRow,
  ListEvidenceCandidate,
} from "../types";
import { EvidenceSection } from "./EvidenceSection";

vi.mock("../api", () => ({
  api: {
    listEvidence: vi.fn(), archivedEvidence: vi.fn(), detachEvidence: vi.fn(),
    stateLists: vi.fn(), linkList: vi.fn(), moveListForward: vi.fn(), unlinkList: vi.fn(),
    stateDocuments: vi.fn(), linkDocument: vi.fn(), moveDocumentForward: vi.fn(), unlinkDocument: vi.fn(),
  },
  assetUrl: (u: string | null) => u,
}));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const NONE: ArchivedEvidenceSection = { items: [], live_evidence: 0, covered_by: [] };

beforeEach(() => {
  vi.mocked(api.listEvidence).mockResolvedValue([]);
  vi.mocked(api.archivedEvidence).mockResolvedValue(NONE);
  vi.mocked(api.stateLists).mockResolvedValue([]);
  vi.mocked(api.stateDocuments).mockResolvedValue([]);
});

function file(over: Partial<EvidenceRow> = {}): EvidenceRow {
  return {
    id: "ev1", title: "Firewall export", artifact_type: "export", kind: "file", reference_location: null,
    note: null, mime_type: "text/csv", file_size_bytes: 2048, collected_at: "2026-09-01T10:00:00Z",
    download_url: "/api/orgs/o/evidence/ev1/download", ...over,
  };
}

function gone(over: Partial<ArchivedEvidence> = {}): ArchivedEvidence {
  return {
    link_id: "lk1", evidence_id: "ev9", source: "evidence", kind: "file", title: "Heimdal console export",
    artifact_type: "export", version_number: null, download_url: "/api/orgs/o/evidence/ev9/download",
    archive_cause: "deactivation", archived_at: "2026-09-12T15:00:00Z", detached_by: null, detached_at: null,
    product_name: "Heimdal", deactivated_by: "Jarrod Winsor", replaced_by_name: null, replaced_by_covers: null,
    ...over,
  };
}

function list(over: Partial<ListEvidenceCandidate> = {}): ListEvidenceCandidate {
  return {
    list_id: "l", list_key: "visitor-log", title: "Visitor Log", current_version_id: "v1",
    current_version_number: 1, offered: true, not_offered: null, linked_version_id: null,
    linked_version_number: null, evidence_id: null, drift: null, is_template: false, ...over,
  };
}

function doc(over: Partial<DocumentEvidenceCandidate> = {}): DocumentEvidenceCandidate {
  return {
    document_id: "d", doc_id: "AC-POL-001", title: "Access Control Policy", approved_version_id: "v2",
    approved_version_number: 2, offered: true, not_offered: null, linked_version_id: null,
    linked_version_number: null, linked_superseded: false, evidence_id: null, superseded: null, ...over,
  };
}

function mount(canWrite = true) {
  render(<EvidenceSection orgId="o" assessmentId="a" controlStateId="cs" canWrite={canWrite} onCountChange={vi.fn()} />);
}

describe("EvidenceSection: one heading, one template sentence", () => {
  it("says Not attached once when a list and a document are both unattached", async () => {
    // The live case of 2026-10-19, which no bench test rendered.
    vi.mocked(api.stateLists).mockResolvedValue([list()]);
    vi.mocked(api.stateDocuments).mockResolvedValue([doc()]);
    mount();
    await screen.findByRole("list", { name: "Documents not attached" });
    expect(screen.getByRole("list", { name: "Lists not attached" })).toBeTruthy();
    expect(screen.getAllByText("Not attached")).toHaveLength(1);
  });

  it("says how to offer a template once, after the rows, however many templates there are", async () => {
    vi.mocked(api.stateLists).mockResolvedValue(["a", "b", "c"].map((k) => list({
      list_id: k, title: `Template ${k}`, offered: false, not_offered: "a template: its rows are examples",
      is_template: true,
    })));
    mount();
    const rows = await screen.findByRole("list", { name: "Lists not attached" });
    expect(within(rows).getAllByRole("listitem")).toHaveLength(3);
    expect(screen.getAllByText(/adopt it into an org and fill it in/)).toHaveLength(1);
    expect(rows.textContent).not.toMatch(/Adopt|adopt/);
  });

  it("says nothing about templates when none is shown", async () => {
    vi.mocked(api.stateLists).mockResolvedValue([list()]);
    mount();
    await screen.findByRole("list", { name: "Lists not attached" });
    expect(screen.queryByText(/adopt it into an org/)).toBeNull();
  });
});

describe("EvidenceSection: detach keeps the artifact", () => {
  it("is labelled Detach, says what happens, and detaches only once confirmed", async () => {
    vi.mocked(api.listEvidence).mockResolvedValue([file()]);
    mount();
    const detach = await screen.findByRole("button", { name: "Detach Firewall export" });
    expect(detach.getAttribute("title")).toMatch(/artifact stays/);
    expect(screen.queryByText("✕")).toBeNull();
    fireEvent.click(detach);
    expect(api.detachEvidence).not.toHaveBeenCalled();
    const confirm = screen.getByRole("group", { name: "Detach Firewall export?" });
    expect(confirm.textContent).toMatch(/The artifact stays/);

    vi.mocked(api.detachEvidence).mockResolvedValue(undefined);
    vi.mocked(api.listEvidence).mockResolvedValue([]);
    vi.mocked(api.archivedEvidence).mockResolvedValue({
      items: [gone({
        evidence_id: "ev1", title: "Firewall export", archive_cause: "detached", product_name: null,
        deactivated_by: null, detached_by: "Jarrod Winsor", detached_at: "2026-10-19T12:00:00Z",
      })],
      live_evidence: 0, covered_by: [],
    });
    fireEvent.click(within(confirm).getByRole("button", { name: "Detach" }));
    const archived = await screen.findByRole("list", { name: "No longer attached" });
    expect(api.detachEvidence).toHaveBeenCalledWith("o", "a", "cs", "ev1");
    expect(within(archived).getByText("(Detached 19 Oct 2026 by Jarrod Winsor)")).toBeTruthy();
  });

  it("never draws an archived artifact the way it draws a live one", async () => {
    vi.mocked(api.listEvidence).mockResolvedValue([file()]);
    vi.mocked(api.archivedEvidence).mockResolvedValue({ items: [gone()], live_evidence: 1, covered_by: [] });
    mount();
    const archived = await screen.findByRole("list", { name: "No longer attached" });
    const live = screen.getByText("Firewall export");
    expect(live.closest(".ev-item")).not.toBeNull();
    const old = within(archived).getByText("Heimdal console export");
    expect(old.closest(".ev-item")).toBeNull();
    expect(old.closest(".ev-list")).toBeNull();
    expect(old.closest("li")?.querySelector(".ev-type-chip, .ev-ref-chip")).toBeNull();
    // Below the live evidence, not among it.
    expect(live.compareDocumentPosition(old) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("gives a read-only role no detach of either kind", async () => {
    vi.mocked(api.listEvidence).mockResolvedValue([file()]);
    vi.mocked(api.archivedEvidence).mockResolvedValue({ items: [gone()], live_evidence: 1, covered_by: [] });
    mount(false);
    await screen.findByRole("list", { name: "No longer attached" });
    expect(screen.queryByRole("button", { name: /Detach/ })).toBeNull();
  });

  it("can detach what a deactivation archived for good, so reactivation does not restore it", async () => {
    vi.mocked(api.archivedEvidence).mockResolvedValue({ items: [gone()], live_evidence: 0, covered_by: [] });
    vi.mocked(api.detachEvidence).mockResolvedValue(undefined);
    mount();
    const archived = await screen.findByRole("list", { name: "No longer attached" });
    expect(within(archived).getByText(/Reactivating Heimdal restores it/)).toBeTruthy();
    const forGood = within(archived).getByRole("button", { name: "Detach for good" });
    expect(forGood.getAttribute("title")).toMatch(/reactivating Heimdal does not bring it back/);
    fireEvent.click(forGood);
    await vi.waitFor(() => expect(api.detachEvidence).toHaveBeenCalledWith("o", "a", "cs", "ev9"));
  });
});

describe("EvidenceSection: why it is no longer attached", () => {
  it("says Replaced by only where a person named the replacement, with who and when", async () => {
    vi.mocked(api.archivedEvidence).mockResolvedValue({
      items: [
        gone({ link_id: "n", replaced_by_name: "Defender", replaced_by_covers: true }),
        gone({ link_id: "u", title: "Heimdal policy export", product_name: "Heimdal", replaced_by_name: null }),
      ],
      live_evidence: 1, covered_by: ["Defender"],
    });
    mount();
    const archived = await screen.findByRole("list", { name: "No longer attached" });
    const [named, unnamed] = within(archived).getAllByRole("listitem");
    expect(named.textContent).toContain(
      "Replaced by Defender — stated by Jarrod Winsor when Heimdal was deactivated, 12 Sep 2026",
    );
    expect(named.textContent).toContain("Defender contributes to this objective.");
    expect(unnamed.textContent).toContain("(Archived 12 Sep 2026 — Heimdal was deactivated)");
    expect(unnamed.textContent).not.toContain("Replaced");
  });

  it("says when the named replacement does not contribute to this objective, without refusing it", async () => {
    vi.mocked(api.archivedEvidence).mockResolvedValue({
      items: [gone({ replaced_by_name: "Defender", replaced_by_covers: false })],
      live_evidence: 0, covered_by: [],
    });
    mount();
    const archived = await screen.findByRole("list", { name: "No longer attached" });
    expect(archived.textContent).toContain("Replaced by Defender");
    expect(archived.textContent).toContain("Defender does not contribute to this objective.");
  });

  it("says a review cycle's record was detached for not attesting", async () => {
    vi.mocked(api.archivedEvidence).mockResolvedValue({
      items: [gone({
        title: "Review cycle record", archive_cause: "attestation_detach", product_name: null,
        deactivated_by: null, detached_by: "cli:review-cycles-detach-unattested",
        detached_at: "2026-10-19T14:05:12Z",
      })],
      live_evidence: 0, covered_by: [],
    });
    mount();
    const archived = await screen.findByRole("list", { name: "No longer attached" });
    expect(archived.textContent).toContain(
      "(Detached 19 Oct 2026: the record of a review cycle that closed without attestation)",
    );
  });
});

describe("EvidenceSection: a tool taken away with nothing in its place is loud", () => {
  const unnamed = { items: [gone()], covered_by: [] as string[] };

  it("says so when the server counted no live evidence", async () => {
    vi.mocked(api.archivedEvidence).mockResolvedValue({ ...unnamed, live_evidence: 0 });
    mount();
    const alarm = await screen.findByRole("alert");
    expect(alarm.textContent).toContain(
      "Heimdal was deactivated and nothing was named to replace it. This objective has no current evidence, and no active tool contributes to it.",
    );
    expect(screen.queryByText("No evidence attached")).toBeNull();
  });

  it("names what still contributes, from the server's count", async () => {
    vi.mocked(api.archivedEvidence).mockResolvedValue({ ...unnamed, live_evidence: 0, covered_by: ["Defender"] });
    mount();
    const alarm = await screen.findByRole("alert");
    expect(alarm.textContent).toContain("no current evidence; Defender still contributes to it.");
  });

  it("is silent when the server counted live evidence", async () => {
    vi.mocked(api.archivedEvidence).mockResolvedValue({ ...unnamed, live_evidence: 1 });
    mount();
    await screen.findByRole("list", { name: "No longer attached" });
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("is silent when a person named the replacement", async () => {
    vi.mocked(api.archivedEvidence).mockResolvedValue({
      items: [gone({ replaced_by_name: "Defender" })], live_evidence: 0, covered_by: [],
    });
    mount();
    await screen.findByRole("list", { name: "No longer attached" });
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("states nothing when the count could not be read", async () => {
    vi.mocked(api.archivedEvidence).mockRejectedValue(new Error("503 Service Unavailable"));
    mount();
    await screen.findByText("503 Service Unavailable");
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.queryByRole("list", { name: "No longer attached" })).toBeNull();
  });
});
