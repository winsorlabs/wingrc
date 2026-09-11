import { useEffect, useState } from "react";
import { api } from "../api";
import type { IntegrationConnector } from "../types";

// D.1: credential entry + test-connection only. No sync history / "Sync
// now" here on purpose -- there's nothing to sync until D.2 builds the
// actual data pull. Structured so a second connector (Datto RMM, etc.,
// see ROADMAP.md item D's priority order) is just another row from
// api.listIntegrations() -- nothing here is Liongard-specific.

const _ACRONYMS = new Set(["id", "url"]);

function fieldLabel(field: string): string {
  return field
    .split("_")
    .map((word) => (_ACRONYMS.has(word) ? word.toUpperCase() : word[0].toUpperCase() + word.slice(1)))
    .join(" ");
}

function formatDateTime(iso: string): string {
  return new Date(iso).toLocaleString();
}

interface Props {
  canWrite: boolean;
}

export function IntegrationsPanel({ canWrite }: Props) {
  const [connectors, setConnectors] = useState<IntegrationConnector[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [testingKey, setTestingKey] = useState<string | null>(null);
  const [confirmRemoveKey, setConfirmRemoveKey] = useState<string | null>(null);
  const [removing, setRemoving] = useState(false);

  const [editingKey, setEditingKey] = useState<string | null>(null);
  const [formValues, setFormValues] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  useEffect(() => {
    load();
  }, []);

  function load() {
    setLoading(true);
    setError(null);
    api
      .listIntegrations()
      .then(setConnectors)
      .catch(() => setError("Could not load integrations"))
      .finally(() => setLoading(false));
  }

  function openEdit(c: IntegrationConnector) {
    const initial: Record<string, string> = {};
    for (const f of c.config_fields) initial[f] = c.config[f] ?? "";
    for (const f of c.credential_fields) initial[f] = "";
    setFormValues(initial);
    setSaveError(null);
    setEditingKey(c.connector_key);
  }

  function closeEdit() {
    setEditingKey(null);
    setFormValues({});
    setSaveError(null);
  }

  async function handleSave(c: IntegrationConnector) {
    setSaving(true);
    setSaveError(null);
    try {
      const config: Record<string, string> = {};
      for (const f of c.config_fields) config[f] = (formValues[f] ?? "").trim();
      const credential: Record<string, string> = {};
      for (const f of c.credential_fields) credential[f] = (formValues[f] ?? "").trim();

      const updated = await api.setIntegrationCredential(c.connector_key, { config, credential });
      setConnectors((prev) => prev.map((row) => (row.connector_key === c.connector_key ? updated : row)));
      closeEdit();
    } catch (e) {
      setSaveError(e instanceof Error ? e.message : "Could not save credential");
    } finally {
      setSaving(false);
    }
  }

  async function handleTest(connectorKey: string) {
    setTestingKey(connectorKey);
    try {
      const updated = await api.testIntegrationConnection(connectorKey);
      setConnectors((prev) => prev.map((row) => (row.connector_key === connectorKey ? updated : row)));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not run test connection");
    } finally {
      setTestingKey(null);
    }
  }

  async function handleRemove(connectorKey: string) {
    setRemoving(true);
    try {
      await api.deleteIntegrationCredential(connectorKey);
      setConfirmRemoveKey(null);
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not remove credential");
    } finally {
      setRemoving(false);
    }
  }

  function statusBadge(c: IntegrationConnector) {
    if (!c.configured) {
      return <span className="status-badge status-inactive">Never connected</span>;
    }
    if (c.last_test_ok === true) {
      return <span className="status-badge status-active">Connected</span>;
    }
    if (c.last_test_ok === false) {
      return <span className="status-badge status-warning">Last attempt failed</span>;
    }
    return <span className="status-badge status-inactive">Not yet tested</span>;
  }

  if (loading) return <div className="loading">Loading integrations…</div>;

  const editing = connectors.find((c) => c.connector_key === editingKey) ?? null;

  return (
    <div className="integrations-panel">
      {/* Credentials are deployment-wide (one Liongard account per MSP
          instance) but a sync is per-org: configuring a credential here
          does not by itself pull anything into any org's scope graph.
          Each org still needs its own Liongard Environment mapped, from
          that org's Scope → Assets screen ("Sync from Liongard") --
          LiongardSyncWizard.tsx. Stated here because this screen and that
          one are no longer adjacent in the nav (this moved to the
          deployment-tier Administration area; the mapping/sync stays
          org-scoped on purpose — see AdminArea.tsx's own comment). */}
      <div className="field-hint" style={{ marginBottom: "1rem" }}>
        Configuring a credential here doesn't sync anything by itself — each org still needs
        its Liongard Environment mapped from that org's Scope → Assets screen ("Sync from
        Liongard").
      </div>

      {error && <div className="form-error">{error}</div>}

      <div className="integrations-list">
        {connectors.map((c) => (
          <div key={c.connector_key} className="integration-card">
            <div className="integration-card-header">
              <div className="integration-card-title">
                <strong>{c.name}</strong>
                {statusBadge(c)}
              </div>
              {canWrite && (
                <div className="integration-card-actions">
                  <button className="btn-ghost btn-sm" onClick={() => openEdit(c)}>
                    {c.configured ? "Edit credential" : "Configure"}
                  </button>
                  {c.configured && (
                    <button
                      className="btn-ghost btn-sm"
                      onClick={() => handleTest(c.connector_key)}
                      disabled={testingKey === c.connector_key}
                    >
                      {testingKey === c.connector_key ? "Testing…" : "Test connection"}
                    </button>
                  )}
                  {c.configured &&
                    (confirmRemoveKey === c.connector_key ? (
                      <span className="delete-confirm">
                        <span>Remove?</span>
                        <button
                          className="btn-danger btn-xs"
                          onClick={() => handleRemove(c.connector_key)}
                          disabled={removing}
                        >
                          {removing ? "Removing…" : "Yes, remove"}
                        </button>
                        <button className="btn-ghost btn-xs" onClick={() => setConfirmRemoveKey(null)}>
                          Cancel
                        </button>
                      </span>
                    ) : (
                      <button
                        className="btn-ghost btn-sm btn-destructive"
                        onClick={() => setConfirmRemoveKey(c.connector_key)}
                      >
                        Remove
                      </button>
                    ))}
                </div>
              )}
            </div>

            {c.configured && (
              <div className="integration-card-detail">
                {Object.entries(c.config).map(([k, v]) => (
                  <span key={k} className="integration-detail-item">
                    {fieldLabel(k)}: {v}
                  </span>
                ))}
                {c.credential_hint && (
                  <span className="integration-detail-item">Key ending in ····{c.credential_hint}</span>
                )}
              </div>
            )}

            {c.last_tested_at && (
              <div className="integration-card-detail">
                <span className="integration-detail-item">
                  Last tested {formatDateTime(c.last_tested_at)}
                </span>
              </div>
            )}
            {c.last_test_ok === false && c.last_test_error && (
              <div className="raci-drawer-error integration-test-error">{c.last_test_error}</div>
            )}
          </div>
        ))}
      </div>

      {editing && (
        <div className="drawer-overlay" onClick={closeEdit}>
          <div className="drawer" onClick={(e) => e.stopPropagation()}>
            <div className="drawer-header">
              <h3>{editing.name} credential</h3>
              <button className="drawer-close" onClick={closeEdit} aria-label="Close">×</button>
            </div>
            <div className="drawer-body">
              <div className="field-hint">{editing.help_text}</div>
              {saveError && <div className="form-error">{saveError}</div>}
              {editing.config_fields.map((f) => (
                <div className="form-field" key={f}>
                  <label>{fieldLabel(f)} <span className="required">*</span></label>
                  <input
                    type="text"
                    value={formValues[f] ?? ""}
                    onChange={(e) => setFormValues((prev) => ({ ...prev, [f]: e.target.value }))}
                  />
                </div>
              ))}
              {editing.credential_fields.map((f) => (
                <div className="form-field" key={f}>
                  <label>{fieldLabel(f)} <span className="required">*</span></label>
                  <input
                    type="password"
                    autoComplete="off"
                    placeholder={editing.configured ? "Leave filled in to replace" : undefined}
                    value={formValues[f] ?? ""}
                    onChange={(e) => setFormValues((prev) => ({ ...prev, [f]: e.target.value }))}
                  />
                </div>
              ))}
              <div className="field-hint">
                Credentials are encrypted at rest and never shown again after saving — only a
                masked hint is displayed.
              </div>
            </div>
            <div className="drawer-footer">
              <div style={{ flex: 1 }} />
              <button className="btn-ghost" onClick={closeEdit}>Cancel</button>
              <button className="btn-primary" onClick={() => handleSave(editing)} disabled={saving}>
                {saving ? "Saving…" : "Save"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
