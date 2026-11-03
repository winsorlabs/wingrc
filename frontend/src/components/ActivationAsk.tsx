/**
 * The question activation asks before it changes a judgement (Jarrod,
 * 2026-11-03): the objectives a person judged -- met, partial, not
 * applicable, inherited -- that this tool would be the first to cover.
 * One screen for all of them, each with its default
 * (backend/app/assessment.py's ACTIVATION_DEFAULTS).
 *
 * The score is the server's: every choice is sent back to the preview,
 * which runs the activation and rolls it back, and Activate waits until
 * the score shown is the one for the choices shown. Cancel calls nothing,
 * so the tool stays inactive and every status stays as it was; a closed
 * tab is the same, because only Activate writes.
 */

import { useEffect } from "react";
import type { ActivationChoice, ActivationPreview } from "../types";
import { STATUS_CLASS, STATUSES } from "./ObjectiveRow";

const LABEL: Record<string, string> = Object.fromEntries(STATUSES.map((s) => [s.value, s.label]));

function scoreLine(now: number | null, after: number | null): string {
  if (now === null || after === null) return "";
  if (after === now) return `SPRS stays at ${now}.`;
  const by = Math.abs(after - now);
  return `SPRS goes from ${now} to ${after} (${after > now ? "up" : "down"} ${by}).`;
}

export function ActivationAsk({
  productName,
  preview,
  choices,
  scoring,
  busy,
  error,
  onChoose,
  onConfirm,
  onCancel,
}: {
  productName: string;
  /** The server's last answer; its score is for the choices it was asked. */
  preview: ActivationPreview;
  choices: Record<string, ActivationChoice>;
  /** True while the score for the current choices is being computed. */
  scoring: boolean;
  busy: boolean;
  error: string | null;
  onChoose: (controlStateId: string, choice: ActivationChoice) => void;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onCancel();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onCancel]);

  const n = preview.objectives.length;
  return (
    <div className="modal-overlay" onClick={onCancel}>
      <div
        className="activation-ask"
        role="dialog"
        aria-modal="true"
        aria-labelledby="activation-ask-title"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 id="activation-ask-title">
          {n === 1
            ? `Activating ${productName} would change an objective you judged`
            : `Activating ${productName} would change ${n} objectives you judged`}
        </h2>
        <p>
          {productName} covers {n === 1 ? "it" : "these"}, and {n === 1 ? "it was" : "each was"} set by a person.
          Choose what each becomes. Nothing changes until you activate; Cancel leaves {productName} inactive.
        </p>
        <div className="activation-ask-rows">
          <table>
            <thead>
              <tr>
                <th scope="col">Objective</th>
                <th scope="col">Now</th>
                <th scope="col">Becomes</th>
              </tr>
            </thead>
            <tbody>
              {preview.objectives.map((o) => {
                const choice = choices[o.control_state_id] ?? o.choice;
                // The server's answer, shown only once it is for this choice.
                const result = o.choice === choice ? o.becomes : null;
                return (
                  <tr key={o.control_state_id}>
                    <td>
                      <div className="activation-ask-key">
                        {o.control_id}[{o.objective_key}]
                      </div>
                      <div className="activation-ask-text">{o.objective_text}</div>
                    </td>
                    <td>
                      <span className={`badge ${STATUS_CLASS[o.status] ?? ""}`}>{LABEL[o.status] ?? o.status}</span>
                    </td>
                    <td>
                      <select
                        aria-label={`What ${o.control_id}[${o.objective_key}] becomes`}
                        value={choice}
                        disabled={busy}
                        onChange={(e) => onChoose(o.control_state_id, e.target.value as ActivationChoice)}
                      >
                        <option value="keep">
                          Keep {LABEL[o.status] ?? o.status}
                          {o.default === "keep" ? " (default)" : ""}
                        </option>
                        <option value="needs_review">
                          Needs Review{o.default === "needs_review" ? " (default)" : ""}
                        </option>
                      </select>{" "}
                      {result !== null && (
                        <span className={`badge ${STATUS_CLASS[result] ?? ""}`}>{LABEL[result] ?? result}</span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        <p className="activation-ask-score" aria-live="polite">
          {scoring ? "Working out the score for these choices…" : scoreLine(preview.sprs_now, preview.sprs_after)}
        </p>
        {error && <div className="doc-notice doc-notice-error">{error}</div>}
        <div className="activation-ask-actions">
          <button type="button" className="btn-primary btn-sm" disabled={busy || scoring} onClick={onConfirm}>
            {busy ? "Activating…" : `Activate ${productName}`}
          </button>
          <button type="button" className="btn-ghost btn-sm" onClick={onCancel}>
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
}
