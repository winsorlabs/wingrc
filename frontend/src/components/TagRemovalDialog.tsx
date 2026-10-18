/**
 * The confirmation for removing a tag, shared by a list's practices and a
 * document's objectives (L.10, then X.1).
 *
 * It says what the server computed for this tag, never the rule in the
 * abstract: nothing to confirm while checking, the links named and no way
 * through when evidence rests on the tag, "no evidence rests" only when
 * the server found none, and "could not check" -- not "nothing rests" --
 * when the check failed.
 *
 * The server answers in fields and this writes the sentence, once, in the
 * subject's title. On live the list dialog wrapped the server's log
 * sentence in its own prefix and repeated it in a bullet, naming the list
 * by its key: one fact three times (L.10's live finding).
 */

import type { TagRemovalCheck } from "../types";

export function TagRemovalDialog({
  tag,
  subject,
  consequence,
  check,
  busy,
  onConfirm,
  onCancel,
}: {
  tag: string;
  /** The list's or document's title, as every other screen names it. */
  subject: string;
  /** What removing it changes, said before asking. */
  consequence: string;
  /** null while the server is asked. */
  check: TagRemovalCheck | "failed" | null;
  busy: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const askable = check === "failed" || (check !== null && check.removable);
  return (
    <div className="doc-notice doc-notice-warn list-tag-confirm" role="group" aria-label={`Remove ${tag}`}>
      {check === null && <p>Checking what rests on {tag}…</p>}
      {check !== null && check !== "failed" && check.reason === "evidence" && (
        <>
          <p>
            <strong>{tag}</strong> can't be removed from <strong>{subject}</strong> while evidence rests on it:
          </p>
          <ul>
            {check.links.map((k) => (
              <li key={`${k.assessment_name} ${k.objective_key} ${k.version_number}`}>
                version {k.version_number}, evidence for {k.objective_key} in <em>{k.assessment_name}</em>
              </li>
            ))}
          </ul>
          <p>Detach it first.</p>
        </>
      )}
      {check !== null && check !== "failed" && check.reason === "last_tag" && (
        <p>
          <strong>{tag}</strong> is the only practice <strong>{subject}</strong> serves. Add another before
          removing it.
        </p>
      )}
      {askable && (
        <>
          <p>
            Remove <strong>{tag}</strong>? {consequence}{" "}
            {check === "failed"
              ? `Could not check what rests on ${tag}; if evidence does, removing it will be refused.`
              : `No evidence rests on ${subject} for ${tag}.`}
          </p>
          <button type="button" className="btn-primary btn-sm" disabled={busy} onClick={onConfirm}>
            Yes, remove {tag}
          </button>{" "}
        </>
      )}
      <button type="button" className="btn-ghost btn-sm" onClick={onCancel}>
        Keep it
      </button>
    </div>
  );
}
