import { useEffect, useState } from "react";
import { api } from "../api";
import { ALL_ROLES, ROLE_LABELS } from "../lib/roles";
import type { InvitedUser, UserRow } from "../types";

// invite_user rejects login_method="api" — that value is reserved for
// service-account users created via POST /users/api, a separate flow not
// covered by this panel. See test_invite_user_rejects_api_login_method.
const LOGIN_METHODS: { value: string; label: string }[] = [
  { value: "local", label: "Local (email + password)" },
  { value: "sso", label: "SSO (Microsoft Entra ID)" },
];

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString();
}

function formatDateTime(iso: string): string {
  return new Date(iso).toLocaleString();
}

// locked_until can be in the past without being cleared — auth.py only
// clears it on the account's next successful login, not on a timer. Compare
// against now() rather than trusting a non-null value to mean "locked".
function isCurrentlyLocked(u: UserRow): boolean {
  return !!u.locked_until && new Date(u.locked_until).getTime() > Date.now();
}

interface Props {
  orgId: string;
  currentUserId: string;
}

export function UsersPanel({ orgId, currentUserId }: Props) {
  const [users, setUsers] = useState<UserRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [showInvite, setShowInvite] = useState(false);
  const [email, setEmail] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [role, setRole] = useState("customer_poc");
  const [loginMethod, setLoginMethod] = useState("local");
  const [inviting, setInviting] = useState(false);
  const [inviteError, setInviteError] = useState<string | null>(null);
  const [invitedResult, setInvitedResult] = useState<InvitedUser | null>(null);
  const [copied, setCopied] = useState(false);

  const [pendingRoleChange, setPendingRoleChange] = useState<
    { userId: string; from: string; to: string } | null
  >(null);
  const [changingRole, setChangingRole] = useState(false);

  const [confirmDeactivateId, setConfirmDeactivateId] = useState<string | null>(null);
  const [deactivating, setDeactivating] = useState(false);

  const [confirmResetMfaId, setConfirmResetMfaId] = useState<string | null>(null);
  const [resettingMfa, setResettingMfa] = useState(false);

  const [unlockingId, setUnlockingId] = useState<string | null>(null);

  const [confirmResetPasswordId, setConfirmResetPasswordId] = useState<string | null>(null);
  const [resettingPassword, setResettingPassword] = useState(false);
  const [resetPasswordResult, setResetPasswordResult] = useState<
    { userId: string; token: string; expiresAt: string } | null
  >(null);
  const [resetPasswordCopied, setResetPasswordCopied] = useState(false);

  useEffect(() => {
    load();
  }, [orgId]);

  function load() {
    setLoading(true);
    setError(null);
    api
      .listUsers(orgId)
      .then(setUsers)
      .catch(() => setError("Could not load users"))
      .finally(() => setLoading(false));
  }

  function openInvite() {
    setEmail("");
    setDisplayName("");
    setRole("customer_poc");
    setLoginMethod("local");
    setInviteError(null);
    setInvitedResult(null);
    setCopied(false);
    setShowInvite(true);
  }

  function closeInvite() {
    setShowInvite(false);
    const hadResult = invitedResult !== null;
    setInvitedResult(null);
    if (hadResult) load(); // pick up the user just invited
  }

  function handleDismissInvite() {
    // Same rule as the API token panel: once the one-time invite token has
    // been minted, only the explicit "Done" button can close the dialog —
    // a stray overlay/×-click must not be able to silently discard it.
    if (invitedResult) return;
    closeInvite();
  }

  async function handleInvite() {
    if (!email.trim()) { setInviteError("Email is required"); return; }
    if (!displayName.trim()) { setInviteError("Display name is required"); return; }
    setInviting(true);
    setInviteError(null);
    try {
      const result = await api.inviteUser(orgId, {
        email: email.trim(),
        display_name: displayName.trim(),
        role,
        login_method: loginMethod,
      });
      setInvitedResult(result);
    } catch (e) {
      setInviteError(e instanceof Error ? e.message : "Could not invite user");
    } finally {
      setInviting(false);
    }
  }

  async function handleCopy() {
    if (!invitedResult) return;
    try {
      await navigator.clipboard.writeText(invitedResult.invite_token);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  }

  function requestRoleChange(u: UserRow, to: string) {
    if (to === u.role) {
      setPendingRoleChange(null);
      return;
    }
    setPendingRoleChange({ userId: u.id, from: u.role, to });
  }

  async function confirmRoleChange() {
    if (!pendingRoleChange) return;
    setChangingRole(true);
    setError(null);
    try {
      const updated = await api.patchUser(orgId, pendingRoleChange.userId, {
        role: pendingRoleChange.to,
      });
      setUsers((prev) => prev.map((u) => (u.id === updated.id ? updated : u)));
      setPendingRoleChange(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not change role");
    } finally {
      setChangingRole(false);
    }
  }

  async function handleDeactivate(userId: string) {
    setDeactivating(true);
    setError(null);
    try {
      await api.deactivateUser(orgId, userId);
      setUsers((prev) =>
        prev.map((u) => (u.id === userId ? { ...u, is_active: false } : u))
      );
      setConfirmDeactivateId(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not deactivate user");
    } finally {
      setDeactivating(false);
    }
  }

  async function handleResetMfa(userId: string) {
    setResettingMfa(true);
    setError(null);
    try {
      await api.resetUserMfa(orgId, userId);
      // reset_user_mfa also deactivates the account server-side until
      // re-enrollment — reflect that here rather than re-fetching.
      setUsers((prev) =>
        prev.map((u) =>
          u.id === userId
            ? { ...u, mfa_enrolled: false, is_active: false, requires_admin_reset: false }
            : u
        )
      );
      setConfirmResetMfaId(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not reset MFA");
    } finally {
      setResettingMfa(false);
    }
  }

  async function handleUnlock(userId: string) {
    setUnlockingId(userId);
    setError(null);
    try {
      await api.unlockUser(orgId, userId);
      setUsers((prev) =>
        prev.map((u) =>
          u.id === userId
            ? { ...u, locked_until: null, lockout_count: 0, requires_admin_reset: false }
            : u
        )
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not unlock user");
    } finally {
      setUnlockingId(null);
    }
  }

  async function handleResetPassword(userId: string) {
    setResettingPassword(true);
    setError(null);
    try {
      const result = await api.resetUserPassword(orgId, userId);
      setResetPasswordResult({ userId, token: result.reset_token, expiresAt: result.expires_at });
      setResetPasswordCopied(false);
      setConfirmResetPasswordId(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not reset password");
    } finally {
      setResettingPassword(false);
    }
  }

  async function handleCopyResetToken() {
    if (!resetPasswordResult) return;
    try {
      await navigator.clipboard.writeText(resetPasswordResult.token);
      setResetPasswordCopied(true);
    } catch {
      setResetPasswordCopied(false);
    }
  }

  if (loading) return <div className="loading">Loading users…</div>;

  return (
    <div className="users-panel">
      <div className="users-panel-header">
        <button className="btn-primary btn-sm" onClick={openInvite}>
          + Invite User
        </button>
      </div>

      {error && <div className="form-error">{error}</div>}

      {users.length === 0 ? (
        <div className="contacts-empty">No users yet. Invite your first team member.</div>
      ) : (
        <table className="contacts-table users-table">
          <thead>
            <tr>
              <th>Name</th>
              <th>Email</th>
              <th>Role</th>
              <th>Login Method</th>
              <th>Active</th>
              <th>MFA</th>
              <th>Status</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {users.map((u) => {
              const isSelf = u.id === currentUserId;
              // Plain && narrowing (not optional chaining into a
              // property-comparison) so TS can actually narrow
              // rowPending to non-null in the branch below, not just
              // read correctly to a human.
              const rowPending =
                pendingRoleChange && pendingRoleChange.userId === u.id ? pendingRoleChange : null;
              const roleValue = rowPending ? rowPending.to : u.role;
              return (
                <tr key={u.id}>
                  <td>
                    <div className="contact-name">{u.display_name}</div>
                    {isSelf && <div className="contact-sub">You</div>}
                  </td>
                  <td>{u.email}</td>
                  <td>
                    <select
                      value={roleValue}
                      onChange={(e) => requestRoleChange(u, e.target.value)}
                    >
                      {ALL_ROLES.map((r) => (
                        <option key={r} value={r}>{ROLE_LABELS[r] ?? r}</option>
                      ))}
                    </select>
                    {rowPending && (
                      <div className="delete-confirm role-change-confirm">
                        <span>
                          Change role: {ROLE_LABELS[rowPending.from] ?? rowPending.from}
                          {" → "}
                          {ROLE_LABELS[rowPending.to] ?? rowPending.to}?
                        </span>
                        <button
                          className="btn-primary btn-xs"
                          onClick={confirmRoleChange}
                          disabled={changingRole}
                        >
                          {changingRole ? "Saving…" : "Confirm"}
                        </button>
                        <button
                          className="btn-ghost btn-xs"
                          onClick={() => setPendingRoleChange(null)}
                          disabled={changingRole}
                        >
                          Cancel
                        </button>
                      </div>
                    )}
                  </td>
                  <td>{LOGIN_METHODS.find((m) => m.value === u.login_method)?.label ?? u.login_method}</td>
                  <td>
                    <span className={`status-badge ${u.is_active ? "status-active" : "status-inactive"}`}>
                      {u.is_active ? "Active" : "Inactive"}
                    </span>
                  </td>
                  <td>
                    {u.mfa_enrolled
                      ? <span className="status-badge status-active">Enrolled</span>
                      : <span className="no-roles">Not enrolled</span>}
                  </td>
                  <td>
                    {u.requires_admin_reset ? (
                      <span className="status-badge status-warning">Requires Admin Reset</span>
                    ) : isCurrentlyLocked(u) ? (
                      <span className="status-badge status-warning">
                        Locked until {formatDateTime(u.locked_until as string)}
                        {u.lockout_count > 0 && ` (lockout #${u.lockout_count})`}
                      </span>
                    ) : (
                      <span className="no-roles">—</span>
                    )}
                  </td>
                  <td>
                    <div className="user-row-actions">
                      {resetPasswordResult?.userId === u.id ? (
                        <div className="reset-password-result">
                          <div className="token-warning">
                            This is the only time this reset token will be shown. Copy
                            it now and deliver it to {u.email} out of band. It expires{" "}
                            {formatDateTime(resetPasswordResult.expiresAt)}.
                          </div>
                          <code className="token-value">{resetPasswordResult.token}</code>
                          <div className="reset-password-result-actions">
                            <button className="btn-ghost btn-xs" onClick={handleCopyResetToken}>
                              {resetPasswordCopied ? "Copied!" : "Copy"}
                            </button>
                            <button
                              className="btn-primary btn-xs"
                              onClick={() => setResetPasswordResult(null)}
                            >
                              Done
                            </button>
                          </div>
                        </div>
                      ) : confirmResetPasswordId === u.id ? (
                        <span className="delete-confirm">
                          <span>Reset password? They'll be signed out immediately.</span>
                          <button
                            className="btn-danger btn-xs"
                            onClick={() => handleResetPassword(u.id)}
                            disabled={resettingPassword}
                          >
                            {resettingPassword ? "Resetting…" : "Yes, reset"}
                          </button>
                          <button
                            className="btn-ghost btn-xs"
                            onClick={() => setConfirmResetPasswordId(null)}
                          >
                            Cancel
                          </button>
                        </span>
                      ) : (
                        <button
                          className="btn-ghost btn-xs"
                          onClick={() => setConfirmResetPasswordId(u.id)}
                        >
                          Reset Password
                        </button>
                      )}
                      {(u.requires_admin_reset || isCurrentlyLocked(u)) && (
                        <button
                          className="btn-ghost btn-xs"
                          onClick={() => handleUnlock(u.id)}
                          disabled={unlockingId === u.id}
                        >
                          {unlockingId === u.id ? "Unlocking…" : "Unlock"}
                        </button>
                      )}
                      {u.mfa_enrolled && (
                        confirmResetMfaId === u.id ? (
                          <span className="delete-confirm">
                            <span>Reset MFA? They'll be deactivated until re-enrolled.</span>
                            <button
                              className="btn-danger btn-xs"
                              onClick={() => handleResetMfa(u.id)}
                              disabled={resettingMfa}
                            >
                              {resettingMfa ? "Resetting…" : "Yes, reset"}
                            </button>
                            <button
                              className="btn-ghost btn-xs"
                              onClick={() => setConfirmResetMfaId(null)}
                            >
                              Cancel
                            </button>
                          </span>
                        ) : (
                          <button
                            className="btn-ghost btn-xs"
                            onClick={() => setConfirmResetMfaId(u.id)}
                          >
                            Reset MFA
                          </button>
                        )
                      )}
                      {u.is_active && !isSelf && (
                        confirmDeactivateId === u.id ? (
                          <span className="delete-confirm">
                            <span>Deactivate?</span>
                            <button
                              className="btn-danger btn-xs"
                              onClick={() => handleDeactivate(u.id)}
                              disabled={deactivating}
                            >
                              {deactivating ? "Deactivating…" : "Yes, deactivate"}
                            </button>
                            <button
                              className="btn-ghost btn-xs"
                              onClick={() => setConfirmDeactivateId(null)}
                            >
                              Cancel
                            </button>
                          </span>
                        ) : (
                          <button
                            className="btn-ghost btn-xs btn-destructive"
                            onClick={() => setConfirmDeactivateId(u.id)}
                          >
                            Deactivate
                          </button>
                        )
                      )}
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}

      {showInvite && (
        <div className="drawer-overlay" onClick={handleDismissInvite}>
          <div className="drawer" onClick={(e) => e.stopPropagation()}>
            <div className="drawer-header">
              <h3>{invitedResult ? "User Invited" : "Invite User"}</h3>
              <button className="drawer-close" onClick={handleDismissInvite} aria-label="Close">×</button>
            </div>

            {invitedResult ? (
              <>
                <div className="drawer-body">
                  <div className="token-warning">
                    This is the only time this invite token will be shown. Copy it
                    now and deliver it to {invitedResult.email} out of band — it
                    cannot be retrieved again. It expires{" "}
                    {formatDate(invitedResult.invite_expires_at)}.
                  </div>
                  <div className="form-field">
                    <label>Invite Token</label>
                    <code className="token-value">{invitedResult.invite_token}</code>
                  </div>
                  <button className="btn-ghost btn-sm" onClick={handleCopy}>
                    {copied ? "Copied!" : "Copy to clipboard"}
                  </button>
                </div>
                <div className="drawer-footer">
                  <div style={{ flex: 1 }} />
                  <button className="btn-primary" onClick={closeInvite}>Done</button>
                </div>
              </>
            ) : (
              <>
                <div className="drawer-body">
                  {inviteError && <div className="form-error">{inviteError}</div>}
                  <div className="form-field">
                    <label>Email <span className="required">*</span></label>
                    <input
                      type="email"
                      value={email}
                      onChange={(e) => setEmail(e.target.value)}
                    />
                  </div>
                  <div className="form-field">
                    <label>Display Name <span className="required">*</span></label>
                    <input
                      type="text"
                      value={displayName}
                      onChange={(e) => setDisplayName(e.target.value)}
                    />
                  </div>
                  <div className="form-grid">
                    <div className="form-field">
                      <label>Role</label>
                      <select value={role} onChange={(e) => setRole(e.target.value)}>
                        {ALL_ROLES.map((r) => (
                          <option key={r} value={r}>{ROLE_LABELS[r] ?? r}</option>
                        ))}
                      </select>
                    </div>
                    <div className="form-field">
                      <label>Login Method</label>
                      <select value={loginMethod} onChange={(e) => setLoginMethod(e.target.value)}>
                        {LOGIN_METHODS.map((m) => (
                          <option key={m.value} value={m.value}>{m.label}</option>
                        ))}
                      </select>
                    </div>
                  </div>
                </div>
                <div className="drawer-footer">
                  <div style={{ flex: 1 }} />
                  <button className="btn-ghost" onClick={closeInvite}>Cancel</button>
                  <button className="btn-primary" onClick={handleInvite} disabled={inviting}>
                    {inviting ? "Inviting…" : "Invite"}
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
