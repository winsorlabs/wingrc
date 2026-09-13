import { useEffect, useState } from "react";
import { api } from "../api";
import type { Contact, SprsSubmission } from "../types";

// The record of what was actually FILED with SPRS -- never to be
// confused with an assessment's computed sprs_score/status (see
// AssessmentBoard's "Complete Assessment" action for that, genuinely
// separate, concept). Org-scoped, not assessment-scoped: the first
// submission is typically captured at onboarding, before any assessment
// exists here at all. Entirely optional -- an org with nothing recorded
// yet is normal, not incomplete, and this panel never implies otherwise.
interface Props {
  orgId: string;
  canWrite: boolean;
}

export function SprsSubmissionsPanel({ orgId, canWrite }: Props) {
  const [history, setHistory] = useState<SprsSubmission[]>([]);
  const [contacts, setContacts] = useState<Contact[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [showForm, setShowForm] = useState(false);
  const [score, setScore] = useState("");
  const [submittedDate, setSubmittedDate] = useState("");
  const [contactId, setContactId] = useState("");
  const [note, setNote] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  const [voidTarget, setVoidTarget] = useState<SprsSubmission | null>(null);
  const [voidReason, setVoidReason] = useState("");
  const [voiding, setVoiding] = useState(false);
  const [voidError, setVoidError] = useState<string | null>(null);

  function load() {
    setLoading(true);
    setError(null);
    Promise.all([api.listSprsSubmissions(orgId), api.getContacts(orgId)])
      .then(([h, c]) => {
        setHistory(h);
        setContacts(c);
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  }

  useEffect(load, [orgId]);

  function openForm() {
    setScore("");
    setSubmittedDate("");
    setContactId("");
    setNote("");
    setSaveError(null);
    setShowForm(true);
  }

  async function handleRecord() {
    if (!score || !submittedDate || !contactId) return;
    setSaving(true);
    setSaveError(null);
    try {
      await api.recordSprsSubmission(orgId, {
        score: Number(score),
        submitted_date: submittedDate,
        submitted_by_contact_id: contactId,
        note: note || undefined,
      });
      setShowForm(false);
      load();
    } catch (e) {
      setSaveError((e as Error).message);
    } finally {
      setSaving(false);
    }
  }

  async function handleVoid() {
    if (!voidTarget || !voidReason.trim()) return;
    setVoiding(true);
    setVoidError(null);
    try {
      await api.voidSprsSubmission(orgId, voidTarget.id, voidReason.trim());
      setVoidTarget(null);
      load();
    } catch (e) {
      setVoidError((e as Error).message);
    } finally {
      setVoiding(false);
    }
  }

  const current = history.find((s) => !s.voided_at) ?? null;

  return (
    <div className="sprs-submissions-panel">
      <div className="products-panel-header" style={{ padding: 0, border: "none", marginBottom: "0.75rem" }}>
        <span className="products-panel-title">SPRS Submissions</span>
        {canWrite && (
          <button className="btn-primary btn-sm" onClick={openForm}>Record a submission</button>
        )}
      </div>

      <p style={{ color: "#6b7280", fontSize: "0.85rem", marginTop: 0 }}>
        What was actually filed with SPRS -- a human action in a DoD system this
        app cannot observe, distinct from any assessment's computed score. Entirely
        optional; an org with nothing recorded yet is normal, not incomplete.
      </p>

      {loading ? (
        <div className="loading">Loading…</div>
      ) : error ? (
        <div className="form-error">{error}</div>
      ) : (
        <>
          {current ? (
            <div className="card" style={{ marginBottom: "1rem" }}>
              <div style={{ fontSize: "0.75rem", color: "#6b7280", textTransform: "uppercase" }}>
                Current submission
              </div>
              <div style={{ fontSize: "1.5rem", fontWeight: 600 }}>{current.score}</div>
              <div>
                Filed {current.submitted_date} by {current.submitted_by_name}
              </div>
              {current.note && <div style={{ color: "#6b7280" }}>{current.note}</div>}
            </div>
          ) : (
            <div className="empty" style={{ marginBottom: "1rem" }}>
              No SPRS submission on file for this org yet.
            </div>
          )}

          {history.length > 0 && (
            <div className="table-scroll">
              <table className="contacts-table">
                <thead>
                  <tr>
                    <th>Filed</th>
                    <th>Score</th>
                    <th>Submitted By</th>
                    <th>Note</th>
                    <th>Status</th>
                    {canWrite && <th></th>}
                  </tr>
                </thead>
                <tbody>
                  {history.map((s) => (
                    <tr key={s.id}>
                      <td>{s.submitted_date}</td>
                      <td>{s.score}</td>
                      <td>{s.submitted_by_name}</td>
                      <td>{s.note ?? ""}</td>
                      <td>
                        {s.voided_at ? (
                          <span className="status-badge status-warning" title={s.voided_reason ?? ""}>
                            Voided
                          </span>
                        ) : s.id === current?.id ? (
                          <span className="status-badge status-active">Current</span>
                        ) : (
                          <span className="status-badge status-inactive">Superseded</span>
                        )}
                      </td>
                      {canWrite && (
                        <td>
                          {!s.voided_at && (
                            <button
                              className="btn-ghost btn-sm"
                              onClick={() => {
                                setVoidTarget(s);
                                setVoidReason("");
                                setVoidError(null);
                              }}
                            >
                              Void
                            </button>
                          )}
                        </td>
                      )}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}

      {showForm && (
        <div className="drawer-overlay" onClick={() => setShowForm(false)}>
          <div className="drawer" onClick={(e) => e.stopPropagation()}>
            <div className="drawer-header">
              <h3>Record an SPRS submission</h3>
              <button className="drawer-close" onClick={() => setShowForm(false)} aria-label="Close">×</button>
            </div>
            <div className="drawer-body">
              <p style={{ color: "#6b7280" }}>
                Record what was actually filed with SPRS -- not a re-computation
                of this org's current score.
              </p>
              {saveError && <div className="form-error">{saveError}</div>}
              <div className="form-field">
                <label>
                  Score
                  <input
                    type="number"
                    min={-204}
                    max={110}
                    value={score}
                    onChange={(e) => setScore(e.target.value)}
                  />
                </label>
              </div>
              <div className="form-field">
                <label>
                  Date submitted
                  <input
                    type="date"
                    value={submittedDate}
                    onChange={(e) => setSubmittedDate(e.target.value)}
                  />
                </label>
              </div>
              <div className="form-field">
                <label>
                  Submitted by
                  <select value={contactId} onChange={(e) => setContactId(e.target.value)}>
                    <option value="">Select a contact…</option>
                    {contacts.map((c) => (
                      <option key={c.id} value={c.id}>{c.name} ({c.affiliation})</option>
                    ))}
                  </select>
                </label>
              </div>
              <div className="form-field">
                <label>
                  Note (optional)
                  <textarea value={note} onChange={(e) => setNote(e.target.value)} />
                </label>
              </div>
            </div>
            <div className="drawer-footer">
              <div style={{ flex: 1 }} />
              <button className="btn-ghost" onClick={() => setShowForm(false)}>Cancel</button>
              <button
                className="btn-primary"
                disabled={saving || !score || !submittedDate || !contactId}
                onClick={handleRecord}
              >
                {saving ? "Saving…" : "Record submission"}
              </button>
            </div>
          </div>
        </div>
      )}

      {voidTarget && (
        <div className="drawer-overlay" onClick={() => setVoidTarget(null)}>
          <div className="drawer" onClick={(e) => e.stopPropagation()}>
            <div className="drawer-header">
              <h3>Void this submission?</h3>
              <button className="drawer-close" onClick={() => setVoidTarget(null)} aria-label="Close">×</button>
            </div>
            <div className="drawer-body">
              <p style={{ color: "#6b7280" }}>
                The record itself is never deleted or edited -- this only marks
                it superseded/incorrect, with a reason, for the history.
              </p>
              {voidError && <div className="form-error">{voidError}</div>}
              <div className="form-field">
                <label>
                  Reason
                  <textarea value={voidReason} onChange={(e) => setVoidReason(e.target.value)} />
                </label>
              </div>
            </div>
            <div className="drawer-footer">
              <div style={{ flex: 1 }} />
              <button className="btn-ghost" onClick={() => setVoidTarget(null)}>Cancel</button>
              <button
                className="btn-primary"
                disabled={voiding || !voidReason.trim()}
                onClick={handleVoid}
              >
                {voiding ? "Voiding…" : "Void submission"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
