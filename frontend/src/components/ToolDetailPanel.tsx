import { useEffect, useState } from "react";
import { api } from "../api";
import type { ProductDetail, ProductDocumentItem, ProductFootprintRow } from "../types";

// Read-only baseline-mapping view (§3c) plus publish/unpublish (§3f) and
// document attachments (§3d). No field-by-field editing here on purpose --
// the YAML is the authoring format; this screen ingests and reviews it.

const _DOCUMENT_KINDS = ["crm", "baseline_doc", "kb_export", "other"];

function formatBytes(n: number | null): string {
  if (n === null) return "";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

interface Props {
  productId: string;
  onBack: () => void;
  onChanged: () => void;
}

export function ToolDetailPanel({ productId, onBack, onChanged }: Props) {
  const [detail, setDetail] = useState<ProductDetail | null>(null);
  const [footprint, setFootprint] = useState<ProductFootprintRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [publishing, setPublishing] = useState(false);

  const [uploadKind, setUploadKind] = useState("other");
  const [uploadTitle, setUploadTitle] = useState("");
  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);
  const [docError, setDocError] = useState<string | null>(null);

  function load() {
    setLoading(true);
    setError(null);
    Promise.all([api.getToolDetail(productId), api.getToolFootprint(productId)])
      .then(([d, f]) => {
        setDetail(d);
        setFootprint(f);
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  }

  useEffect(load, [productId]);

  async function handleTogglePublish() {
    if (!detail) return;
    setPublishing(true);
    try {
      const result = detail.is_published ? await api.unpublishTool(productId) : await api.publishTool(productId);
      setDetail((prev) => (prev ? { ...prev, is_published: result.is_published } : prev));
      onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not change publish state");
    } finally {
      setPublishing(false);
    }
  }

  async function handleUpload() {
    if (!uploadFile) return;
    setUploading(true);
    setDocError(null);
    try {
      const doc = await api.uploadToolDocument(productId, uploadFile, {
        title: uploadTitle || undefined,
        kind: uploadKind,
      });
      setDetail((prev) => (prev ? { ...prev, documents: [...prev.documents, doc] } : prev));
      setUploadFile(null);
      setUploadTitle("");
    } catch (e) {
      setDocError(e instanceof Error ? e.message : "Upload failed");
    } finally {
      setUploading(false);
    }
  }

  async function handleDeleteDoc(doc: ProductDocumentItem) {
    try {
      await api.deleteToolDocument(productId, doc.id);
      setDetail((prev) =>
        prev ? { ...prev, documents: prev.documents.filter((d) => d.id !== doc.id) } : prev
      );
    } catch (e) {
      setDocError(e instanceof Error ? e.message : "Could not remove document");
    }
  }

  if (loading) return <div className="loading">Loading tool…</div>;
  if (error || !detail) return <div className="form-error">{error ?? "Not found"}</div>;

  return (
    <div className="tool-detail-panel">
      <button className="btn-ghost btn-sm" onClick={onBack}>&larr; Back to library</button>

      <div className="tool-detail-header">
        <div>
          <h2>{detail.name}</h2>
          <div className="contact-sub">
            {detail.provider} · {detail.category} · {detail.asset_type} · role: {detail.role}
          </div>
        </div>
        <div>
          <span className={`status-badge ${detail.is_published ? "status-active" : "status-inactive"}`}>
            {detail.is_published ? "Published" : "Unpublished"}
          </span>
          <button className="btn-ghost btn-sm" onClick={handleTogglePublish} disabled={publishing} style={{ marginLeft: "0.5rem" }}>
            {publishing ? "Working…" : detail.is_published ? "Unpublish" : "Publish"}
          </button>
        </div>
      </div>

      {detail.assumed_config.length > 0 && (
        <div className="field-hint">
          Assumed config: {detail.assumed_config.join(", ")}
        </div>
      )}

      <h3>Deployment footprint</h3>
      {footprint.length === 0 ? (
        <div className="empty">No org has activated this product.</div>
      ) : (
        <div className="table-scroll">
          <table className="contacts-table">
            <thead>
              <tr><th>Org</th><th>Status</th></tr>
            </thead>
            <tbody>
              {footprint.map((row) => (
                <tr key={row.org_id}>
                  <td>{row.org_name}</td>
                  <td>{row.status}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <h3>Baseline mapping</h3>
      <div className="table-scroll">
        <table className="contacts-table">
          <thead>
            <tr>
              <th>Control</th>
              <th>Classification</th>
              <th>Coverage basis</th>
              <th>Objectives</th>
              <th>Provider contribution / customer action</th>
              <th>Evidence</th>
            </tr>
          </thead>
          <tbody>
            {detail.baseline_controls.map((bc) => (
              <tr
                key={bc.control_id}
                className={bc.coverage_basis === "platform_only" ? "coverage-basis-platform-only" : undefined}
              >
                <td>
                  <strong>{bc.control_id}</strong>
                  <div className="contact-sub">{bc.title}</div>
                </td>
                <td>{bc.classification}</td>
                <td>
                  {bc.coverage_basis}
                  {bc.coverage_basis === "platform_only" && (
                    <div className="contact-sub">Excluded from magic-loop activation — vendor self-attestation, not customer-system coverage.</div>
                  )}
                </td>
                <td>{bc.objectives.join(", ")}</td>
                <td>
                  {bc.provider_contribution && <div>{bc.provider_contribution}</div>}
                  {bc.customer_action && <div className="contact-sub">{bc.customer_action}</div>}
                  {bc.scope_note && <div className="contact-sub">{bc.scope_note}</div>}
                </td>
                <td>
                  {bc.evidence_specs.length === 0 ? (
                    <span className="contact-sub">none</span>
                  ) : (
                    bc.evidence_specs.map((s) => (
                      <div key={s.id} className="contact-sub">{s.artifact_description} ({s.evidence_type})</div>
                    ))
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <h3>Documentation attachments</h3>
      {detail.source_docs.length > 0 && (
        <div className="field-hint">
          Source docs cited by the mapping: {detail.source_docs.join(", ")}
        </div>
      )}
      {docError && <div className="form-error">{docError}</div>}
      {detail.documents.length === 0 ? (
        <div className="empty">No documents attached.</div>
      ) : (
        <div className="table-scroll">
          <table className="contacts-table">
            <thead>
              <tr><th>Title</th><th>Kind</th><th>Size</th><th></th></tr>
            </thead>
            <tbody>
              {detail.documents.map((d) => (
                <tr key={d.id}>
                  <td>{d.title}</td>
                  <td>{d.kind}</td>
                  <td>{formatBytes(d.file_size_bytes)}</td>
                  <td>
                    <a className="btn-ghost btn-xs" href={api.toolDocumentDownloadUrl(productId, d.id)}>Download</a>{" "}
                    <button className="btn-ghost btn-xs btn-destructive" onClick={() => handleDeleteDoc(d)}>Remove</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="form-field-row">
        <div className="form-field">
          <label>File</label>
          <input type="file" onChange={(e) => setUploadFile(e.target.files?.[0] ?? null)} />
        </div>
        <div className="form-field">
          <label>Title (optional)</label>
          <input type="text" value={uploadTitle} onChange={(e) => setUploadTitle(e.target.value)} />
        </div>
        <div className="form-field">
          <label>Kind</label>
          <select value={uploadKind} onChange={(e) => setUploadKind(e.target.value)}>
            {_DOCUMENT_KINDS.map((k) => (
              <option key={k} value={k}>{k}</option>
            ))}
          </select>
        </div>
        <button className="btn-primary btn-sm" onClick={handleUpload} disabled={!uploadFile || uploading}>
          {uploading ? "Uploading…" : "Attach document"}
        </button>
      </div>
    </div>
  );
}
