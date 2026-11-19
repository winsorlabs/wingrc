// The parts of the Tool Import review both modes share: the legend that says
// what each column asks (docs/PLAN-tool-import.md 5.2), the one banner of
// problems (5.8), and the existing-mapping diff (section 4).
import { useState } from "react";
import { CLASSIFICATION_LABELS, COVERAGE_BASIS_LABELS } from "../lib/baseline";
import { COLUMN_HELP, LEGEND_EXAMPLE, type BannerItem } from "../lib/baselineReview";
import type { BaselineImportPreview } from "../types";

const LEGEND_KEY = "wingrc.toolImport.legendOpen";

function readLegendOpen(): boolean {
  try {
    return window.localStorage.getItem(LEGEND_KEY) !== "closed";
  } catch {
    return true;
  }
}

export function ReviewLegend({ mode }: { mode: "yaml" | "documents" }) {
  const [open, setOpen] = useState(readLegendOpen);
  function toggle() {
    const next = !open;
    setOpen(next);
    try {
      window.localStorage.setItem(LEGEND_KEY, next ? "open" : "closed");
    } catch {
      // Per-browser convenience only; the legend still works without it.
    }
  }
  return (
    <section className="review-legend" aria-label="What each column asks">
      <button type="button" className="btn-ghost btn-sm" aria-expanded={open} onClick={toggle}>
        {open ? "▾" : "▸"} What each column asks
      </button>
      {open && (
        <dl>
          <dt>Control</dt>
          <dd>{COLUMN_HELP.control}</dd>
          <dt>Who is responsible?</dt>
          <dd>{COLUMN_HELP.responsible}</dd>
          <dt>On whose systems?</dt>
          <dd>{COLUMN_HELP.systems}</dd>
          {mode === "documents" && (
            <>
              <dt>Source</dt>
              <dd>{COLUMN_HELP.source}</dd>
              <dt>Covers</dt>
              <dd>{COLUMN_HELP.covers}</dd>
              <dt>In the row</dt>
              <dd>{COLUMN_HELP.expansion}</dd>
            </>
          )}
          <dt>For example</dt>
          <dd>{LEGEND_EXAMPLE}</dd>
        </dl>
      )}
    </section>
  );
}

export function ProblemsBanner({
  items,
  disclaimRows,
  filter,
  onFilter,
}: {
  items: BannerItem[];
  disclaimRows: number[];
  filter: string | null;
  onFilter: (kind: string | null) => void;
}) {
  if (items.length === 0 && disclaimRows.length === 0) return null;
  const blocking = items.length > 0;
  const entries: { kind: string; text: string }[] = items.map((i) => ({ kind: i.kind, text: i.text }));
  if (disclaimRows.length > 0) {
    const n = disclaimRows.length;
    entries.push({
      kind: "_disclaim",
      text: `${n} row${n === 1 ? "" : "s"} ${n === 1 ? "has" : "have"} a control the CRM disclaims (a nudge, not a block)`,
    });
  }
  return (
    <div className="form-info-banner review-banner" role="status">
      <div>{blocking ? "Before Apply:" : "Nothing stands in the way of Apply. To look at:"}</div>
      <ul>
        {entries.map((e) => (
          <li key={e.kind}>
            <button
              type="button"
              className="review-banner-filter"
              aria-pressed={filter === e.kind}
              onClick={() => onFilter(filter === e.kind ? null : e.kind)}
            >
              {e.text}
            </button>
          </li>
        ))}
      </ul>
      {filter && (
        <button type="button" className="review-banner-filter" onClick={() => onFilter(null)}>
          Show every row
        </button>
      )}
    </div>
  );
}

export function ChangeDiff({
  preview,
  onOpenControl,
}: {
  preview: BaselineImportPreview;
  onOpenControl?: (controlId: string) => void;
}) {
  if (preview.control_changes.length === 0) {
    return <div className="empty">No control changes in this file.</div>;
  }
  return (
    <div className="table-scroll">
      <table className="contacts-table">
        <thead>
          <tr>
            <th title={COLUMN_HELP.control}>Control</th>
            <th>Change</th>
            <th title={COLUMN_HELP.responsible}>Who is responsible?</th>
            <th title={COLUMN_HELP.systems}>On whose systems?</th>
          </tr>
        </thead>
        <tbody>
          {preview.control_changes.map((c) => (
            <tr key={c.control_id} className={c.change_type === "removed" ? "baseline-change-removed" : undefined}>
              <td>
                {onOpenControl && c.change_type !== "removed" ? (
                  <button type="button" className="link-button" onClick={() => onOpenControl(c.control_id)}>
                    {c.control_id}
                  </button>
                ) : (
                  c.control_id
                )}
              </td>
              <td>{c.change_type === "removed" ? "removed (dropped from this import)" : c.change_type}</td>
              <td>{c.classification ? CLASSIFICATION_LABELS[c.classification] ?? c.classification : "to decide"}</td>
              <td className={c.coverage_basis === "platform_only" ? "coverage-basis-platform-only" : undefined}>
                {c.coverage_basis ? COVERAGE_BASIS_LABELS[c.coverage_basis] ?? c.coverage_basis : "to decide"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
