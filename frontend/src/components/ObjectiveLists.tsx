/**
 * Lists as evidence for one objective (roadmap L.4).
 *
 * Every list tagged with this objective's practice appears here: offered
 * (a person attaches it -- nothing is attached for them), linked at a
 * version, or not offered and saying why. An adopted template starts
 * unfilled by design, so "still showing template examples" is said rather
 * than the list silently missing. A link stays on the version it named;
 * when the list has moved on, that is said, and moving forward is a
 * deliberate act.
 */

import { useEffect, useState } from "react";

import { api } from "../api";
import type { ListEvidenceCandidate } from "../types";

interface Props {
  orgId: string;
  assessmentId: string;
  controlStateId: string;
  canWrite: boolean;
  onCountChange: (linked: number) => void;
}

export function ObjectiveLists({ orgId, assessmentId, controlStateId, canWrite, onCountChange }: Props) {
  const [lists, setLists] = useState<ListEvidenceCandidate[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  function show(rows: ListEvidenceCandidate[]) {
    setLists(rows);
    onCountChange(rows.filter((l) => l.linked_version_id !== null).length);
  }

  useEffect(() => {
    api.stateLists(orgId, assessmentId, controlStateId).then(show, (e: Error) => setError(e.message));
  }, [orgId, assessmentId, controlStateId]); // eslint-disable-line react-hooks/exhaustive-deps

  async function act(listId: string, fn: typeof api.linkList) {
    setBusy(listId);
    setError(null);
    try {
      show(await fn(orgId, assessmentId, controlStateId, listId));
    } catch (e: unknown) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  if (lists === null || lists.length === 0) {
    return error ? <div className="form-error">{error}</div> : null;
  }
  return (
    <div className="ev-lists" aria-label="Lists for this practice">
      <span className="ev-section-label">Lists for this practice</span>
      {lists.map((l) => (
        <div key={l.list_id} className="ev-list-row">
          <div className="ev-item ev-item--ref">
            <span className="ev-type-chip">List</span>
            <span className="ev-title" title={l.title}>{l.title}</span>
            {l.linked_version_number !== null ? (
              <span className="field-hint">linked at version {l.linked_version_number}</span>
            ) : (
              l.current_version_number !== null && (
                <span className="field-hint">version {l.current_version_number}</span>
              )
            )}
            {canWrite && l.linked_version_id === null && l.offered && (
              <button
                type="button"
                className="btn-ghost btn-sm"
                disabled={busy !== null}
                onClick={() => act(l.list_id, api.linkList)}
              >
                Attach version {l.current_version_number}
              </button>
            )}
            {canWrite && l.linked_version_id !== null && (
              <button
                type="button"
                className="ev-remove"
                disabled={busy !== null}
                onClick={() => act(l.list_id, api.unlinkList)}
                title="Remove"
                aria-label={`Remove ${l.title}`}
              >
                ✕
              </button>
            )}
          </div>
          {l.linked_version_id === null && !l.offered && (
            <p className="field-hint">Not offered as evidence: {l.not_offered}.</p>
          )}
          {l.drift && (
            <div className="doc-notice doc-notice-warn">
              <p>{l.drift}</p>
              {canWrite && l.offered && (
                <button
                  type="button"
                  className="btn-ghost btn-sm"
                  disabled={busy !== null}
                  onClick={() => act(l.list_id, api.moveListForward)}
                >
                  Move to version {l.current_version_number}
                </button>
              )}
              {canWrite && !l.offered && (
                <p className="field-hint">Version {l.current_version_number} is not offered: {l.not_offered}.</p>
              )}
            </div>
          )}
        </div>
      ))}
      {error && <div className="form-error">{error}</div>}
    </div>
  );
}
