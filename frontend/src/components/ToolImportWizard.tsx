import { useState } from "react";
import { api } from "../api";
import type { BaselineImportPreview } from "../types";

// Mirrors AssetImportWizard's dry-run -> review -> apply discipline, with
// one deliberate divergence: a baseline YAML is one atomic unit, not a
// list of independently-selectable rows, so there is no per-row exclusion
// set here -- Apply always re-validates and re-applies the whole file
// (see backend/app/baseline_import.py's module docstring for why apply
// revalidates instead of trusting a token from this dry-run).
interface Props {
  onClose: () => void;
  onApplied: () => void;
}

export function ToolImportWizard({ onClose, onApplied }: Props) {
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<BaselineImportPreview | null>(null);
  const [loading, setLoading] = useState(false);
  const [applying, setApplying] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [applied, setApplied] = useState<{ baseline_controls: number; evidence_specs: number } | null>(null);

  async function handlePreview() {
    if (!file) return;
    setLoading(true);
    setError(null);
    try {
      const result = await api.dryRunBaselineImport(file);
      setPreview(result);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Preview failed");
    } finally {
      setLoading(false);
    }
  }

  async function handleApply() {
    if (!file) return;
    setApplying(true);
    setError(null);
    try {
      const result = await api.applyBaselineImport(file);
      setApplied(result);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Apply failed");
    } finally {
      setApplying(false);
    }
  }

  const hasProblems = !!preview && preview.problems.length > 0;

  return (
    <div className="wizard-overlay" onClick={onClose}>
      <div className="wizard" onClick={(e) => e.stopPropagation()}>
        <div className="wizard-header">
          <span className="wizard-title">Import Baseline</span>
          <button className="wizard-close" onClick={onClose} aria-label="Close">×</button>
        </div>

        <div className="wizard-body">
          {error && <div className="form-error">{error}</div>}

          {applied ? (
            <div className="wizard-complete-msg">
              Imported {applied.baseline_controls} baseline control{applied.baseline_controls === 1 ? "" : "s"} and{" "}
              {applied.evidence_specs} evidence spec{applied.evidence_specs === 1 ? "" : "s"}. The product lands
              unpublished — publish it from the detail view once you've reviewed the mapping.
            </div>
          ) : !preview ? (
            <>
              <div className="field-hint">
                Upload a baseline YAML file. Nothing is written until you review the diff below.
              </div>
              <div className="form-field">
                <input
                  type="file"
                  accept=".yaml,.yml"
                  onChange={(e) => setFile(e.target.files?.[0] ?? null)}
                />
              </div>
            </>
          ) : (
            <>
              {hasProblems ? (
                <div className="form-error">
                  <div style={{ marginBottom: "0.4rem" }}>
                    This file has {preview.problems.length} problem{preview.problems.length === 1 ? "" : "s"} and
                    cannot be imported:
                  </div>
                  {preview.problems.map((p, i) => (
                    <div key={i}>⚠ {p}</div>
                  ))}
                </div>
              ) : (
                <>
                  <div className="field-hint">
                    {preview.product_is_new ? (
                      <>New product: <strong>{preview.product_name}</strong> ({preview.product_key})</>
                    ) : (
                      <>Updates existing product: <strong>{preview.product_name}</strong> ({preview.product_key})</>
                    )}
                  </div>

                  {preview.affected_org_count > 0 && (
                    <div className="form-error">
                      ⚠ {preview.affected_org_count} org{preview.affected_org_count === 1 ? "" : "s"} already{" "}
                      {preview.affected_org_count === 1 ? "has" : "have"} this product active or candidate:{" "}
                      {preview.affected_org_names.join(", ")}. Re-importing changes the compliance mapping those
                      orgs' control states were set under.
                    </div>
                  )}

                  {preview.control_changes.length === 0 ? (
                    <div className="empty">No control changes in this file.</div>
                  ) : (
                    <div className="table-scroll">
                      <table className="contacts-table">
                        <thead>
                          <tr>
                            <th>Control</th>
                            <th>Change</th>
                            <th>Classification</th>
                            <th>Coverage basis</th>
                          </tr>
                        </thead>
                        <tbody>
                          {preview.control_changes.map((c) => (
                            <tr key={c.control_id}>
                              <td>{c.control_id}</td>
                              <td>{c.change_type}</td>
                              <td>{c.classification}</td>
                              <td className={c.coverage_basis === "platform_only" ? "coverage-basis-platform-only" : undefined}>
                                {c.coverage_basis}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </>
              )}
            </>
          )}
        </div>

        <div className="wizard-footer">
          <div style={{ flex: 1 }} />
          {applied ? (
            <button className="btn-primary" onClick={onApplied}>Done</button>
          ) : !preview ? (
            <>
              <button className="btn-ghost" onClick={onClose}>Cancel</button>
              <button className="btn-primary" onClick={handlePreview} disabled={!file || loading}>
                {loading ? "Previewing…" : "Preview Import"}
              </button>
            </>
          ) : (
            <>
              <button className="btn-ghost" onClick={() => setPreview(null)}>Back</button>
              <button className="btn-primary" onClick={handleApply} disabled={applying || hasProblems}>
                {applying ? "Applying…" : "Apply Import"}
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
