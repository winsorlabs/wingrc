// Pure logic behind Library › Lists (roadmap L.2): which family a practice
// belongs to, which lists a family or practice view shows, and how an edit
// that lost a save race is re-applied onto the version that won.
//
// Rows have no identity key (backend ListVersion), so an edit is tracked by
// where each draft row came from (`origin`, an index into the version it was
// read from) rather than inferred afterwards from content. That is what
// makes the re-apply exact for every row the other editor did not touch, and
// honest about the ones they did.

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
