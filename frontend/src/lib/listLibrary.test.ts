import { describe, expect, it } from "vitest";
import type { LibraryListVersion, ListTagEvent } from "../types";
import {
  adoptedFromText,
  distributionText,
  exampleRowsText,
  updateAvailableText,
  causeLabel,
  changeCounts,
  columnLossWarning,
  columnsChanged,
  columnsFrom,
  draftFrom,
  droppedColumns,
  historyEntries,
  renamedColumns,
  tagSentence,
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
    expect(versionAuthor(v("created_by_hand", {}, "Pat Builder"))).toBe("Pat Builder");
  });

  it("says a hand-built list was written by a person, from nothing, on its date -- never imported", () => {
    const built = { ...v("created_by_hand", { origin_note: "old SharePoint sheet" }, "Pat Builder"), created_at: "2026-10-15T14:02:00Z" };
    expect(causeLabel("created_by_hand")).toBe("built by hand");
    expect(whyVersion(built)).toBe(
      "Written by Pat Builder, from nothing: built by hand in WinGRC on 2026-10-15, not imported from a template.",
    );
    expect(whyVersion(built)).not.toMatch(/SharePoint/);
  });

  it("says when a first import replaced a list a person had built", () => {
    expect(whyVersion(v("first_import", { source_file: "New Lists/x.xlsx", replaces_edit_in_version: 1 }))).toBe(
      "Imported from New Lists/x.xlsx. It replaced the edit in version 1.",
    );
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

describe("tag history beside the versions", () => {
  const ver = (n: number, at: string) => ({ id: `v${n}`, version_number: n, created_at: at }) as LibraryListVersion;
  const tag = (id: string, key: string, change: "added" | "removed", at: string, over: Partial<ListTagEvent> = {}): ListTagEvent => ({
    id, control_key: key, change, via: "api", saved_by: "Tess Tagger", at_version: 1, note: null, created_at: at, ...over,
  });

  it("interleaves them newest first, a tag made with a version sorting after it", () => {
    const t0 = "2026-10-15T10:00:00Z";
    const entries = historyEntries(
      [ver(1, t0), ver(2, "2026-10-15T12:00:00Z")],
      [tag("a", "PE.L2-3.10.3", "added", t0), tag("b", "PE.L2-3.10.4", "added", t0), tag("c", "CM.L2-3.4.2", "added", "2026-10-15T11:00:00Z")],
    );
    expect(entries.map((e) => (e.kind === "version" ? `v${e.version.version_number}` : e.event.id))).toEqual([
      "v2", "c", "b", "a", "v1",
    ]);
  });

  it("says who claimed or dropped a practice, a person or the importer", () => {
    expect(tagSentence(tag("a", "CM.L2-3.4.2", "added", ""))).toBe(
      "Tagged CM.L2-3.4.2: this list became a candidate for it. By Tess Tagger.",
    );
    expect(tagSentence(tag("b", "RA.L2-3.11.2", "removed", "", { via: "template_import", saved_by: null }))).toBe(
      "Untagged RA.L2-3.11.2: this list no longer claims to serve it. By WinGRC importer, from the source sheet's practice row.",
    );
  });
});

describe("column edits", () => {
  const base = ["Date", "Visitor", "Escort"];
  const rows = [["2026-10-01", "A", "J"], ["2026-10-02", "B", ""]];

  it("tells a rename, which keeps its cells, from a removal, which drops them", () => {
    const cols = columnsFrom(base);
    expect(columnsChanged(base, cols)).toBe(false);
    const edited = [{ name: "Date", from: 0 }, { name: "Guest", from: 1 }];
    expect(columnsChanged(base, edited)).toBe(true);
    expect(renamedColumns(base, edited)).toEqual([{ from: "Visitor", to: "Guest" }]);
    expect(droppedColumns(base, rows, edited)).toEqual([{ name: "Escort", filled: 1 }]);
  });

  it("says plainly what a save will lose, and that the prior version keeps it", () => {
    expect(columnLossWarning([{ name: "Escort", filled: 1 }], 3)).toBe(
      "Saving removes the column Escort (1 filled cell) from version 3: 1 filled cell will not be in it. Version 2 keeps it, with every value.",
    );
    expect(columnLossWarning([{ name: "Notes", filled: 0 }], 2)).toBe(
      "Saving removes the column Notes (0 filled cells) from version 2. Version 1 keeps it.",
    );
    expect(columnLossWarning([], 2)).toBe("");
  });
});

describe("adopted copies (L.7)", () => {
  const a = {
    template_list_id: "t", template_org_id: "msp", same_org: false, template_title: "Visitor Log",
    template_key: "3-10-3a-visitor-log", based_on_version_number: 2, head_version_number: 3, update_available: true,
  };

  it("says a copy was copied by a person, from which template, at which version", () => {
    const v1 = {
      cause: "adopted_from_template" as const, saved_by: "Ada Adopter", created_at: "2026-10-16T09:00:00Z",
      provenance: { template_title: "Visitor Log", template_version_number: 2 },
    };
    expect(causeLabel("adopted_from_template")).toBe("adopted");
    expect(versionAuthor(v1)).toBe("Ada Adopter");
    expect(whyVersion(v1)).toBe("Copied by Ada Adopter from the template Visitor Log at version 2 on 2026-10-16.");
  });

  it("says what taking a newer template version did to the rows and to a replaced edit", () => {
    const taken = {
      cause: "adopted_from_template" as const, saved_by: "Ada Adopter",
      provenance: {
        template_title: "Visitor Log", template_version_number: 3, previous_template_version_number: 2,
        rows_from: "copy" as const, dropped_cells: 1, replaces_edit_in_version: 4,
      },
    };
    expect(whyVersion(taken)).toBe(
      "Ada Adopter took version 3 of the template Visitor Log (this list was based on version 2): this org's own " +
        "rows were kept, matched to the template's columns by name. 1 filled cell(s) in dropped columns are in the " +
        "earlier version only. It replaced the edit in version 4.",
    );
  });

  it("says where a copy came from, in this org or another, and when a newer version waits", () => {
    expect(adoptedFromText(a)).toBe("Copied from the template Visitor Log, version 2.");
    expect(adoptedFromText({ ...a, same_org: true })).toBe("Copied from this org's template Visitor Log, version 2.");
    expect(updateAvailableText(a)).toBe(
      "Template version 3 is available. This list stays on version 2 until someone takes it.",
    );
    expect(updateAvailableText({ ...a, update_available: false })).toBeNull();
  });

  it("counts a template's copies by version, never by whose", () => {
    expect(distributionText([])).toBeNull();
    expect(
      distributionText([
        { version_number: 2, copies: 1, this_org: true },
        { version_number: 1, copies: 2, this_org: false },
      ]),
    ).toBe("Adopted by 3 orgs: 2 at version 1, 1 at version 2. This org's own copy is one of them.");
    expect(distributionText([{ version_number: 1, copies: 1, this_org: false }])).toBe("Adopted by 1 org: 1 at version 1.");
  });

  it("says why a list is not yet filled in", () => {
    expect(exampleRowsText(0, 3)).toBeNull();
    expect(exampleRowsText(2, 2)).toMatch(
      /^All 2 rows are still the template's examples, with a fill-in such as \[PLACEHOLDER\] or \[CUSTOMER_NAME\]/,
    );
    expect(exampleRowsText(1, 3)).toMatch(/^1 of 3 rows are still/);
  });
});
