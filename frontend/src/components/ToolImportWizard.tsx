import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { CANDIDATE_STATES, CLASSIFICATIONS, COVERAGE_BASES, EVIDENCE_TYPES } from "../lib/baseline";
import type {
  BaselineControlDraft,
  BaselineEvidenceDraft,
  BaselineImportPreview,
  ProductDetail,
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
//
// A third way in: `editProductId` (set from ToolDetailPanel.tsx's "Edit
// Baseline Mapping" button) skips the chooser screen entirely, loads the
// product's CURRENT mapping via the same read model ToolDetailPanel uses,
// transforms it into this exact draft shape, and immediately re-validates
// it through the structured endpoint -- same table, same Re-check/Apply,
// no new backend endpoint. `control` is always a single-id list per row
// once applied (multi-control batch grouping isn't recoverable from the
// read model and doesn't survive a round trip through this screen -- a
// cosmetic loss only, see docs/roadmap.md).
interface Props {
  onClose: () => void;
  onApplied: () => void;
  editProductId?: string;
}

type Mode = "yaml" | "documents";

const _MAX_INGEST_FILES = 2;

function yamlToFile(text: string): File {
  return new File([text], "reviewed-baseline.yaml", { type: "application/x-yaml" });
}

function emptyEvidence(): BaselineEvidenceDraft {
  return { artifact: "", type: EVIDENCE_TYPES[1], kb: null };
}

function productDetailToDraft(
  detail: ProductDetail
): { product: ProductMetaDraft; controls: BaselineControlDraft[] } {
  return {
    product: {
      key: detail.key,
      name: detail.name,
      provider: detail.provider,
      category: detail.category,
      asset_type: detail.asset_type,
      framework: "",
      role: detail.role,
      assumed_config: detail.assumed_config,
      source_docs: detail.source_docs,
      ai_generated_at: detail.ai_generated_at,
      ai_generated_model: detail.ai_generated_model,
    },
    controls: detail.baseline_controls.map((bc, i) => ({
      row_index: i,
      control: [bc.control_id],
      classification: bc.classification,
      coverage_basis: bc.coverage_basis,
      candidate_state: bc.candidate_state,
      objectives: bc.objectives,
      provider_contribution: bc.provider_contribution,
      customer_action: bc.customer_action,
      evidence: bc.evidence_specs.map((e) => ({
        artifact: e.artifact_description,
        type: e.evidence_type,
        kb: e.kb_reference,
      })),
      note: bc.note,
      scope_note: bc.scope_note,
    })),
  };
}

export function ToolImportWizard({ onClose, onApplied, editProductId }: Props) {
  const [mode, setMode] = useState<Mode>(editProductId ? "documents" : "yaml");
  // True until the edit entry point's initial load+validate completes --
  // avoids flashing the "Upload YAML / Generate from vendor documents"
  // chooser screen (or its footer buttons) before `preview`/`draft` are
  // populated.
  const [editLoading, setEditLoading] = useState(!!editProductId);

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

  // Sticky horizontal scrollbar for the control-row table: a slim strip
  // pinned to the bottom of `.wizard-body`'s own scroll area (not the
  // bottom of the table, which can be many rows tall) whose scroll
  // position is mirrored both ways with the real table wrapper. No CSS-only
  // way to pin a container's own overflow scrollbar elsewhere exists in
  // any current browser -- this is the standard synced-strip pattern.
  const tableScrollRef = useRef<HTMLDivElement>(null);
  const stickyScrollRef = useRef<HTMLDivElement>(null);
  const [tableScrollWidth, setTableScrollWidth] = useState(0);
  const [tableClientWidth, setTableClientWidth] = useState(0);

  function seedListText(product: ProductMetaDraft, controls: BaselineControlDraft[]) {
    const next: Record<string, string> = { "product:assumed_config": product.assumed_config.join(", ") };
    for (const c of controls) {
      next[`${c.row_index}:control`] = c.control.join(", ");
      next[`${c.row_index}:objectives`] = c.objectives.join(", ");
    }
    setListText(next);
  }

  useEffect(() => {
    if (!editProductId) return;
    setEditLoading(true);
    setError(null);
    api
      .getToolDetail(editProductId)
      .then((detail) => productDetailToDraft(detail))
      .then((initialDraft) => api.previewStructuredBaselineImport(initialDraft))
      .then((result) => {
        setDraft({ product: result.product, controls: result.controls });
        seedListText(result.product, result.controls);
        setLatestYaml(result.yaml);
        setPreview(result.preview);
        setDirty(false);
      })
      .catch((e) => setError(e instanceof Error ? e.message : "Could not load current mapping"))
      .finally(() => setEditLoading(false));
    // Runs once per mount for a given product -- editProductId doesn't
    // change over this component's lifetime.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editProductId]);

  useEffect(() => {
    function measure() {
      const el = tableScrollRef.current;
      if (!el) return;
      setTableScrollWidth(el.scrollWidth);
      setTableClientWidth(el.clientWidth);
    }
    measure();
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, [draft]);

  function handleTableScroll() {
    if (stickyScrollRef.current && tableScrollRef.current) {
      stickyScrollRef.current.scrollLeft = tableScrollRef.current.scrollLeft;
    }
  }

  function handleStickyScroll() {
    if (stickyScrollRef.current && tableScrollRef.current) {
      tableScrollRef.current.scrollLeft = stickyScrollRef.current.scrollLeft;
    }
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
      // machinery as-is, no new storage-handling code for this path. Not
      // relevant when editing an existing product -- there's no new
      // vendor file uploaded in an edit session.
      if (mode === "documents" && !editProductId) {
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

  function addControlRow() {
    setDraft((prev) => {
      if (!prev) return prev;
      // Always a fresh, non-colliding index -- server-provided rows are
      // always a contiguous 0..N-1 from the last response, so the current
      // length is guaranteed unused (even across multiple adds before the
      // next Re-check, since the array grows by one each time).
      const newRow: BaselineControlDraft = {
        row_index: prev.controls.length,
        control: [],
        classification: null,
        coverage_basis: null,
        candidate_state: "pending_evidence",
        objectives: [],
        provider_contribution: null,
        customer_action: null,
        evidence: [],
        note: null,
        scope_note: null,
      };
      return { ...prev, controls: [...prev.controls, newRow] };
    });
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
      <div className="wizard wizard--wide" onClick={(e) => e.stopPropagation()}>
        <div className="wizard-header">
          <span className="wizard-title">{editProductId ? "Edit Baseline Mapping" : "Import Baseline"}</span>
          <button className="wizard-close" onClick={onClose} aria-label="Close">×</button>
        </div>

        <div className="wizard-body">
          {error && <div className="form-error">{error}</div>}

          {applied ? (
            <div className="wizard-complete-msg">
              {editProductId ? "Saved" : "Imported"} {applied.baseline_controls} baseline control
              {applied.baseline_controls === 1 ? "" : "s"} and {applied.evidence_specs} evidence spec
              {applied.evidence_specs === 1 ? "" : "s"}. The product is now <strong>unpublished</strong> — publish
              it again from the detail view once you've reviewed the mapping.
            </div>
          ) : editLoading ? (
            <div className="loading">Loading current mapping…</div>
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
                          // Changing the key while editing would retarget
                          // Apply at a different (or brand new) product,
                          // since apply upserts by key -- not a typo a
                          // reviewer should be able to make by accident.
                          disabled={f === "key" && !!editProductId}
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

              <div className="table-scroll" ref={tableScrollRef} onScroll={handleTableScroll}>
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
              {tableScrollWidth > tableClientWidth && (
                <div className="sticky-scroll-x" ref={stickyScrollRef} onScroll={handleStickyScroll}>
                  <div style={{ width: tableScrollWidth, height: 1 }} />
                </div>
              )}

              <div style={{ marginTop: "0.6rem", display: "flex", gap: "0.5rem" }}>
                <button className="btn-ghost btn-sm" onClick={addControlRow}>
                  + Add control
                </button>
                <button className="btn-ghost btn-sm" onClick={handleRecheck} disabled={checking}>
                  {checking ? "Re-checking…" : "Re-check"}
                </button>
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
          ) : editLoading ? null : !preview ? (
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
                  // Editing has no chooser screen to go back to.
                  if (editProductId) {
                    onClose();
                    return;
                  }
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
                {applying ? "Applying…" : editProductId ? "Save Changes" : "Apply Import"}
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
