import { useState } from "react";
import { api } from "../api";

// Shared between LoginPage (mfa_enrolled=true on ordinary login) and
// InviteAcceptPage (mfa_enrolled=true on a password reset — an existing
// user redeeming a reset token already has MFA, unlike a brand-new invite,
// so /set-password responds next="verify" for them too; see I.5). Extracted
// for the same reason MfaEnrollmentFlow was: an auth-critical form
// duplicated across two pre-auth surfaces drifts.

interface Props {
  onComplete: () => void;
}

export function MfaVerifyFlow({ onComplete }: Props) {
  const [mfaCode, setMfaCode] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setBusy(true);
    try {
      await api.mfaVerify(mfaCode);
      onComplete();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Invalid code");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="login-form">
      <h2>Two-factor authentication</h2>
      <p>Enter the 6-digit code from your authenticator app, or a backup code.</p>
      <label>
        Code
        <input
          type="text"
          inputMode="numeric"
          value={mfaCode}
          onChange={e => setMfaCode(e.target.value)}
          required
          autoComplete="one-time-code"
          maxLength={20}
        />
      </label>
      {error && <p className="login-error">{error}</p>}
      <button type="submit" disabled={busy} className="btn btn-primary">
        {busy ? "Verifying…" : "Verify"}
      </button>
    </form>
  );
}
