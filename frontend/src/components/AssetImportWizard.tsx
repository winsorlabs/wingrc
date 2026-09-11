import { useState } from "react";
import { api } from "../api";
import type { DryRunResult, ScopeChange } from "../types";
import { ScopeChangeDiffTable } from "./ScopeChangeDiffTable";

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
              {dryRun.warnings.length > 0 && (
                <div className="form-error">
                  {dryRun.warnings.map((w, i) => (
                    <div key={i}>⚠ {w}</div>
                  ))}
                </div>
              )}
              {dryRun.changes.length > 0 && (
                <ScopeChangeDiffTable
                  changes={dryRun.changes}
                  excluded={excluded}
                  onToggle={toggleExcluded}
                />
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
