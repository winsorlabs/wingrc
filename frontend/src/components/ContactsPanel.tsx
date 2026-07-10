import { useEffect, useState } from "react";
import { api } from "../api";
import type { Contact } from "../types";
import { ContactDrawer } from "./ContactDrawer";

const ROLE_LABELS: Record<string, string> = {
  it_admin: "IT Admin",
  security_officer: "Security Officer",
  system_owner: "System Owner",
  authorizing_official: "Authorizing Official",
  president: "President",
  cui_user: "CUI User",
  assessor: "Assessor",
  mssp: "MSSP",
  consultant: "Consultant",
  other: "Other",
};

interface Props {
  orgId: string;
  onChanged?: () => void;
}

export function ContactsPanel({ orgId, onChanged }: Props) {
  const [contacts, setContacts] = useState<Contact[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [drawerContact, setDrawerContact] = useState<Contact | null | undefined>(undefined);

  useEffect(() => {
    load();
  }, [orgId]);

  function load() {
    setLoading(true);
    api.getContacts(orgId).then((cs) => {
      setContacts(cs);
      setLoading(false);
    }).catch(() => {
      setError("Could not load contacts");
      setLoading(false);
    });
  }

  function handleSaved(c: Contact) {
    setContacts((prev) => {
      const idx = prev.findIndex((p) => p.id === c.id);
      if (idx >= 0) {
        const next = [...prev];
        next[idx] = c;
        return next;
      }
      return [...prev, c].sort((a, b) => a.name.localeCompare(b.name));
    });
    setDrawerContact(undefined);
    onChanged?.();
  }

  function handleDeleted(id: string) {
    setContacts((prev) => prev.filter((c) => c.id !== id));
    setDrawerContact(undefined);
    onChanged?.();
  }

  if (loading) return <div className="loading">Loading contacts…</div>;
  if (error) return <div className="form-error">{error}</div>;

  return (
    <div className="contacts-panel">
      <div className="contacts-panel-header">
        <button className="btn-primary btn-sm" onClick={() => setDrawerContact(null)}>
          + Add Contact
        </button>
      </div>

      {contacts.length === 0 ? (
        <div className="contacts-empty">
          No contacts yet. Add the key personnel who should appear in your SSP and CRM.
        </div>
      ) : (
        <table className="contacts-table">
          <thead>
            <tr>
              <th>Name</th>
              <th>Email</th>
              <th>Affiliation</th>
              <th>Documentation Roles</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {contacts.map((c) => (
              <tr key={c.id}>
                <td>
                  <div className="contact-name">{c.name}</div>
                  {c.role_title && <div className="contact-sub">{c.role_title}</div>}
                </td>
                <td>{c.email}</td>
                <td><span className="affiliation-badge">{c.affiliation}</span></td>
                <td>
                  <div className="role-chips-inline">
                    {c.documentation_roles.length === 0
                      ? <span className="no-roles">—</span>
                      : c.documentation_roles.map((r) => (
                          <span key={r.role} className="role-chip role-chip-sm">
                            {ROLE_LABELS[r.role] ?? r.role}
                          </span>
                        ))
                    }
                  </div>
                </td>
                <td>
                  <button className="btn-ghost btn-sm" onClick={() => setDrawerContact(c)}>Edit</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {drawerContact !== undefined && (
        <ContactDrawer
          orgId={orgId}
          contact={drawerContact}
          onClose={() => setDrawerContact(undefined)}
          onSaved={handleSaved}
          onDeleted={handleDeleted}
        />
      )}
    </div>
  );
}
