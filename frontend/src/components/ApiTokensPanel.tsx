import { useEffect, useState } from "react";
import { api } from "../api";
import { ALL_ROLES, ROLE_LABELS, ROLE_RANK } from "../lib/roles";
import type { ApiTokenRow, CreatedApiToken } from "../types";

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString();
}

interface Props {
  orgId: string;
  currentUserRole: string;
}

export function ApiTokensPanel({ orgId, currentUserRole }: Props) {
  const [tokens, setTokens] = useState<ApiTokenRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [showCreate, setShowCreate] = useState(false);
  const [name, setName] = useState("");
  const [role, setRole] = useState("customer_poc");
  const [expiresInDays, setExpiresInDays] = useState("");
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);
  const [newToken, setNewToken] = useState<CreatedApiToken | null>(null);
  const [copied, setCopied] = useState(false);

  const [confirmRevokeId, setConfirmRevokeId] = useState<string | null>(null);
  const [revoking, setRevoking] = useState(false);

  const availableRoles = ALL_ROLES.filter(
    (r) => (ROLE_RANK[r] ?? 0) <= (ROLE_RANK[currentUserRole] ?? 0)
  );

  useEffect(() => {
    load();
  }, [orgId]);

  function load() {
    setLoading(true);
    setError(null);
    api
      .listApiTokens(orgId)
      .then(setTokens)
      .catch(() => setError("Could not load API tokens"))
      .finally(() => setLoading(false));
  }

  function openCreate() {
    setName("");
    setRole(availableRoles.includes("customer_poc") ? "customer_poc" : availableRoles[0] ?? "");
    setExpiresInDays("");
    setCreateError(null);
    setNewToken(null);
    setCopied(false);
    setShowCreate(true);
  }

  function closeCreate() {
    setShowCreate(false);
    setNewToken(null);
    if (newToken) load(); // pick up the token just created
  }

  function handleDismiss() {
    // Once a token has been minted, force the explicit "Done" button rather
    // than letting a stray overlay/×-click silently dismiss a secret that
    // will never be shown again.
    if (newToken) return;
    closeCreate();
  }

  async function handleCreate() {
    if (!name.trim()) {
      setCreateError("Name is required");
      return;
    }
    setCreating(true);
    setCreateError(null);
    try {
      const days = expiresInDays.trim() ? Number(expiresInDays) : null;
      const created = await api.createApiToken(orgId, {
        name: name.trim(),
        role,
        expires_in_days: days,
      });
      setNewToken(created);
    } catch (e) {
      setCreateError(e instanceof Error ? e.message : "Could not create token");
    } finally {
      setCreating(false);
    }
  }

  async function handleCopy() {
    if (!newToken) return;
    try {
      await navigator.clipboard.writeText(newToken.token);
      setCopied(true);
    } catch {
      // Clipboard API unavailable (non-secure context, permissions, etc.) —
      // the raw value is still selectable/visible in the panel, so this is
      // a soft failure, not a blocker.
      setCopied(false);
    }
  }

  async function handleRevoke(tokenId: string) {
    setRevoking(true);
    try {
      await api.revokeApiToken(orgId, tokenId);
      setTokens((prev) => prev.filter((t) => t.id !== tokenId));
      setConfirmRevokeId(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not revoke token");
    } finally {
      setRevoking(false);
    }
  }

  if (loading) return <div className="loading">Loading API tokens…</div>;

  return (
    <div className="api-tokens-panel">
      <div className="api-tokens-panel-header">
        <button className="btn-primary btn-sm" onClick={openCreate}>
          + Create Token
        </button>
      </div>

      {error && <div className="form-error">{error}</div>}

      {tokens.length === 0 ? (
        <div className="contacts-empty">
          No API tokens yet. Create one to authenticate scripts or integrations
          against this org's API.
        </div>
      ) : (
        <table className="contacts-table api-tokens-table">
          <thead>
            <tr>
              <th>Name</th>
              <th>Role</th>
              <th>Created</th>
              <th>Expires</th>
              <th>Last Used</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {tokens.map((t) => (
              <tr key={t.id}>
                <td>{t.name}</td>
                <td><span className="role-badge">{ROLE_LABELS[t.role] ?? t.role}</span></td>
                <td>{formatDate(t.created_at)}</td>
                <td>{t.expires_at ? formatDate(t.expires_at) : <span className="no-roles">Never</span>}</td>
                <td>{t.last_used_at ? formatDate(t.last_used_at) : <span className="no-roles">Never</span>}</td>
                <td>
                  {confirmRevokeId === t.id ? (
                    <span className="delete-confirm">
                      <span>Revoke?</span>
                      <button
                        className="btn-danger btn-xs"
                        onClick={() => handleRevoke(t.id)}
                        disabled={revoking}
                      >
                        {revoking ? "Revoking…" : "Yes, revoke"}
                      </button>
                      <button className="btn-ghost btn-xs" onClick={() => setConfirmRevokeId(null)}>
                        Cancel
                      </button>
                    </span>
                  ) : (
                    <button
                      className="btn-ghost btn-sm btn-destructive"
                      onClick={() => setConfirmRevokeId(t.id)}
                    >
                      Revoke
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {showCreate && (
        <div className="drawer-overlay" onClick={handleDismiss}>
          <div className="drawer" onClick={(e) => e.stopPropagation()}>
            <div className="drawer-header">
              <h3>{newToken ? "Token Created" : "Create API Token"}</h3>
              <button className="drawer-close" onClick={handleDismiss} aria-label="Close">×</button>
            </div>

            {newToken ? (
              <>
                <div className="drawer-body">
                  <div className="token-warning">
                    This is the only time this token will be shown. Copy it now and
                    store it somewhere safe — it cannot be retrieved again.
                  </div>
                  <div className="form-field">
                    <label>Token</label>
                    <code className="token-value">{newToken.token}</code>
                  </div>
                  <button className="btn-ghost btn-sm" onClick={handleCopy}>
                    {copied ? "Copied!" : "Copy to clipboard"}
                  </button>
                </div>
                <div className="drawer-footer">
                  <div style={{ flex: 1 }} />
                  <button className="btn-primary" onClick={closeCreate}>Done</button>
                </div>
              </>
            ) : (
              <>
                <div className="drawer-body">
                  {createError && <div className="form-error">{createError}</div>}
                  <div className="form-field">
                    <label>Name <span className="required">*</span></label>
                    <input
                      type="text"
                      value={name}
                      onChange={(e) => setName(e.target.value)}
                      placeholder="e.g. CI integration"
                    />
                  </div>
                  <div className="form-grid">
                    <div className="form-field">
                      <label>Role</label>
                      <select value={role} onChange={(e) => setRole(e.target.value)}>
                        {availableRoles.map((r) => (
                          <option key={r} value={r}>{ROLE_LABELS[r] ?? r}</option>
                        ))}
                      </select>
                    </div>
                    <div className="form-field">
                      <label>Expires (days)</label>
                      <input
                        type="number"
                        min={1}
                        value={expiresInDays}
                        onChange={(e) => setExpiresInDays(e.target.value)}
                        placeholder="Never"
                      />
                    </div>
                  </div>
                  <div className="field-hint">
                    Role is limited to your own rank or below — the API rejects anything higher.
                  </div>
                </div>
                <div className="drawer-footer">
                  <div style={{ flex: 1 }} />
                  <button className="btn-ghost" onClick={closeCreate}>Cancel</button>
                  <button className="btn-primary" onClick={handleCreate} disabled={creating}>
                    {creating ? "Creating…" : "Create"}
                  </button>
                </div>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
