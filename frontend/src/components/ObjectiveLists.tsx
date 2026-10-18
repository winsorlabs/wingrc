/**
 * Lists as evidence for one objective (roadmap L.4, L.10).
 *
 * Every list tagged with this objective's practice appears here, in one of
 * two shapes that never look alike (L.10). An attached list is an evidence
 * item -- the List chip, the version it names, a remove button. Any other
 * list is a sentence, not an item: offered (with an Attach button; nothing
 * is attached for anyone) or not offered and saying why. On live the two
 * once shared a row, with a muted footnote telling them apart, and the
 * person who designed the rule read an unfilled list as attached
 * (2026-10-18). An adopted template starts unfilled by design, so "still
 * showing template examples" is said rather than the list silently
 * missing. A link stays on the version it named; when the list has moved
 * on, that is said, and moving forward is a deliberate act.
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
  const attached = lists.filter((l) => l.linked_version_id !== null);
  const others = lists.filter((l) => l.linked_version_id === null);
  return (
    <div className="ev-lists" aria-label="Lists for this practice">
      {attached.length > 0 && (
        <>
          <span className="ev-section-label">Lists attached as evidence</span>
          <ul className="ev-list-attached" aria-label="Lists attached as evidence">
            {attached.map((l) => (
              <li key={l.list_id} className="ev-list-row">
                <div className="ev-item ev-item--ref">
                  <span className="ev-type-chip">List</span>
                  <span className="ev-title" title={l.title}>{l.title}</span>
                  <span className="field-hint">linked at version {l.linked_version_number}</span>
                  {canWrite && (
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
              </li>
            ))}
          </ul>
        </>
      )}
      {others.length > 0 && (
        <>
          <span className="ev-section-label">Not attached</span>
          <ul className="ev-list-others" aria-label="Lists not attached">
            {others.map((l) => {
              const name = l.current_version_number !== null ? `${l.title} (version ${l.current_version_number})` : l.title;
              return l.offered ? (
                <li key={l.list_id}>
                  {name} can serve this practice and is not attached.
                  {canWrite && (
                    <button
                      type="button"
                      className="btn-ghost btn-sm"
                      disabled={busy !== null}
                      onClick={() => act(l.list_id, api.linkList)}
                    >
                      Attach version {l.current_version_number}
                    </button>
                  )}
                </li>
              ) : (
                <li key={l.list_id} className="field-hint">
                  {name} is tagged with this practice but is not attached, and cannot be: {l.not_offered}.
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
