import { useEffect, useState } from "react";
import { api } from "../api";
import type { AuthUser, SessionRow } from "../types";
import { BackupCodesDisplay } from "./BackupCodesDisplay";
import { MfaReenrollFlow } from "./MfaReenrollFlow";

// Per-user account self-service (I.9) — deliberately NOT nested under
// OrgSettings. Every OrgSettings tab acts on orgId; this acts on the
// viewer's own account, which is orthogonal to which org's board happens
// to be open (same "wrong axis" lesson as the OrgPicker landing fix).
// Mounted at the top level in App.tsx, reachable from the header
// regardless of org state.
//
// None of this is gated on canWrite (I.8) — that axis governs assessment/
// org data mutation, not "can this person manage their own login." A
// c3pao_assessor must be able to change their own password same as
// anyone; the real gate here is login_method — SSO/api accounts have no
// local password or TOTP secret to manage at all.

interface Props {
  user: AuthUser;
  onClose: () => void;
  onSignedOutEverywhere: () => void;
}

export function AccountSettings({ user, onClose, onSignedOutEverywhere }: Props) {
  const isLocal = user.login_method === "local";

  return (
    <div className="settings-shell">
      <div className="settings-header">
        <div className="settings-title">
          <span className="settings-org-name">My Account</span>
          <span className="settings-title-sep">·</span>
          <span>{user.email}</span>
        </div>
        <button className="drawer-close" onClick={onClose} aria-label="Close account settings">×</button>
      </div>
      <div className="settings-content">
        {isLocal && <PasswordSection />}
        {isLocal && <MfaSection />}
        {!isLocal && (
          <div className="card">
            <h2>Sign-in</h2>
            <p className="field-hint">
              Signed in via {user.login_method === "sso" ? "Microsoft Entra ID" : user.login_method}.
              Password and two-factor authentication are managed by your identity
              provider, not here.
            </p>
          </div>
        )}
        <SessionsSection onSignedOutEverywhere={onSignedOutEverywhere} />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Password
// ---------------------------------------------------------------------------

function PasswordSection() {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState("");
  const [success, setSuccess] = useState(false);
  const [busy, setBusy] = useState(false);

  const mismatch = next.length > 0 && confirm.length > 0 && next !== confirm;
  // Backend joins multiple simultaneous policy violations with "; " — split
  // back out so each renders as its own line rather than one run-on string.
  const errorItems = error.split("; ").filter(Boolean);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setSuccess(false);
    if (mismatch) return;
    setBusy(true);
    try {
      await api.changePassword(current, next);
      setSuccess(true);
      setCurrent("");
      setNext("");
      setConfirm("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not change password");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card">
      <h2>Password</h2>
      <form className="form-body" onSubmit={handleSubmit}>
        <div className="form-field">
          <label>Current password</label>
          <input
            type="password"
            value={current}
            onChange={(e) => setCurrent(e.target.value)}
            autoComplete="current-password"
            required
          />
        </div>
        <div className="form-field">
          <label>New password</label>
          <input
            type="password"
            value={next}
            onChange={(e) => setNext(e.target.value)}
            autoComplete="new-password"
            required
          />
        </div>
        <div className="form-field">
          <label>Confirm new password</label>
          <input
            type="password"
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
            autoComplete="new-password"
            required
          />
          {mismatch && <div className="field-hint" style={{ color: "#dc3545" }}>Passwords do not match.</div>}
        </div>
        {errorItems.length > 0 && (
          <div className="form-error">
            <ul style={{ margin: 0, paddingLeft: "1.1rem" }}>
              {errorItems.map((msg) => <li key={msg}>{msg}</li>)}
            </ul>
          </div>
        )}
        {success && <div className="form-success">Password changed.</div>}
        <div className="form-actions">
          <button className="btn-primary" type="submit" disabled={busy || mismatch}>
            {busy ? "Changing…" : "Change password"}
          </button>
        </div>
      </form>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Two-factor authentication
// ---------------------------------------------------------------------------

type MfaAction = "none" | "reenroll" | "regenerate";

function MfaSection() {
  const [action, setAction] = useState<MfaAction>("none");
  const [regenPassword, setRegenPassword] = useState("");
  const [regenTotp, setRegenTotp] = useState("");
  const [regenCodes, setRegenCodes] = useState<string[] | null>(null);
  const [regenError, setRegenError] = useState("");
  const [busy, setBusy] = useState(false);

  async function handleRegenerate(e: React.FormEvent) {
    e.preventDefault();
    setRegenError("");
    setBusy(true);
    try {
      const result = await api.regenerateBackupCodes({
        current_password: regenPassword || undefined,
        totp_code: regenTotp || undefined,
      });
      setRegenCodes(result.backup_codes);
    } catch (err) {
      setRegenError(err instanceof Error ? err.message : "Could not verify your identity");
    } finally {
      setBusy(false);
    }
  }

  function resetRegen() {
    setAction("none");
    setRegenPassword("");
    setRegenTotp("");
    setRegenCodes(null);
    setRegenError("");
  }

  if (action === "reenroll") {
    return (
      <div className="card">
        <h2>Two-Factor Authentication</h2>
        <MfaReenrollFlow onDone={() => setAction("none")} onCancel={() => setAction("none")} />
      </div>
    );
  }

  if (action === "regenerate") {
    if (regenCodes) {
      return (
        <div className="card">
          <h2>Two-Factor Authentication</h2>
          <BackupCodesDisplay codes={regenCodes} onContinue={resetRegen} continueLabel="Done" />
        </div>
      );
    }
    return (
      <div className="card">
        <h2>Two-Factor Authentication</h2>
        <form className="form-body" onSubmit={handleRegenerate}>
          <p className="field-hint">
            Confirm it's you before replacing your backup codes. Your existing
            codes stop working the moment new ones are issued.
          </p>
          <div className="form-field">
            <label>Current password</label>
            <input
              type="password"
              value={regenPassword}
              onChange={(e) => setRegenPassword(e.target.value)}
              autoComplete="current-password"
            />
          </div>
          <div className="form-field">
            <label>— or current authenticator code —</label>
            <input
              type="text"
              inputMode="numeric"
              value={regenTotp}
              onChange={(e) => setRegenTotp(e.target.value)}
              maxLength={6}
              placeholder="000000"
            />
          </div>
          {regenError && <div className="form-error">{regenError}</div>}
          <div className="form-actions">
            <button
              className="btn-primary"
              type="submit"
              disabled={busy || (!regenPassword && !regenTotp)}
            >
              {busy ? "Verifying…" : "Regenerate codes"}
            </button>
            <button type="button" className="btn-ghost" onClick={resetRegen}>Cancel</button>
          </div>
        </form>
      </div>
    );
  }

  return (
    <div className="card">
      <h2>Two-Factor Authentication</h2>
      <p className="field-hint">Two-factor authentication is enabled on your account.</p>
      <div className="form-actions" style={{ justifyContent: "flex-start" }}>
        <button className="btn-ghost" onClick={() => setAction("reenroll")}>
          Re-enroll (new device)
        </button>
        <button className="btn-ghost" onClick={() => setAction("regenerate")}>
          Regenerate backup codes
        </button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Active sessions
// ---------------------------------------------------------------------------

function SessionsSection({ onSignedOutEverywhere }: { onSignedOutEverywhere: () => void }) {
  const [sessions, setSessions] = useState<SessionRow[] | null>(null);
  const [error, setError] = useState("");
  const [confirming, setConfirming] = useState(false);
  const [revoking, setRevoking] = useState(false);

  useEffect(() => {
    api.listSessions().then(setSessions).catch(() => setError("Could not load sessions"));
  }, []);

  async function handleRevokeAll() {
    setRevoking(true);
    try {
      await api.revokeAllSessions();
      onSignedOutEverywhere();
    } catch {
      setError("Could not sign out everywhere");
      setRevoking(false);
    }
  }

  return (
    <div className="card">
      <h2>Active Sessions</h2>
      {error && <p className="form-error">{error}</p>}
      <ul className="item-list">
        {(sessions ?? []).map((s) => (
          <li key={s.id} className="item-row">
            <div>
              <div className="item-name">
                {s.is_current ? "This session" : "Session"}
                {s.is_current && <span className="role-badge" style={{ marginLeft: ".4rem" }}>current</span>}
              </div>
              <div className="item-meta">
                Last active {new Date(s.last_activity_at).toLocaleString()} · started{" "}
                {new Date(s.created_at).toLocaleString()}
              </div>
            </div>
          </li>
        ))}
        {sessions !== null && sessions.length === 0 && (
          <li className="empty">No active sessions.</li>
        )}
      </ul>

      <div className="divider" />
      {confirming ? (
        <span className="delete-confirm">
          <span>Sign out everywhere? This ends every session, including this one.</span>
          <button className="btn-danger btn-xs" onClick={handleRevokeAll} disabled={revoking}>
            {revoking ? "Signing out…" : "Yes, sign out everywhere"}
          </button>
          <button className="btn-ghost btn-xs" onClick={() => setConfirming(false)} disabled={revoking}>
            Cancel
          </button>
        </span>
      ) : (
        <button className="btn-ghost" onClick={() => setConfirming(true)}>
          Sign out everywhere
        </button>
      )}
    </div>
  );
}
