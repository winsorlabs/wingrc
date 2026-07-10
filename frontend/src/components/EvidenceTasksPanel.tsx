import { useEffect, useRef, useState } from "react";
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

const CADENCE_LABELS: Record<string, string> = {
  annual: "Annual",
  quarterly: "Quarterly",
  monthly: "Monthly",
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

// Which expand mode is open on a task card
type ExpandMode = "ref" | null;

export function EvidenceTasksPanel({ orgId, assessmentId, onClose }: Props) {
  const [tasks, setTasks] = useState<EvidenceTaskRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState<Record<string, boolean>>({});
  const [showArchived, setShowArchived] = useState(false);

  // Per-task expand state for reference form
  const [expandMode, setExpandMode] = useState<Record<string, ExpandMode>>({});
  const [refTitle, setRefTitle] = useState<Record<string, string>>({});
  const [refUrl, setRefUrl] = useState<Record<string, string>>({});
  const [collectError, setCollectError] = useState<Record<string, string>>({});

  // Single hidden file input, tracks which task is pending
  const fileInputRef = useRef<HTMLInputElement>(null);
  const pendingTaskRef = useRef<string | null>(null);

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

  function triggerFileUpload(taskId: string) {
    pendingTaskRef.current = taskId;
    fileInputRef.current?.click();
  }

  async function handleFileSelected(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    const taskId = pendingTaskRef.current;
    if (!file || !taskId) return;

    // Reset input so the same file can be re-selected if needed
    e.target.value = "";
    pendingTaskRef.current = null;

    const task = tasks.find((t) => t.id === taskId);
    const artifactType = task?.artifact_type ?? "document";

    setSaving((s) => ({ ...s, [taskId]: true }));
    setCollectError((s) => ({ ...s, [taskId]: "" }));
    try {
      await api.uploadTaskEvidence(orgId, assessmentId, taskId, file, artifactType);
      setTasks((prev) =>
        prev.map((t) => (t.id === taskId ? { ...t, status: "collected" } : t))
      );
    } catch (e: unknown) {
      setCollectError((s) => ({ ...s, [taskId]: (e as Error).message }));
    } finally {
      setSaving((s) => ({ ...s, [taskId]: false }));
    }
  }

  async function handleAddReference(taskId: string) {
    const title = (refTitle[taskId] ?? "").trim();
    const url = (refUrl[taskId] ?? "").trim();
    if (!title || !url) {
      setCollectError((s) => ({ ...s, [taskId]: "Title and URL are required" }));
      return;
    }

    const task = tasks.find((t) => t.id === taskId);
    const artifactType = task?.artifact_type ?? "document";

    setSaving((s) => ({ ...s, [taskId]: true }));
    setCollectError((s) => ({ ...s, [taskId]: "" }));
    try {
      await api.addTaskReference(orgId, assessmentId, taskId, {
        title,
        location: url,
        artifact_type: artifactType,
      });
      setTasks((prev) =>
        prev.map((t) => (t.id === taskId ? { ...t, status: "collected" } : t))
      );
      setExpandMode((m) => ({ ...m, [taskId]: null }));
      setRefTitle((s) => ({ ...s, [taskId]: "" }));
      setRefUrl((s) => ({ ...s, [taskId]: "" }));
    } catch (e: unknown) {
      setCollectError((s) => ({ ...s, [taskId]: (e as Error).message }));
    } finally {
      setSaving((s) => ({ ...s, [taskId]: false }));
    }
  }

  function toggleRefForm(taskId: string) {
    setExpandMode((m) => ({ ...m, [taskId]: m[taskId] === "ref" ? null : "ref" }));
    setCollectError((s) => ({ ...s, [taskId]: "" }));
  }

  const visible = showArchived ? tasks : tasks.filter((t) => !t.is_archived);
  const archivedCount = tasks.filter((t) => t.is_archived).length;
  const groups = groupBySession(visible);

  return (
    <div className="panel-overlay" onClick={onClose}>
      <aside className="products-panel tasks-panel" onClick={(e) => e.stopPropagation()}>
      {/* Hidden file input INSIDE the aside — programmatic .click() bubbles to aside, not overlay */}
      <input
        ref={fileInputRef}
        type="file"
        style={{ display: "none" }}
        accept=".png,.jpg,.jpeg,.gif,.webp,.pdf,.doc,.docx,.xls,.xlsx,.txt,.csv,.zip"
        onChange={handleFileSelected}
      />
        <div className="products-panel-header">
          <span className="products-panel-title">Evidence Tasks</span>
          <button className="drawer-close" onClick={onClose} aria-label="Close">
            &#x2715;
          </button>
        </div>
        <div className="products-panel-subtitle">
          Grouped by collection session. Upload evidence directly on a task to mark it collected
          and attach the artifact to every linked objective automatically.
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
                  {sessionTasks.map((task) => {
                    const isExpanded = expandMode[task.id] === "ref";
                    const isSaving = saving[task.id];
                    const taskError = collectError[task.id];

                    return (
                      <div
                        key={task.id}
                        className={`task-card${task.is_archived ? " task-card--archived" : ""}`}
                      >
                        <div className="task-card-header">
                          <span className="task-card-title">{task.title}</span>
                          <div className="task-card-badges">
                            <span className={`task-type-badge task-type-${task.artifact_type}`}>
                              {ARTIFACT_LABELS[task.artifact_type] ?? task.artifact_type}
                            </span>
                            {task.cadence && (
                              <span className="cadence-badge">
                                {CADENCE_LABELS[task.cadence] ?? task.cadence}
                              </span>
                            )}
                          </div>
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

                        {taskError && (
                          <div className="task-collect-error">{taskError}</div>
                        )}

                        {/* Reference form (inline expand) */}
                        {isExpanded && !task.is_archived && (
                          <div className="task-ref-form">
                            <input
                              type="text"
                              placeholder="Title (e.g. Audit log share)"
                              value={refTitle[task.id] ?? ""}
                              onChange={(e) =>
                                setRefTitle((s) => ({ ...s, [task.id]: e.target.value }))
                              }
                            />
                            <input
                              type="text"
                              placeholder="URL or path (https://… or \\server\share)"
                              value={refUrl[task.id] ?? ""}
                              onChange={(e) =>
                                setRefUrl((s) => ({ ...s, [task.id]: e.target.value }))
                              }
                              onKeyDown={(e) => {
                                if (e.key === "Enter") handleAddReference(task.id);
                              }}
                            />
                            <div className="task-ref-form-actions">
                              <button
                                className="btn-ghost btn-sm"
                                onClick={() => toggleRefForm(task.id)}
                              >
                                Cancel
                              </button>
                              <button
                                className="btn-primary btn-sm"
                                onClick={() => handleAddReference(task.id)}
                                disabled={isSaving}
                              >
                                {isSaving ? "Saving…" : "Save reference"}
                              </button>
                            </div>
                          </div>
                        )}

                        <div className="task-card-footer">
                          {task.is_archived ? (
                            <span className="task-archived-label">
                              Archived
                              {task.archived_at
                                ? ` — ${new Date(task.archived_at).toLocaleDateString()}`
                                : ""}
                            </span>
                          ) : (
                            <>
                              <select
                                className="task-status-select"
                                value={task.status}
                                disabled={isSaving}
                                onChange={(e) => handleStatusChange(task.id, e.target.value)}
                                aria-label={`Status for ${task.title}`}
                              >
                                {Object.entries(STATUS_LABELS).map(([val, label]) => (
                                  <option key={val} value={val}>
                                    {label}
                                  </option>
                                ))}
                              </select>
                              <div className="task-collect-actions">
                                <button
                                  className="btn-ghost btn-xs"
                                  onClick={() => triggerFileUpload(task.id)}
                                  disabled={isSaving}
                                  title="Upload a file"
                                >
                                  {isSaving && pendingTaskRef.current === task.id
                                    ? "Uploading…"
                                    : "↑ File"}
                                </button>
                                <button
                                  className={`btn-ghost btn-xs${isExpanded ? " active" : ""}`}
                                  onClick={() => toggleRefForm(task.id)}
                                  disabled={isSaving}
                                  title="Add a reference URL or path"
                                >
                                  ⊕ Reference
                                </button>
                              </div>
                            </>
                          )}
                        </div>
                      </div>
                    );
                  })}
                </div>
              ))}
            </div>
          </>
        )}
      </aside>
    </div>
  );
}
