import { useState } from "react";
import { api } from "../api";
import { MfaEnrollmentFlow } from "./MfaEnrollmentFlow";
import { MfaVerifyFlow } from "./MfaVerifyFlow";

type Step = "form" | "mfa_verify" | "enrolling";

interface Props {
  onAuthenticated: () => void;
  onCancel: () => void;
}

export function InviteAcceptPage({ onAuthenticated, onCancel }: Props) {
  const [step, setStep] = useState<Step>("form");
  const [token, setToken] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setBusy(true);
    try {
      // This token is either a fresh invite or an admin-issued password
      // reset (I.5) — same endpoint, same shape. A brand-new invite has no
      // MFA yet (next: "enroll"); a reset targets an existing, already
      // MFA-enrolled account (next: "verify"). Branch exactly like
      // LoginPage's handleCredentials does for ordinary login.
      const result = await api.setPassword(token.trim(), password);
      setStep(result.next === "enroll" ? "enrolling" : "mfa_verify");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not set password");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login-page">
      <div className="login-card">
        <div className="login-brand">
          <span className="login-brand-mark">W</span>
          <span className="login-brand-name">WinGRC</span>
        </div>

        {step === "form" && (
          <form onSubmit={handleSubmit} className="login-form">
            <h2>Accept your invitation</h2>
            <p>Enter the invite token your admin sent you, then choose a password.</p>
            <label>
              Invite Token
              <input
                type="text"
                value={token}
                onChange={e => setToken(e.target.value)}
                required
                autoComplete="off"
              />
            </label>
            <label>
              Password
              <input
                type="password"
                value={password}
                onChange={e => setPassword(e.target.value)}
                required
                autoComplete="new-password"
              />
            </label>
            <p className="field-hint">
              Must be at least 15 characters. It's checked against a list of
              known breached passwords — if it's ever appeared in a public
              breach, it will be rejected even if it otherwise meets the
              length requirement.
            </p>
            {error && <p className="login-error">{error}</p>}
            <button type="submit" disabled={busy} className="btn btn-primary">
              {busy ? "Setting password…" : "Continue"}
            </button>
            <button type="button" className="btn btn-ghost" onClick={onCancel}>
              Back to sign in
            </button>
          </form>
        )}

        {step === "mfa_verify" && (
          <MfaVerifyFlow onComplete={onAuthenticated} />
        )}

        {step === "enrolling" && (
          <MfaEnrollmentFlow onComplete={onAuthenticated} />
        )}
      </div>
    </div>
  );
}
