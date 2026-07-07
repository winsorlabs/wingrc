import { useEffect, useState } from "react";
import { api } from "../api";

const STMT_STATUSES = [
  { value: "draft", label: "Draft" },
  { value: "reviewed", label: "Reviewed" },
  { value: "approved", label: "Approved" },
] as const;

interface Props {
  orgId: string;
  assessmentId: string;
  controlDbId: string;
  controlId: string;
  controlTitle: string;
  onClose: () => void;
  onSave: (controlDbId: string, newStatus: string) => void;
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
  const [stmtBody, setStmtBody] = useState("");
  const [status, setStatus] = useState("draft");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    setLoading(true);
    setError(null);
    setSaved(false);
    api
      .getStatement(orgId, assessmentId, controlDbId)
      .then((s) => {
        setStmtBody(s.body);
        setStatus(s.status ?? "draft");
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  }, [orgId, assessmentId, controlDbId]);

  async function handleSave() {
    setSaving(true);
    setError(null);
    setSaved(false);
    try {
      const result = await api.putStatement(
        orgId,
        assessmentId,
        controlDbId,
        stmtBody,
        status
      );
      setSaved(true);
      onSave(controlDbId, result.status);
    } catch (e: unknown) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="drawer-overlay" onClick={onClose}>
      <aside className="drawer" onClick={(e) => e.stopPropagation()}>
        <div className="drawer-header">
          <div>
            <div className="drawer-control-id">{controlId}</div>
            <div className="drawer-control-title">{controlTitle}</div>
          </div>
          <button className="drawer-close" onClick={onClose} aria-label="Close">
            ✕
          </button>
        </div>

        <div className="drawer-section-label">Implementation Statement</div>

        {loading ? (
          <div className="loading">Loading…</div>
        ) : (
          <>
            <textarea
              className="drawer-textarea"
              value={stmtBody}
              onChange={(e) => {
                setStmtBody(e.target.value);
                setSaved(false);
              }}
              placeholder="Describe how this control is implemented…"
              rows={12}
            />
            <div className="drawer-footer">
              <select
                className="drawer-status-select"
                value={status}
                onChange={(e) => setStatus(e.target.value)}
              >
                {STMT_STATUSES.map((s) => (
                  <option key={s.value} value={s.value}>
                    {s.label}
                  </option>
                ))}
              </select>
              <button
                className="btn-primary"
                onClick={handleSave}
                disabled={saving}
              >
                {saving ? "Saving…" : "Save"}
              </button>
              {saved && (
                <span className="drawer-saved-msg">Saved</span>
              )}
            </div>
            {error && (
              <div className="error-msg" style={{ padding: "0.5rem 1.25rem" }}>
                {error}
              </div>
            )}
          </>
        )}
      </aside>
    </div>
  );
}
