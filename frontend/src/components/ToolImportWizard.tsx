import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import {
  CLASSIFICATION_LABELS,
  CLASSIFICATIONS,
  COVERAGE_BASES,
  COVERAGE_BASIS_LABELS,
  EVIDENCE_TYPES,
} from "../lib/baseline";
import {
  bannerItems,
  COLUMN_HELP,
  coverSummary,
  formatAssumedConfig,
  FRAMEWORK_TEXT,
  kindText,
  nonRowSentence,
  parseAssumedConfig,
  PRODUCT_FIELDS,
  rowIsWrong,
} from "../lib/baselineReview";
import { ChangeDiff, ProblemsBanner, ReviewLegend } from "./ToolImportReview";
import type {
  BaselineControlDraft,
  BaselineEvidenceDraft,
  BaselineImportPreview,
  BaselineImportResult,
  FetchUrlResult,
  ProductDetail,
  ProductMetaDraft,
  ResearchCost,
  UrlSuggestion,
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

function sameList(a: string[], b: string[]): boolean {
  return a.length === b.length && a.every((x, i) => x === b[i]);
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
      source: null,
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
  // Free-text buffers for list fields (control ids and objectives,
  // comma-separated; assumed_config, one item per line) -- kept separate from the committed draft
  // so retyping a trailing comma mid-edit doesn't get eaten by a
  // join()-derived controlled value on every keystroke. Committed to the
  // real draft onBlur. Keyed "<row_index>:<field>" ("product:<field>" for
  // product-level list fields).
  const [listText, setListText] = useState<Record<string, string>>({});
  // A row's expansion: open by default when the row has a problem or a
  // disclaim flag (PLAN-tool-import 5.1, 5.8); a person's toggle wins.
  const [openRows, setOpenRows] = useState<Record<number, boolean>>({});
  // The banner's filter: a problem kind, "_disclaim", or null for every row.
  const [rowFilter, setRowFilter] = useState<string | null>(null);
  // True once any field has been edited since the last successful
  // generate/re-check -- Apply is blocked while true so it can never fire
  // against a `latestYaml` that no longer matches what's on screen.
  const [dirty, setDirty] = useState(false);

  const [preview, setPreview] = useState<BaselineImportPreview | null>(null);
  const [checking, setChecking] = useState(false);
  const [applying, setApplying] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [applied, setApplied] = useState<BaselineImportResult | null>(null);

  // AI research of vendor platform documentation (edit-existing-product
  // flow only -- see backend/app/routers/admin_products.py's own note on
  // why: a fetched page attaches to a real product_id, which a brand-new
  // product doesn't have until Apply). Propose -> approve -> fetch (§1):
  // suggestions are never trusted, only fetchedDocumentIds (built from an
  // explicit approve/paste step) are ever sent to re-generate the mapping.
  const [showResearch, setShowResearch] = useState(false);
  const [researchFiles, setResearchFiles] = useState<File[]>([]);
  const [suggestions, setSuggestions] = useState<UrlSuggestion[]>([]);
  const [suggesting, setSuggesting] = useState(false);
  const [approvedUrls, setApprovedUrls] = useState<Set<string>>(new Set());
  const [pastedUrls, setPastedUrls] = useState("");
  const [fetchResults, setFetchResults] = useState<FetchUrlResult[]>([]);
  const [fetchedDocumentIds, setFetchedDocumentIds] = useState<string[]>([]);
  const [fetching, setFetching] = useState(false);
  const [researching, setResearching] = useState(false);
  const [researchCost, setResearchCost] = useState<ResearchCost | null>(null);

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
    const next: Record<string, string> = { "product:assumed_config": formatAssumedConfig(product.assumed_config) };
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

  async function handleSuggestUrls() {
    if (!editProductId) return;
    setSuggesting(true);
    setError(null);
    try {
      setSuggestions(await api.suggestResearchUrls(editProductId));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not suggest documentation URLs");
    } finally {
      setSuggesting(false);
    }
  }

  function toggleApprovedUrl(url: string) {
    setApprovedUrls((prev) => {
      const next = new Set(prev);
      if (next.has(url)) next.delete(url);
      else next.add(url);
      return next;
    });
  }

  async function handleFetchUrls() {
    if (!editProductId) return;
    const pasted = pastedUrls
      .split(/[\n,]/)
      .map((u) => u.trim())
      .filter(Boolean);
    const urls = Array.from(new Set([...approvedUrls, ...pasted]));
    if (urls.length === 0) return;
    setFetching(true);
    setError(null);
    try {
      const result = await api.fetchResearchUrls(editProductId, urls);
      setFetchResults(result.results);
      setFetchedDocumentIds((prev) => {
        const ids = result.results.filter((r) => r.ok && r.document_id).map((r) => r.document_id as string);
        return Array.from(new Set([...prev, ...ids]));
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Fetch failed");
    } finally {
      setFetching(false);
    }
  }

  async function handleGenerateWithResearch() {
    if (!editProductId || researchFiles.length === 0 || fetchedDocumentIds.length === 0) return;
    setResearching(true);
    setError(null);
    try {
      const result = await api.ingestBaselineWithResearch(editProductId, researchFiles, fetchedDocumentIds);
      setDraft({ product: result.product, controls: result.controls });
      seedListText(result.product, result.controls);
      setLatestYaml(result.yaml);
      setPreview(result.preview);
      setResearchCost(result.research ?? null);
      setDirty(false);
      setShowResearch(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Research-enabled generation failed");
    } finally {
      setResearching(false);
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
        objectives: [],
        provider_contribution: null,
        customer_action: null,
        evidence: [],
        note: null,
        scope_note: null,
        source: null,
      };
      return { ...prev, controls: [...prev.controls, newRow] };
    });
    setDirty(true);
  }

  function commitListField(rowIndex: number, field: "control" | "objectives") {
    const text = listText[`${rowIndex}:${field}`] ?? "";
    const parsed = text.split(",").map((s) => s.trim()).filter(Boolean);
    const current = draft?.controls.find((c) => c.row_index === rowIndex)?.[field];
    if (current && sameList(parsed, current)) return;
    updateControlField(rowIndex, field, parsed);
  }

  function commitProductListField() {
    const text = listText["product:assumed_config"] ?? "";
    // One item per line: an item is a sentence and may hold a comma.
    const parsed = parseAssumedConfig(text);
    // A blur that changes nothing must not re-block Apply (seen on the bench).
    if (draft && sameList(parsed, draft.product.assumed_config)) return;
    setDraft((prev) => (prev ? { ...prev, product: { ...prev.product, assumed_config: parsed } } : prev));
    setDirty(true);
  }

  // Advisory only -- distinct from rowProblem/hasProblems on purpose. A
  // disclaim flag never blocks Apply and is never treated as something to
  // fix before Re-check will pass; it's a "look at this" nudge, not a
  // validation error.
  function disclaimFlag(rowIndex: number) {
    return preview?.disclaim_flags.find((f) => f.row_index === rowIndex);
  }

  const banner = bannerItems(preview?.row_problems ?? []);
  const disclaimRows = (preview?.disclaim_flags ?? []).map((f) => f.row_index);

  function isRowOpen(rowIndex: number) {
    const chosen = openRows[rowIndex];
    if (chosen !== undefined) return chosen;
    return (
      !!preview?.row_problems.some((p) => p.row_index === rowIndex) || !!disclaimFlag(rowIndex)
    );
  }

  function rowShown(rowIndex: number) {
    if (!rowFilter) return true;
    if (rowFilter === "_disclaim") return disclaimRows.includes(rowIndex);
    return !!banner.find((b) => b.kind === rowFilter)?.rows.includes(rowIndex);
  }

  function openRowForControl(controlId: string) {
    const row = draft?.controls.find((c) => c.control.includes(controlId));
    if (!row) return;
    setRowFilter(null);
    setOpenRows((prev) => ({ ...prev, [row.row_index]: true }));
    requestAnimationFrame(() =>
      document.querySelector(`[data-row-index="${row.row_index}"]`)?.scrollIntoView({ block: "center" })
    );
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
              {applied.version_created ? (
                <>
                  {editProductId ? "Saved" : "Imported"} {applied.baseline_controls} baseline control
                  {applied.baseline_controls === 1 ? "" : "s"} and {applied.evidence_specs} evidence spec
                  {applied.evidence_specs === 1 ? "" : "s"} as version {applied.version_number}.
                  {applied.removed_controls.length > 0 && (
                    <> {applied.removed_controls.length} control{applied.removed_controls.length === 1 ? "" : "s"} no
                      longer appear{applied.removed_controls.length === 1 ? "s" : ""} in this version:{" "}
                      {applied.removed_controls.join(", ")}.</>
                  )}{" "}
                  Orgs already on an earlier version are unaffected until someone explicitly moves them. The
                  product is now <strong>unpublished</strong> — publish it again from the detail view once
                  you've reviewed the mapping.
                </>
              ) : (
                <>
                  Nothing in this file differs from version {applied.version_number} — no new version was
                  created, and the product's publish state is unchanged.
                </>
              )}
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
                  <div style={{ marginBottom: "0.3rem" }}>About the tool itself:</div>
                  {nonRowProblems.map((p, i) => (
                    <div key={i}>⚠ {nonRowSentence(p)}</div>
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
                <legend>About this tool</legend>
                <div className="tool-fields">
                  {PRODUCT_FIELDS.map(({ field, label, hint }) =>
                    // Its own hint asks for one or two sentences, so it gets
                    // the room the row's text fields have (Jarrod, T.1 walk).
                    field === "role" ? (
                      <label key={field} className="tool-field-wide">
                        <span className="tool-field-label">{label}</span>
                        <textarea
                          rows={3}
                          value={draft.product.role}
                          onChange={(e) => updateProductField("role", e.target.value)}
                        />
                        <span className="field-hint">{hint}</span>
                      </label>
                    ) : (
                      <label key={field}>
                        <span className="tool-field-label">{label}</span>
                        <input
                          type="text"
                          value={draft.product[field]}
                          // Changing the key while editing would retarget
                          // Apply at a different (or brand new) product,
                          // since apply upserts by key -- not a typo a
                          // reviewer should be able to make by accident.
                          disabled={field === "key" && !!editProductId}
                          onChange={(e) => updateProductField(field, e.target.value)}
                        />
                        {hint && <span className="field-hint">{hint}</span>}
                      </label>
                    )
                  )}
                  <div>
                    <span className="tool-field-label">Framework</span>
                    {/* Read-only: apply seeds every tool against the one
                        framework whatever this says (seeds/baselines.py,
                        _FRAMEWORK_KEY), so it was never a choice. */}
                    <div className="tool-field-static">{FRAMEWORK_TEXT}</div>
                    <span className="field-hint">The only framework; every tool maps to it.</span>
                  </div>
                  <label className="tool-field-wide">
                    <span className="tool-field-label">Assumed configuration</span>
                    <textarea
                      rows={Math.max(3, draft.product.assumed_config.length + 1)}
                      value={listText["product:assumed_config"] ?? formatAssumedConfig(draft.product.assumed_config)}
                      onChange={(e) =>
                        setListText((prev) => ({ ...prev, "product:assumed_config": e.target.value }))
                      }
                      onBlur={commitProductListField}
                    />
                    <span className="field-hint">
                      One item per line: what this mapping assumes is configured. Each line is shown on the
                      tool's page.
                    </span>
                  </label>
                </div>
              </fieldset>

              <ReviewLegend mode="documents" />

              {!preview.product_is_new && preview.control_changes.length > 0 && (
                <details className="review-diff">
                  <summary>
                    Changes against version {preview.current_version_number ?? "?"} of {preview.product_name}
                  </summary>
                  <ChangeDiff preview={preview} onOpenControl={openRowForControl} />
                </details>
              )}

              <ProblemsBanner
                items={banner}
                disclaimRows={disclaimRows}
                filter={rowFilter}
                onFilter={setRowFilter}
              />

              <div className="table-scroll" ref={tableScrollRef} onScroll={handleTableScroll}>
                <table className="contacts-table control-row-table">
                  <thead>
                    <tr>
                      <th aria-label="Open row" />
                      <th title={COLUMN_HELP.control}>Control</th>
                      <th title={COLUMN_HELP.responsible}>Who is responsible?</th>
                      <th title={COLUMN_HELP.systems}>On whose systems?</th>
                      <th title={COLUMN_HELP.source}>Source</th>
                      <th title={COLUMN_HELP.covers}>Covers</th>
                      <th>Needs</th>
                    </tr>
                  </thead>
                  <tbody>
                    {draft.controls.filter((row) => rowShown(row.row_index)).map((row) => {
                      const idx = row.row_index;
                      const problems = preview.row_problems.filter((p) => p.row_index === idx);
                      const wrong = rowIsWrong(preview.row_problems, idx);
                      const disclaim = disclaimFlag(idx);
                      const open = isRowOpen(idx);
                      const field = (f: string) => problems.find((p) => p.field === f);
                      const covProblem = field("coverage_basis");
                      const clsProblem = field("classification");
                      const controlProblem = field("control");
                      const objProblem = field("objectives");
                      const evProblem = field("evidence");
                      const wrongField = (p: typeof covProblem) => !!p && kindText(p.kind).severity === "wrong";
                      const rowClass = wrong ? "needs-decision" : disclaim ? "disclaim-flag-row" : undefined;
                      const controlIds = listText[`${idx}:control`] ?? row.control.join(", ");
                      const known = row.control.length === 1 ? preview.control_objectives?.[row.control[0]] : undefined;
                      return [
                        <tr key={`${idx}:row`} className={rowClass} data-row-index={idx}>
                          <td>
                            <button
                              type="button"
                              className="row-toggle"
                              aria-expanded={open}
                              aria-label={`${open ? "Close" : "Open"} row ${controlIds || idx}`}
                              onClick={() => setOpenRows((prev) => ({ ...prev, [idx]: !open }))}
                            >
                              {open ? "▾" : "▸"}
                            </button>
                          </td>
                          <td>
                            <input
                              type="text"
                              aria-label={`Control(s) for row ${idx}`}
                              className={wrongField(controlProblem) ? "field-needs-decision" : undefined}
                              value={controlIds}
                              onChange={(e) =>
                                setListText((prev) => ({ ...prev, [`${idx}:control`]: e.target.value }))
                              }
                              onBlur={() => commitListField(idx, "control")}
                            />
                          </td>
                          <td>
                            <select
                              aria-label={`Who is responsible for row ${idx}`}
                              className={
                                wrongField(clsProblem) ? "field-needs-decision" : disclaim ? "field-disclaim-flag" : undefined
                              }
                              value={row.classification ?? ""}
                              onChange={(e) => updateControlField(idx, "classification", e.target.value || null)}
                            >
                              <option value="">to decide</option>
                              {CLASSIFICATIONS.map((c) => (
                                <option key={c} value={c}>{CLASSIFICATION_LABELS[c]}</option>
                              ))}
                            </select>
                          </td>
                          <td>
                            {row.classification === "customer_owns" ? (
                              <span className="field-hint">not asked: the customer owns it</span>
                            ) : (
                              <select
                                aria-label={`On whose systems for row ${idx}`}
                                className={wrongField(covProblem) ? "field-needs-decision" : undefined}
                                value={row.coverage_basis ?? ""}
                                onChange={(e) => updateControlField(idx, "coverage_basis", e.target.value || null)}
                              >
                                <option value="">to decide</option>
                                {COVERAGE_BASES.map((c) => (
                                  <option key={c} value={c}>{COVERAGE_BASIS_LABELS[c]}</option>
                                ))}
                              </select>
                            )}
                          </td>
                          <td>
                            {row.source ? (
                              <a
                                href={row.source}
                                target="_blank"
                                rel="noreferrer"
                                className="source-badge source-badge-shared"
                                title={row.source}
                              >
                                web research
                              </a>
                            ) : (
                              <span className="field-hint">Document</span>
                            )}
                          </td>
                          <td className="field-hint">{coverSummary(row.objectives, known, row.evidence.length)}</td>
                          <td>
                            {problems.map((p, i) => (
                              <div
                                key={i}
                                className={kindText(p.kind).severity === "wrong" ? "field-problem-hint" : "row-marker-decide"}
                              >
                                {kindText(p.kind).marker}
                              </div>
                            ))}
                            {disclaim && <div className="field-disclaim-hint">CRM disclaims this</div>}
                          </td>
                        </tr>,
                        open && (
                          <tr key={`${idx}:open`} className="row-expansion">
                            <td />
                            <td colSpan={6}>
                              {(problems.length > 0 || disclaim) && (
                                <ul className="row-expansion-problems">
                                  {problems.map((p, i) => (
                                    <li
                                      key={i}
                                      className={kindText(p.kind).severity === "wrong" ? "field-problem-hint" : "row-marker-decide"}
                                    >
                                      {kindText(p.kind).detail(p.value ?? null)}
                                    </li>
                                  ))}
                                  {disclaim && <li className="field-disclaim-hint">⚠ {disclaim.message}</li>}
                                </ul>
                              )}
                              <div className="row-expansion-grid">
                                <label>
                                  What the vendor does
                                  <textarea
                                    rows={3}
                                    value={row.provider_contribution ?? ""}
                                    onChange={(e) => updateControlField(idx, "provider_contribution", e.target.value)}
                                  />
                                </label>
                                <label>
                                  What the customer must do
                                  <textarea
                                    rows={3}
                                    value={row.customer_action ?? ""}
                                    onChange={(e) => updateControlField(idx, "customer_action", e.target.value)}
                                  />
                                </label>
                                <label>
                                  Scope limit
                                  <textarea
                                    rows={2}
                                    value={row.scope_note ?? ""}
                                    onChange={(e) => updateControlField(idx, "scope_note", e.target.value)}
                                  />
                                </label>
                                <label>
                                  Note
                                  <textarea
                                    rows={2}
                                    value={row.note ?? ""}
                                    onChange={(e) => updateControlField(idx, "note", e.target.value)}
                                  />
                                </label>
                                <label>
                                  Objectives credited (letters, comma-separated)
                                  <input
                                    type="text"
                                    className={wrongField(objProblem) ? "field-needs-decision" : undefined}
                                    value={listText[`${idx}:objectives`] ?? row.objectives.join(", ")}
                                    onChange={(e) =>
                                      setListText((prev) => ({ ...prev, [`${idx}:objectives`]: e.target.value }))
                                    }
                                    onBlur={() => commitListField(idx, "objectives")}
                                  />
                                  {known && <span className="field-hint">This control's objectives: {known.join(", ")}</span>}
                                </label>
                                <div className="row-expansion-evidence">
                                  Evidence
                                  <div className={`evidence-mini-list${wrongField(evProblem) ? " field-needs-decision" : ""}`}>
                                    {row.evidence.map((ev, evIdx) => (
                                      <div className="evidence-mini-row" key={evIdx}>
                                        <input
                                          type="text"
                                          placeholder="artifact"
                                          aria-label="Evidence artifact"
                                          value={ev.artifact}
                                          onChange={(e) => updateEvidenceField(idx, evIdx, { artifact: e.target.value })}
                                        />
                                        <select
                                          aria-label="Evidence type"
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
                                          aria-label="Evidence knowledge-base reference"
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
                                  </div>
                                </div>
                              </div>
                            </td>
                          </tr>
                        ),
                      ];
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
                {editProductId && (
                  <button className="btn-ghost btn-sm" onClick={() => setShowResearch((v) => !v)}>
                    {showResearch ? "Hide research" : "🔬 Add web research"}
                  </button>
                )}
              </div>

              {editProductId && showResearch && (
                <fieldset className="form-field" style={{ marginTop: "0.6rem" }}>
                  <legend>AI research of vendor platform documentation</legend>
                  <div className="field-hint" style={{ marginBottom: "0.5rem" }}>
                    Attached documents come first — the CRM/baseline you upload here stays
                    authoritative. Web research can only ADD candidate coverage; it can never
                    override a control the CRM disclaims (a conflict is flagged instead, never
                    silently applied). Re-upload the CRM/baseline document(s) below, then
                    optionally add approved documentation pages.
                  </div>

                  <div className="form-field">
                    <label>
                      Vendor document(s) to re-analyze (required)
                      <input
                        type="file"
                        accept=".pdf,.doc,.docx"
                        multiple
                        onChange={(e) => setResearchFiles(Array.from(e.target.files ?? []).slice(0, _MAX_INGEST_FILES))}
                      />
                    </label>
                  </div>
                  {researchFiles.length > 0 && (
                    <div className="field-hint">{researchFiles.map((f) => f.name).join(", ")}</div>
                  )}

                  <div style={{ marginTop: "0.5rem" }}>
                    <button className="btn-ghost btn-sm" onClick={handleSuggestUrls} disabled={suggesting}>
                      {suggesting ? "Asking AI…" : "Suggest documentation URLs"}
                    </button>
                  </div>

                  {suggestions.length > 0 && (
                    <div style={{ marginTop: "0.4rem" }}>
                      {suggestions.map((s) => (
                        <label key={s.url} style={{ display: "block", fontSize: "0.75rem", marginBottom: "0.25rem" }}>
                          <input
                            type="checkbox"
                            checked={approvedUrls.has(s.url)}
                            onChange={() => toggleApprovedUrl(s.url)}
                          />{" "}
                          <strong>{s.url}</strong> — {s.rationale}
                        </label>
                      ))}
                    </div>
                  )}

                  <div className="form-field" style={{ marginTop: "0.5rem" }}>
                    <label>
                      Paste additional URLs (one per line)
                      <textarea
                        rows={2}
                        value={pastedUrls}
                        onChange={(e) => setPastedUrls(e.target.value)}
                        placeholder="https://docs.vendor.com/admin/security"
                      />
                    </label>
                  </div>

                  <div style={{ marginTop: "0.4rem", display: "flex", gap: "0.5rem" }}>
                    <button
                      className="btn-ghost btn-sm"
                      onClick={handleFetchUrls}
                      disabled={fetching || (approvedUrls.size === 0 && !pastedUrls.trim())}
                    >
                      {fetching ? "Fetching…" : "Fetch approved URLs"}
                    </button>
                  </div>

                  {fetchResults.length > 0 && (
                    <div style={{ marginTop: "0.4rem", fontSize: "0.75rem" }}>
                      {fetchResults.map((r) => (
                        <div key={r.url}>
                          {r.ok ? (
                            <span>✓ {r.title ?? r.url} ({r.characters?.toLocaleString()} chars)</span>
                          ) : (
                            <span className="form-error">✗ {r.url}: {r.error}</span>
                          )}
                        </div>
                      ))}
                    </div>
                  )}

                  {fetchedDocumentIds.length > 0 && (
                    <div className="field-hint" style={{ marginTop: "0.4rem" }}>
                      {fetchedDocumentIds.length} page{fetchedDocumentIds.length === 1 ? "" : "s"} ready to include.
                    </div>
                  )}

                  <div style={{ marginTop: "0.5rem" }}>
                    <button
                      className="btn-primary btn-sm"
                      onClick={handleGenerateWithResearch}
                      disabled={researching || researchFiles.length === 0 || fetchedDocumentIds.length === 0}
                    >
                      {researching ? "Generating…" : "Re-generate mapping with research"}
                    </button>
                  </div>
                </fieldset>
              )}

              {researchCost && (
                <div className="field-hint" style={{ marginTop: "0.5rem" }}>
                  Research added: {researchCost.pages_included} page{researchCost.pages_included === 1 ? "" : "s"},{" "}
                  {researchCost.total_web_characters.toLocaleString()} characters (~
                  {researchCost.estimated_added_tokens.toLocaleString()} tokens), {researchCost.ai_calls_added}{" "}
                  additional AI call{researchCost.ai_calls_added === 1 ? "" : "s"},{" "}
                  {researchCost.elapsed_seconds}s elapsed — {researchCost.controls_added_from_web} control
                  {researchCost.controls_added_from_web === 1 ? "" : "s"} added,{" "}
                  {researchCost.conflicts_flagged} conflict{researchCost.conflicts_flagged === 1 ? "" : "s"} flagged.
                </div>
              )}
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
                    <div className="field-hint">
                      {preview.has_changes ? (
                        <>
                          {preview.affected_org_count} org{preview.affected_org_count === 1 ? "" : "s"} already{" "}
                          {preview.affected_org_count === 1 ? "has" : "have"} this product active or candidate:{" "}
                          {preview.affected_org_names.join(", ")}. Applying creates version{" "}
                          {preview.next_version_number} -- those orgs stay pinned to{" "}
                          {preview.current_version_number != null ? `v${preview.current_version_number}` : "their current version"}{" "}
                          until someone explicitly moves them.
                        </>
                      ) : (
                        <>
                          {preview.affected_org_count} org{preview.affected_org_count === 1 ? "" : "s"} already{" "}
                          {preview.affected_org_count === 1 ? "has" : "have"} this product active or candidate, but
                          nothing in this file differs from the current version (v{preview.current_version_number}) --
                          Apply will be a no-op.
                        </>
                      )}
                    </div>
                  )}

                  {!preview.has_changes && preview.affected_org_count === 0 && preview.current_version_number != null && (
                    <div className="field-hint">
                      Nothing in this file differs from the current version (v{preview.current_version_number}) --
                      Apply will be a no-op.
                    </div>
                  )}

                  <ReviewLegend mode="yaml" />
                  <ChangeDiff preview={preview} />
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
                  setOpenRows({});
                  setRowFilter(null);
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
