import { useEffect, useState } from "react";
import { api } from "../api";
import type { Contact, ScopeEntity } from "../types";

const ASSET_TYPES = [
  { value: "device", label: "Device" },
  { value: "software", label: "Software" },
];

const SCOPE_CATEGORIES = [
  "CUI Asset",
  "SPA",
  "CRMA",
  "Specialized Asset",
  "ESP",
  "CSP",
  "Out of Scope",
  "Unclassified",
];

interface Props {
  orgId: string;
  asset: ScopeEntity | null;
  canWrite: boolean;
  onClose: () => void;
  onSaved: (a: ScopeEntity) => void;
  onDeleted?: (id: string) => void;
}

export function AssetDrawer({ orgId, asset, canWrite, onClose, onSaved, onDeleted }: Props) {
  const isNew = asset === null;

  const [entityType, setEntityType] = useState("device");
  const [naturalKey, setNaturalKey] = useState("");
  const [scopeCategory, setScopeCategory] = useState("");
  const [status, setStatus] = useState("active");
  const [makeOem, setMakeOem] = useState("");
  const [model, setModel] = useState("");
  const [version, setVersion] = useState("");
  const [responsibleContactId, setResponsibleContactId] = useState("");
  const [contacts, setContacts] = useState<Contact[]>([]);

  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.getContacts(orgId).then(setContacts).catch(() => {});
  }, [orgId]);

  useEffect(() => {
    if (asset) {
      setEntityType(asset.entity_type);
      setNaturalKey(asset.natural_key);
      setScopeCategory(asset.scope_category ?? "");
      setStatus(asset.status);
      setMakeOem((asset.attributes.make_oem as string | null) ?? "");
      setModel((asset.attributes.model as string | null) ?? "");
      setVersion((asset.attributes.version as string | null) ?? "");
      setResponsibleContactId((asset.attributes.responsible_contact_id as string | null) ?? "");
    } else {
      setEntityType("device");
      setNaturalKey("");
      setScopeCategory("");
      setStatus("active");
      setMakeOem("");
      setModel("");
      setVersion("");
      setResponsibleContactId("");
    }
    setError(null);
    setConfirmDelete(false);
  }, [asset]);

  async function handleSave() {
    if (!naturalKey.trim()) {
      setError("Name is required");
      return;
    }
    setSaving(true);
    setError(null);
    // Always send all four known keys, using null for blanks — a PATCH
    // shallow-merges `attributes` (see routers/scope.py's ScopeEntityPatch
    // docstring), so a key that's simply omitted here could never be
    // cleared through this form once set.
    const attributes: Record<string, unknown> = {
      make_oem: makeOem.trim() || null,
      model: model.trim() || null,
      version: version.trim() || null,
      responsible_contact_id: responsibleContactId || null,
    };
    try {
      let saved: ScopeEntity;
      if (isNew) {
        saved = await api.createScopeEntity(orgId, {
          entity_type: entityType,
          natural_key: naturalKey.trim(),
          scope_category: scopeCategory || null,
          status,
          attributes,
        });
      } else {
        saved = await api.patchScopeEntity(orgId, asset!.id, {
          scope_category: scopeCategory || null,
          status,
          attributes,
        });
      }
      onSaved(saved);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Save failed");
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete() {
    if (!asset) return;
    setDeleting(true);
    setError(null);
    try {
      await api.deleteScopeEntity(orgId, asset.id);
      onDeleted?.(asset.id);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Delete failed");
      setDeleting(false);
    }
  }

  return (
    <div className="drawer-overlay" onClick={onClose}>
      <div className="drawer" onClick={(e) => e.stopPropagation()}>
        <div className="drawer-header">
          <h3>{isNew ? "Add Asset" : "Edit Asset"}</h3>
          <button className="drawer-close" onClick={onClose} aria-label="Close">×</button>
        </div>
        <div className="drawer-body">
          {error && <div className="form-error">{error}</div>}

          <fieldset className="fieldset-reset" disabled={!canWrite}>
            <div className="form-grid">
              <div className="form-field">
                <label>Type <span className="required">*</span></label>
                <select value={entityType} onChange={(e) => setEntityType(e.target.value)} disabled={!isNew}>
                  {ASSET_TYPES.map((t) => (
                    <option key={t.value} value={t.value}>{t.label}</option>
                  ))}
                </select>
              </div>
              <div className="form-field">
                <label>Status</label>
                <select value={status} onChange={(e) => setStatus(e.target.value)}>
                  <option value="active">Active</option>
                  <option value="decommissioned">Decommissioned</option>
                </select>
              </div>
            </div>

            <div className="form-field">
              <label>Name <span className="required">*</span></label>
              <input
                type="text"
                value={naturalKey}
                onChange={(e) => setNaturalKey(e.target.value)}
                disabled={!isNew}
                placeholder="Asset tag, serial number, or product name"
              />
              {!isNew && (
                <div className="field-hint">
                  Name can't be changed after creation — delete and re-add to rename.
                </div>
              )}
            </div>

            <div className="form-field">
              <label>Scope Category</label>
              <select value={scopeCategory} onChange={(e) => setScopeCategory(e.target.value)}>
                <option value="">— Unset —</option>
                {SCOPE_CATEGORIES.map((c) => (
                  <option key={c} value={c}>{c}</option>
                ))}
              </select>
            </div>

            <div className="form-section-heading">Component Details</div>
            <div className="field-hint" style={{ marginBottom: "0.5rem" }}>
              Make/OEM, model, and version — the NIST CUI SSP template's component-inventory
              field list (docs/pdf_ssp_template_spec.md).
            </div>
            <div className="form-grid">
              <div className="form-field">
                <label>Make / OEM</label>
                <input type="text" value={makeOem} onChange={(e) => setMakeOem(e.target.value)} />
              </div>
              <div className="form-field">
                <label>Model</label>
                <input type="text" value={model} onChange={(e) => setModel(e.target.value)} />
              </div>
            </div>
            <div className="form-field">
              <label>Version</label>
              <input type="text" value={version} onChange={(e) => setVersion(e.target.value)} />
            </div>
            <div className="form-field">
              <label>Responsible Contact</label>
              <select
                value={responsibleContactId}
                onChange={(e) => setResponsibleContactId(e.target.value)}
              >
                <option value="">— Unassigned —</option>
                {contacts.map((c) => (
                  <option key={c.id} value={c.id}>{c.name}</option>
                ))}
              </select>
            </div>
          </fieldset>
        </div>
        <div className="drawer-footer">
          {canWrite && !isNew && (
            confirmDelete ? (
              <div className="delete-confirm">
                <span>Delete this asset?</span>
                <button className="btn-danger btn-sm" onClick={handleDelete} disabled={deleting}>
                  {deleting ? "Deleting…" : "Yes, delete"}
                </button>
                <button className="btn-ghost btn-sm" onClick={() => setConfirmDelete(false)}>Cancel</button>
              </div>
            ) : (
              <button className="btn-ghost btn-sm btn-destructive" onClick={() => setConfirmDelete(true)}>
                Delete
              </button>
            )
          )}
          <div style={{ flex: 1 }} />
          <button className="btn-ghost" onClick={onClose}>Cancel</button>
          {canWrite && (
            <button className="btn-primary" onClick={handleSave} disabled={saving}>
              {saving ? "Saving…" : "Save"}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
