import { useEffect, useState } from "react";
import { api } from "../api";
import type { EvidenceTaskRow } from "../types";

interface Props {
  orgId: string;
  assessmentId: string;
  onClose: () => void;
}

const ARTIFACT_LABELS: Record<string, string> = {
  screenshot: "Screenshot",
  export: "Export",
  document: "Document",
  link: "Link",
  policy: "Policy",
};

const STATUS_LABELS: Record<string, string> = {
  open: "Open",
  collected: "Collected",
  na: "N/A",
};

function groupBySession(tasks: EvidenceTaskRow[]): [string, EvidenceTaskRow[]][] {
  const order: string[] = [];
  const groups: Record<string, EvidenceTaskRow[]> = {};
  for (const t of tasks) {
    const key = t.collection_session ?? "Uncategorised";
    if (!groups[key]) {
      groups[key] = [];
      order.push(key);
    }
    groups[key].push(t);
  }
  return order.map((k) => [k, groups[k]]);
}

export function EvidenceTasksPanel({ orgId, assessmentId, onClose }: Props) {
  const [tasks, setTasks] = useState<EvidenceTaskRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState<Record<string, boolean>>({});
  const [showArchived, setShowArchived] = useState(false);

  function load() {
    setLoading(true);
    setError(null);
    api
      .getEvidenceTasks(orgId, assessmentId)
      .then(setTasks)
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  }

  useEffect(load, [orgId, assessmentId]);

  async function handleStatusChange(taskId: string, newStatus: string) {
    setSaving((s) => ({ ...s, [taskId]: true }));
    try {
      const updated = await api.patchEvidenceTask(orgId, assessmentId, taskId, newStatus);
      setTasks((prev) =>
        prev.map((t) => (t.id === updated.id ? { ...t, status: updated.status } : t))
      );
    } catch (e: unknown) {
      setError((e as Error).message);
    } finally {
      setSaving((s) => ({ ...s, [taskId]: false }));
    }
  }

  const visible = showArchived ? tasks : tasks.filter((t) => !t.is_archived);
  const archivedCount = tasks.filter((t) => t.is_archived).length;
  const groups = groupBySession(visible);

  return (
    <div className="panel-overlay" onClick={onClose}>
      <aside className="products-panel tasks-panel" onClick={(e) => e.stopPropagation()}>
        <div className="products-panel-header">
          <span className="products-panel-title">Evidence Tasks</span>
          <button className="drawer-close" onClick={onClose} aria-label="Close">
            &#x2715;
          </button>
        </div>
        <div className="products-panel-subtitle">
          Grouped by collection session. Mark tasks collected when evidence is gathered.
        </div>

        {loading ? (
          <div className="loading">Loading tasks&#x2026;</div>
        ) : error ? (
          <div className="error-msg">{error}</div>
        ) : tasks.length === 0 ? (
          <div className="empty">No evidence tasks — activate a product to generate them.</div>
        ) : (
          <>
            {archivedCount > 0 && (
              <button
                className="tasks-archive-toggle"
                onClick={() => setShowArchived((v) => !v)}
              >
                {showArchived
                  ? `Hide ${archivedCount} archived task${archivedCount !== 1 ? "s" : ""}`
                  : `Show ${archivedCount} archived task${archivedCount !== 1 ? "s" : ""}`}
              </button>
            )}

            <div className="tasks-list">
              {groups.map(([session, sessionTasks]) => (
                <div key={session} className="tasks-session">
                  <div className="tasks-session-label">{session}</div>
                  {sessionTasks.map((task) => (
                    <div
                      key={task.id}
                      className={`task-card${task.is_archived ? " task-card--archived" : ""}`}
                    >
                      <div className="task-card-header">
                        <span className="task-card-title">{task.title}</span>
                        <span className={`task-type-badge task-type-${task.artifact_type}`}>
                          {ARTIFACT_LABELS[task.artifact_type] ?? task.artifact_type}
                        </span>
                      </div>

                      {task.source_product_name && (
                        <div className="task-card-source">{task.source_product_name}</div>
                      )}

                      <div className="task-card-controls">
                        {task.linked_states.map((ls) => (
                          <span key={ls.control_state_id} className="task-ctrl-chip">
                            {ls.control_id}[{ls.objective_key}]
                          </span>
                        ))}
                      </div>

                      <div className="task-card-footer">
                        {task.is_archived ? (
                          <span className="task-archived-label">
                            Archived
                            {task.archived_at
                              ? ` — ${new Date(task.archived_at).toLocaleDateString()}`
                              : ""}
                          </span>
                        ) : (
                          <select
                            className="task-status-select"
                            value={task.status}
                            disabled={saving[task.id]}
                            onChange={(e) => handleStatusChange(task.id, e.target.value)}
                            aria-label={`Status for ${task.title}`}
                          >
                            {Object.entries(STATUS_LABELS).map(([val, label]) => (
                              <option key={val} value={val}>
                                {label}
                              </option>
                            ))}
                          </select>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              ))}
            </div>
          </>
        )}
      </aside>
    </div>
  );
}
