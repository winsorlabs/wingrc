import { useEffect, useState } from "react";
import { api } from "../api";
import { FAMILY_NAMES, FAMILY_ORDER } from "../lib/families";
import { RACI_LETTERS as LETTERS, RACI_LETTER_LABELS as LETTER_LABELS, sortedForSuggestion, suggestedAffiliation } from "../lib/raci";
import type { Affiliation } from "../lib/raci";
import type { Contact, ControlStateRow, RaciAssignmentRow } from "../types";

interface Props {
  orgId: string;
  assessmentId: string;
  canWrite: boolean;
}

function dominantAffiliation(rows: ControlStateRow[]): Affiliation {
  let msp = 0;
  let customer = 0;
  for (const row of rows) {
    if (suggestedAffiliation(row.responsibility) === "msp") msp++;
    else customer++;
  }
  return msp >= customer ? "msp" : "customer";
}

interface BulkFormState {
  raci_letter: string;
  contact_id: string;
}

export function RolesPanel({ orgId, assessmentId, canWrite }: Props) {
  const [rows, setRows] = useState<ControlStateRow[]>([]);
  const [assignments, setAssignments] = useState<RaciAssignmentRow[]>([]);
  const [contacts, setContacts] = useState<Contact[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [openFamily, setOpenFamily] = useState<string | null>(null);
  const [bulkForms, setBulkForms] = useState<Record<string, BulkFormState>>({});
  const [overrideForms, setOverrideForms] = useState<Record<string, BulkFormState>>({});

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [orgId, assessmentId]);

  function load() {
    setLoading(true);
    setError(null);
    Promise.all([
      api.getControlStates(orgId, assessmentId),
      api.getRaciAssignments(orgId, assessmentId),
      api.getContacts(orgId),
    ])
      .then(([r, a, c]) => {
        setRows(r);
        setAssignments(a);
        setContacts(c);
        setLoading(false);
      })
      .catch(() => {
        setError("Could not load roles data");
        setLoading(false);
      });
  }

  if (loading) return <div className="loading">Loading roles…</div>;
  if (error) return <div className="form-error">{error}</div>;

  const byFamily: Record<string, ControlStateRow[]> = {};
  for (const row of rows) (byFamily[row.family] ??= []).push(row);
  const families = FAMILY_ORDER.filter((f) => byFamily[f]?.length);

  const assignmentsByControlState: Record<string, RaciAssignmentRow[]> = {};
  for (const a of assignments) (assignmentsByControlState[a.control_state_id] ??= []).push(a);

  function bulkForm(family: string): BulkFormState {
    return bulkForms[family] ?? { raci_letter: "R", contact_id: "" };
  }

  function setBulkField(family: string, field: keyof BulkFormState, value: string) {
    setBulkForms((prev) => ({ ...prev, [family]: { ...bulkForm(family), [field]: value } }));
  }

  async function handleBulkAssign(family: string) {
    const form = bulkForm(family);
    if (!form.contact_id) {
      setError("Choose a contact before assigning");
      return;
    }
    setError(null);
    setNotice(null);
    try {
      const result = await api.bulkAssignRaci(orgId, assessmentId, {
        family, contact_id: form.contact_id, raci_letter: form.raci_letter,
      });
      const contactName = contacts.find((c) => c.id === form.contact_id)?.name ?? "Contact";
      setNotice(
        result.skipped > 0
          ? `${contactName} assigned as ${form.raci_letter} on ${result.assigned} objective(s) in ${family}` +
            ` — ${result.skipped} already had an assignment for that role and were left as-is.`
          : `${contactName} assigned as ${form.raci_letter} on all ${result.assigned} objective(s) in ${family}.`
      );
      const fresh = await api.getRaciAssignments(orgId, assessmentId);
      setAssignments(fresh);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Bulk assign failed");
    }
  }

  function overrideForm(controlStateId: string): BulkFormState {
    return overrideForms[controlStateId] ?? { raci_letter: "R", contact_id: "" };
  }

  function setOverrideField(controlStateId: string, field: keyof BulkFormState, value: string) {
    setOverrideForms((prev) => ({
      ...prev,
      [controlStateId]: { ...overrideForm(controlStateId), [field]: value },
    }));
  }

  async function handleAddOverride(controlStateId: string) {
    const form = overrideForm(controlStateId);
    if (!form.contact_id) {
      setError("Choose a contact before assigning");
      return;
    }
    setError(null);
    setNotice(null);
    try {
      const created = await api.createRaciAssignment(orgId, assessmentId, {
        control_state_id: controlStateId, contact_id: form.contact_id, raci_letter: form.raci_letter,
      });
      setAssignments((prev) => [...prev, created]);
      setOverrideForms((prev) => ({ ...prev, [controlStateId]: { raci_letter: "R", contact_id: "" } }));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Assign failed");
    }
  }

  async function handleRemove(raciId: string) {
    setError(null);
    setNotice(null);
    try {
      await api.deleteRaciAssignment(orgId, assessmentId, raciId);
      setAssignments((prev) => prev.filter((a) => a.id !== raciId));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Remove failed");
    }
  }

  return (
    <div className="roles-panel">
      <h2>Roles (RACI)</h2>
      <p className="field-hint">
        Assign a contact to every objective in a family at once, or override individual objectives
        below. A family-level assign only fills objectives that don't already have an assignment for
        that role — existing overrides are never touched.
      </p>

      {error && <div className="form-error">{error}</div>}
      {notice && <div className="form-success">{notice}</div>}

      {families.map((family) => {
        const familyRows = byFamily[family];
        const suggestion = dominantAffiliation(familyRows);
        const sortedContacts = sortedForSuggestion(contacts, suggestion);
        const form = bulkForm(family);
        const open = openFamily === family;

        return (
          <div className="family-section" key={family}>
            <div className="family-header" onClick={() => setOpenFamily(open ? null : family)}>
              <span className={`chevron ${open ? "open" : ""}`}>▶</span>
              <span className="family-key">{family}</span>
              <span className="family-label">{FAMILY_NAMES[family] ?? ""}</span>
              <span className="family-stats">
                <span>{familyRows.length} objective(s)</span>
                <span>· suggested: {suggestion === "msp" ? "MSP contact" : "Customer contact"}</span>
              </span>
            </div>

            {open && (
              <div className="family-body">
                {canWrite && (
                  <div className="raci-bulk-form" onClick={(e) => e.stopPropagation()}>
                    <select
                      value={form.raci_letter}
                      onChange={(e) => setBulkField(family, "raci_letter", e.target.value)}
                    >
                      {LETTERS.map((l) => (
                        <option key={l} value={l}>{l} — {LETTER_LABELS[l]}</option>
                      ))}
                    </select>
                    <select
                      value={form.contact_id}
                      onChange={(e) => setBulkField(family, "contact_id", e.target.value)}
                    >
                      <option value="">Choose contact…</option>
                      {sortedContacts.map((c) => (
                        <option key={c.id} value={c.id}>
                          {c.name} ({c.affiliation}){c.affiliation === suggestion ? " — suggested" : ""}
                        </option>
                      ))}
                    </select>
                    <button className="btn-primary btn-sm" onClick={() => handleBulkAssign(family)}>
                      Assign whole family
                    </button>
                  </div>
                )}

                {familyRows.map((row) => {
                  const rowAssignments = assignmentsByControlState[row.id] ?? [];
                  const rowForm = overrideForm(row.id);
                  return (
                    <div className="raci-objective-row" key={row.id}>
                      <div className="raci-objective-label">
                        <strong>{row.control_id}[{row.objective_key}]</strong>
                        <span className="field-hint">{row.objective_text}</span>
                      </div>
                      <div className="role-chip-grid">
                        {rowAssignments.length === 0 && (
                          <span className="field-hint">No assignments</span>
                        )}
                        {rowAssignments.map((a) => (
                          <span className="role-chip" key={a.id}>
                            {a.raci_letter}: {a.contact_name}
                            {canWrite && (
                              <button
                                className="chip-remove"
                                aria-label="Remove"
                                onClick={() => handleRemove(a.id)}
                              >
                                ×
                              </button>
                            )}
                          </span>
                        ))}
                      </div>
                      {canWrite && (
                        <div className="raci-override-form">
                          <select
                            value={rowForm.raci_letter}
                            onChange={(e) => setOverrideField(row.id, "raci_letter", e.target.value)}
                          >
                            {LETTERS.map((l) => (
                              <option key={l} value={l}>{l}</option>
                            ))}
                          </select>
                          <select
                            value={rowForm.contact_id}
                            onChange={(e) => setOverrideField(row.id, "contact_id", e.target.value)}
                          >
                            <option value="">Choose contact…</option>
                            {sortedForSuggestion(contacts, suggestedAffiliation(row.responsibility)).map((c) => (
                              <option key={c.id} value={c.id}>{c.name} ({c.affiliation})</option>
                            ))}
                          </select>
                          <button className="btn-ghost btn-sm" onClick={() => handleAddOverride(row.id)}>
                            + Override
                          </button>
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        );
      })}

      {families.length === 0 && <div className="empty">No control objectives in this assessment yet.</div>}
    </div>
  );
}
