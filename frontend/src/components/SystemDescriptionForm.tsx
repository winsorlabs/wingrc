import { useEffect, useState } from "react";
import { api } from "../api";
import type { ExternalConnection, StorageLocation, SystemDescriptionData } from "../types";

interface Props {
  orgId: string;
  onSaved?: () => void;
}

const SYSTEM_TYPES = [
  { value: "major_application", label: "Major Application" },
  { value: "general_support_system", label: "General Support System" },
  { value: "minor_application", label: "Minor Application" },
];

const OP_STATUSES = [
  { value: "operational", label: "Operational" },
  { value: "under_development", label: "Under Development" },
  { value: "undergoing_major_modification", label: "Undergoing Major Modification" },
];

function emptyStorageLoc(): StorageLocation { return { type: "", description: "" }; }
function emptyExtConn(): ExternalConnection { return { name: "", direction: "bidirectional", purpose: "" }; }

export function SystemDescriptionForm({ orgId, onSaved }: Props) {
  const [data, setData] = useState<SystemDescriptionData | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [systemName, setSystemName] = useState("");
  const [systemType, setSystemType] = useState("major_application");
  const [opStatus, setOpStatus] = useState("operational");
  const [description, setDescription] = useState("");
  const [cuiCategories, setCuiCategories] = useState<string[]>([]);
  const [cuiCategoryInput, setCuiCategoryInput] = useState("");
  const [storageLocations, setStorageLocations] = useState<StorageLocation[]>([]);
  const [authBoundary, setAuthBoundary] = useState("");
  const [externalConns, setExternalConns] = useState<ExternalConnection[]>([]);
  const [cuiFlow, setCuiFlow] = useState("");

  useEffect(() => {
    api.getSystemDescription(orgId).then((sd) => {
      if (sd) {
        setData(sd);
        setSystemName(sd.system_name);
        setSystemType(sd.system_type);
        setOpStatus(sd.operational_status);
        setDescription(sd.system_description ?? "");
        setCuiCategories(sd.cui_categories);
        setStorageLocations(sd.cui_storage_locations.length ? sd.cui_storage_locations : []);
        setAuthBoundary(sd.authorization_boundary_description ?? "");
        setExternalConns(sd.external_connections.length ? sd.external_connections : []);
        setCuiFlow(sd.cui_flow_description ?? "");
      }
      setLoaded(true);
    }).catch(() => {
      setError("Could not load system description");
      setLoaded(true);
    });
  }, [orgId]);

  function addCuiCategory() {
    const val = cuiCategoryInput.trim();
    if (val && !cuiCategories.includes(val)) {
      setCuiCategories((prev) => [...prev, val]);
      setSaved(false);
    }
    setCuiCategoryInput("");
  }

  function removeCuiCategory(cat: string) {
    setCuiCategories((prev) => prev.filter((c) => c !== cat));
    setSaved(false);
  }

  function addStorageLoc() {
    setStorageLocations((prev) => [...prev, emptyStorageLoc()]);
    setSaved(false);
  }

  function setStorageLoc(idx: number, field: keyof StorageLocation, val: string) {
    setStorageLocations((prev) => prev.map((l, i) => i === idx ? { ...l, [field]: val } : l));
    setSaved(false);
  }

  function removeStorageLoc(idx: number) {
    setStorageLocations((prev) => prev.filter((_, i) => i !== idx));
    setSaved(false);
  }

  function addExtConn() {
    setExternalConns((prev) => [...prev, emptyExtConn()]);
    setSaved(false);
  }

  function setExtConn(idx: number, field: keyof ExternalConnection, val: string) {
    setExternalConns((prev) => prev.map((c, i) => i === idx ? { ...c, [field]: val } : c));
    setSaved(false);
  }

  function removeExtConn(idx: number) {
    setExternalConns((prev) => prev.filter((_, i) => i !== idx));
    setSaved(false);
  }

  async function handleSave() {
    if (!systemName.trim()) {
      setError("System name is required");
      return;
    }
    setSaving(true);
    setError(null);
    setSaved(false);
    try {
      const saved_data = await api.putSystemDescription(orgId, {
        system_name: systemName.trim(),
        system_type: systemType,
        operational_status: opStatus,
        system_description: description || null,
        cui_categories: cuiCategories,
        cui_storage_locations: storageLocations.filter((l) => l.type),
        authorization_boundary_description: authBoundary || null,
        external_connections: externalConns.filter((c) => c.name),
        cui_flow_description: cuiFlow || null,
      });
      setData(saved_data);
      setSaved(true);
      onSaved?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Save failed");
    } finally {
      setSaving(false);
    }
  }

  if (!loaded) return <div className="loading">Loading…</div>;

  return (
    <div className="form-body">
      {error && <div className="form-error">{error}</div>}
      {!data && <div className="form-info-banner">No system description yet — fill in the fields below and save.</div>}

      <div className="form-grid">
        <div className="form-field" style={{ gridColumn: "1 / -1" }}>
          <label>System Name <span className="required">*</span></label>
          <input type="text" value={systemName} onChange={(e) => { setSystemName(e.target.value); setSaved(false); }} placeholder="e.g. Acme Defense Network" />
        </div>
        <div className="form-field">
          <label>System Type</label>
          <select value={systemType} onChange={(e) => { setSystemType(e.target.value); setSaved(false); }}>
            {SYSTEM_TYPES.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
          </select>
        </div>
        <div className="form-field">
          <label>Operational Status</label>
          <select value={opStatus} onChange={(e) => { setOpStatus(e.target.value); setSaved(false); }}>
            {OP_STATUSES.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}
          </select>
        </div>
      </div>

      <div className="form-field">
        <label>System Description</label>
        <textarea rows={4} value={description} onChange={(e) => { setDescription(e.target.value); setSaved(false); }} placeholder="Describe the system's purpose, function, and users…" />
      </div>

      <div className="form-section-heading">CUI Categories</div>
      <div className="tag-input-area">
        {cuiCategories.map((cat) => (
          <span key={cat} className="role-chip">
            {cat}
            <button className="chip-remove" onClick={() => removeCuiCategory(cat)} aria-label={`Remove ${cat}`}>×</button>
          </span>
        ))}
        <div className="tag-input-row">
          <input
            type="text"
            value={cuiCategoryInput}
            onChange={(e) => setCuiCategoryInput(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); addCuiCategory(); } }}
            placeholder="e.g. Export Controlled, Privacy Act…"
          />
          <button className="btn-ghost btn-sm" onClick={addCuiCategory}>Add</button>
        </div>
      </div>

      <div className="form-section-heading">CUI Storage Locations</div>
      {storageLocations.map((loc, idx) => (
        <div key={idx} className="repeating-row">
          <div className="form-field" style={{ flex: "0 0 180px" }}>
            <label>Type</label>
            <input type="text" value={loc.type} onChange={(e) => setStorageLoc(idx, "type", e.target.value)} placeholder="e.g. File server, Cloud" />
          </div>
          <div className="form-field" style={{ flex: 1 }}>
            <label>Description</label>
            <input type="text" value={loc.description} onChange={(e) => setStorageLoc(idx, "description", e.target.value)} placeholder="Location details" />
          </div>
          <button className="btn-ghost btn-sm repeating-remove" onClick={() => removeStorageLoc(idx)} aria-label="Remove">×</button>
        </div>
      ))}
      <button className="btn-ghost btn-sm" onClick={addStorageLoc}>+ Add storage location</button>

      <div className="form-field" style={{ marginTop: "1.5rem" }}>
        <label>Authorization Boundary Description</label>
        <textarea rows={3} value={authBoundary} onChange={(e) => { setAuthBoundary(e.target.value); setSaved(false); }} placeholder="Describe the authorization boundary…" />
      </div>

      <div className="form-section-heading">External Connections</div>
      {externalConns.map((conn, idx) => (
        <div key={idx} className="repeating-row">
          <div className="form-field" style={{ flex: "0 0 150px" }}>
            <label>Name</label>
            <input type="text" value={conn.name} onChange={(e) => setExtConn(idx, "name", e.target.value)} placeholder="System name" />
          </div>
          <div className="form-field" style={{ flex: "0 0 140px" }}>
            <label>Direction</label>
            <select value={conn.direction} onChange={(e) => setExtConn(idx, "direction", e.target.value)}>
              <option value="inbound">Inbound</option>
              <option value="outbound">Outbound</option>
              <option value="bidirectional">Bidirectional</option>
            </select>
          </div>
          <div className="form-field" style={{ flex: 1 }}>
            <label>Purpose</label>
            <input type="text" value={conn.purpose} onChange={(e) => setExtConn(idx, "purpose", e.target.value)} placeholder="Data exchanged / purpose" />
          </div>
          <button className="btn-ghost btn-sm repeating-remove" onClick={() => removeExtConn(idx)} aria-label="Remove">×</button>
        </div>
      ))}
      <button className="btn-ghost btn-sm" onClick={addExtConn}>+ Add external connection</button>

      <div className="form-field" style={{ marginTop: "1.5rem" }}>
        <label>CUI Flow Description</label>
        <textarea rows={3} value={cuiFlow} onChange={(e) => { setCuiFlow(e.target.value); setSaved(false); }} placeholder="Describe how CUI flows through the system…" />
      </div>

      <div className="form-actions">
        <button className="btn-primary" onClick={handleSave} disabled={saving}>
          {saving ? "Saving…" : saved ? "Saved ✓" : "Save System Description"}
        </button>
      </div>
    </div>
  );
}
