import { useState } from "react";
import { api } from "../api";
import type { BaselineImportPreview } from "../types";

// Mirrors AssetImportWizard's dry-run -> review -> apply discipline, with
// one deliberate divergence: a baseline YAML is one atomic unit, not a
// list of independently-selectable rows, so there is no per-row exclusion
// set here -- Apply always re-validates and re-applies the whole file
// (see backend/app/baseline_import.py's module docstring for why apply
// revalidates instead of trusting a token from this dry-run).
//
// Two ways to reach the same review: upload a finished YAML, or generate
// a candidate from vendor documents (backend/app/importers/document.py)
// and edit it before it enters the exact same dry-run/apply path. Either
// way, apply always lands the product unpublished and forces
// coverage_basis to be explicitly set for every non-customer_owns entry
// -- the AI pipeline deliberately never fills that in itself.
interface Props {
  onClose: () => void;
  onApplied: () => void;
}

type Mode = "yaml" | "documents";

const _MAX_INGEST_FILES = 2;

function yamlToFile(text: string): File {
  return new File([text], "reviewed-baseline.yaml", { type: "application/x-yaml" });
}

export function ToolImportWizard({ onClose, onApplied }: Props) {
  const [mode, setMode] = useState<Mode>("yaml");

  // YAML-upload mode
  const [file, setFile] = useState<File | null>(null);

  // Document-ingestion mode
  const [productKey, setProductKey] = useState("");
  const [ingestFiles, setIngestFiles] = useState<File[]>([]);
  const [ingesting, setIngesting] = useState(false);
  const [yamlDraft, setYamlDraft] = useState<string | null>(null);

  const [preview, setPreview] = useState<BaselineImportPreview | null>(null);
  const [checking, setChecking] = useState(false);
  const [applying, setApplying] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [applied, setApplied] = useState<{ baseline_controls: number; evidence_specs: number } | null>(null);

  async function handlePreview() {
    if (!file) return;
    setChecking(true);
    setError(null);
    try {
      const result = await api.dryRunBaselineImport(file);
      setPreview(result);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Preview failed");
    } finally {
      setChecking(false);
    }
  }

  async function handleGenerate() {
    if (ingestFiles.length === 0 || !productKey.trim()) return;
    setIngesting(true);
    setError(null);
    try {
      const result = await api.ingestBaselineFromDocuments(ingestFiles, productKey.trim());
      setYamlDraft(result.yaml);
      setPreview(result.preview);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Generation failed");
    } finally {
      setIngesting(false);
    }
  }

  async function handleRecheck() {
    if (yamlDraft === null) return;
    setChecking(true);
    setError(null);
    try {
      const result = await api.dryRunBaselineImport(yamlToFile(yamlDraft));
      setPreview(result);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Re-check failed");
    } finally {
      setChecking(false);
    }
  }

  async function handleApply() {
    const submitFile = mode === "yaml" ? file : yamlDraft !== null ? yamlToFile(yamlDraft) : null;
    if (!submitFile) return;
    setApplying(true);
    setError(null);
    try {
      const result = await api.applyBaselineImport(submitFile);
      // Attach the original source documents as real ProductDocument rows
      // now that a real product_id exists -- reuses the document-upload
      // machinery as-is, no new storage-handling code for this path.
      if (mode === "documents") {
        for (const f of ingestFiles) {
          await api.uploadToolDocument(result.product_id, f, { title: f.name, kind: "other" });
        }
      }
      setApplied(result);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Apply failed");
    } finally {
      setApplying(false);
    }
  }

  const hasProblems = !!preview && preview.problems.length > 0;
  const canGenerate = mode === "documents" && ingestFiles.length > 0 && !!productKey.trim() && !ingesting;

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
              <div style={{ marginBottom: "0.75rem" }}>
                <button
                  className={mode === "yaml" ? "btn-primary btn-sm" : "btn-ghost btn-sm"}
                  onClick={() => setMode("yaml")}
                >
                  Upload YAML
                </button>{" "}
                <button
                  className={mode === "documents" ? "btn-primary btn-sm" : "btn-ghost btn-sm"}
                  onClick={() => setMode("documents")}
                >
                  Generate from vendor documents
                </button>
              </div>

              {mode === "yaml" ? (
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
                  <div className="field-hint">
                    Upload the vendor's CRM and/or MSP baseline document (PDF or Word, up to{" "}
                    {_MAX_INGEST_FILES}). An AI model drafts a candidate mapping for you to edit and review below —
                    it never writes anything on its own, and it always leaves "coverage basis" unset for you to
                    confirm on every control the vendor claims to satisfy.
                  </div>
                  <div className="form-field">
                    <label>
                      Product key
                      <input
                        type="text"
                        value={productKey}
                        onChange={(e) => setProductKey(e.target.value)}
                        placeholder="e.g. rocketcyber"
                      />
                    </label>
                  </div>
                  <div className="form-field">
                    <label>
                      Vendor document(s)
                      <input
                        type="file"
                        accept=".pdf,.doc,.docx"
                        multiple
                        onChange={(e) =>
                          setIngestFiles(Array.from(e.target.files ?? []).slice(0, _MAX_INGEST_FILES))
                        }
                      />
                    </label>
                  </div>
                  {ingestFiles.length > 0 && (
                    <div className="field-hint">{ingestFiles.map((f) => f.name).join(", ")}</div>
                  )}
                </>
              )}
            </>
          ) : (
            <>
              {mode === "documents" && yamlDraft !== null && (
                <div className="form-field">
                  <label>
                    Generated baseline (edit before re-checking — set <code>coverage_basis</code> on every
                    control the vendor is credited for)
                    <textarea
                      rows={16}
                      value={yamlDraft}
                      onChange={(e) => setYamlDraft(e.target.value)}
                      style={{ fontFamily: "monospace", width: "100%", boxSizing: "border-box" }}
                    />
                  </label>
                  <button className="btn-ghost btn-sm" onClick={handleRecheck} disabled={checking} style={{ marginTop: "0.5rem" }}>
                    {checking ? "Re-checking…" : "Re-check"}
                  </button>
                </div>
              )}

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
              {mode === "yaml" ? (
                <button className="btn-primary" onClick={handlePreview} disabled={!file || checking}>
                  {checking ? "Previewing…" : "Preview Import"}
                </button>
              ) : (
                <button className="btn-primary" onClick={handleGenerate} disabled={!canGenerate}>
                  {ingesting ? "Generating…" : "Generate Baseline"}
                </button>
              )}
            </>
          ) : (
            <>
              <button
                className="btn-ghost"
                onClick={() => {
                  setPreview(null);
                  if (mode === "documents") setYamlDraft(null);
                }}
              >
                Back
              </button>
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
