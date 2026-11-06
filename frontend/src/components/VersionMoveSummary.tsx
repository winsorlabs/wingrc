/**
 * What a baseline version move left at needs_review, and why, said once
 * after the move (Jarrod, 2026-11-05). The move does not ask, so it
 * tells: needs_review appearing with no explanation is the same kind of
 * surprise activation's question removed, only smaller. The list is the
 * server's (engine.py:move_org_product_version's set_to_needs_review);
 * this writes the sentences.
 */

import type { MoveVersionResult, MovedToReview } from "../types";
import { STATUS_CLASS, STATUSES } from "./ObjectiveRow";

const LABEL: Record<string, string> = Object.fromEntries(STATUSES.map((s) => [s.value, s.label]));

// The first line names the product; each reason names only the version.
function why(r: MovedToReview, to: number): string {
  switch (r.why) {
    case "no_longer_covered":
      return r.still_covered
        ? `v${to} no longer covers it. Another tool still does.`
        : `v${to} no longer covers it.`;
    case "mapping_changed":
      return `v${to} covers this control differently.`;
    case "judged_newly_covered":
      return `v${to} now covers it, and a person had set it. A move does not ask, so it needs review.`;
    default:
      return r.why;
  }
}

export function VersionMoveSummary({
  productName,
  result,
  onDismiss,
}: {
  productName: string;
  result: MoveVersionResult;
  onDismiss: () => void;
}) {
  const rows = result.set_to_needs_review;
  const n = rows.length;
  const from = result.from_version === null ? "" : ` from v${result.from_version}`;
  return (
    <div className="doc-notice version-move-summary" role="status">
      <div className="version-move-summary-head">
        <strong>
          Moved {productName}
          {from} to v{result.to_version}.
        </strong>{" "}
        {n === 0
          ? "No objective was set to Needs Review."
          : n === 1
            ? "One objective was set to Needs Review:"
            : `${n} objectives were set to Needs Review:`}
      </div>
      {n > 0 && (
        <ul className="version-move-summary-rows">
          {rows.map((r) => (
            <li key={r.control_state_id}>
              <span className="version-move-summary-key">
                {r.control_id}[{r.objective_key}]
              </span>{" "}
              was <span className={`badge ${STATUS_CLASS[r.status_before] ?? ""}`}>{LABEL[r.status_before] ?? r.status_before}</span>.{" "}
              {why(r, result.to_version)}
            </li>
          ))}
        </ul>
      )}
      {result.tasks_created > 0 && (
        <div>
          {result.tasks_created === 1
            ? "One new evidence task was created."
            : `${result.tasks_created} new evidence tasks were created.`}
        </div>
      )}
      <button type="button" className="btn-ghost btn-sm" onClick={onDismiss}>
        Dismiss
      </button>
    </div>
  );
}
