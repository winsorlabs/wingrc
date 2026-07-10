import { useEffect, useState } from "react";
import { api } from "../api";
import type { Contact } from "../types";

const AFFILIATIONS = ["msp", "customer", "mssp", "government", "other"];

const DOC_ROLES: { value: string; label: string }[] = [
  { value: "it_admin", label: "IT Admin" },
  { value: "security_officer", label: "Security Officer" },
  { value: "system_owner", label: "System Owner" },
  { value: "authorizing_official", label: "Authorizing Official" },
  { value: "president", label: "President / Owner" },
  { value: "cui_user", label: "CUI User" },
  { value: "assessor", label: "Assessor" },
  { value: "mssp", label: "MSSP" },
  { value: "consultant", label: "Consultant" },
  { value: "other", label: "Other" },
];

interface Props {
  orgId: string;
  contact: Contact | null;
  onClose: () => void;
  onSaved: (c: Contact) => void;
  onDeleted?: (id: string) => void;
}

export function ContactDrawer({ orgId, contact, onClose, onSaved, onDeleted }: Props) {
  const isNew = contact === null;

  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [affiliation, setAffiliation] = useState("customer");
  const [phone, setPhone] = useState("");
  const [roleTitle, setRoleTitle] = useState("");
  const [contractRef, setContractRef] = useState("");
  const [notes, setNotes] = useState("");

  // roles: initRoles = what the contact already has; pendingRoles = current set in UI
  const [initRoles, setInitRoles] = useState<string[]>([]);
  const [pendingRoles, setPendingRoles] = useState<string[]>([]);

  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (contact) {
      setName(contact.name);
      setEmail(contact.email);
      setAffiliation(contact.affiliation);
      setPhone(contact.phone ?? "");
      setRoleTitle(contact.role_title ?? "");
      setContractRef(contact.contract_ref ?? "");
      setNotes(contact.notes ?? "");
      const existing = contact.documentation_roles.map((r) => r.role);
      setInitRoles(existing);
      setPendingRoles(existing);
    } else {
      setName(""); setEmail(""); setAffiliation("customer"); setPhone("");
      setRoleTitle(""); setContractRef(""); setNotes("");
      setInitRoles([]); setPendingRoles([]);
    }
    setError(null);
    setConfirmDelete(false);
  }, [contact]);

  function toggleRole(role: string) {
    setPendingRoles((prev) =>
      prev.includes(role) ? prev.filter((r) => r !== role) : [...prev, role]
    );
  }

  async function handleSave() {
    if (!name.trim()) { setError("Name is required"); return; }
    if (!email.trim()) { setError("Email is required"); return; }
    setSaving(true);
    setError(null);
    try {
      let saved: Contact;
      if (isNew) {
        saved = await api.createContact(orgId, {
          name: name.trim(),
          email: email.trim(),
          affiliation,
          phone: phone || null,
          role_title: roleTitle || null,
          contract_ref: contractRef || null,
          notes: notes || null,
        });
      } else {
        saved = await api.patchContact(orgId, contact!.id, {
          name: name.trim(),
          email: email.trim(),
          affiliation,
          phone: phone || null,
          role_title: roleTitle || null,
          contract_ref: contractRef || null,
          notes: notes || null,
        });
      }

      // Diff roles: add what's in pending but not init; remove what's in init but not pending
      const toAdd = pendingRoles.filter((r) => !initRoles.includes(r));
      const toRemove = initRoles.filter((r) => !pendingRoles.includes(r));

      await Promise.all([
        ...toAdd.map((r) => api.addContactRole(orgId, saved.id, r)),
        ...toRemove.map((r) => api.removeContactRole(orgId, saved.id, r)),
      ]);

      // Reload the contact with fresh roles
      const contacts = await api.getContacts(orgId);
      const fresh = contacts.find((c) => c.id === saved.id) ?? saved;
      onSaved(fresh);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Save failed");
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete() {
    if (!contact) return;
    setDeleting(true);
    setError(null);
    try {
      await api.deleteContact(orgId, contact.id);
      onDeleted?.(contact.id);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Delete failed");
      setDeleting(false);
    }
  }

  return (
    <>
      <div className="drawer-overlay" onClick={onClose} />
      <div className="drawer">
        <div className="drawer-header">
          <h3>{isNew ? "Add Contact" : "Edit Contact"}</h3>
          <button className="drawer-close" onClick={onClose} aria-label="Close">×</button>
        </div>
        <div className="drawer-body">
          {error && <div className="form-error">{error}</div>}

          <div className="form-field">
            <label>Name <span className="required">*</span></label>
            <input type="text" value={name} onChange={(e) => setName(e.target.value)} />
          </div>
          <div className="form-field">
            <label>Email <span className="required">*</span></label>
            <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} />
          </div>
          <div className="form-grid">
            <div className="form-field">
              <label>Affiliation</label>
              <select value={affiliation} onChange={(e) => setAffiliation(e.target.value)}>
                {AFFILIATIONS.map((a) => <option key={a} value={a}>{a}</option>)}
              </select>
            </div>
            <div className="form-field">
              <label>Phone</label>
              <input type="text" value={phone} onChange={(e) => setPhone(e.target.value)} />
            </div>
          </div>
          <div className="form-field">
            <label>Role / Title</label>
            <input type="text" value={roleTitle} onChange={(e) => setRoleTitle(e.target.value)} placeholder="e.g. IT Director" />
          </div>
          <div className="form-field">
            <label>Contract Reference</label>
            <input type="text" value={contractRef} onChange={(e) => setContractRef(e.target.value)} placeholder="Contract or PO number" />
          </div>
          <div className="form-field">
            <label>Notes</label>
            <textarea rows={2} value={notes} onChange={(e) => setNotes(e.target.value)} />
          </div>

          <div className="form-section-heading">Documentation Roles</div>
          <div className="field-hint" style={{ marginBottom: "0.5rem" }}>Select all that apply — a contact may hold multiple roles.</div>
          <div className="role-chip-grid">
            {DOC_ROLES.map(({ value, label }) => (
              <button
                key={value}
                className={`role-chip-toggle${pendingRoles.includes(value) ? " selected" : ""}`}
                onClick={() => toggleRole(value)}
                type="button"
              >
                {label}
              </button>
            ))}
          </div>
        </div>
        <div className="drawer-footer">
          {!isNew && (
            confirmDelete ? (
              <div className="delete-confirm">
                <span>Delete this contact?</span>
                <button className="btn-danger btn-sm" onClick={handleDelete} disabled={deleting}>
                  {deleting ? "Deleting…" : "Yes, delete"}
                </button>
                <button className="btn-ghost btn-sm" onClick={() => setConfirmDelete(false)}>Cancel</button>
              </div>
            ) : (
              <button className="btn-ghost btn-sm btn-destructive" onClick={() => setConfirmDelete(true)}>Delete</button>
            )
          )}
          <div style={{ flex: 1 }} />
          <button className="btn-ghost" onClick={onClose}>Cancel</button>
          <button className="btn-primary" onClick={handleSave} disabled={saving}>
            {saving ? "Saving…" : "Save"}
          </button>
        </div>
      </div>
    </>
  );
}
