/**
 * Version history with the audit trail beside it (roadmap N.2 section 4).
 *
 * There is no parallel history table and there should not be one: the
 * versions ARE the content record, `audit_log` IS the action record, and
 * "who changed what, when" is those two read together. The server joins
 * them at read time (`routers/documents.py:get_document_history`); this
 * renders the result.
 *
 * Actor names come from the same resolution the audit-log screen uses
 * (`app/audit.py:identity_out`), so a deleted or anonymized user reads the
 * same way here as it does there rather than showing a bare GUID.
 */

import { useEffect, useState } from "react";

import { api } from "../api";
import type { DocumentAuditEvent, DocumentHistory, DocumentVersionRow } from "../types";

const ACTION_LABEL: Record<string, string> = {
  "document.create": "Document created",
  "document.update": "Details edited",
  "document.delete": "Document deleted",
  "document.version.create": "New version saved",
  "document.version.status_change": "Status changed",
  "document.objective_tag.add": "Objective tagged",
  "document.objective_tag.remove": "Objective untagged",
  "document.publish": "Published and approved",
};

function actorName(event: DocumentAuditEvent): string {
  if (event.actor_user) {
    if (event.actor_user.status === "anonymized") return "(anonymized user)";
    if (event.actor_user.status === "deleted") return "(deleted user)";
    return event.actor_user.display_name || event.actor_user.email || event.actor;
  }
  return event.actor === "system" ? "System" : event.actor;
}

function statusChange(event: DocumentAuditEvent): string | null {
  const before = event.before_value?.status;
  const after = event.after_value?.status;
  if (typeof before === "string" && typeof after === "string") {
    return `${before.replace("_", " ")} → ${after.replace("_", " ")}`;
  }
  return null;
}

export function DocumentHistoryPanel({
  orgId,
  documentId,
  reloadKey,
  onCompare,
}: {
  orgId: string;
  documentId: string;
  reloadKey?: number;
  onCompare: (fromVersionId: string | undefined, toVersionId: string) => void;
}) {
  const [history, setHistory] = useState<DocumentHistory | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setError(null);
    api
      .getDocumentHistory(orgId, documentId)
      .then((h) => {
        if (!cancelled) setHistory(h);
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : "Could not load history.");
      });
    return () => {
      cancelled = true;
    };
  }, [orgId, documentId, reloadKey]);

  if (error) return <div className="doc-notice doc-notice-error">{error}</div>;
  if (!history) return <div className="empty">Loading history…</div>;

  // Versions come back newest first; the predecessor of versions[i] is
  // versions[i + 1]. The oldest has none, which is what makes its compare
  // a legitimate "initial version" view rather than a disabled button.
  const versions: DocumentVersionRow[] = history.versions;

  return (
    <div className="doc-history">
      <h3>Versions</h3>
      <table className="doc-table">
        <thead>
          <tr>
            <th>Version</th>
            <th>Status</th>
            <th>Created</th>
            <th>Approved</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {versions.map((version, i) => {
            const predecessor = versions[i + 1];
            return (
              <tr key={version.id}>
                <td>{version.version_number}</td>
                <td>
                  <span className={`doc-status doc-status-${version.status}`}>
                    {version.status.replace("_", " ")}
                  </span>
                </td>
                <td>{new Date(version.created_at).toLocaleString()}</td>
                <td>
                  {version.approved_at ? new Date(version.approved_at).toLocaleDateString() : "—"}
                </td>
                <td>
                  <button
                    type="button"
                    className="btn-ghost btn-sm"
                    onClick={() => onCompare(predecessor?.id, version.id)}
                  >
                    {predecessor ? `Compare with v${predecessor.version_number}` : "View as initial"}
                  </button>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>

      <h3>Activity</h3>
      {history.events.length === 0 ? (
        <p className="field-hint">No recorded activity for this document.</p>
      ) : (
        <ul className="doc-timeline">
          {history.events.map((event) => {
            const transition = statusChange(event);
            return (
              <li key={event.id}>
                <span className="doc-timeline-when">
                  {new Date(event.created_at).toLocaleString()}
                </span>
                <span className="doc-timeline-what">
                  {ACTION_LABEL[event.action] ?? event.action}
                  {transition && <span className="field-hint"> ({transition})</span>}
                  {event.action === "document.version.create" &&
                    typeof event.after_value?.version_number === "number" && (
                      <span className="field-hint"> (v{String(event.after_value.version_number)})</span>
                    )}
                </span>
                <span className="doc-timeline-who">{actorName(event)}</span>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
