import { useEffect, useState } from "react";
import { api } from "../api";
import type {
  LiongardContactSelection,
  LiongardIdentityCandidate,
  LiongardImportResultItem,
} from "../types";

// Liongard identities -> contacts import (selection-based, never a bulk
// create-per-identity). Mirrors LiongardSyncWizard's overall shape (load ->
// review -> confirm), but the "review" step here is per-row selection with
// admin-entered affiliation, not a reconcile diff -- there is nothing to
// dry-run, since nothing is proposed until the admin actually picks rows.
// See backend/app/routers/contacts.py's module docstring for the full
// design (why affiliation can't be guessed, why refresh is opt-in per
// field, why this never touches scope_entity).

const AFFILIATIONS = ["msp", "customer", "mssp", "government", "other"];

interface Props {
  orgId: string;
  onClose: () => void;
  onImported: () => void;
}

export function ContactImportWizard({ orgId, onClose, onImported }: Props) {
  const [candidates, setCandidates] = useState<LiongardIdentityCandidate[] | null>(null);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [sourceRef, setSourceRef] = useState<string>("");
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [rowName, setRowName] = useState<Record<number, string>>({});
  const [rowAffiliation, setRowAffiliation] = useState<Record<number, string>>({});
  const [rowRoleTitle, setRowRoleTitle] = useState<Record<number, string>>({});
  const [rowRefresh, setRowRefresh] = useState<Record<number, Set<"name" | "phone">>>({});

  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [results, setResults] = useState<LiongardImportResultItem[] | null>(null);

  useEffect(() => {
    setLoading(true);
    api
      .listLiongardContactCandidates(orgId)
      .then((r) => {
        setCandidates(r.candidates);
        setWarnings(r.warnings);
        setSourceRef(r.source_ref);
        const names: Record<number, string> = {};
        r.candidates.forEach((c, idx) => (names[idx] = c.name));
        setRowName(names);
      })
      .catch((e) => setLoadError(e instanceof Error ? e.message : "Could not load Liongard identities"))
      .finally(() => setLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [orgId]);

  function toggleSelected(idx: number) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(idx)) next.delete(idx);
      else next.add(idx);
      return next;
    });
  }

  function toggleRefresh(idx: number, field: "name" | "phone") {
    setRowRefresh((prev) => {
      const next = { ...prev };
      const set = new Set(next[idx] ?? []);
      if (set.has(field)) set.delete(field);
      else set.add(field);
      next[idx] = set;
      return next;
    });
  }

  function rowReady(idx: number): boolean {
    const c = candidates?.[idx];
    if (!c || !c.has_email) return false;
    if (c.existing_contact) return true;
    return !!rowAffiliation[idx];
  }

  const selectedIndices = [...selected].filter((idx) => candidates?.[idx]?.has_email);
  const canSubmit = selectedIndices.length > 0 && selectedIndices.every(rowReady);

  async function handleImport() {
    if (!candidates) return;
    setSubmitting(true);
    setSubmitError(null);
    try {
      const selections: LiongardContactSelection[] = selectedIndices.map((idx) => {
        const c = candidates[idx];
        if (c.existing_contact) {
          return {
            email: c.email!,
            name: rowName[idx] ?? c.name,
            phone: c.phone,
            contact_id: c.existing_contact.id,
            refresh_fields: [...(rowRefresh[idx] ?? [])],
          };
        }
        return {
          email: c.email!,
          name: rowName[idx] ?? c.name,
          phone: c.phone,
          role_title: rowRoleTitle[idx]?.trim() || null,
          affiliation: rowAffiliation[idx],
        };
      });
      const result = await api.importLiongardContacts(orgId, sourceRef, selections);
      setResults(result.results);
      onImported();
    } catch (e) {
      setSubmitError(e instanceof Error ? e.message : "Import failed");
    } finally {
      setSubmitting(false);
    }
  }

  function renderBody() {
    if (loading) return <div className="loading">Loading Liongard identities…</div>;

    if (loadError) {
      return (
        <div className="form-error">
          {loadError}
          {loadError.toLowerCase().includes("mapped") && (
            <div className="field-hint" style={{ marginTop: "0.5rem" }}>
              Map this org to a Liongard Environment from the Scope tab first.
            </div>
          )}
        </div>
      );
    }

    if (results !== null) {
      const counts = results.reduce<Record<string, number>>((acc, r) => {
        acc[r.outcome] = (acc[r.outcome] ?? 0) + 1;
        return acc;
      }, {});
      return (
        <div className="wizard-complete-msg">
          <div>
            {counts.created ?? 0} created, {counts.refreshed ?? 0} refreshed,{" "}
            {counts.unchanged ?? 0} unchanged, {counts.skipped ?? 0} skipped.
          </div>
          <table className="contacts-table" style={{ marginTop: "0.75rem" }}>
            <thead>
              <tr>
                <th>Email</th>
                <th>Outcome</th>
                <th>Detail</th>
              </tr>
            </thead>
            <tbody>
              {results.map((r, i) => (
                <tr key={i}>
                  <td>{r.email}</td>
                  <td>{r.outcome}</td>
                  <td>{r.detail ?? ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
    }

    const warningsBlock = warnings.length > 0 && (
      <div className="form-error">
        {warnings.map((w, i) => (
          <div key={i}>⚠ {w}</div>
        ))}
      </div>
    );

    if (!candidates || candidates.length === 0) {
      return (
        <>
          {warningsBlock}
          <div className="field-hint">
            No identities were returned from this org's mapped Liongard Environment.
          </div>
        </>
      );
    }

    return (
      <>
        <div className="field-hint">
          An org can have hundreds of identities; only pick the people who need to appear in
          RACI, documentation roles, or the CRM. Nothing is created until you confirm below.
        </div>
        {warningsBlock}
        {submitError && <div className="form-error">{submitError}</div>}
        <div className="table-scroll">
          <table className="contacts-table import-diff-table">
            <thead>
              <tr>
                <th></th>
                <th>Name</th>
                <th>Email</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {candidates.map((c, idx) => (
                <tr key={c.liongard_id ?? idx}>
                  <td>
                    {c.has_email ? (
                      <input
                        type="checkbox"
                        checked={selected.has(idx)}
                        onChange={() => toggleSelected(idx)}
                      />
                    ) : (
                      <input type="checkbox" disabled title="No email — can't become a contact" />
                    )}
                  </td>
                  <td colSpan={3}>
                    {!c.has_email ? (
                      <div>
                        <strong>{c.name}</strong>{" "}
                        <span className="field-hint">— no email on this identity, can't be imported</span>
                      </div>
                    ) : c.existing_contact ? (
                      <div>
                        <div>
                          <strong>{c.name}</strong> — {c.email}{" "}
                          <span className="affiliation-badge">already a contact</span>
                        </div>
                        {selected.has(idx) && (
                          <div className="form-grid" style={{ marginTop: "0.4rem" }}>
                            <label>
                              <input
                                type="checkbox"
                                checked={(rowRefresh[idx] ?? new Set()).has("name")}
                                onChange={() => toggleRefresh(idx, "name")}
                              />{" "}
                              Update name to{" "}
                              <input
                                type="text"
                                value={rowName[idx] ?? c.name}
                                onChange={(e) =>
                                  setRowName((prev) => ({ ...prev, [idx]: e.target.value }))
                                }
                                style={{ width: "10rem" }}
                              />
                            </label>
                            <label>
                              <input
                                type="checkbox"
                                checked={(rowRefresh[idx] ?? new Set()).has("phone")}
                                onChange={() => toggleRefresh(idx, "phone")}
                              />{" "}
                              Update phone to {c.phone ?? "(none)"}
                            </label>
                          </div>
                        )}
                        <div className="field-hint">
                          Current: {c.existing_contact.name}
                          {c.existing_contact.phone ? `, ${c.existing_contact.phone}` : ""} (
                          {c.existing_contact.affiliation})
                        </div>
                      </div>
                    ) : (
                      <div>
                        <div>
                          <strong>{c.name}</strong> — {c.email}
                        </div>
                        {selected.has(idx) && (
                          <div className="form-grid" style={{ marginTop: "0.4rem" }}>
                            <div className="form-field">
                              <label>Name</label>
                              <input
                                type="text"
                                value={rowName[idx] ?? c.name}
                                onChange={(e) =>
                                  setRowName((prev) => ({ ...prev, [idx]: e.target.value }))
                                }
                              />
                            </div>
                            <div className="form-field">
                              <label>Affiliation <span className="required">*</span></label>
                              <select
                                value={rowAffiliation[idx] ?? ""}
                                onChange={(e) =>
                                  setRowAffiliation((prev) => ({ ...prev, [idx]: e.target.value }))
                                }
                              >
                                <option value="">Choose…</option>
                                {AFFILIATIONS.map((a) => (
                                  <option key={a} value={a}>
                                    {a}
                                  </option>
                                ))}
                              </select>
                            </div>
                            <div className="form-field">
                              <label>Role / Title</label>
                              <input
                                type="text"
                                value={rowRoleTitle[idx] ?? ""}
                                onChange={(e) =>
                                  setRowRoleTitle((prev) => ({ ...prev, [idx]: e.target.value }))
                                }
                                placeholder="e.g. IT Director (not provided by Liongard)"
                              />
                            </div>
                          </div>
                        )}
                        {selected.has(idx) && !rowAffiliation[idx] && (
                          <div className="form-error">Affiliation is required to import this contact.</div>
                        )}
                      </div>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </>
    );
  }

  function renderFooter() {
    if (loading || loadError) {
      return <button className="btn-ghost" onClick={onClose}>Close</button>;
    }
    if (results !== null) {
      return <button className="btn-primary" onClick={onClose}>Done</button>;
    }
    return (
      <>
        <button className="btn-ghost" onClick={onClose}>Cancel</button>
        <button className="btn-primary" onClick={handleImport} disabled={!canSubmit || submitting}>
          {submitting
            ? "Importing…"
            : `Import ${selectedIndices.length} Selected`}
        </button>
      </>
    );
  }

  return (
    <div className="wizard-overlay" onClick={onClose}>
      <div className="wizard" onClick={(e) => e.stopPropagation()}>
        <div className="wizard-header">
          <span className="wizard-title">Import Contacts from Liongard</span>
          <button className="wizard-close" onClick={onClose} aria-label="Close">×</button>
        </div>
        <div className="wizard-body">{renderBody()}</div>
        <div className="wizard-footer">
          <div style={{ flex: 1 }} />
          {renderFooter()}
        </div>
      </div>
    </div>
  );
}
