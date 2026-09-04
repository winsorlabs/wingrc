import { useState } from "react";
import { api } from "../api";
import type { DryRunResult, ScopeChange } from "../types";

const CHANGE_LABELS: Record<string, string> = {
  new: "New",
  changed: "Changed",
  missing: "Missing",
};

interface Props {
  orgId: string;
  onClose: () => void;
  onApplied: () => void;
}

export function AssetImportWizard({ orgId, onClose, onApplied }: Props) {
  const [file, setFile] = useState<File | null>(null);
  const [dryRun, setDryRun] = useState<DryRunResult | null>(null);
  const [excluded, setExcluded] = useState<Set<number>>(new Set());
  const [loading, setLoading] = useState(false);
  const [applying, setApplying] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [appliedCount, setAppliedCount] = useState<number | null>(null);

  async function handlePreview() {
    if (!file) return;
    setLoading(true);
    setError(null);
    try {
      const result = await api.dryRunWorkbookImport(orgId, file);
      setDryRun(result);
      setExcluded(new Set());
    } catch (e) {
      setError(e instanceof Error ? e.message : "Preview failed");
    } finally {
      setLoading(false);
    }
  }

  function toggleExcluded(idx: number) {
    setExcluded((prev) => {
      const next = new Set(prev);
      if (next.has(idx)) next.delete(idx);
      else next.add(idx);
      return next;
    });
  }

  function selectedChanges(): ScopeChange[] {
    if (!dryRun) return [];
    return dryRun.changes.filter((c, idx) => {
      if (c.change_type !== "new" && c.change_type !== "changed") return false;
      return !excluded.has(idx);
    });
  }

  async function handleApply() {
    setApplying(true);
    setError(null);
    try {
      const result = await api.applyWorkbookImport(orgId, selectedChanges());
      setAppliedCount(result.applied);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Apply failed");
    } finally {
      setApplying(false);
    }
  }

  const applicableCount = selectedChanges().length;

  return (
    <div className="wizard-overlay" onClick={onClose}>
      <div className="wizard" onClick={(e) => e.stopPropagation()}>
        <div className="wizard-header">
          <span className="wizard-title">Import Assets from Workbook</span>
          <button className="wizard-close" onClick={onClose} aria-label="Close">×</button>
        </div>

        <div className="wizard-body">
          {error && <div className="form-error">{error}</div>}

          {appliedCount !== null ? (
            <div className="wizard-complete-msg">
              Applied {appliedCount} change{appliedCount === 1 ? "" : "s"} to the scope graph.
            </div>
          ) : !dryRun ? (
            <>
              <div className="field-hint">
                Upload an Authorized-Entities workbook (.xlsx). Nothing is written until you
                review and confirm the changes below.
              </div>
              <div className="form-field">
                <input
                  type="file"
                  accept=".xlsx"
                  onChange={(e) => setFile(e.target.files?.[0] ?? null)}
                />
              </div>
            </>
          ) : (
            <>
              <div className="field-hint">
                {dryRun.changes.length === 0
                  ? "No changes detected — the scope graph already matches this workbook."
                  : "Review the changes below. Uncheck any row you don't want applied, then confirm."}
              </div>
              {dryRun.changes.length > 0 && (
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
                      {dryRun.changes.map((c, idx) => {
                        const applicable = c.change_type === "new" || c.change_type === "changed";
                        return (
                          <tr key={`${c.entity_type}-${c.natural_key}-${idx}`}>
                            <td>
                              {applicable && (
                                <input
                                  type="checkbox"
                                  checked={!excluded.has(idx)}
                                  onChange={() => toggleExcluded(idx)}
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
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              )}
            </>
          )}
        </div>

        <div className="wizard-footer">
          <div style={{ flex: 1 }} />
          {appliedCount !== null ? (
            <button className="btn-primary" onClick={onApplied}>Done</button>
          ) : !dryRun ? (
            <>
              <button className="btn-ghost" onClick={onClose}>Cancel</button>
              <button className="btn-primary" onClick={handlePreview} disabled={!file || loading}>
                {loading ? "Previewing…" : "Preview Import"}
              </button>
            </>
          ) : (
            <>
              <button className="btn-ghost" onClick={() => setDryRun(null)}>Back</button>
              <button
                className="btn-primary"
                onClick={handleApply}
                disabled={applying || applicableCount === 0}
              >
                {applying ? "Applying…" : `Apply ${applicableCount} Change${applicableCount === 1 ? "" : "s"}`}
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
