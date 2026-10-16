// Pure logic behind Library › Lists (roadmap L.2): which family a practice
// belongs to, which lists a family or practice view shows, and how an edit
// that lost a save race is re-applied onto the version that won.
//
// Rows have no identity key (backend ListVersion), so an edit is tracked by
// where each draft row came from (`origin`, an index into the version it was
// read from) rather than inferred afterwards from content. That is what
// makes the re-apply exact for every row the other editor did not touch, and
// honest about the ones they did.

import type { LibraryListVersion, ListTagEvent, VersionCause } from "../types";
import { FAMILY_ORDER } from "./families";

export function familyOf(controlKey: string): string {
  return controlKey.split(".")[0] ?? "";
}

export interface Tagged {
  control_keys: string[];
}

/** Families a list serves, in the standard family order, each once. */
export function familiesOf(list: Tagged): string[] {
  const fams = new Set(list.control_keys.map(familyOf));
  const known: readonly string[] = FAMILY_ORDER;
  return [...known.filter((f) => fams.has(f)), ...[...fams].filter((f) => !known.includes(f)).sort()];
}

/**
 * A list belongs to a family view if any of its practices is in that family,
 * and to a practice view if it carries that practice. It is one entry either
 * way: a list serving three practices is one list, not three.
 */
export function filterLists<T extends Tagged & { title: string }>(
  lists: T[],
  opts: { family: string | null; practice: string | null; search: string },
): T[] {
  const q = opts.search.trim().toLowerCase();
  return lists.filter(
    (l) =>
      (opts.family === null || l.control_keys.some((k) => familyOf(k) === opts.family)) &&
      (opts.practice === null || l.control_keys.includes(opts.practice)) &&
      (q === "" ||
        l.title.toLowerCase().includes(q) ||
        l.control_keys.some((k) => k.toLowerCase().includes(q))),
  );
}

// "AC.L2-3.1.12" -> [family rank, 3, 1, 12], so 3.1.2 sorts before 3.1.12.
function practiceRank(key: string): number[] {
  const known: readonly string[] = FAMILY_ORDER;
  const i = known.indexOf(familyOf(key));
  const nums = (key.split("-")[1] ?? "").split(".").map((n) => Number.parseInt(n, 10) || 0);
  return [i === -1 ? known.length : i, ...nums];
}

function compareRanks(a: number[], b: number[]): number {
  for (let i = 0; i < Math.max(a.length, b.length); i++) {
    const d = (a[i] ?? -1) - (b[i] ?? -1);
    if (d !== 0) return d;
  }
  return 0;
}

/** In the standard family order, then by first practice, then title -- the
 * order an engineer reads the practices in, not the order lists were keyed. */
export function sortLists<T extends Tagged & { title: string }>(lists: T[]): T[] {
  const first = (l: T) =>
    l.control_keys.map(practiceRank).sort(compareRanks)[0] ?? [Number.MAX_SAFE_INTEGER];
  return [...lists].sort(
    (a, b) => compareRanks(first(a), first(b)) || a.title.localeCompare(b.title),
  );
}

/** Lists per family; a list serving two families counts once in each. */
export function familyCounts(lists: Tagged[]): Map<string, number> {
  const counts = new Map<string, number>();
  for (const l of lists) for (const f of familiesOf(l)) counts.set(f, (counts.get(f) ?? 0) + 1);
  return counts;
}

export interface DraftRow {
  // Index of this row in the version the draft started from; null when the
  // editor added it.
  origin: number | null;
  cells: string[];
}

export function draftFrom(rows: string[][]): DraftRow[] {
  return rows.map((cells, i) => ({ origin: i, cells: [...cells] }));
}

export function draftRows(draft: DraftRow[]): string[][] {
  return draft.map((r) => r.cells);
}

const same = (a: string[], b: string[]) => a.length === b.length && a.every((c, i) => c === b[i]);

export function isDirty(base: string[][], draft: DraftRow[]): boolean {
  return draft.length !== base.length || draft.some((r, i) => r.origin !== i || !same(r.cells, base[i]));
}

export interface RebaseConflict {
  kind: "edited" | "removed";
  // The row as it stood in the version the editor started from.
  original: string[];
  // The editor's version of it ("edited" only).
  mine: string[] | null;
}

export interface RebaseResult {
  rows: DraftRow[];
  applied: number;
  conflicts: RebaseConflict[];
}

/**
 * Re-applies the edits in `draft` (made against `base`) onto `theirs`, the
 * version that was saved in the meantime. A row the editor kept, changed or
 * removed is found in `theirs` by its original content; when it is still
 * there the change applies to it, and when the other editor changed or
 * removed it too, the change is returned as a conflict for a person to
 * decide -- never guessed. Added rows keep their place after the row they
 * followed, or go to the end if that row is gone.
 *
 * The result is a draft against `theirs` (origins index into it), so saving
 * it is an ordinary save onto the current version.
 */
export function rebase(base: string[][], draft: DraftRow[], theirs: string[][]): RebaseResult {
  const used = new Set<number>();
  const locate = (row: string[]): number | null => {
    for (let j = 0; j < theirs.length; j++) {
      if (!used.has(j) && same(theirs[j], row)) {
        used.add(j);
        return j;
      }
    }
    return null;
  };
  const where = base.map((row) => locate(row));

  const byOrigin = new Map<number, DraftRow>();
  for (const r of draft) if (r.origin !== null) byOrigin.set(r.origin, r);

  const cells: (string[] | null)[] = theirs.map((r) => [...r]); // null = removed
  const conflicts: RebaseConflict[] = [];
  let applied = 0;

  base.forEach((orig, i) => {
    const mine = byOrigin.get(i);
    const j = where[i];
    if (mine && same(mine.cells, orig)) return; // untouched by this editor
    if (j === null) {
      conflicts.push({ kind: mine ? "edited" : "removed", original: orig, mine: mine ? [...mine.cells] : null });
      return;
    }
    cells[j] = mine ? [...mine.cells] : null;
    applied++;
  });

  // Added rows, anchored after the nearest preceding row the editor read.
  const before: string[][] = [];
  const after = new Map<number, string[][]>();
  const atEnd: string[][] = [];
  let anchor: number | null | "none" = "none";
  for (const r of draft) {
    if (r.origin !== null) {
      anchor = where[r.origin];
      continue;
    }
    applied++;
    if (anchor === "none") before.push([...r.cells]);
    else if (anchor === null) atEnd.push([...r.cells]);
    else after.set(anchor, [...(after.get(anchor) ?? []), [...r.cells]]);
  }

  const rows: DraftRow[] = before.map((c) => ({ origin: null, cells: c }));
  cells.forEach((c, j) => {
    if (c !== null) rows.push({ origin: j, cells: c });
    for (const added of after.get(j) ?? []) rows.push({ origin: null, cells: added });
  });
  for (const c of atEnd) rows.push({ origin: null, cells: c });
  return { rows, applied, conflicts };
}

/** What the other editor changed between `base` and `theirs`, by content. */
export function changeCounts(base: string[][], theirs: string[][]): { added: number; removed: number } {
  const key = (r: string[]) => JSON.stringify(r);
  const count = new Map<string, number>();
  for (const r of base) count.set(key(r), (count.get(key(r)) ?? 0) + 1);
  let added = 0;
  for (const r of theirs) {
    const n = count.get(key(r)) ?? 0;
    if (n > 0) count.set(key(r), n - 1);
    else added++;
  }
  const removed = [...count.values()].reduce((a, b) => a + b, 0);
  return { added, removed };
}

/**
 * The three kinds of list the index keeps apart (L.2.1). They share a shape
 * and nothing else:
 * - sourced: drawn live from the scope graph; its rows are real records.
 * - kept: a library list this org keeps for itself. A copy adopted from a
 *   template will be one of these -- it becomes the org's own records.
 * - template: an MSP template; its rows are illustrative examples.
 */
export type ListKind = "sourced" | "kept" | "template";

export function kindOf(entry: { kind: "library" | "scope"; library?: { is_template: boolean } }): ListKind {
  if (entry.kind === "scope") return "sourced";
  return entry.library?.is_template ? "template" : "kept";
}

/**
 * Why a version exists, said where a reader looks first: the version list,
 * not the audit log. Importer-caused versions recur -- an importer change
 * writes a version of every list it touches -- and must not read as a
 * person having changed the template.
 */
export const CAUSE_LABELS: Record<VersionCause, string> = {
  edit: "edited",
  first_import: "imported",
  source_changed: "source changed",
  importer_changed: "importer changed",
  source_reapplied: "source put back",
  created_by_hand: "built by hand",
};

export function causeLabel(cause: VersionCause): string {
  return CAUSE_LABELS[cause] ?? cause;
}

const PERSON_CAUSES: ReadonlySet<VersionCause> = new Set(["edit", "created_by_hand"]);

/** Who made the version: a person's name, or the product. */
export function versionAuthor(v: Pick<LibraryListVersion, "cause" | "saved_by">): string {
  return PERSON_CAUSES.has(v.cause) ? (v.saved_by ?? "a person") : "WinGRC importer";
}

// The calendar date a record was written, as stored (UTC): the same on
// every reader's screen, unlike a locale rendering of the instant.
const dayOf = (iso: string) => iso.slice(0, 10);

export function whyVersion(
  v: Pick<LibraryListVersion, "cause" | "saved_by" | "provenance"> & { created_at?: string },
): string {
  const p = v.provenance;
  const since = p.previous_import_version ? ` since version ${p.previous_import_version}` : "";
  const replaced = p.replaces_edit_in_version
    ? ` It replaced the edit in version ${p.replaces_edit_in_version}.`
    : "";
  const fields =
    (p.changed_fields?.length ? ` Changed: ${p.changed_fields.join(", ")}.` : "") + replaced;
  switch (v.cause) {
    case "edit":
      return `Saved by ${versionAuthor(v)}${p.based_on_version ? `, from version ${p.based_on_version}` : ""}.`;
    case "first_import":
      return `Imported from ${p.source_file ?? "the template library"}${p.source_sheet ? ` (sheet ${p.source_sheet})` : ""}.${replaced}`;
    case "created_by_hand":
      return (
        `Written by ${versionAuthor(v)}, from nothing: built by hand in WinGRC` +
        `${v.created_at ? ` on ${dayOf(v.created_at)}` : ""}, not imported from a template.`
      );
    case "source_changed":
      return `Re-imported because the source sheet changed${since}.${fields}`;
    case "importer_changed":
      return (
        `Written by WinGRC, not by a person: the importer changed how it reads the source, ` +
        `and the source sheet is unchanged${since}.${fields}`
      );
    case "source_reapplied":
      return (
        `Re-imported with the source unchanged${since}: its content replaced ` +
        `${p.replaces_edit_in_version ? `the edit in version ${p.replaces_edit_in_version}` : "a later edit"}.`
      );
  }
  return "";
}

/** One line of a list's tag history: what changed, and who said so. */
export function tagSentence(e: Pick<ListTagEvent, "control_key" | "change" | "via" | "saved_by">): string {
  const who = e.via === "api" ? (e.saved_by ?? "a person") : "WinGRC importer, from the source sheet's practice row";
  return e.change === "added"
    ? `Tagged ${e.control_key}: this list became a candidate for it. By ${who}.`
    : `Untagged ${e.control_key}: this list no longer claims to serve it. By ${who}.`;
}

export type HistoryEntry =
  | { kind: "version"; at: string; version: LibraryListVersion }
  | { kind: "tag"; at: string; event: ListTagEvent };

/**
 * Versions and tag changes in one history, newest first (L.2.2). Tags are
 * not version content, so they have their own record; showing it here, not
 * in a second place, is what lets "when did this list start claiming that
 * practice, and who said so?" be answered where a person looks first. A tag
 * change made in the same moment as a version (a list built with its tags)
 * sorts after that version; tag events keep their recorded order.
 */
export function historyEntries(versions: LibraryListVersion[], events: ListTagEvent[]): HistoryEntry[] {
  const all = [
    ...versions.map((v, i) => ({ e: { kind: "version", at: v.created_at, version: v } as HistoryEntry, rank: 0, i })),
    ...events.map((ev, i) => ({ e: { kind: "tag", at: ev.created_at, event: ev } as HistoryEntry, rank: 1, i })),
  ];
  const time = (iso: string) => new Date(iso).getTime();
  all.sort((a, b) => time(a.e.at) - time(b.e.at) || a.rank - b.rank || a.i - b.i);
  return all.map((x) => x.e).reverse();
}

// Columns while editing. Rows are positional -- a cell belongs to a column
// by index, not by header -- so `from` (the column's index in the version
// the edit started from) is what tells a rename, which keeps its cells, from
// a removal, which drops them.
export interface DraftColumn {
  name: string;
  from: number | null;
}

export function columnsFrom(columns: string[]): DraftColumn[] {
  return columns.map((name, i) => ({ name, from: i }));
}

export function columnsChanged(base: string[], cols: DraftColumn[]): boolean {
  return cols.length !== base.length || cols.some((c, i) => c.from !== i || c.name !== base[i]);
}

/** The base version's columns this edit drops, with how many filled cells
 * each holds there -- what will not be in the new version. */
export function droppedColumns(base: string[], baseRows: string[][], cols: DraftColumn[]): { name: string; filled: number }[] {
  const kept = new Set(cols.map((c) => c.from).filter((f): f is number => f !== null));
  return base
    .map((name, i) => ({ name, filled: baseRows.filter((r) => (r[i] ?? "").trim() !== "").length, i }))
    .filter((c) => !kept.has(c.i))
    .map(({ name, filled }) => ({ name, filled }));
}

/** Renamed columns: same cells, new header. */
export function renamedColumns(base: string[], cols: DraftColumn[]): { from: string; to: string }[] {
  return cols
    .filter((c) => c.from !== null && c.name !== base[c.from])
    .map((c) => ({ from: base[c.from as number], to: c.name }));
}

/** Plainly, before a save: what the new version will not have. Empty when
 * the edit drops nothing. */
export function columnLossWarning(dropped: { name: string; filled: number }[], nextVersion: number): string {
  if (dropped.length === 0) return "";
  const cells = dropped.reduce((a, d) => a + d.filled, 0);
  const names = dropped.map((d) => `${d.name} (${d.filled} filled cell${d.filled === 1 ? "" : "s"})`).join(", ");
  return (
    `Saving removes ${dropped.length === 1 ? "the column" : "the columns"} ${names} from version ${nextVersion}` +
    (cells > 0 ? `: ${cells} filled cell${cells === 1 ? "" : "s"} will not be in it.` : ".") +
    ` Version ${nextVersion - 1} keeps ${dropped.length === 1 ? "it" : "them"}${cells > 0 ? ", with every value" : ""}.`
  );
}
