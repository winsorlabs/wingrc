/**
 * Which library lists go into the assessor bundle (roadmap L.3).
 *
 * None by default. A bundle is one tenant's assessment and most of an MSP's
 * lists are templates, so "every list in the org" would be wrong by
 * omission. The operator picks here; each chosen list goes in as its
 * current version, named by id, and the bundle's lists/index.html records
 * the choice.
 *
 * Lists linked as evidence in this assessment (L.4) come first: they are
 * the ones with a reason to be there. They are still not ticked for the
 * operator. A chosen list also carries every version its links name, so
 * evidence linked at an older version resolves to exactly that version.
 */

import { useEffect, useState } from "react";

import { api } from "../api";
import type { LibraryListSummary, ListEvidenceLink } from "../types";

export function BundleListsPicker({
  orgId,
  assessmentId,
  selected,
  onChange,
}: {
  orgId: string;
  assessmentId: string;
  selected: string[];
  onChange: (ids: string[]) => void;
}) {
  const [open, setOpen] = useState(false);
  const [lists, setLists] = useState<LibraryListSummary[] | null>(null);
  const [links, setLinks] = useState<ListEvidenceLink[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open || lists !== null) return;
    api.listLibrary(orgId).then(setLists, (e: Error) => setError(e.message));
    // Which are evidence here adds an order and a note; the picker works without it.
    Promise.resolve()
      .then(() => api.assessmentListLinks(orgId, assessmentId))
      .then((ls) => setLinks(ls ?? []), () => setLinks([]));
  }, [open, lists, orgId, assessmentId]);

  const chosen = new Set(selected);
  const toggle = (id: string) =>
    onChange(chosen.has(id) ? selected.filter((x) => x !== id) : [...selected, id]);
  const linksOf = new Map<string, ListEvidenceLink[]>();
  for (const l of links) linksOf.set(l.list_id, [...(linksOf.get(l.list_id) ?? []), l]);
  // Linked as evidence first, then the org's own lists: they are the
  // records. Templates are examples.
  const ordered = [...(lists ?? [])].sort(
    (a, b) =>
      Number(!linksOf.has(a.id)) - Number(!linksOf.has(b.id)) ||
      Number(a.is_template) - Number(b.is_template) ||
      a.title.localeCompare(b.title),
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
          {ordered.map((l) => {
            const linked = linksOf.get(l.id) ?? [];
            const drift = [...new Set(linked.map((x) => x.drift).filter((d): d is string => !!d))];
            return (
              <li key={l.id}>
                <label>
                  <input type="checkbox" checked={chosen.has(l.id)} onChange={() => toggle(l.id)} />{" "}
                  {l.title} <span className="field-hint">v{l.version_number}</span>
                  {l.is_template && <span className="affiliation-badge">template</span>}
                  {linked.length > 0 && (
                    <span className="affiliation-badge">
                      evidence for {linked.length} objective{linked.length === 1 ? "" : "s"}
                    </span>
                  )}
                </label>
                {drift.map((d) => (
                  <p key={d} className="field-hint">
                    {d} Choosing it includes the linked version too.
                  </p>
                ))}
              </li>
            );
          })}
        </ul>
      </div>
    </details>
  );
}
