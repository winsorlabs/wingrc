/**
 * Version-to-version diff for a document (roadmap N.2 section 2).
 *
 * Built for a reader who is not a developer -- an MSP engineer or a C3PAO
 * assessor asking "what changed in this policy between March and
 * September". So: side-by-side or inline, changed words highlighted inside
 * a changed paragraph rather than the whole paragraph flagged, and long
 * unchanged runs collapsed behind a count.
 *
 * All of the diffing happens server-side in `app/document_diff.py`, which
 * is a pure function over two strings. This component renders rows; it does
 * not compute them, and it does not receive a patch string to parse.
 *
 * Objective-tag changes are shown alongside the text diff, because a
 * document whose tags changed has changed in a way that affects the SSP
 * even when the body is byte-identical. Where the two sides' objective sets
 * were resolved on different bases the server says so and that note is
 * rendered verbatim -- an unpublished draft's live tag set is not presented
 * as a record of what that version covered.
 */

import { useEffect, useState } from "react";

import { api } from "../api";
import type { DocumentDiff, DocumentDiffRow, DiffSpan } from "../types";

type Layout = "side-by-side" | "inline";

/** Split a line into alternating unchanged/changed segments. */
function segments(text: string, spans: DiffSpan[]): Array<{ text: string; changed: boolean }> {
  if (!spans.length) return [{ text, changed: false }];
  const out: Array<{ text: string; changed: boolean }> = [];
  let cursor = 0;
  for (const span of [...spans].sort((a, b) => a.start - b.start)) {
    if (span.start > cursor) out.push({ text: text.slice(cursor, span.start), changed: false });
    out.push({ text: text.slice(span.start, span.end), changed: true });
    cursor = span.end;
  }
  if (cursor < text.length) out.push({ text: text.slice(cursor), changed: false });
  return out;
}

function Line({ text, spans }: { text: string | null; spans: DiffSpan[] }) {
  if (text === null) return <span className="diff-absent" aria-hidden="true" />;
  if (text === "") return <span className="diff-blank">(blank line)</span>;
  return (
    <>
      {segments(text, spans).map((seg, i) =>
        seg.changed ? (
          <mark key={i} className="diff-word">
            {seg.text}
          </mark>
        ) : (
          <span key={i}>{seg.text}</span>
        ),
      )}
    </>
  );
}

const OP_LABEL: Record<string, string> = {
  insert: "added",
  delete: "removed",
  replace: "changed",
  equal: "unchanged",
};

function SideBySideRows({ rows }: { rows: DocumentDiffRow[] }) {
  return (
    <table className="diff-table">
      <thead>
        <tr>
          <th className="diff-gutter">#</th>
          <th>Previous version</th>
          <th className="diff-gutter">#</th>
          <th>This version</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row, i) => {
          if (row.op === "skip") {
            return (
              <tr key={i} className="diff-skip">
                <td colSpan={4}>{row.skipped} unchanged lines</td>
              </tr>
            );
          }
          return (
            <tr key={i} className={`diff-row diff-${row.op}`}>
              <td className="diff-gutter">{row.old_line_no ?? ""}</td>
              <td className={row.op === "insert" ? "diff-cell diff-cell-absent" : "diff-cell"}>
                <Line text={row.old_text} spans={row.old_spans} />
              </td>
              <td className="diff-gutter">{row.new_line_no ?? ""}</td>
              <td className={row.op === "delete" ? "diff-cell diff-cell-absent" : "diff-cell"}>
                <Line text={row.new_text} spans={row.new_spans} />
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

function InlineRows({ rows }: { rows: DocumentDiffRow[] }) {
  return (
    <div className="diff-inline">
      {rows.map((row, i) => {
        if (row.op === "skip") {
          return (
            <div key={i} className="diff-skip">
              {row.skipped} unchanged lines
            </div>
          );
        }
        if (row.op === "replace") {
          return (
            <div key={i} className="diff-inline-pair">
              <div className="diff-inline-line diff-delete">
                <span className="diff-marker">−</span>
                <Line text={row.old_text} spans={row.old_spans} />
              </div>
              <div className="diff-inline-line diff-insert">
                <span className="diff-marker">+</span>
                <Line text={row.new_text} spans={row.new_spans} />
              </div>
            </div>
          );
        }
        const isDelete = row.op === "delete";
        return (
          <div key={i} className={`diff-inline-line diff-${row.op}`}>
            <span className="diff-marker">{isDelete ? "−" : row.op === "insert" ? "+" : " "}</span>
            <Line
              text={isDelete ? row.old_text : row.new_text}
              spans={isDelete ? row.old_spans : row.new_spans}
            />
          </div>
        );
      })}
    </div>
  );
}

function ObjectiveChanges({ diff }: { diff: DocumentDiff }) {
  const { objectives } = diff;
  return (
    <div className="diff-objectives">
      <h4>Objective coverage</h4>
      {!objectives.changed && (
        <p className="field-hint">
          Unchanged — {objectives.unchanged.length} objective
          {objectives.unchanged.length === 1 ? "" : "s"}.
        </p>
      )}
      {objectives.added.length > 0 && (
        <p>
          <span className="diff-marker diff-insert">+</span> Now also covers{" "}
          {objectives.added.length} objective{objectives.added.length === 1 ? "" : "s"}.
        </p>
      )}
      {objectives.removed.length > 0 && (
        <p>
          <span className="diff-marker diff-delete">−</span> No longer covers{" "}
          {objectives.removed.length} objective{objectives.removed.length === 1 ? "" : "s"}.
        </p>
      )}
      {diff.objective_basis_note && <p className="diff-basis-note">{diff.objective_basis_note}</p>}
    </div>
  );
}

export function DocumentDiffView({
  orgId,
  documentId,
  fromVersionId,
  toVersionId,
  onClose,
}: {
  orgId: string;
  documentId: string;
  fromVersionId?: string;
  toVersionId?: string;
  onClose?: () => void;
}) {
  const [diff, setDiff] = useState<DocumentDiff | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [layout, setLayout] = useState<Layout>("side-by-side");
  const [showUnchanged, setShowUnchanged] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setDiff(null);
    setError(null);
    api
      .getDocumentDiff(orgId, documentId, {
        fromVersionId,
        toVersionId,
        contextLines: showUnchanged ? 100 : 3,
      })
      .then((d) => {
        if (!cancelled) setDiff(d);
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : "Could not load the diff.");
      });
    return () => {
      cancelled = true;
    };
  }, [orgId, documentId, fromVersionId, toVersionId, showUnchanged]);

  if (error) return <div className="doc-notice doc-notice-error">{error}</div>;
  if (!diff) return <div className="empty">Loading diff…</div>;

  const { body } = diff;

  return (
    <div className="doc-diff">
      <div className="doc-diff-head">
        <div>
          <h3>
            {body.is_initial ? (
              <>Initial version {diff.to_version.version_number}</>
            ) : (
              <>
                Version {diff.from_version?.version_number} → {diff.to_version.version_number}
              </>
            )}
          </h3>
          <p className="field-hint">
            {body.is_initial ? (
              <>
                Nothing to compare against — this is the first version of the document.{" "}
                {body.added_lines} line{body.added_lines === 1 ? "" : "s"}.
              </>
            ) : body.identical ? (
              <>The text is identical between these two versions.</>
            ) : (
              <>
                {body.changed_lines} changed, {body.added_lines} added, {body.removed_lines}{" "}
                removed.
              </>
            )}
          </p>
        </div>
        <div className="doc-diff-actions">
          <div role="group" aria-label="Diff layout">
            {(["side-by-side", "inline"] as Layout[]).map((l) => (
              <button
                key={l}
                type="button"
                className={layout === l ? "doc-tbtn active" : "doc-tbtn"}
                onClick={() => setLayout(l)}
              >
                {l}
              </button>
            ))}
          </div>
          <label className="doc-diff-toggle">
            <input
              type="checkbox"
              checked={showUnchanged}
              onChange={(e) => setShowUnchanged(e.target.checked)}
            />{" "}
            Show all unchanged text
          </label>
          {onClose && (
            <button type="button" className="btn-ghost btn-sm" onClick={onClose}>
              Close
            </button>
          )}
        </div>
      </div>

      <ObjectiveChanges diff={diff} />

      {body.rows.length === 0 ? (
        <p className="field-hint">Both versions are empty.</p>
      ) : layout === "side-by-side" ? (
        <SideBySideRows rows={body.rows} />
      ) : (
        <InlineRows rows={body.rows} />
      )}

      {diff.events.length > 0 && (
        <div className="doc-diff-events">
          <h4>What happened between these versions</h4>
          <ul className="doc-timeline">
            {diff.events.map((event) => (
              <li key={event.id}>
                <span className="doc-timeline-when">
                  {new Date(event.created_at).toLocaleString()}
                </span>
                <span className="doc-timeline-what">{event.action}</span>
                <span className="doc-timeline-who">
                  {event.actor_user?.display_name ?? event.actor_user?.email ?? event.actor}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      <p className="field-hint diff-legend">
        Highlighted words are the parts of a line that changed. {OP_LABEL.insert} /{" "}
        {OP_LABEL.delete} / {OP_LABEL.replace} lines are colour-coded and marked with + and −.
      </p>
    </div>
  );
}
