import { describe, expect, it } from "vitest";
import {
  causeLabel,
  changeCounts,
  draftFrom,
  familiesOf,
  familyCounts,
  filterLists,
  isDirty,
  kindOf,
  rebase,
  sortLists,
  versionAuthor,
  whyVersion,
  type DraftRow,
} from "./listLibrary";

const lists = [
  { title: "Separation of Duties", control_keys: ["AC.L2-3.1.4", "AU.L2-3.3.9", "CM.L2-3.4.5"] },
  { title: "Authorized Devices", control_keys: ["AC.L2-3.1.1", "CM.L2-3.4.1"] },
  { title: "Risk Register", control_keys: ["RA.L2-3.11.1"] },
];

describe("kindOf", () => {
  it("keeps a live list, an org's own list and a template apart", () => {
    expect(kindOf({ kind: "scope" })).toBe("sourced");
    expect(kindOf({ kind: "library", library: { is_template: false } })).toBe("kept");
    expect(kindOf({ kind: "library", library: { is_template: true } })).toBe("template");
  });
});

describe("family and practice views", () => {
  it("reaches a three-family list from each family, once", () => {
    for (const family of ["AC", "AU", "CM"]) {
      const shown = filterLists(lists, { family, practice: null, search: "" });
      expect(shown.filter((l) => l.title === "Separation of Duties")).toHaveLength(1);
    }
    expect(filterLists(lists, { family: "RA", practice: null, search: "" }).map((l) => l.title)).toEqual([
      "Risk Register",
    ]);
  });

  it("reaches a list from each practice it carries", () => {
    for (const practice of lists[0].control_keys) {
      expect(filterLists(lists, { family: null, practice, search: "" }).map((l) => l.title)).toEqual([
        "Separation of Duties",
      ]);
    }
  });

  it("counts a list once in each family it serves, and lists families in the standard order", () => {
    expect(familiesOf(lists[0])).toEqual(["AC", "AU", "CM"]);
    expect(Object.fromEntries(familyCounts(lists))).toEqual({ AC: 2, AU: 1, CM: 2, RA: 1 });
  });

  it("searches titles and practice ids", () => {
    expect(filterLists(lists, { family: null, practice: null, search: "3.4.1" }).map((l) => l.title)).toEqual([
      "Authorized Devices",
    ]);
  });
});

const base = [
  ["R-1", "Phishing", "Open"],
  ["R-2", "Lost laptop", "Open"],
  ["R-3", "Insider", "Open"],
];

function edit(rows: string[][], fn: (d: DraftRow[]) => DraftRow[]): DraftRow[] {
  return fn(draftFrom(rows));
}

describe("isDirty", () => {
  it("is false for an untouched draft and true for any change", () => {
    expect(isDirty(base, draftFrom(base))).toBe(false);
    expect(isDirty(base, draftFrom(base).slice(1))).toBe(true);
    const d = draftFrom(base);
    d[0] = { ...d[0], cells: ["R-1", "Phishing", "Closed"] };
    expect(isDirty(base, d)).toBe(true);
  });
});

describe("rebase: re-applying a stale edit onto the newer version", () => {
  it("applies every change to rows the other editor did not touch", () => {
    const mine = edit(base, (d) => [
      { ...d[0], cells: ["R-1", "Phishing", "Closed"] },
      d[2],
      { origin: null, cells: ["R-5", "Flood", "Open"] },
    ]);
    // They added a row at the end and edited nothing I touched.
    const theirs = [...base, ["R-4", "Ransomware", "Open"]];
    const r = rebase(base, mine, theirs);
    expect(r.conflicts).toEqual([]);
    expect(r.applied).toBe(3);
    expect(r.rows.map((x) => x.cells)).toEqual([
      ["R-1", "Phishing", "Closed"],
      ["R-3", "Insider", "Open"],
      ["R-5", "Flood", "Open"],
      ["R-4", "Ransomware", "Open"],
    ]);
  });

  it("returns a row both editors changed as a conflict, and changes neither", () => {
    const mine = edit(base, (d) => [{ ...d[0], cells: ["R-1", "Phishing", "Closed"] }, d[1], d[2]]);
    const theirs = [["R-1", "Phishing", "Mitigated"], base[1], base[2]];
    const r = rebase(base, mine, theirs);
    expect(r.applied).toBe(0);
    expect(r.conflicts).toEqual([
      { kind: "edited", original: base[0], mine: ["R-1", "Phishing", "Closed"] },
    ]);
    expect(r.rows.map((x) => x.cells)).toEqual(theirs);
  });

  it("reports a removal of a row the other editor changed instead of removing their row", () => {
    const mine = edit(base, (d) => [d[0], d[2]]);
    const theirs = [base[0], ["R-2", "Lost laptop", "Closed"], base[2]];
    const r = rebase(base, mine, theirs);
    expect(r.conflicts).toEqual([{ kind: "removed", original: base[1], mine: null }]);
    expect(r.rows.map((x) => x.cells)).toEqual(theirs);
  });

  it("keeps an added row after the row it followed", () => {
    const mine = edit(base, (d) => [d[0], { origin: null, cells: ["R-1b", "Tailgating", "Open"] }, d[1], d[2]]);
    const theirs = [["R-0", "Vendor", "Open"], ...base];
    const r = rebase(base, mine, theirs);
    expect(r.rows.map((x) => x.cells[0])).toEqual(["R-0", "R-1", "R-1b", "R-2", "R-3"]);
  });

  it("produces a draft against the newer version, so saving it is an ordinary save", () => {
    const mine = edit(base, (d) => [d[0], d[1]]);
    const theirs = [...base, ["R-4", "Ransomware", "Open"]];
    const r = rebase(base, mine, theirs);
    expect(r.rows.map((x) => x.origin)).toEqual([0, 1, 3]);
    expect(isDirty(theirs, r.rows)).toBe(true);
  });

  it("handles identical template rows by count, not by position", () => {
    const tpl = [["[PLACEHOLDER]", "x"], ["[PLACEHOLDER]", "x"]];
    const mine = edit(tpl, (d) => [d[0], { ...d[1], cells: ["R-1", "real"] }]);
    const theirs = [["[PLACEHOLDER]", "x"], ["[PLACEHOLDER]", "x"], ["R-9", "theirs"]];
    const r = rebase(tpl, mine, theirs);
    expect(r.conflicts).toEqual([]);
    expect(r.rows.map((x) => x.cells)).toEqual([["[PLACEHOLDER]", "x"], ["R-1", "real"], ["R-9", "theirs"]]);
  });
});

describe("changeCounts", () => {
  it("counts what the other editor added and removed", () => {
    expect(changeCounts(base, [base[0], ["R-2", "Lost laptop", "Closed"], base[2], ["R-4", "x", "y"]])).toEqual({
      added: 2,
      removed: 1,
    });
  });
});

describe("sortLists", () => {
  it("orders by the standard family order, then practice number, then title", () => {
    const sorted = sortLists([
      { title: "Visitor Log", control_keys: ["PE.L2-3.10.4"] },
      { title: "Remote Access", control_keys: ["AC.L2-3.1.12"] },
      { title: "Devices", control_keys: ["CM.L2-3.4.1", "AC.L2-3.1.1"] },
      { title: "Risk", control_keys: ["RA.L2-3.11.1"] },
      { title: "Accounts", control_keys: ["AC.L2-3.1.1"] },
    ]);
    expect(sorted.map((l) => l.title)).toEqual(["Accounts", "Devices", "Remote Access", "Visitor Log", "Risk"]);
  });
});

describe("why a version exists", () => {
  const v = (cause: Parameters<typeof causeLabel>[0], provenance = {}, saved_by: string | null = null) => ({
    cause, provenance, saved_by,
  });

  it("names the product, never a person, for anything the importer wrote", () => {
    for (const c of ["first_import", "source_changed", "importer_changed", "source_reapplied"] as const) {
      expect(versionAuthor(v(c))).toBe("WinGRC importer");
    }
    expect(versionAuthor(v("edit", {}, "Ada Admin"))).toBe("Ada Admin");
  });

  it("tells a changed source from an unchanged one", () => {
    expect(whyVersion(v("source_changed", { previous_import_version: 2, changed_fields: ["rows"] }))).toBe(
      "Re-imported because the source sheet changed since version 2. Changed: rows.",
    );
    expect(whyVersion(v("importer_changed", { changed_fields: ["columns"] }))).toMatch(
      /^Written by WinGRC, not by a person: .* the source sheet is unchanged\. Changed: columns\.$/,
    );
  });

  it("says plainly when an import replaced a person's edit", () => {
    expect(whyVersion(v("source_reapplied", { previous_import_version: 1, replaces_edit_in_version: 2 }))).toBe(
      "Re-imported with the source unchanged since version 1: its content replaced the edit in version 2.",
    );
    expect(whyVersion(v("importer_changed", { replaces_edit_in_version: 4 }))).toMatch(
      /It replaced the edit in version 4\.$/,
    );
  });
});
