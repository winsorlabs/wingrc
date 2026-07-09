import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { EvidenceRow } from "../types";

const ARTIFACT_LABELS: Record<string, string> = {
  screenshot: "Screenshot",
  export: "Export",
  document: "Document",
  link: "Link",
  policy: "Policy",
};

function inferArtifactType(file: File): string {
  const ext = file.name.split(".").pop()?.toLowerCase() ?? "";
  if (["png", "jpg", "jpeg", "gif", "webp", "bmp", "tiff"].includes(ext)) return "screenshot";
  if (["csv", "xlsx", "xls", "json", "xml"].includes(ext)) return "export";
  if (["htm", "html"].includes(ext)) return "link";
  return "document";
}

function formatBytes(n: number | null): string {
  if (n === null || n === 0) return "";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

interface RefRow {
  title: string;
  location: string;
}

interface Props {
  orgId: string;
  assessmentId: string;
  controlStateId: string;
  onCountChange: (count: number) => void;
}

export function EvidenceSection({ orgId, assessmentId, controlStateId, onCountChange }: Props) {
  const [items, setItems] = useState<EvidenceRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [showRefForm, setShowRefForm] = useState(false);
  const [refRows, setRefRows] = useState<RefRow[]>([{ title: "", location: "" }]);
  const [addingRefs, setAddingRefs] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  function reload() {
    return api.listEvidence(orgId, assessmentId, controlStateId).then((rows) => {
      setItems(rows);
      onCountChange(rows.length);
    });
  }

  useEffect(() => {
    setLoading(true);
    reload().finally(() => setLoading(false));
  }, [orgId, assessmentId, controlStateId]);  // eslint-disable-line react-hooks/exhaustive-deps

  async function handleFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    e.target.value = "";
    setUploading(true);
    setError(null);
    try {
      await api.uploadEvidence(orgId, assessmentId, controlStateId, file, inferArtifactType(file));
      await reload();
    } catch (err: unknown) {
      setError((err as Error).message);
    } finally {
      setUploading(false);
    }
  }

  async function handleDelete(id: string) {
    setError(null);
    try {
      await api.deleteEvidence(orgId, assessmentId, controlStateId, id);
      await reload();
    } catch (err: unknown) {
      setError((err as Error).message);
    }
  }

  function handleAddRefRow() {
    setRefRows((rows) => [...rows, { title: "", location: "" }]);
  }

  function handleRemoveRefRow(i: number) {
    setRefRows((rows) => rows.filter((_, idx) => idx !== i));
  }

  function handleRefChange(i: number, field: keyof RefRow, val: string) {
    setRefRows((rows) => rows.map((r, idx) => (idx === i ? { ...r, [field]: val } : r)));
  }

  function handleCancelRef() {
    setShowRefForm(false);
    setRefRows([{ title: "", location: "" }]);
    setError(null);
  }

  async function handleSubmitRefs() {
    const valid = refRows.filter((r) => r.title.trim() && r.location.trim());
    if (!valid.length) {
      setError("Enter at least one label and location.");
      return;
    }
    setAddingRefs(true);
    setError(null);
    try {
      await api.addReferences(
        orgId,
        assessmentId,
        controlStateId,
        valid.map((r) => ({ title: r.title.trim(), location: r.location.trim(), artifact_type: "link" }))
      );
      setShowRefForm(false);
      setRefRows([{ title: "", location: "" }]);
      await reload();
    } catch (err: unknown) {
      setError((err as Error).message);
    } finally {
      setAddingRefs(false);
    }
  }

  return (
    <div className="ev-section">
      <div className="ev-section-header">
        <span className="ev-section-label">Evidence</span>
        {items.length > 0 && (
          <span
            className="ev-count-badge"
            aria-label={`${items.length} evidence item${items.length === 1 ? "" : "s"}`}
          >
            {items.length}
          </span>
        )}
      </div>

      {loading ? (
        <div className="ev-status">Loading…</div>
      ) : (
        <>
          {items.length === 0 && !uploading && (
            <div className="ev-status ev-empty">No evidence attached</div>
          )}
          <div className="ev-list">
            {items.map((ev) => (
              <div
                key={ev.id}
                className={`ev-item${ev.kind === "reference" ? " ev-item--ref" : ""}`}
              >
                {ev.kind === "reference" ? (
                  <span className="ev-ref-chip">↗ Ref</span>
                ) : (
                  <span className="ev-type-chip">
                    {ARTIFACT_LABELS[ev.artifact_type] ?? ev.artifact_type}
                  </span>
                )}
                {ev.kind === "reference" ? (
                  <div className="ev-item-body">
                    <span className="ev-title" title={ev.title}>{ev.title}</span>
                    {ev.reference_location && (
                      <span className="ev-ref-location" title={ev.reference_location}>
                        {ev.reference_location.startsWith("http") ? (
                          <a href={ev.reference_location} target="_blank" rel="noreferrer">
                            {ev.reference_location}
                          </a>
                        ) : (
                          ev.reference_location
                        )}
                      </span>
                    )}
                  </div>
                ) : (
                  <span className="ev-title" title={ev.title}>{ev.title}</span>
                )}
                {ev.kind !== "reference" && (
                  <span className="ev-size">{formatBytes(ev.file_size_bytes)}</span>
                )}
                {ev.download_url && (
                  <a
                    className="ev-download"
                    href={ev.download_url}
                    target="_blank"
                    rel="noreferrer"
                    title="Download"
                    aria-label={`Download ${ev.title}`}
                  >
                    ↓
                  </a>
                )}
                <button
                  className="ev-remove"
                  onClick={() => handleDelete(ev.id)}
                  title="Remove"
                  aria-label={`Remove ${ev.title}`}
                >
                  ✕
                </button>
              </div>
            ))}
            {uploading && <div className="ev-status">Uploading…</div>}
          </div>

          {showRefForm && (
            <div className="ev-add-loc-form">
              {refRows.map((row, i) => (
                <div key={i} className="ev-add-loc-row">
                  <input
                    className="ev-add-loc-input"
                    placeholder="Label"
                    value={row.title}
                    onChange={(e) => handleRefChange(i, "title", e.target.value)}
                    disabled={addingRefs}
                  />
                  <input
                    className="ev-add-loc-input ev-add-loc-input--loc"
                    placeholder="URL or path  (e.g. https://… or M:\CMMC\…)"
                    value={row.location}
                    onChange={(e) => handleRefChange(i, "location", e.target.value)}
                    disabled={addingRefs}
                  />
                  {refRows.length > 1 && (
                    <button
                      className="ev-remove"
                      onClick={() => handleRemoveRefRow(i)}
                      disabled={addingRefs}
                      title="Remove row"
                    >
                      ✕
                    </button>
                  )}
                </div>
              ))}
              <div className="ev-add-loc-actions">
                <button
                  className="btn-ghost btn-sm"
                  onClick={handleAddRefRow}
                  disabled={addingRefs || refRows.length >= 20}
                >
                  + Add another
                </button>
                <button
                  className="btn-primary btn-sm"
                  onClick={handleSubmitRefs}
                  disabled={addingRefs}
                >
                  {addingRefs ? "Saving…" : "Save locations"}
                </button>
                <button className="btn-ghost btn-sm" onClick={handleCancelRef} disabled={addingRefs}>
                  Cancel
                </button>
              </div>
            </div>
          )}

          <div className="ev-footer">
            <button
              className="btn-ghost btn-sm"
              onClick={() => fileRef.current?.click()}
              disabled={uploading}
            >
              + Attach file
            </button>
            <button
              className="btn-ghost btn-sm"
              onClick={() => {
                setShowRefForm(true);
                setRefRows([{ title: "", location: "" }]);
                setError(null);
              }}
              disabled={uploading || showRefForm}
            >
              + Add location
            </button>
            <input
              ref={fileRef}
              type="file"
              className="ev-file-input"
              onChange={handleFileChange}
            />
            <span className="ev-hint">Attaching evidence does not change status</span>
          </div>
        </>
      )}

      {error && <div className="ev-error">{error}</div>}
    </div>
  );
}
