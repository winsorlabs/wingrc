/**
 * Lists as evidence for one objective (roadmap L.4, L.10).
 *
 * Every list tagged with this objective's practice appears in the
 * objective's evidence section, in one of two shapes that never look alike
 * (L.10). An attached list is an evidence item -- the List chip, the
 * version it names, a Detach button. Any other list is a sentence, not an
 * item: offered (with an Attach button; nothing is attached for anyone) or
 * not offered and saying why. On live the two once shared a row, with a
 * muted footnote telling them apart, and the person who designed the rule
 * read an unfilled list as attached (2026-10-18). An adopted template
 * starts unfilled by design, so "still showing template examples" is said
 * rather than the list silently missing. A link stays on the version it
 * named; when the list has moved on, that is said, and moving forward is a
 * deliberate act.
 *
 * EvidenceSection loads the rows and draws this twice: `part="attached"`
 * among the attached evidence, and `part="others"` under the section's one
 * "Not attached" heading, which belongs to the section, not to each source
 * (2026-10-19: a list and a document both unattached drew it twice).
 */

import { useState } from "react";

import { api } from "../api";
import type { ListEvidenceCandidate } from "../types";

interface Props {
  orgId: string;
  assessmentId: string;
  controlStateId: string;
  canWrite: boolean;
  lists: ListEvidenceCandidate[];
  onChange: (rows: ListEvidenceCandidate[]) => void;
  part: "attached" | "others";
}

export function ObjectiveLists({ orgId, assessmentId, controlStateId, canWrite, lists, onChange, part }: Props) {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function act(listId: string, fn: typeof api.linkList) {
    setBusy(listId);
    setError(null);
    try {
      onChange(await fn(orgId, assessmentId, controlStateId, listId));
    } catch (e: unknown) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  const errorLine = error && <div className="form-error">{error}</div>;

  if (part === "attached") {
    const attached = lists.filter((l) => l.linked_version_id !== null);
    if (attached.length === 0) return errorLine || null;
    return (
      <div className="ev-lists" aria-label="Lists for this practice">
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
                    className="btn-ghost btn-sm ev-detach"
                    disabled={busy !== null}
                    onClick={() => act(l.list_id, api.unlinkList)}
                    title="Detach from this objective. The list and its versions stay; this objective stops resting on it, and shows it as detached."
                    aria-label={`Detach ${l.title}`}
                  >
                    Detach
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
        {errorLine}
      </div>
    );
  }

  const others = lists.filter((l) => l.linked_version_id === null);
  if (others.length === 0) return errorLine || null;
  const templates = others.filter((l) => l.is_template).length;
  return (
    <>
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
      {templates > 0 && (
        <p className="field-hint ev-template-note">
          {templates === 1 ? "To offer the template" : "To offer a template"}, adopt it into an org and fill it in.
        </p>
      )}
      {errorLine}
    </>
  );
}
