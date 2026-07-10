import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { OrgProfile } from "../types";

interface Props {
  orgId: string;
  onSaved?: () => void;
}

export function OrgProfileForm({ orgId, onSaved }: Props) {
  const [profile, setProfile] = useState<OrgProfile | null>(null);
  const [form, setForm] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [logoUploading, setLogoUploading] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    api.getOrgProfile(orgId).then((p) => {
      setProfile(p);
      setForm({
        cage_code: p.cage_code ?? "",
        uei: p.uei ?? "",
        year_established: p.year_established?.toString() ?? "",
        industry: p.industry ?? "",
        address_line1: p.address_line1 ?? "",
        address_line2: p.address_line2 ?? "",
        city: p.city ?? "",
        state_or_province: p.state_or_province ?? "",
        postal_code: p.postal_code ?? "",
        country: p.country ?? "US",
        phone_primary: p.phone_primary ?? "",
        phone_secondary: p.phone_secondary ?? "",
        website: p.website ?? "",
      });
    }).catch(() => setError("Could not load profile"));
  }, [orgId]);

  function set(key: string, val: string) {
    setForm((prev) => ({ ...prev, [key]: val }));
    setSaved(false);
  }

  async function handleSave() {
    setSaving(true);
    setError(null);
    setSaved(false);
    try {
      await api.patchOrgProfile(orgId, {
        cage_code: form.cage_code || null,
        uei: form.uei || null,
        year_established: form.year_established ? parseInt(form.year_established, 10) : null,
        industry: form.industry || null,
        address_line1: form.address_line1 || null,
        address_line2: form.address_line2 || null,
        city: form.city || null,
        state_or_province: form.state_or_province || null,
        postal_code: form.postal_code || null,
        country: form.country || null,
        phone_primary: form.phone_primary || null,
        phone_secondary: form.phone_secondary || null,
        website: form.website || null,
      });
      setSaved(true);
      onSaved?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Save failed");
    } finally {
      setSaving(false);
    }
  }

  async function handleLogoUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    setLogoUploading(true);
    setError(null);
    try {
      const result = await api.uploadLogo(orgId, file);
      setProfile((prev) => prev ? { ...prev, logo_url: result.logo_url, logo_storage_key: result.logo_storage_key } : prev);
    } catch (e) {
      setError("Logo upload failed: " + (e instanceof Error ? e.message : String(e)));
    } finally {
      setLogoUploading(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  }

  if (!profile) return <div className="loading">Loading…</div>;

  return (
    <div className="form-body">
      {error && <div className="form-error">{error}</div>}

      <div className="form-field">
        <label>Organization Name</label>
        <input type="text" value={profile.name} readOnly className="input-readonly" />
        <div className="field-hint">Set at org creation. Contact support to rename.</div>
      </div>

      <div className="form-section-heading">Government Identifiers</div>
      <div className="form-grid">
        <div className="form-field">
          <label>CAGE Code</label>
          <input type="text" value={form.cage_code} onChange={(e) => set("cage_code", e.target.value)} placeholder="5-char alphanumeric" maxLength={10} />
        </div>
        <div className="form-field">
          <label>UEI (SAM.gov)</label>
          <input type="text" value={form.uei} onChange={(e) => set("uei", e.target.value)} placeholder="12-char alphanumeric" maxLength={20} />
        </div>
        <div className="form-field">
          <label>Year Established</label>
          <input type="number" value={form.year_established} onChange={(e) => set("year_established", e.target.value)} placeholder="e.g. 2010" min={1800} max={2099} />
        </div>
        <div className="form-field">
          <label>Industry</label>
          <input type="text" value={form.industry} onChange={(e) => set("industry", e.target.value)} placeholder="e.g. Defense, Manufacturing" />
        </div>
      </div>

      <div className="form-section-heading">Address</div>
      <div className="form-field">
        <label>Address Line 1</label>
        <input type="text" value={form.address_line1} onChange={(e) => set("address_line1", e.target.value)} placeholder="Street address" />
      </div>
      <div className="form-field">
        <label>Address Line 2</label>
        <input type="text" value={form.address_line2} onChange={(e) => set("address_line2", e.target.value)} placeholder="Suite, Unit, Floor…" />
      </div>
      <div className="form-grid">
        <div className="form-field">
          <label>City</label>
          <input type="text" value={form.city} onChange={(e) => set("city", e.target.value)} />
        </div>
        <div className="form-field">
          <label>State / Province</label>
          <input type="text" value={form.state_or_province} onChange={(e) => set("state_or_province", e.target.value)} />
        </div>
        <div className="form-field">
          <label>Postal Code</label>
          <input type="text" value={form.postal_code} onChange={(e) => set("postal_code", e.target.value)} />
        </div>
        <div className="form-field">
          <label>Country</label>
          <input type="text" value={form.country} onChange={(e) => set("country", e.target.value)} />
        </div>
      </div>

      <div className="form-section-heading">Contact</div>
      <div className="form-grid">
        <div className="form-field">
          <label>Primary Phone</label>
          <input type="text" value={form.phone_primary} onChange={(e) => set("phone_primary", e.target.value)} placeholder="e.g. 703-555-0100" />
        </div>
        <div className="form-field">
          <label>Secondary Phone</label>
          <input type="text" value={form.phone_secondary} onChange={(e) => set("phone_secondary", e.target.value)} />
        </div>
      </div>
      <div className="form-field">
        <label>Website</label>
        <input type="text" value={form.website} onChange={(e) => set("website", e.target.value)} placeholder="https://…" />
      </div>

      <div className="form-section-heading">Logo</div>
      <div className="logo-upload-area">
        {profile.logo_url
          ? <img src={profile.logo_url} alt="Org logo" className="logo-preview" />
          : <div className="logo-placeholder">No logo</div>}
        <div>
          <button className="btn-ghost btn-sm" onClick={() => fileRef.current?.click()} disabled={logoUploading}>
            {logoUploading ? "Uploading…" : profile.logo_url ? "Replace" : "Upload logo"}
          </button>
          <div className="field-hint">PNG, JPEG, GIF or WEBP · max 10 MB</div>
        </div>
        <input ref={fileRef} type="file" accept="image/png,image/jpeg,image/gif,image/webp" style={{ display: "none" }} onChange={handleLogoUpload} />
      </div>

      <div className="form-actions">
        <button className="btn-primary" onClick={handleSave} disabled={saving}>
          {saving ? "Saving…" : saved ? "Saved ✓" : "Save Profile"}
        </button>
      </div>
    </div>
  );
}
