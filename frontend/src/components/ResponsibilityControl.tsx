/**
 * An objective's responsibility, set by a person (docs/roadmap.md,
 * "Responsibility had no human write path"). It prints in the bundle's
 * customer responsibility matrix and the SSP, so it is a claim an assessor
 * reads; before this control nothing but a tool's activation could set it.
 *
 * The RACI suggestion is shown, never applied: a person clicks it, or
 * dismisses it. A tool that would change a value set here asks at
 * activation (ActivationAsk).
 */
import { useEffect, useState } from "react";
import { api } from "../api";
import {
  RESPONSIBILITY_LABELS,
  responsibilitySuggestionText,
  suggestedResponsibility,
} from "../lib/raci";
import type { RaciAssignmentRow } from "../types";

const VALUES = ["provider_satisfies", "shared", "customer_owns", "external_system"] as const;

export function provenanceLine(setBy: string | null, setAt: string | null): string {
  if (setBy) return `Set by ${setBy}${setAt ? ` on ${new Date(setAt).toLocaleDateString()}` : ""}.`;
  return "Not set by a person: the default, or the tools covering this objective.";
}

export function ResponsibilityControl({
  orgId,
  assessmentId,
  controlStateId,
  responsibility,
  setBy,
  setAt,
  assignments,
  canWrite,
  onChanged,
}: {
  orgId: string;
  assessmentId: string;
  controlStateId: string;
  responsibility: string;
  setBy: string | null;
  setAt: string | null;
  /** This objective's RACI assignments, for the suggestion. */
  assignments: RaciAssignmentRow[];
  canWrite: boolean;
  onChanged: (value: string, setBy: string | null, setAt: string | null) => void;
}) {
  const [value, setValue] = useState(responsibility);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [dismissed, setDismissed] = useState<string | null>(null);
  useEffect(() => setValue(responsibility), [responsibility]);

  const suggested = suggestedResponsibility(assignments);
  const showSuggestion = canWrite && suggested !== null && suggested !== responsibility && dismissed !== suggested;

  async function save(next: string, via: "manual" | "raci_suggestion") {
    setBusy(true);
    setErr(null);
    try {
      const r = await api.setResponsibility(orgId, assessmentId, controlStateId, next, via);
      onChanged(r.responsibility, r.responsibility_set_by, r.responsibility_set_at);
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="resp-control">
      <div className="resp-control-row">
        <label htmlFor={`resp-${controlStateId}`} className="resp-control-label">Responsibility</label>
        <select
          id={`resp-${controlStateId}`}
          value={value}
          disabled={!canWrite || busy}
          onChange={(e) => setValue(e.target.value)}
        >
          {VALUES.map((v) => (
            <option key={v} value={v}>{RESPONSIBILITY_LABELS[v]}</option>
          ))}
        </select>
        {canWrite && value !== responsibility && (
          <>
            <button className="btn-primary btn-sm" disabled={busy} onClick={() => save(value, "manual")}>Save</button>
            <button className="btn-ghost btn-sm" disabled={busy} onClick={() => setValue(responsibility)}>Cancel</button>
          </>
        )}
      </div>
      <div className="resp-control-meta">{provenanceLine(setBy, setAt)}</div>
      {showSuggestion && suggested && (
        <div className="resp-suggestion" role="status">
          <span>{responsibilitySuggestionText(assignments, suggested)}</span>
          <span className="resp-suggestion-actions">
            <button className="btn-ghost btn-sm" disabled={busy} onClick={() => save(suggested, "raci_suggestion")}>
              Set to {RESPONSIBILITY_LABELS[suggested]}
            </button>
            <button className="btn-ghost btn-sm" disabled={busy} onClick={() => setDismissed(suggested)}>Dismiss</button>
          </span>
        </div>
      )}
      {err && <div className="error-msg">{err}</div>}
    </div>
  );
}
