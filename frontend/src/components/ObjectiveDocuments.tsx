/**
 * Documents as evidence for one objective (roadmap item X).
 *
 * Publishing a document attaches nothing: a document with an approved
 * version is offered on the objectives it is tagged with, and a person
 * attaches it here. The twin of ObjectiveLists, and bound by the same
 * L.10 rule from the start: an attached document and one that is not
 * never share a row shape. Attached is an evidence item (chip, version,
 * remove button); anything else is a sentence.
 *
 * A link stays on the version it named. When that version is superseded
 * it says so in those words -- a superseded policy may no longer be in
 * force, which is not what a list's "a newer version is available" says.
 */

import { useEffect, useState } from "react";

import { api } from "../api";
import type { DocumentEvidenceCandidate } from "../types";

interface Props {
  orgId: string;
  assessmentId: string;
  controlStateId: string;
  canWrite: boolean;
  onCountChange: (linked: number) => void;
}

export function ObjectiveDocuments({ orgId, assessmentId, controlStateId, canWrite, onCountChange }: Props) {
  const [docs, setDocs] = useState<DocumentEvidenceCandidate[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  function show(rows: DocumentEvidenceCandidate[]) {
    setDocs(rows);
    onCountChange(rows.filter((d) => d.linked_version_id !== null).length);
  }

  useEffect(() => {
    api.stateDocuments(orgId, assessmentId, controlStateId).then(show, (e: Error) => setError(e.message));
  }, [orgId, assessmentId, controlStateId]); // eslint-disable-line react-hooks/exhaustive-deps

  async function act(documentId: string, fn: typeof api.linkDocument) {
    setBusy(documentId);
    setError(null);
    try {
      show(await fn(orgId, assessmentId, controlStateId, documentId));
    } catch (e: unknown) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  if (docs === null || docs.length === 0) {
    return error ? <div className="form-error">{error}</div> : null;
  }
  const attached = docs.filter((d) => d.linked_version_id !== null);
  const others = docs.filter((d) => d.linked_version_id === null);
  return (
    <div className="ev-lists" aria-label="Documents for this objective">
      {attached.length > 0 && (
        <>
          <span className="ev-section-label">Documents attached as evidence</span>
          <ul className="ev-list-attached" aria-label="Documents attached as evidence">
            {attached.map((d) => (
              <li key={d.document_id} className="ev-list-row">
                <div className="ev-item ev-item--ref">
                  <span className="ev-type-chip">Document</span>
                  <span className="ev-title" title={d.title}>{d.doc_id} — {d.title}</span>
                  <span className="field-hint">version {d.linked_version_number}</span>
                  {d.linked_superseded && <span className="ev-superseded">superseded</span>}
                  {canWrite && (
                    <button
                      type="button"
                      className="ev-remove"
                      disabled={busy !== null}
                      onClick={() => act(d.document_id, api.unlinkDocument)}
                      title="Remove"
                      aria-label={`Remove ${d.title}`}
                    >
                      ✕
                    </button>
                  )}
                </div>
                {d.superseded && (
                  <div className="doc-notice doc-notice-warn">
                    <p>{d.superseded}</p>
                    {canWrite && d.offered && (
                      <button
                        type="button"
                        className="btn-ghost btn-sm"
                        disabled={busy !== null}
                        onClick={() => act(d.document_id, api.moveDocumentForward)}
                      >
                        Move to version {d.approved_version_number}
                      </button>
                    )}
                    {canWrite && !d.offered && (
                      <p className="field-hint">It cannot be moved forward: {d.not_offered}.</p>
                    )}
                  </div>
                )}
              </li>
            ))}
          </ul>
        </>
      )}
      {others.length > 0 && (
        <>
          <span className="ev-section-label">Not attached</span>
          <ul className="ev-list-others" aria-label="Documents not attached">
            {others.map((d) => {
              const name = d.approved_version_number !== null
                ? `${d.doc_id} — ${d.title} (version ${d.approved_version_number})`
                : `${d.doc_id} — ${d.title}`;
              return d.offered ? (
                <li key={d.document_id}>
                  {name} is approved, tagged with this objective, and not attached.
                  {canWrite && (
                    <button
                      type="button"
                      className="btn-ghost btn-sm"
                      disabled={busy !== null}
                      onClick={() => act(d.document_id, api.linkDocument)}
                    >
                      Attach version {d.approved_version_number}
                    </button>
                  )}
                </li>
              ) : (
                <li key={d.document_id} className="field-hint">
                  {name} is tagged with this objective but is not attached, and cannot be: {d.not_offered}.
                </li>
              );
            })}
          </ul>
        </>
      )}
      {error && <div className="form-error">{error}</div>}
    </div>
  );
}
