/**
 * Which library lists go into the assessor bundle (roadmap L.3).
 *
 * None by default. A bundle is one tenant's assessment and most of an MSP's
 * lists are templates, so "every list in the org" would be wrong by
 * omission. Until a list can be linked as evidence (L.4) the operator picks
 * here; each chosen list goes in as its current version, named by id, and
 * the bundle's lists/index.html records the choice.
 */

import { useEffect, useState } from "react";

import { api } from "../api";
import type { LibraryListSummary } from "../types";

export function BundleListsPicker({
  orgId,
  selected,
  onChange,
}: {
  orgId: string;
  selected: string[];
  onChange: (ids: string[]) => void;
}) {
  const [open, setOpen] = useState(false);
  const [lists, setLists] = useState<LibraryListSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open || lists !== null) return;
    api.listLibrary(orgId).then(setLists, (e: Error) => setError(e.message));
  }, [open, lists, orgId]);

  const chosen = new Set(selected);
  const toggle = (id: string) =>
    onChange(chosen.has(id) ? selected.filter((x) => x !== id) : [...selected, id]);
  // The org's own lists first: they are the records. Templates are examples.
  const ordered = [...(lists ?? [])].sort(
    (a, b) => Number(a.is_template) - Number(b.is_template) || a.title.localeCompare(b.title),
  );

  return (
    <details className="bundle-lists" onToggle={(e) => setOpen((e.target as HTMLDetailsElement).open)}>
      <summary className="btn-ghost btn-sm">
        Lists in bundle ({selected.length})
      </summary>
      <div className="bundle-lists-panel">
        <p className="field-hint">
          No list is included unless chosen here. Each goes in as its current version, named by its
          version id, and the bundle records which were chosen. A template&apos;s rows are examples, not
          this org&apos;s records.
        </p>
        {error && <div className="form-error">{error}</div>}
        {lists === null && !error && <div className="loading">Loading lists…</div>}
        {lists !== null && lists.length === 0 && (
          <p className="field-hint">This org&apos;s list library is empty.</p>
        )}
        <ul>
          {ordered.map((l) => (
            <li key={l.id}>
              <label>
                <input type="checkbox" checked={chosen.has(l.id)} onChange={() => toggle(l.id)} />{" "}
                {l.title} <span className="field-hint">v{l.version_number}</span>
                {l.is_template && <span className="affiliation-badge">template</span>}
              </label>
            </li>
          ))}
        </ul>
      </div>
    </details>
  );
}
