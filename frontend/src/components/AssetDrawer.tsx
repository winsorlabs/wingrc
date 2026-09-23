import { useEffect, useState } from "react";
import { api } from "../api";
import type { AssetApproval, Contact, ScopeEntity } from "../types";

function formatDateTime(iso: string): string {
  return new Date(iso).toLocaleString();
}

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

// Mirrors backend/app/domain.py's DeviceSubtype -- keep in sync if that
// vocabulary changes. Controlled vocabulary (not free text) because three
// writers populate this field: this form, workbook import, and the future
// Liongard connector (roadmap D.2).
const DEVICE_SUBTYPES = [
  { value: "desktop", label: "Desktop" },
  { value: "laptop", label: "Laptop" },
  { value: "server", label: "Server" },
  { value: "printer", label: "Printer" },
  { value: "scanner", label: "Scanner" },
  { value: "multifunction_device", label: "Multifunction Device" },
  { value: "desk_phone", label: "Desk Phone" },
  { value: "mobile_phone", label: "Mobile Phone" },
  { value: "tablet", label: "Tablet" },
  { value: "tv_display", label: "TV / Display" },
  { value: "presentation_device", label: "Presentation Device" },
  { value: "network_device", label: "Network Device" },
  { value: "storage_device", label: "Storage Device" },
  { value: "other", label: "Other" },
];

// Mirrors backend/app/domain.py's SPECIALIZED_ASSET_SUGGESTED_SUBTYPES.
// Suggestion only -- never auto-sets scope_category (see the "candidates,
// never auto-met" rule: scoping decisions stay with the engineer).
const SPECIALIZED_ASSET_SUBTYPES = new Set([
  "tv_display",
  "presentation_device",
  "desk_phone",
  "printer",
  "scanner",
  "multifunction_device",
]);

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
  const [displayName, setDisplayName] = useState("");
  const [scopeCategory, setScopeCategory] = useState("");
  const [status, setStatus] = useState("active");
  const [makeOem, setMakeOem] = useState("");
  const [model, setModel] = useState("");
  const [version, setVersion] = useState("");
  const [responsibleContactId, setResponsibleContactId] = useState("");
  const [deviceSubtype, setDeviceSubtype] = useState("");
  const [deviceSubtypeOther, setDeviceSubtypeOther] = useState("");
  const [assetTag, setAssetTag] = useState("");
  const [macAddresses, setMacAddresses] = useState<string[]>([]);
  const [contacts, setContacts] = useState<Contact[]>([]);

  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Acceptance record (2026-09-23) -- read-only, display only. null while
  // loading/not applicable (a brand-new asset has no id to fetch), [] once
  // loaded with no rows (manual entry, workbook import -- a normal case,
  // rendered as nothing, not an empty state).
  const [approvals, setApprovals] = useState<AssetApproval[] | null>(null);
  const [showApprovalHistory, setShowApprovalHistory] = useState(false);
  const [showChecklist, setShowChecklist] = useState<Set<string>>(new Set());

  useEffect(() => {
    setShowApprovalHistory(false);
    setShowChecklist(new Set());
    if (!asset) {
      setApprovals(null);
      return;
    }
    api.getScopeEntityApprovals(orgId, asset.id).then(setApprovals).catch(() => setApprovals([]));
  }, [orgId, asset]);

  function toggleChecklist(approvalId: string) {
    setShowChecklist((prev) => {
      const next = new Set(prev);
      if (next.has(approvalId)) next.delete(approvalId);
      else next.add(approvalId);
      return next;
    });
  }

  useEffect(() => {
    api.getContacts(orgId).then(setContacts).catch(() => {});
  }, [orgId]);

  useEffect(() => {
    if (asset) {
      setEntityType(asset.entity_type);
      setNaturalKey(asset.natural_key);
      setDisplayName((asset.attributes.display_name as string | null) ?? "");
      setScopeCategory(asset.scope_category ?? "");
      setStatus(asset.status);
      setMakeOem((asset.attributes.make_oem as string | null) ?? "");
      setModel((asset.attributes.model as string | null) ?? "");
      setVersion((asset.attributes.version as string | null) ?? "");
      setResponsibleContactId((asset.attributes.responsible_contact_id as string | null) ?? "");
      setDeviceSubtype((asset.attributes.device_subtype as string | null) ?? "");
      setDeviceSubtypeOther((asset.attributes.device_subtype_other as string | null) ?? "");
      setAssetTag((asset.attributes.asset_tag as string | null) ?? "");
      setMacAddresses((asset.attributes.mac_addresses as string[] | null) ?? []);
    } else {
      setEntityType("device");
      setNaturalKey("");
      setDisplayName("");
      setScopeCategory("");
      setStatus("active");
      setMakeOem("");
      setModel("");
      setVersion("");
      setResponsibleContactId("");
      setDeviceSubtype("");
      setDeviceSubtypeOther("");
      setAssetTag("");
      setMacAddresses([]);
    }
    setError(null);
    setConfirmDelete(false);
  }, [asset]);

  async function handleSave() {
    if (!naturalKey.trim()) {
      setError("Natural key is required");
      return;
    }
    setSaving(true);
    setError(null);
    // Always send all known keys, using null for blanks — a PATCH
    // shallow-merges `attributes` (see routers/scope.py's ScopeEntityPatch
    // docstring), so a key that's simply omitted here could never be
    // cleared through this form once set. display_name is user-settable
    // here (unlike last_login_user, informational-only — see its own
    // display below) so a manually-entered or workbook-imported asset can
    // get one too, not just Liongard-synced devices.
    const attributes: Record<string, unknown> = {
      display_name: displayName.trim() || null,
      make_oem: makeOem.trim() || null,
      model: model.trim() || null,
      version: version.trim() || null,
      responsible_contact_id: responsibleContactId || null,
      device_subtype: deviceSubtype || null,
      device_subtype_other: deviceSubtype === "other" ? deviceSubtypeOther.trim() || null : null,
      asset_tag: assetTag.trim() || null,
      mac_addresses: macAddresses.filter((m) => m.trim()).length
        ? macAddresses.filter((m) => m.trim())
        : null,
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

  function updateMac(index: number, value: string) {
    setMacAddresses((prev) => prev.map((m, i) => (i === index ? value : m)));
  }

  function removeMac(index: number) {
    setMacAddresses((prev) => prev.filter((_, i) => i !== index));
  }

  function renderApprovalEntry(a: AssetApproval) {
    const confirmedCount = a.checklist.filter((c) => c.confirmed).length;
    return (
      <div key={a.id}>
        <span
          className={`status-badge ${a.decision === "approved" ? "status-active" : "status-warning"}`}
        >
          {a.decision === "approved" ? "Approved" : "Rejected"}
        </span>{" "}
        by {a.decided_by_name} on {formatDateTime(a.decided_at)}
        {a.decision === "rejected" && a.rejection_reason && (
          <div className="field-hint">{a.rejection_reason}</div>
        )}
        {a.checklist.length > 0 && (
          <div>
            <button
              type="button"
              className="btn-ghost btn-xs"
              onClick={() => toggleChecklist(a.id)}
            >
              {showChecklist.has(a.id) ? "Hide" : "Show"} checklist ({confirmedCount}/
              {a.checklist.length} confirmed)
            </button>
            {showChecklist.has(a.id) && (
              <ul className="field-hint">
                {a.checklist.map((c) => (
                  <li key={c.product_key}>
                    {c.confirmed ? "✓" : "—"} {c.product_name}
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </div>
    );
  }

  const showSpecializedAssetHint =
    entityType === "device" &&
    SPECIALIZED_ASSET_SUBTYPES.has(deviceSubtype) &&
    scopeCategory !== "Specialized Asset";

  return (
    <div className="drawer-overlay" onClick={onClose}>
      <div className="drawer" onClick={(e) => e.stopPropagation()}>
        <div className="drawer-header">
          <h3>{isNew ? "Add Asset" : "Edit Asset"}</h3>
          <button className="drawer-close" onClick={onClose} aria-label="Close">×</button>
        </div>
        <div className="drawer-body">
          {error && <div className="form-error">{error}</div>}

          {approvals && approvals.length > 0 && (
            <div className="form-field">
              <label>Approval</label>
              {renderApprovalEntry(approvals[0])}
              {approvals.length > 1 && (
                <button
                  type="button"
                  className="btn-ghost btn-xs"
                  onClick={() => setShowApprovalHistory((v) => !v)}
                >
                  {showApprovalHistory ? "Hide" : "View"} full history ({approvals.length})
                </button>
              )}
              {showApprovalHistory && (
                <div
                  style={{
                    display: "flex", flexDirection: "column", gap: "0.5rem",
                    borderTop: "1px solid var(--border)", paddingTop: "0.5rem", marginTop: "0.25rem",
                  }}
                >
                  {approvals.slice(1).map((a) => renderApprovalEntry(a))}
                </div>
              )}
            </div>
          )}

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
              <label>Display Name</label>
              <input
                type="text"
                value={displayName}
                onChange={(e) => setDisplayName(e.target.value)}
                placeholder="Optional — shown in lists instead of the natural key below"
              />
              <div className="field-hint">
                What people actually call this asset. Falls back to the natural key when unset —
                a Liongard-synced device sets this automatically (Device Alias, then Hostname).
              </div>
            </div>

            <div className="form-field">
              <label>Natural Key <span className="required">*</span></label>
              <input
                type="text"
                value={naturalKey}
                onChange={(e) => setNaturalKey(e.target.value)}
                disabled={!isNew}
                placeholder="Asset tag, serial number, or product name"
              />
              <div className="field-hint">
                What this asset is matched against on re-import or re-sync — never renamed by a
                display-name change.{" "}
                {!isNew && "Can't be changed after creation — delete and re-add to change it."}
              </div>
            </div>

            <div className="form-field">
              <label>Scope Category</label>
              <select value={scopeCategory} onChange={(e) => setScopeCategory(e.target.value)}>
                <option value="">— Unset —</option>
                {SCOPE_CATEGORIES.map((c) => (
                  <option key={c} value={c}>{c}</option>
                ))}
              </select>
              {showSpecializedAssetHint && (
                <div className="field-hint">
                  Devices of this subtype are commonly categorized as a CMMC Specialized
                  Asset (IoT/OT, GFE, restricted systems, test equipment) — a suggestion
                  only, this stays whatever you set above.{" "}
                  <button
                    type="button"
                    className="btn-ghost btn-sm"
                    onClick={() => setScopeCategory("Specialized Asset")}
                  >
                    Use Specialized Asset
                  </button>
                </div>
              )}
            </div>

            {entityType === "device" && (
              <>
                <div className="form-grid">
                  <div className="form-field">
                    <label>Device Subtype</label>
                    <select value={deviceSubtype} onChange={(e) => setDeviceSubtype(e.target.value)}>
                      <option value="">— Unset —</option>
                      {DEVICE_SUBTYPES.map((s) => (
                        <option key={s.value} value={s.value}>{s.label}</option>
                      ))}
                    </select>
                  </div>
                  <div className="form-field">
                    <label>Asset Tag</label>
                    <input
                      type="text"
                      value={assetTag}
                      onChange={(e) => setAssetTag(e.target.value)}
                      placeholder="Physical sticker ID"
                    />
                  </div>
                </div>
                {deviceSubtype === "other" && (
                  <div className="form-field">
                    <label>Subtype (describe)</label>
                    <input
                      type="text"
                      value={deviceSubtypeOther}
                      onChange={(e) => setDeviceSubtypeOther(e.target.value)}
                    />
                  </div>
                )}
                {asset?.attributes.last_login_user != null && (
                  <div className="form-field">
                    <label>Last Login User</label>
                    <div>{asset.attributes.last_login_user as string}</div>
                    <div className="field-hint">
                      Last observed login from Liongard, as of the most recent sync — telemetry,
                      not an ownership assignment. Never used to set Responsible Contact above;
                      set that manually if this device has an assigned owner.
                    </div>
                  </div>
                )}
                <div className="form-field">
                  <label>MAC Addresses</label>
                  {macAddresses.map((mac, i) => (
                    <div key={i} className="form-grid" style={{ marginBottom: "0.25rem" }}>
                      <input
                        type="text"
                        value={mac}
                        onChange={(e) => updateMac(i, e.target.value)}
                        placeholder="aa:bb:cc:dd:ee:ff"
                      />
                      <button
                        type="button"
                        className="btn-ghost btn-sm btn-destructive"
                        onClick={() => removeMac(i)}
                      >
                        Remove
                      </button>
                    </div>
                  ))}
                  <button
                    type="button"
                    className="btn-ghost btn-sm"
                    onClick={() => setMacAddresses((prev) => [...prev, ""])}
                  >
                    + Add MAC
                  </button>
                  <div className="field-hint">
                    One per NIC (wifi, ethernet, dock/USB adapter). Accepts
                    colon-, hyphen-, or unseparated hex — normalized on save.
                  </div>
                </div>
              </>
            )}

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
