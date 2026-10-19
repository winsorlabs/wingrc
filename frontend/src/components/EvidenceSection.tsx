/**
 * An objective's evidence section tells the truth about what is attached,
 * what used to be, and what happened to it (2026-10-19).
 *
 * - Attached: files and locations, then lists, then documents, each an
 *   evidence item with a chip.
 * - Not attached: lists and documents that are tagged here and could be,
 *   under one heading that belongs to this section.
 * - No longer attached: archived links, greyed, each a sentence with its
 *   reason, never an item: an archived artifact and a live one never share
 *   a row shape (L.10). "Replaced by X" appears only where a person named
 *   X at deactivation. A product taken away with nothing named, on an
 *   objective with no current evidence, is said loudly, from what the
 *   server counted.
 *
 * Detach archives the link and keeps the artifact (evidence_links.py). It
 * used to be a ✕ titled "Remove" that deleted the artifact and its bytes.
 */
import { useEffect, useRef, useState } from "react";
import { api, assetUrl } from "../api";
import type {
  ArchivedEvidence,
  ArchivedEvidenceSection,
  DocumentEvidenceCandidate,
  EvidenceRow,
  ListEvidenceCandidate,
} from "../types";
import { ObjectiveDocuments } from "./ObjectiveDocuments";
import { ObjectiveLists } from "./ObjectiveLists";

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

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** "12 Sep 2026", in UTC. Built by hand: a locale's short month is the
 * runtime's ICU data (en-GB gives "Sept" on some, "Sep" on others). */
function day(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  return `${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]} ${d.getUTCFullYear()}`;
}

function archivedName(a: ArchivedEvidence): string {
  if (a.source === "list" && a.version_number !== null) return `${a.title} (list, version ${a.version_number})`;
  if (a.source === "document" && a.version_number !== null) return `${a.title} (document, version ${a.version_number})`;
  return a.title;
}

/** The reason, from the row's fields; never inferred. */
function archivedReason(a: ArchivedEvidence): string {
  if (a.archive_cause === "deactivation") {
    if (a.replaced_by_name) {
      const who = a.deactivated_by ? ` by ${a.deactivated_by}` : "";
      return `Replaced by ${a.replaced_by_name} — stated${who} when ${a.product_name} was deactivated, ${day(a.archived_at)}`;
    }
    return `(Archived ${day(a.archived_at)} — ${a.product_name} was deactivated)`;
  }
  if (a.archive_cause === "attestation_detach") {
    return `(Detached ${day(a.detached_at)}: the record of a review cycle that closed without attestation)`;
  }
  const who = a.detached_by ? ` by ${a.detached_by}` : "";
  return `(Detached ${day(a.detached_at)}${who})`;
}

interface RefRow {
  title: string;
  location: string;
}

interface Props {
  orgId: string;
  assessmentId: string;
  controlStateId: string;
  canWrite: boolean;
  onCountChange: (count: number) => void;
}

export function EvidenceSection({ orgId, assessmentId, controlStateId, canWrite, onCountChange }: Props) {
  const [items, setItems] = useState<EvidenceRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [showRefForm, setShowRefForm] = useState(false);
  const [refRows, setRefRows] = useState<RefRow[]>([{ title: "", location: "" }]);
  const [addingRefs, setAddingRefs] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Lists linked as evidence (L.4) and documents attached (item X) are
  // drawn by ObjectiveLists and ObjectiveDocuments; the rows load here, so
  // that the section can say "Not attached" once for both.
  const [lists, setLists] = useState<ListEvidenceCandidate[]>([]);
  const [docs, setDocs] = useState<DocumentEvidenceCandidate[]>([]);
  const [archived, setArchived] = useState<ArchivedEvidenceSection | null>(null);
  const [confirming, setConfirming] = useState<string | null>(null);
  const [detaching, setDetaching] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const linkedLists = lists.filter((l) => l.linked_version_id !== null).length;
  const linkedDocuments = docs.filter((d) => d.linked_version_id !== null).length;
  const liveCount = items.length + linkedLists + linkedDocuments;

  useEffect(() => {
    onCountChange(liveCount);
  }, [liveCount]); // eslint-disable-line react-hooks/exhaustive-deps

  function reloadArchived() {
    return api.archivedEvidence(orgId, assessmentId, controlStateId).then(setArchived, (e: Error) => setError(e.message));
  }

  function reload() {
    return Promise.all([
      api.listEvidence(orgId, assessmentId, controlStateId).then(setItems),
      reloadArchived(),
    ]);
  }

  useEffect(() => {
    setLoading(true);
    Promise.all([
      reload(),
      api.stateLists(orgId, assessmentId, controlStateId).then(setLists, (e: Error) => setError(e.message)),
      api.stateDocuments(orgId, assessmentId, controlStateId).then(setDocs, (e: Error) => setError(e.message)),
    ]).finally(() => setLoading(false));
  }, [orgId, assessmentId, controlStateId]);  // eslint-disable-line react-hooks/exhaustive-deps

  function listsChanged(rows: ListEvidenceCandidate[]) {
    setLists(rows);
    void reloadArchived();
  }

  function docsChanged(rows: DocumentEvidenceCandidate[]) {
    setDocs(rows);
    void reloadArchived();
  }

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

  async function handleDetach(id: string) {
    setError(null);
    setDetaching(true);
    try {
      await api.detachEvidence(orgId, assessmentId, controlStateId, id);
      setConfirming(null);
      await reload();
    } catch (err: unknown) {
      setError((err as Error).message);
    } finally {
      setDetaching(false);
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

  const unattachedLists = lists.some((l) => l.linked_version_id === null);
  const unattachedDocs = docs.some((d) => d.linked_version_id === null);
  const archivedItems = archived?.items ?? [];
  // Said only from what the server counted: no live evidence of any kind,
  // and a product deactivated here with nothing named to replace it.
  const unreplaced = [
    ...new Set(
      archivedItems
        .filter((a) => a.archive_cause === "deactivation" && !a.replaced_by_name)
        .map((a) => a.product_name ?? ""),
    ),
  ];
  const alarm = archived !== null && archived.live_evidence === 0 && unreplaced.length > 0;
  const coveredBy = archived?.covered_by ?? [];

  return (
    <div className="ev-section">
      <div className="ev-section-header">
        <span className="ev-section-label">Evidence</span>
        {liveCount > 0 && (
          <span
            className="ev-count-badge"
            aria-label={`${liveCount} evidence item${liveCount === 1 ? "" : "s"}`}
          >
            {liveCount}
          </span>
        )}
      </div>

      {loading ? (
        <div className="ev-status">Loading…</div>
      ) : (
        <>
          {alarm && (
            <div className="ev-alarm" role="alert">
              {unreplaced.join(", ")} {unreplaced.length === 1 ? "was" : "were"} deactivated and nothing was named
              to replace {unreplaced.length === 1 ? "it" : "them"}. This objective has no current evidence
              {coveredBy.length === 0
                ? ", and no active tool contributes to it."
                : `; ${coveredBy.join(", ")} still ${coveredBy.length === 1 ? "contributes" : "contribute"} to it.`}
            </div>
          )}
          {liveCount === 0 && !uploading && !alarm && (
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
                    href={assetUrl(ev.download_url) ?? undefined}
                    target="_blank"
                    rel="noreferrer"
                    title="Download"
                    aria-label={`Download ${ev.title}`}
                  >
                    ↓
                  </a>
                )}
                {canWrite && confirming !== ev.id && (
                  <button
                    className="btn-ghost btn-sm ev-detach"
                    onClick={() => setConfirming(ev.id)}
                    title="Detach from this objective. The artifact stays; this objective stops resting on it."
                    aria-label={`Detach ${ev.title}`}
                  >
                    Detach
                  </button>
                )}
                {confirming === ev.id && (
                  <div className="ev-detach-confirm" role="group" aria-label={`Detach ${ev.title}?`}>
                    <span>
                      The artifact stays, for every objective it serves. This objective stops resting
                      on it and shows it under No longer attached.
                    </span>
                    <button className="btn-primary btn-sm" disabled={detaching} onClick={() => handleDetach(ev.id)}>
                      {detaching ? "Detaching…" : "Detach"}
                    </button>
                    <button className="btn-ghost btn-sm" disabled={detaching} onClick={() => setConfirming(null)}>
                      Cancel
                    </button>
                  </div>
                )}
              </div>
            ))}
            {uploading && <div className="ev-status">Uploading…</div>}
          </div>

          <ObjectiveLists
            orgId={orgId}
            assessmentId={assessmentId}
            controlStateId={controlStateId}
            canWrite={canWrite}
            lists={lists}
            onChange={listsChanged}
            part="attached"
          />

          <ObjectiveDocuments
            orgId={orgId}
            assessmentId={assessmentId}
            controlStateId={controlStateId}
            canWrite={canWrite}
            docs={docs}
            onChange={docsChanged}
            part="attached"
          />

          {(unattachedLists || unattachedDocs) && (
            <div className="ev-lists" aria-label="Not attached">
              <span className="ev-section-label">Not attached</span>
              <ObjectiveLists
                orgId={orgId}
                assessmentId={assessmentId}
                controlStateId={controlStateId}
                canWrite={canWrite}
                lists={lists}
                onChange={listsChanged}
                part="others"
              />
              <ObjectiveDocuments
                orgId={orgId}
                assessmentId={assessmentId}
                controlStateId={controlStateId}
                canWrite={canWrite}
                docs={docs}
                onChange={docsChanged}
                part="others"
              />
            </div>
          )}

          {archivedItems.length > 0 && (
            <div className="ev-archived">
              <span className="ev-section-label">No longer attached</span>
              <p className="field-hint">Shown for the record. None of these counts as evidence for this objective.</p>
              <ul aria-label="No longer attached">
                {archivedItems.map((a) => (
                  <li key={a.link_id} className="ev-archived-item">
                    <span className="ev-archived-title">{archivedName(a)}</span>{" "}
                    <span className="ev-archived-reason">{archivedReason(a)}</span>
                    {a.replaced_by_name && a.replaced_by_covers === false && (
                      <span className="field-hint"> {a.replaced_by_name} does not contribute to this objective.</span>
                    )}
                    {a.replaced_by_name && a.replaced_by_covers === true && (
                      <span className="field-hint"> {a.replaced_by_name} contributes to this objective.</span>
                    )}
                    {a.archive_cause === "deactivation" && (
                      <span className="field-hint"> Reactivating {a.product_name} restores it.</span>
                    )}
                    {a.download_url && (
                      <>
                        {" "}
                        <a href={assetUrl(a.download_url) ?? undefined} target="_blank" rel="noreferrer">
                          Download
                        </a>
                      </>
                    )}
                    {canWrite && a.archive_cause === "deactivation" && a.source === "evidence" && (
                      <button
                        className="btn-ghost btn-sm"
                        disabled={detaching}
                        onClick={() => handleDetach(a.evidence_id)}
                        title={`Detach so that reactivating ${a.product_name} does not bring it back.`}
                      >
                        Detach for good
                      </button>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          )}

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

          {canWrite && (
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
          )}
        </>
      )}

      {error && <div className="ev-error">{error}</div>}
    </div>
  );
}
