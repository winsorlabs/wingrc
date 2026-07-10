import type { ControlStateRow } from "../types";

export interface FilterOpts {
  l1Only: boolean;
  weights: ReadonlySet<number>;
  statuses: ReadonlySet<string>;
  resps: ReadonlySet<string>;
}

/**
 * OR within each group, AND across groups.
 *
 * l1Only:   true  → only CMMC L1 rows (17 controls)
 * weights:  {5,3} → row.sprs_weight must be 5 OR 3   (empty = all)
 * statuses: {…}   → row.status must be in set          (empty = all)
 * resps:    {…}   → row.responsibility must be in set  (empty = all)
 */
export function applyFilters(
  rows: ControlStateRow[],
  { l1Only, weights, statuses, resps }: FilterOpts,
): ControlStateRow[] {
  return rows.filter((r) => {
    if (l1Only && !r.is_level_1) return false;
    if (weights.size > 0 && !weights.has(r.sprs_weight)) return false;
    if (statuses.size > 0 && !statuses.has(r.status)) return false;
    if (resps.size > 0 && !resps.has(r.responsibility)) return false;
    return true;
  });
}

export function filtersActive({ l1Only, weights, statuses, resps }: FilterOpts): boolean {
  return l1Only || weights.size > 0 || statuses.size > 0 || resps.size > 0;
}

export function toggleSetItem<T>(set: ReadonlySet<T>, value: T): Set<T> {
  const next = new Set(set);
  if (next.has(value)) next.delete(value);
  else next.add(value);
  return next;
}

export function clearFilters(): FilterOpts {
  return { l1Only: false, weights: new Set(), statuses: new Set(), resps: new Set() };
}
