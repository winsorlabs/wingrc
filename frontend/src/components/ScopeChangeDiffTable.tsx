import type { ScopeChange } from "../types";

// Shared by AssetImportWizard (workbook) and LiongardSyncWizard (D.2) --
// both produce the identical DryRunResult/ScopeChange shape from
// routers/scope.py, so the review diff only needs building once.

const CHANGE_LABELS: Record<string, string> = {
  new: "New",
  changed: "Changed",
  missing: "Missing",
};

interface Props {
  changes: ScopeChange[];
  excluded: Set<number>;
  onToggle: (idx: number) => void;
}

export function ScopeChangeDiffTable({ changes, excluded, onToggle }: Props) {
  return (
    <div className="table-scroll">
      <table className="contacts-table import-diff-table">
        <thead>
          <tr>
            <th></th>
            <th>Change</th>
            <th>Type</th>
            <th>Name</th>
            <th>Detail</th>
          </tr>
        </thead>
        <tbody>
          {changes.map((c, idx) => {
            const applicable = c.change_type === "new" || c.change_type === "changed";
            return (
              <tr key={`${c.entity_type}-${c.natural_key}-${idx}`}>
                <td>
                  {applicable && (
                    <input
                      type="checkbox"
                      checked={!excluded.has(idx)}
                      onChange={() => onToggle(idx)}
                    />
                  )}
                </td>
                <td>
                  <span className={`change-badge change-badge-${c.change_type}`}>
                    {CHANGE_LABELS[c.change_type] ?? c.change_type}
                  </span>
                </td>
                <td>{c.entity_type}</td>
                <td>{c.natural_key}</td>
                <td>
                  {c.change_type === "missing" ? (
                    <span className="field-hint">Not touched — apply never deletes</span>
                  ) : Object.keys(c.field_diffs).length > 0 ? (
                    Object.keys(c.field_diffs).join(", ")
                  ) : (
                    <span className="field-hint">New row</span>
                  )}
                  {c.warnings.map((w, wIdx) => (
                    <div key={wIdx} className="field-hint">
                      ⚠ {w}
                    </div>
                  ))}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
