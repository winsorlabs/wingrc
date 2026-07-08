import { useEffect, useState } from "react";
import { api } from "../api";
import type { StatementRow } from "../types";

const STMT_STATUSES = [
  { value: "draft", label: "Draft" },
  { value: "reviewed", label: "Reviewed" },
  { value: "approved", label: "Approved" },
] as const;

interface StatementItem {
  objective_id: string;
  objective_key: string;
  objective_text: string;
  objective_guidance: string | null;
  body: string;
  status: string;
  id: string | null;
}

interface Props {
  orgId: string;
  assessmentId: string;
  controlDbId: string;
  controlId: string;
  controlTitle: string;
  onClose: () => void;
  onSave: (updates: Array<{ objectiveId: string; status: string }>) => void;
}

function fromRow(row: StatementRow): StatementItem {
  return {
    objective_id: row.objective_id,
    objective_key: row.objective_key,
    objective_text: row.objective_text,
    objective_guidance: row.objective_guidance,
    body: row.body,
    status: row.status ?? "draft",
    id: row.id,
  };
}

export function ControlDrawer({
  orgId,
  assessmentId,
  controlDbId,
  controlId,
  controlTitle,
  onClose,
  onSave,
}: Props) {
  const [items, setItems] = useState<StatementItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [openGuidance, setOpenGuidance] = useState<Record<string, boolean>>({});
  const [showDiscussion, setShowDiscussion] = useState(false);

  useEffect(() => {
    setLoading(true);
    setError(null);
    setSaved(false);
    api
      .getStatements(orgId, assessmentId, controlDbId)
      .then((rows) => setItems(rows.map(fromRow)))
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  }, [orgId, assessmentId, controlDbId]);

  function updateItem(objectiveId: string, field: "body" | "status", value: string) {
    setSaved(false);
    setItems((prev) =>
      prev.map((it) => (it.objective_id === objectiveId ? { ...it, [field]: value } : it))
    );
  }

  function applyToAllEmpty() {
    const first = items.find((it) => it.body.trim() !== "");
    if (!first) return;
    setSaved(false);
    setItems((prev) =>
      prev.map((it) => (it.body.trim() === "" ? { ...it, body: first.body } : it))
    );
  }

  async function handleSave() {
    setSaving(true);
    setError(null);
    setSaved(false);
    try {
      const payload = items
        .filter((it) => it.body.trim() !== "")
        .map((it) => ({
          objective_id: it.objective_id,
          body: it.body,
          status: it.status,
        }));
      if (payload.length === 0) {
        setSaved(true);
        return;
      }
      const result = await api.putStatements(orgId, assessmentId, controlDbId, payload);
      setItems((prev) => {
        const byId = Object.fromEntries(result.map((r) => [r.objective_id, r]));
        return prev.map((it) =>
          byId[it.objective_id]
            ? { ...it, id: byId[it.objective_id].id, status: byId[it.objective_id].status ?? it.status }
            : it
        );
      });
      setSaved(true);
      onSave(result.map((r) => ({ objectiveId: r.objective_id, status: r.status ?? "draft" })));
    } catch (e: unknown) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  }

  const hasAnyBody = items.some((it) => it.body.trim() !== "");
  const hasEmptyItems = items.some((it) => it.body.trim() === "");

  return (
    <div className="drawer-overlay" onClick={onClose}>
      <aside className="drawer" onClick={(e) => e.stopPropagation()}>
        <div className="drawer-header">
          <div>
            <div className="drawer-control-id">{controlId}</div>
            <div className="drawer-control-title">{controlTitle}</div>
          </div>
          <button className="drawer-close" onClick={onClose} aria-label="Close">
            &#x2715;
          </button>
        </div>

        {loading ? (
          <div className="loading">Loading&#x2026;</div>
        ) : (
          <div className="drawer-scroll">
            {items[0]?.control_discussion && (
              <div className="drawer-control-discussion">
                <button
                  className="drawer-guidance-toggle"
                  onClick={() => setShowDiscussion((v) => !v)}
                >
                  {showDiscussion ? "Hide discussion" : "About this control"}
                </button>
                {showDiscussion && (
                  <div className="drawer-guidance-text">{items[0].control_discussion}</div>
                )}
              </div>
            )}
            {items.map((item) => (
              <div key={item.objective_id} className="drawer-objective">
                <div className="drawer-obj-header">
                  <span className="drawer-obj-key">[{item.objective_key}]</span>
                  <span className="drawer-obj-text">{item.objective_text}</span>
                  {item.objective_guidance && (
                    <button
                      className="drawer-guidance-toggle"
                      onClick={() =>
                        setOpenGuidance((prev) => ({
                          ...prev,
                          [item.objective_id]: !prev[item.objective_id],
                        }))
                      }
                    >
                      {openGuidance[item.objective_id] ? "Hide guidance" : "Show guidance"}
                    </button>
                  )}
                </div>
                {item.objective_guidance && openGuidance[item.objective_id] && (
                  <div className="drawer-guidance-text">{item.objective_guidance}</div>
                )}
                <div className="drawer-obj-controls">
                  <textarea
                    className="drawer-textarea drawer-textarea-sm"
                    value={item.body}
                    onChange={(e) => updateItem(item.objective_id, "body", e.target.value)}
                    placeholder={`Describe how [${item.objective_key}] is implemented…`}
                    rows={4}
                  />
                  <select
                    className="drawer-status-select"
                    value={item.status}
                    onChange={(e) => updateItem(item.objective_id, "status", e.target.value)}
                  >
                    {STMT_STATUSES.map((s) => (
                      <option key={s.value} value={s.value}>
                        {s.label}
                      </option>
                    ))}
                  </select>
                </div>
              </div>
            ))}

            <div className="drawer-footer">
              {hasAnyBody && hasEmptyItems && (
                <button className="btn-ghost btn-sm" onClick={applyToAllEmpty}>
                  Apply first to all empty
                </button>
              )}
              <button className="btn-primary" onClick={handleSave} disabled={saving}>
                {saving ? "Saving…" : "Save"}
              </button>
              {saved && <span className="drawer-saved-msg">Saved</span>}
            </div>

            {error && (
              <div className="error-msg" style={{ padding: "0.5rem 1.25rem" }}>
                {error}
              </div>
            )}
          </div>
        )}
      </aside>
    </div>
  );
}
