import { describe, expect, it } from "vitest";
import { decidedLine, familyProgress, overallProgress, rowState, startingFamily, unsavedNotice, unsavedRows } from "./sspImport";
import type { SspImportRow, SspImportView } from "../types";

function row(over: Partial<SspImportRow> = {}): SspImportRow {
  return {
    id: "r1", number: 1, family: "AC", heading: "8.1 Access Control", control_text: "t",
    statement: "s", source_status: "Met", notes_present: false, raci_present: true,
    match_kind: "exact", objective_id: "o1", objective_ref: "AC.L2-3.1.1[a]", objective_text: "t",
    candidates: [], acceptable_onto: ["o1"], not_acceptable: null, decision: "pending",
    resolution: null, decided_objective_id: null, decided_ref: null, statement_id: null,
    decided_by: null, decided_at: null, existing: null, shares_objective_with: [],
    ...over,
  };
}

function view(families: SspImportView["families"], decided: number, rows: number, written: number): SspImportView {
  return {
    session: {
      id: "s", status: "open", file_name: "f.docx", file_sha256: "a".repeat(64), file_size: 1,
      started_by: null, started_at: "", closed_by: null, closed_at: null, rows, decided, written,
    },
    families,
    families_complete: families.filter((f) => f.decided === f.total).length,
    rows: [],
  };
}

describe("progress wording", () => {
  it("reads per family and overall", () => {
    expect(familyProgress({ key: "AC", heading: "", total: 22, decided: 12 })).toBe("AC: 12 of 22 decided");
    const v = view([{ key: "AC", heading: "", total: 2, decided: 2 }, { key: "AU", heading: "", total: 3, decided: 1 }], 3, 5, 1);
    expect(overallProgress(v)).toBe("3 of 5 rows decided · 1 of 2 families complete · 1 draft written");
  });

  it("opens on the first family with an undecided row", () => {
    const v = view([{ key: "AC", heading: "", total: 2, decided: 2 }, { key: "AU", heading: "", total: 3, decided: 1 }], 3, 5, 1);
    expect(startingFamily(v)).toBe("AU");
    const done = view([{ key: "AC", heading: "", total: 2, decided: 2 }], 2, 2, 2);
    expect(startingFamily(done)).toBe("AC");
  });
});

describe("row state, each case written out", () => {
  it("pending, conflict, cannot accept, written, rejected", () => {
    expect(rowState(row())).toBe("pending");
    expect(rowState(row({ existing: { id: "x", body: "b", status: "reviewed", updated_at: "", from_this_session: false } }))).toBe("conflict");
    expect(rowState(row({ match_kind: "unmatched", acceptable_onto: [], not_acceptable: "no" }))).toBe("cannot_accept");
    expect(rowState(row({ decision: "accepted", statement_id: "st" }))).toBe("written");
    expect(rowState(row({ decision: "edited", statement_id: "st" }))).toBe("written");
    expect(rowState(row({ decision: "rejected" }))).toBe("rejected");
  });

  it("an undecided unmatched row never reads as acceptable, even with candidates", () => {
    const r = row({ match_kind: "unmatched", acceptable_onto: [], candidates: [{ objective_id: "o", ref: "X", score: 0.94 }] });
    expect(rowState(r)).toBe("cannot_accept");
  });
});

describe("what a decided row says", () => {
  it("names the outcome", () => {
    expect(decidedLine(row({ decision: "accepted", decided_ref: "AC.L2-3.1.1[a]", decided_by: "Pat" })))
      .toBe("Accepted onto AC.L2-3.1.1[a] by Pat. Draft written.");
    expect(decidedLine(row({ decision: "accepted", decided_ref: "X", resolution: "replaced" })))
      .toBe("Accepted onto X. Draft written, replacing the statement that was there.");
    expect(decidedLine(row({ decision: "rejected", resolution: "kept_existing", decided_ref: "X" })))
      .toBe("Rejected: the statement already on X was kept.");
    expect(decidedLine(row({ decision: "rejected" }))).toBe("Rejected. Nothing was written.");
  });
});

describe("unsaved edits", () => {
  it("counts only text that differs from the source", () => {
    const rows = [row({ id: "a", number: 3, statement: "orig" }), row({ id: "b", number: 1, statement: "x" }), row({ id: "c", number: 2 })];
    expect(unsavedRows(rows, {})).toEqual([]);
    expect(unsavedRows(rows, { a: "orig" })).toEqual([]);
    expect(unsavedRows(rows, { a: "changed", b: "y" }).map((r) => r.id)).toEqual(["a", "b"]);
  });

  it("says which rows", () => {
    expect(unsavedNotice([row({ number: 4 })])).toBe(
      "Unsaved edit on row 4. Save it as a draft before reloading or leaving this screen, or it is lost.");
    expect(unsavedNotice([row({ number: 7 }), row({ number: 2 })])).toBe(
      "Unsaved edit on rows 2, 7. Save it as a draft before reloading or leaving this screen, or it is lost.");
  });
});
