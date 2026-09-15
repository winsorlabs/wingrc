import { useState } from "react";
import { api } from "../api";
import { CANDIDATE_STATES, CLASSIFICATIONS, COVERAGE_BASES, EVIDENCE_TYPES } from "../lib/baseline";
import type {
  BaselineControlDraft,
  BaselineEvidenceDraft,
  BaselineImportPreview,
  ProductMetaDraft,
} from "../types";

// Mirrors AssetImportWizard's dry-run -> review -> apply discipline, with
// one deliberate divergence: a baseline YAML is one atomic unit, not a
// list of independently-selectable rows, so there is no per-row exclusion
// set here -- Apply always re-validates and re-applies the whole file
// (see backend/app/baseline_import.py's module docstring for why apply
// revalidates instead of trusting a token from this dry-run). Rows in the
// documents-mode review table below ARE individually editable before
// Apply, but that's still all-or-nothing at Apply time -- editing a field
// is not the same thing as opting a row in/out of the import.
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

function emptyEvidence(): BaselineEvidenceDraft {
  return { artifact: "", type: EVIDENCE_TYPES[1], kb: null };
}

export function ToolImportWizard({ onClose, onApplied }: Props) {
  const [mode, setMode] = useState<Mode>("yaml");

  // YAML-upload mode
  const [file, setFile] = useState<File | null>(null);

  // Document-ingestion mode
  const [productKey, setProductKey] = useState("");
  const [ingestFiles, setIngestFiles] = useState<File[]>([]);
  const [ingesting, setIngesting] = useState(false);

  // The editable structured draft (documents mode only) -- product meta +
  // one row per control. `latestYaml` is never hand-edited; it's whatever
  // the server most recently re-serialized from a validated draft, and is
  // the only thing Apply ever submits.
  const [draft, setDraft] = useState<{ product: ProductMetaDraft; controls: BaselineControlDraft[] } | null>(null);
  const [latestYaml, setLatestYaml] = useState<string | null>(null);
  // Free-text buffers for comma-separated list fields (control ids,
  // objectives, assumed_config) -- kept separate from the committed draft
  // so retyping a trailing comma mid-edit doesn't get eaten by a
  // join()-derived controlled value on every keystroke. Committed to the
  // real draft onBlur. Keyed "<row_index>:<field>" ("product:<field>" for
  // product-level list fields).
  const [listText, setListText] = useState<Record<string, string>>({});
  // True once any field has been edited since the last successful
  // generate/re-check -- Apply is blocked while true so it can never fire
  // against a `latestYaml` that no longer matches what's on screen.
  const [dirty, setDirty] = useState(false);

  const [preview, setPreview] = useState<BaselineImportPreview | null>(null);
  const [checking, setChecking] = useState(false);
  const [applying, setApplying] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [applied, setApplied] = useState<{ baseline_controls: number; evidence_specs: number } | null>(null);

  function seedListText(product: ProductMetaDraft, controls: BaselineControlDraft[]) {
    const next: Record<string, string> = { "product:assumed_config": product.assumed_config.join(", ") };
    for (const c of controls) {
      next[`${c.row_index}:control`] = c.control.join(", ");
      next[`${c.row_index}:objectives`] = c.objectives.join(", ");
    }
    setListText(next);
  }

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
      setDraft({ product: result.product, controls: result.controls });
      seedListText(result.product, result.controls);
      setLatestYaml(result.yaml);
      setPreview(result.preview);
      setDirty(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Generation failed");
    } finally {
      setIngesting(false);
    }
  }

  async function handleRecheck() {
    if (!draft) return;
    setChecking(true);
    setError(null);
    try {
      const result = await api.previewStructuredBaselineImport(draft);
      setDraft({ product: result.product, controls: result.controls });
      seedListText(result.product, result.controls);
      setLatestYaml(result.yaml);
      setPreview(result.preview);
      setDirty(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Re-check failed");
    } finally {
      setChecking(false);
    }
  }

  async function handleApply() {
    const submitFile = mode === "yaml" ? file : latestYaml !== null ? yamlToFile(latestYaml) : null;
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

  function updateProductField(field: keyof ProductMetaDraft, value: string) {
    setDraft((prev) => (prev ? { ...prev, product: { ...prev.product, [field]: value } } : prev));
    setDirty(true);
  }

  function updateControlField<K extends keyof BaselineControlDraft>(
    rowIndex: number,
    field: K,
    value: BaselineControlDraft[K]
  ) {
    setDraft((prev) =>
      prev
        ? { ...prev, controls: prev.controls.map((c) => (c.row_index === rowIndex ? { ...c, [field]: value } : c)) }
        : prev
    );
    setDirty(true);
  }

  function updateEvidenceField(rowIndex: number, evIdx: number, patch: Partial<BaselineEvidenceDraft>) {
    setDraft((prev) =>
      prev
        ? {
            ...prev,
            controls: prev.controls.map((c) =>
              c.row_index === rowIndex
                ? { ...c, evidence: c.evidence.map((e, i) => (i === evIdx ? { ...e, ...patch } : e)) }
                : c
            ),
          }
        : prev
    );
    setDirty(true);
  }

  function addEvidenceRow(rowIndex: number) {
    setDraft((prev) =>
      prev
        ? {
            ...prev,
            controls: prev.controls.map((c) =>
              c.row_index === rowIndex ? { ...c, evidence: [...c.evidence, emptyEvidence()] } : c
            ),
          }
        : prev
    );
    setDirty(true);
  }

  function removeEvidenceRow(rowIndex: number, evIdx: number) {
    setDraft((prev) =>
      prev
        ? {
            ...prev,
            controls: prev.controls.map((c) =>
              c.row_index === rowIndex ? { ...c, evidence: c.evidence.filter((_, i) => i !== evIdx) } : c
            ),
          }
        : prev
    );
    setDirty(true);
  }

  function commitListField(rowIndex: number, field: "control" | "objectives") {
    const text = listText[`${rowIndex}:${field}`] ?? "";
    const parsed = text.split(",").map((s) => s.trim()).filter(Boolean);
    updateControlField(rowIndex, field, parsed);
  }

  function commitProductListField() {
    const text = listText["product:assumed_config"] ?? "";
    const parsed = text.split(",").map((s) => s.trim()).filter(Boolean);
    setDraft((prev) => (prev ? { ...prev, product: { ...prev.product, assumed_config: parsed } } : prev));
    setDirty(true);
  }

  function rowProblem(rowIndex: number, field: string) {
    return preview?.row_problems.find((p) => p.row_index === rowIndex && p.field === field);
  }

  function rowHasAnyProblem(rowIndex: number) {
    return !!preview?.row_problems.some((p) => p.row_index === rowIndex);
  }

  const hasProblems = !!preview && preview.problems.length > 0;
  const canGenerate = mode === "documents" && ingestFiles.length > 0 && !!productKey.trim() && !ingesting;
  // Problems with no specific row (e.g. a missing product field) --
  // documents mode shows these separately since the table only ever
  // covers control rows.
  const nonRowProblems = preview?.row_problems.filter((p) => p.row_index === null) ?? [];

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
                    {_MAX_INGEST_FILES}). An AI model drafts a candidate mapping for you to review below as an
                    editable table — it never writes anything on its own, and it always leaves "coverage basis"
                    unset for you to confirm on every control the vendor claims to satisfy.
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
          ) : mode === "documents" && draft ? (
            <>
              {preview.affected_org_count > 0 && (
                <div className="form-error" style={{ marginBottom: "0.6rem" }}>
                  ⚠ {preview.affected_org_count} org{preview.affected_org_count === 1 ? "" : "s"} already{" "}
                  {preview.affected_org_count === 1 ? "has" : "have"} this product active or candidate:{" "}
                  {preview.affected_org_names.join(", ")}. Re-importing changes the compliance mapping those orgs'
                  control states were set under.
                </div>
              )}

              {nonRowProblems.length > 0 && (
                <div className="form-error" style={{ marginBottom: "0.6rem" }}>
                  <div style={{ marginBottom: "0.3rem" }}>Problems not tied to a specific control:</div>
                  {nonRowProblems.map((p, i) => (
                    <div key={i}>⚠ {p.message}</div>
                  ))}
                </div>
              )}

              <div className="field-hint" style={{ marginBottom: "0.5rem" }}>
                {preview.product_is_new ? (
                  <>New product — edit fields below, then Re-check.</>
                ) : (
                  <>Updates existing product: <strong>{preview.product_name}</strong> ({preview.product_key})</>
                )}
                {dirty && " · Edited since last Re-check — click Re-check before Apply."}
              </div>

              <fieldset className="form-field" style={{ marginBottom: "0.75rem" }}>
                <legend>Product</legend>
                <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: "0.5rem" }}>
                  {(["key", "name", "provider", "category", "asset_type", "framework", "role"] as const).map(
                    (f) => (
                      <label key={f} style={{ fontSize: "0.72rem" }}>
                        {f}
                        <input
                          type="text"
                          value={draft.product[f]}
                          onChange={(e) => updateProductField(f, e.target.value)}
                        />
                      </label>
                    )
                  )}
                  <label style={{ fontSize: "0.72rem" }}>
                    assumed_config (comma-separated)
                    <input
                      type="text"
                      value={listText["product:assumed_config"] ?? draft.product.assumed_config.join(", ")}
                      onChange={(e) =>
                        setListText((prev) => ({ ...prev, "product:assumed_config": e.target.value }))
                      }
                      onBlur={commitProductListField}
                    />
                  </label>
                </div>
              </fieldset>

              <div className="table-scroll">
                <table className="contacts-table control-row-table">
                  <thead>
                    <tr>
                      <th>Control(s)</th>
                      <th>Classification</th>
                      <th>Coverage basis</th>
                      <th>Candidate state</th>
                      <th>Objectives</th>
                      <th>Provider contribution</th>
                      <th>Customer action</th>
                      <th>Note</th>
                      <th>Scope note</th>
                      <th>Evidence</th>
                    </tr>
                  </thead>
                  <tbody>
                    {draft.controls.map((row) => {
                      const idx = row.row_index;
                      const rowFlagged = rowHasAnyProblem(idx);
                      const covProblem = rowProblem(idx, "coverage_basis");
                      const clsProblem = rowProblem(idx, "classification");
                      const cstProblem = rowProblem(idx, "candidate_state");
                      const controlProblem = rowProblem(idx, "control");
                      const objProblem = rowProblem(idx, "objectives");
                      const evProblem = rowProblem(idx, "evidence");
                      return (
                        <tr key={idx} className={rowFlagged ? "needs-decision" : undefined}>
                          <td>
                            <input
                              type="text"
                              aria-label={`Control(s) for row ${idx}`}
                              className={controlProblem ? "field-needs-decision" : undefined}
                              value={listText[`${idx}:control`] ?? row.control.join(", ")}
                              onChange={(e) =>
                                setListText((prev) => ({ ...prev, [`${idx}:control`]: e.target.value }))
                              }
                              onBlur={() => commitListField(idx, "control")}
                            />
                            {controlProblem && <div className="field-problem-hint">{controlProblem.message}</div>}
                          </td>
                          <td>
                            <select
                              aria-label={`Classification for row ${idx}`}
                              className={clsProblem ? "field-needs-decision" : undefined}
                              value={row.classification ?? ""}
                              onChange={(e) => updateControlField(idx, "classification", e.target.value || null)}
                            >
                              <option value="">-- select --</option>
                              {CLASSIFICATIONS.map((c) => (
                                <option key={c} value={c}>{c}</option>
                              ))}
                            </select>
                            {clsProblem && <div className="field-problem-hint">{clsProblem.message}</div>}
                          </td>
                          <td>
                            <select
                              aria-label={`Coverage basis for row ${idx}`}
                              className={covProblem ? "field-needs-decision" : undefined}
                              value={row.coverage_basis ?? ""}
                              disabled={row.classification === "customer_owns"}
                              onChange={(e) => updateControlField(idx, "coverage_basis", e.target.value || null)}
                            >
                              <option value="">-- select --</option>
                              {COVERAGE_BASES.map((c) => (
                                <option key={c} value={c}>{c}</option>
                              ))}
                            </select>
                            {row.classification === "customer_owns" ? (
                              <div className="field-hint">not applicable</div>
                            ) : (
                              covProblem && <div className="field-problem-hint">{covProblem.message}</div>
                            )}
                          </td>
                          <td>
                            <select
                              aria-label={`Candidate state for row ${idx}`}
                              className={cstProblem ? "field-needs-decision" : undefined}
                              value={row.candidate_state ?? ""}
                              onChange={(e) => updateControlField(idx, "candidate_state", e.target.value || null)}
                            >
                              <option value="">-- select --</option>
                              {CANDIDATE_STATES.map((c) => (
                                <option key={c} value={c}>{c}</option>
                              ))}
                            </select>
                            {cstProblem && <div className="field-problem-hint">{cstProblem.message}</div>}
                          </td>
                          <td>
                            <input
                              type="text"
                              className={objProblem ? "field-needs-decision" : undefined}
                              value={listText[`${idx}:objectives`] ?? row.objectives.join(", ")}
                              onChange={(e) =>
                                setListText((prev) => ({ ...prev, [`${idx}:objectives`]: e.target.value }))
                              }
                              onBlur={() => commitListField(idx, "objectives")}
                            />
                            {objProblem && <div className="field-problem-hint">{objProblem.message}</div>}
                          </td>
                          <td>
                            <input
                              type="text"
                              value={row.provider_contribution ?? ""}
                              onChange={(e) => updateControlField(idx, "provider_contribution", e.target.value)}
                            />
                          </td>
                          <td>
                            <input
                              type="text"
                              value={row.customer_action ?? ""}
                              onChange={(e) => updateControlField(idx, "customer_action", e.target.value)}
                            />
                          </td>
                          <td>
                            <input
                              type="text"
                              value={row.note ?? ""}
                              onChange={(e) => updateControlField(idx, "note", e.target.value)}
                            />
                          </td>
                          <td>
                            <input
                              type="text"
                              value={row.scope_note ?? ""}
                              onChange={(e) => updateControlField(idx, "scope_note", e.target.value)}
                            />
                          </td>
                          <td>
                            <div className={`evidence-mini-list${evProblem ? " field-needs-decision" : ""}`}>
                              {row.evidence.map((ev, evIdx) => (
                                <div className="evidence-mini-row" key={evIdx}>
                                  <input
                                    type="text"
                                    placeholder="artifact"
                                    value={ev.artifact}
                                    onChange={(e) =>
                                      updateEvidenceField(idx, evIdx, { artifact: e.target.value })
                                    }
                                  />
                                  <select
                                    value={ev.type}
                                    onChange={(e) => updateEvidenceField(idx, evIdx, { type: e.target.value })}
                                  >
                                    {EVIDENCE_TYPES.map((t) => (
                                      <option key={t} value={t}>{t}</option>
                                    ))}
                                  </select>
                                  <input
                                    type="text"
                                    placeholder="kb (optional)"
                                    value={ev.kb ?? ""}
                                    onChange={(e) => updateEvidenceField(idx, evIdx, { kb: e.target.value })}
                                  />
                                  <button
                                    type="button"
                                    className="chip-remove"
                                    aria-label="Remove evidence item"
                                    onClick={() => removeEvidenceRow(idx, evIdx)}
                                  >
                                    ×
                                  </button>
                                </div>
                              ))}
                              <button type="button" className="btn-ghost btn-sm" onClick={() => addEvidenceRow(idx)}>
                                + Add evidence
                              </button>
                              {evProblem && <div className="field-problem-hint">{evProblem.message}</div>}
                            </div>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>

              <button className="btn-ghost btn-sm" onClick={handleRecheck} disabled={checking} style={{ marginTop: "0.6rem" }}>
                {checking ? "Re-checking…" : "Re-check"}
              </button>
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
                  if (mode === "documents") {
                    setDraft(null);
                    setLatestYaml(null);
                    setListText({});
                    setDirty(false);
                  }
                }}
              >
                Back
              </button>
              <button
                className="btn-primary"
                onClick={handleApply}
                disabled={applying || hasProblems || (mode === "documents" && dirty)}
              >
                {applying ? "Applying…" : "Apply Import"}
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
