import { useState } from "react";
import { api } from "../api";
import { RACI_LETTERS, RACI_LETTER_LABELS, sortedForSuggestion, suggestedAffiliation } from "../lib/raci";
import type { Contact, RaciAssignmentRow } from "../types";

interface Props {
  orgId: string;
  assessmentId: string;
  controlStateId: string;
  canWrite: boolean;
  // Owned by ControlDrawer (fetched once for the whole drawer, not once per
  // objective) and filtered down to this objective's own rows -- see
  // ControlDrawer's own comment on why, and RolesPanel.tsx for the other
  // presentation of the same underlying data/endpoints (G.7 Part 2: "one
  // API, two presentations").
  assignments: RaciAssignmentRow[];
  contacts: Contact[];
  responsibility: string | null;
  onAssigned: (row: RaciAssignmentRow) => void;
  onRemoved: (raciId: string) => void;
}

export function RaciSection({
  orgId,
  assessmentId,
  controlStateId,
  canWrite,
  assignments,
  contacts,
  responsibility,
  onAssigned,
  onRemoved,
}: Props) {
  const [letter, setLetter] = useState<string>("R");
  const [contactId, setContactId] = useState("");
  const [assigning, setAssigning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const suggestion = responsibility ? suggestedAffiliation(responsibility) : null;
  const sortedContacts = suggestion ? sortedForSuggestion(contacts, suggestion) : contacts;

  async function handleAssign() {
    if (!contactId) {
      setError("Choose a contact before assigning");
      return;
    }
    setAssigning(true);
    setError(null);
    try {
      const created = await api.createRaciAssignment(orgId, assessmentId, {
        control_state_id: controlStateId,
        contact_id: contactId,
        raci_letter: letter,
      });
      onAssigned(created);
      setContactId("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Assign failed");
    } finally {
      setAssigning(false);
    }
  }

  async function handleRemove(raciId: string) {
    setError(null);
    try {
      await api.deleteRaciAssignment(orgId, assessmentId, raciId);
      onRemoved(raciId);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Remove failed");
    }
  }

  return (
    <div className="raci-drawer-section">
      <div className="raci-drawer-section-header">
        <span className="raci-drawer-section-label">RACI</span>
        {assignments.length > 0 && (
          <span
            className="raci-drawer-count-badge"
            aria-label={`${assignments.length} assignment${assignments.length === 1 ? "" : "s"}`}
          >
            {assignments.length}
          </span>
        )}
      </div>

      {assignments.length === 0 && (
        <div className="raci-drawer-status raci-drawer-empty">No assignments</div>
      )}
      {assignments.length > 0 && (
        <div className="role-chip-grid">
          {assignments.map((a) => (
            <span className="role-chip" key={a.id}>
              {a.raci_letter}: {a.contact_name}
              {canWrite && (
                <button className="chip-remove" aria-label="Remove" onClick={() => handleRemove(a.id)}>
                  ×
                </button>
              )}
            </span>
          ))}
        </div>
      )}

      {canWrite && (
        <div className="raci-drawer-footer">
          <select value={letter} onChange={(e) => setLetter(e.target.value)} disabled={assigning}>
            {RACI_LETTERS.map((l) => (
              <option key={l} value={l}>{l} — {RACI_LETTER_LABELS[l]}</option>
            ))}
          </select>
          <select value={contactId} onChange={(e) => setContactId(e.target.value)} disabled={assigning}>
            <option value="">Choose contact…</option>
            {sortedContacts.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name} ({c.affiliation}){suggestion === c.affiliation ? " — suggested" : ""}
              </option>
            ))}
          </select>
          <button className="btn-ghost btn-sm" onClick={handleAssign} disabled={assigning}>
            {assigning ? "Assigning…" : "+ Assign"}
          </button>
        </div>
      )}

      {error && <div className="raci-drawer-error">{error}</div>}
    </div>
  );
}
