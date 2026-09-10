import { useEffect, useState } from "react";
import { api } from "../api";
import { canEditPractitionerNotes } from "../lib/roles";
import type { Contact, RaciAssignmentRow, ResolvedIdentity, StatementRow } from "../types";
import { EvidenceSection } from "./EvidenceSection";
import { RaciSection } from "./RaciSection";

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString();
}

// Migration 0032: provenance replaces the old draft/reviewed status. Never
// implies the note is now authoritative -- editorLabel below deliberately
// says "edited by", never "reviewed by".
function editorLabel(identity: ResolvedIdentity): string {
  if (identity.status === "active") return identity.display_name ?? identity.email ?? "a user";
  if (identity.status === "anonymized") return "an anonymized user";
  return "a deleted user";
}

const STMT_STATUSES = [
  { value: "draft", label: "Draft" },
  { value: "reviewed", label: "Reviewed" },
  { value: "approved", label: "Approved" },
] as const;

interface StatementItem {
  objective_id: string;
  control_state_id: string | null;
  objective_key: string;
  objective_text: string;
  official_guidance: string | null;
  official_guidance_source: string | null;
  practitioner_notes: string | null;
  practitioner_notes_generated_at: string | null;
  practitioner_notes_model: string | null;
  practitioner_notes_edited_at: string | null;
  practitioner_notes_edited_by: ResolvedIdentity | null;
  body: string;
  status: string;
  id: string | null;
  control_discussion: string | null;
  responsibility: string | null;
}

interface Props {
  orgId: string;
  assessmentId: string;
  controlDbId: string;
  controlId: string;
  controlTitle: string;
  canWrite: boolean;
  currentUserRole: string | null | undefined;
  onClose: () => void;
  onSave: (updates: Array<{ objectiveId: string; status: string }>) => void;
  onEvidenceChanged?: () => void;
}

function fromRow(row: StatementRow): StatementItem {
  return {
    objective_id: row.objective_id,
    control_state_id: row.control_state_id,
    objective_key: row.objective_key,
    objective_text: row.objective_text,
    official_guidance: row.official_guidance,
    official_guidance_source: row.official_guidance_source,
    practitioner_notes: row.practitioner_notes,
    practitioner_notes_generated_at: row.practitioner_notes_generated_at,
    practitioner_notes_model: row.practitioner_notes_model,
    practitioner_notes_edited_at: row.practitioner_notes_edited_at,
    practitioner_notes_edited_by: row.practitioner_notes_edited_by,
    body: row.body,
    status: row.status ?? "draft",
    id: row.id,
    control_discussion: row.control_discussion,
    responsibility: row.responsibility,
  };
}

export function ControlDrawer({
  orgId,
  assessmentId,
  controlDbId,
  controlId,
  controlTitle,
  canWrite,
  currentUserRole,
  onClose,
  onSave,
  onEvidenceChanged,
}: Props) {
  const [items, setItems] = useState<StatementItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [openGuidance, setOpenGuidance] = useState<Record<string, boolean>>({});
  const [showDiscussion, setShowDiscussion] = useState(false);
  const canEditNotes = canEditPractitionerNotes(currentUserRole);
  // Practitioner-notes edit/revert state, keyed by objective_id -- separate
  // from the implementation-statement body/status state above (fieldset
  // further down): these are two unrelated forms living in the same
  // objective row, gated by two different permission axes (canWrite vs
  // canEditNotes).
  const [editingNotesId, setEditingNotesId] = useState<string | null>(null);
  const [notesDraft, setNotesDraft] = useState("");
  const [savingNotes, setSavingNotes] = useState(false);
  const [notesError, setNotesError] = useState<string | null>(null);
  const [confirmRevertId, setConfirmRevertId] = useState<string | null>(null);
  const [evidenceCounts, setEvidenceCounts] = useState<Record<string, number>>({});
  const [evidenceDirty, setEvidenceDirty] = useState(false);
  // Fetched once per drawer open (the whole assessment's worth), not once
  // per objective -- RaciSection below only reads its own slice via
  // control_state_id. Same endpoints RolesPanel.tsx uses (G.7 Part 2: "one
  // API, two presentations"), so an assign/remove here shows up there too.
  const [raciAssignments, setRaciAssignments] = useState<RaciAssignmentRow[]>([]);
  const [contacts, setContacts] = useState<Contact[]>([]);

  useEffect(() => {
    setLoading(true);
    setError(null);
    setSaved(false);
    api
      .getStatements(orgId, assessmentId, controlDbId)
      .then((rows) => setItems(rows.map(fromRow)))
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
    api.getRaciAssignments(orgId, assessmentId).then(setRaciAssignments).catch(() => {});
    api.getContacts(orgId).then(setContacts).catch(() => {});
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

  function handleEvidenceCountChange(objectiveId: string, count: number) {
    setEvidenceCounts((prev) => ({ ...prev, [objectiveId]: count }));
    setEvidenceDirty(true);
  }

  function handleRaciAssigned(row: RaciAssignmentRow) {
    setRaciAssignments((prev) => [...prev, row]);
  }

  function handleRaciRemoved(raciId: string) {
    setRaciAssignments((prev) => prev.filter((a) => a.id !== raciId));
  }

  function startEditingNotes(item: StatementItem) {
    setNotesError(null);
    setEditingNotesId(item.objective_id);
    setNotesDraft(item.practitioner_notes ?? "");
  }

  function cancelEditingNotes() {
    setEditingNotesId(null);
    setNotesDraft("");
    setNotesError(null);
  }

  function applyNotesUpdate(objectiveId: string, update: {
    practitioner_notes: string | null;
    practitioner_notes_generated_at: string | null;
    practitioner_notes_model: string | null;
    practitioner_notes_edited_at: string | null;
    practitioner_notes_edited_by: ResolvedIdentity | null;
  }) {
    setItems((prev) =>
      prev.map((it) => (it.objective_id === objectiveId ? { ...it, ...update } : it))
    );
  }

  async function handleSaveNotes(objectiveId: string) {
    setSavingNotes(true);
    setNotesError(null);
    try {
      const result = await api.editPractitionerNotes(objectiveId, notesDraft);
      applyNotesUpdate(objectiveId, result);
      setEditingNotesId(null);
      setNotesDraft("");
    } catch (e: unknown) {
      setNotesError((e as Error).message);
    } finally {
      setSavingNotes(false);
    }
  }

  async function handleRevertNotes(objectiveId: string) {
    setSavingNotes(true);
    setNotesError(null);
    try {
      const result = await api.revertPractitionerNotes(objectiveId);
      applyNotesUpdate(objectiveId, result);
      setConfirmRevertId(null);
    } catch (e: unknown) {
      setNotesError((e as Error).message);
    } finally {
      setSavingNotes(false);
    }
  }

  function handleClose() {
    if (evidenceDirty && onEvidenceChanged) onEvidenceChanged();
    onClose();
  }

  const hasAnyBody = items.some((it) => it.body.trim() !== "");
  const hasEmptyItems = items.some((it) => it.body.trim() === "");

  return (
    <div className="drawer-overlay" onClick={handleClose}>
      <aside className="drawer" onClick={(e) => e.stopPropagation()}>
        <div className="drawer-header">
          <div>
            <div className="drawer-control-id">{controlId}</div>
            <div className="drawer-control-title">{controlTitle}</div>
          </div>
          <button className="drawer-close" onClick={handleClose} aria-label="Close">
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
                  {/* Always available, not conditioned on content existing --
                      every objective should offer this affordance; the panel
                      itself says so when official text isn't available for
                      this specific objective rather than hiding the button. */}
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
                </div>
                {openGuidance[item.objective_id] && (
                  <div className="drawer-guidance-panel">
                    <div className="drawer-guidance-section drawer-guidance-official">
                      <div className="drawer-guidance-section-label">
                        Official CMMC Assessment Guide Text
                      </div>
                      {item.official_guidance ? (
                        <>
                          <div className="drawer-guidance-text">{item.official_guidance}</div>
                          {item.official_guidance_source && (
                            <div className="drawer-guidance-source">
                              Source: {item.official_guidance_source}
                            </div>
                          )}
                        </>
                      ) : (
                        <div className="drawer-guidance-empty">
                          The Assessment Guide does not provide official text specific to this
                          objective.
                        </div>
                      )}
                    </div>

                    <div className="drawer-guidance-section drawer-guidance-practitioner">
                      <div className="drawer-guidance-section-label">MSP Practitioner Notes</div>
                      <div className="drawer-ai-caveat">
                        <strong>AI-generated guidance — not official CMMC content.</strong>{" "}
                        Written by an AI model to summarize what assessors typically look for.
                        It may contain errors, omissions, or outdated interpretations. Verify
                        against the CMMC Assessment Guide and your C3PAO before relying on it.
                      </div>
                      {editingNotesId === item.objective_id ? (
                        <div className="drawer-notes-edit">
                          <textarea
                            className="drawer-textarea drawer-textarea-sm"
                            value={notesDraft}
                            onChange={(e) => setNotesDraft(e.target.value)}
                            rows={6}
                          />
                          {notesError && (
                            <div className="error-msg" style={{ padding: "0.25rem 0" }}>
                              {notesError}
                            </div>
                          )}
                          <div className="drawer-notes-edit-actions">
                            <button
                              className="btn-primary btn-sm"
                              onClick={() => handleSaveNotes(item.objective_id)}
                              disabled={savingNotes || notesDraft.trim() === ""}
                            >
                              {savingNotes ? "Saving…" : "Save"}
                            </button>
                            <button
                              className="btn-ghost btn-sm"
                              onClick={cancelEditingNotes}
                              disabled={savingNotes}
                            >
                              Cancel
                            </button>
                          </div>
                        </div>
                      ) : item.practitioner_notes ? (
                        <>
                          <div className="drawer-guidance-text">{item.practitioner_notes}</div>
                          <div className="drawer-guidance-meta">
                            {/* Provenance, not status (migration 0032) -- an
                                edit adds "edited by X on Y" alongside the AI
                                origin, it never replaces it. Nothing here
                                ever reads "reviewed" -- that would imply the
                                note is now authoritative, which it isn't. */}
                            {item.practitioner_notes_edited_at && item.practitioner_notes_edited_by ? (
                              <span className="drawer-guidance-meta-item">
                                AI-generated, edited by{" "}
                                {editorLabel(item.practitioner_notes_edited_by)} on{" "}
                                {formatDate(item.practitioner_notes_edited_at)} — not official CMMC
                                guidance.
                              </span>
                            ) : (
                              <span className="drawer-guidance-meta-item">
                                AI-generated — not official CMMC guidance.
                                {item.practitioner_notes_generated_at &&
                                  ` Generated ${formatDate(item.practitioner_notes_generated_at)}${
                                    item.practitioner_notes_model
                                      ? ` by ${item.practitioner_notes_model}`
                                      : ""
                                  }.`}
                              </span>
                            )}
                          </div>
                          {canEditNotes && (
                            <div className="drawer-notes-actions">
                              <button
                                className="btn-ghost btn-xs"
                                onClick={() => startEditingNotes(item)}
                              >
                                Edit
                              </button>
                              {item.practitioner_notes_edited_at &&
                                (confirmRevertId === item.objective_id ? (
                                  <span className="delete-confirm">
                                    <span>Restore AI original?</span>
                                    <button
                                      className="btn-danger btn-xs"
                                      onClick={() => handleRevertNotes(item.objective_id)}
                                      disabled={savingNotes}
                                    >
                                      {savingNotes ? "Reverting…" : "Yes, revert"}
                                    </button>
                                    <button
                                      className="btn-ghost btn-xs"
                                      onClick={() => setConfirmRevertId(null)}
                                    >
                                      Cancel
                                    </button>
                                  </span>
                                ) : (
                                  <button
                                    className="btn-ghost btn-xs btn-destructive"
                                    onClick={() => setConfirmRevertId(item.objective_id)}
                                  >
                                    Revert to AI original
                                  </button>
                                ))}
                            </div>
                          )}
                        </>
                      ) : (
                        <div className="drawer-guidance-empty">
                          No practitioner notes yet for this objective.
                          {canEditNotes && (
                            <button
                              className="btn-ghost btn-xs"
                              style={{ marginLeft: "0.5rem" }}
                              onClick={() => startEditingNotes(item)}
                            >
                              Add notes
                            </button>
                          )}
                        </div>
                      )}
                    </div>
                  </div>
                )}
                <fieldset className="fieldset-reset" disabled={!canWrite}>
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
                </fieldset>
                {item.control_state_id && (
                  <>
                    {/* Ownership before proof: RACI above Evidence in this
                        drawer, per G.7 Part 2's explicit placement call. */}
                    <RaciSection
                      orgId={orgId}
                      assessmentId={assessmentId}
                      controlStateId={item.control_state_id}
                      canWrite={canWrite}
                      assignments={raciAssignments.filter(
                        (a) => a.control_state_id === item.control_state_id
                      )}
                      contacts={contacts}
                      responsibility={item.responsibility}
                      onAssigned={handleRaciAssigned}
                      onRemoved={handleRaciRemoved}
                    />
                    <EvidenceSection
                      orgId={orgId}
                      assessmentId={assessmentId}
                      controlStateId={item.control_state_id}
                      canWrite={canWrite}
                      onCountChange={(count) => handleEvidenceCountChange(item.objective_id, count)}
                    />
                  </>
                )}
              </div>
            ))}

            {canWrite && (
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
            )}

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
