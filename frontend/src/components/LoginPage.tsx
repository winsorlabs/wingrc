import { useState } from "react";
import { api } from "../api";
import { MfaEnrollmentFlow } from "./MfaEnrollmentFlow";

type LoginStep = "credentials" | "mfa_verify" | "enrolling";

interface Props {
  onAuthenticated: () => void;
  onWantInvite: () => void;
}

export function LoginPage({ onAuthenticated, onWantInvite }: Props) {
  const [step, setStep] = useState<LoginStep>("credentials");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [mfaCode, setMfaCode] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const ssoConfigured = !!(
    import.meta.env.VITE_SSO_ENABLED === "true"
  );

  async function handleCredentials(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setBusy(true);
    try {
      const result = await api.localLogin(email, password);
      setStep(result.next === "enroll" ? "enrolling" : "mfa_verify");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Login failed");
    } finally {
      setBusy(false);
    }
  }

  async function handleMfaVerify(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setBusy(true);
    try {
      await api.mfaVerify(mfaCode);
      onAuthenticated();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Invalid code");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login-page">
      <div className="login-card">
        <h1 className="login-title">WinGRC</h1>

        {step === "credentials" && (
          <>
            {ssoConfigured && (
              <a href="/api/auth/login" className="btn btn-primary btn-sso">
                Sign in with Microsoft
              </a>
            )}

            <form onSubmit={handleCredentials} className="login-form">
              {ssoConfigured && <div className="login-divider">or use local account</div>}
              <label>
                Email
                <input
                  type="email"
                  value={email}
                  onChange={e => setEmail(e.target.value)}
                  required
                  autoComplete="username"
                />
              </label>
              <label>
                Password
                <input
                  type="password"
                  value={password}
                  onChange={e => setPassword(e.target.value)}
                  required
                  autoComplete="current-password"
                />
              </label>
              {error && <p className="login-error">{error}</p>}
              <button type="submit" disabled={busy} className="btn btn-primary">
                {busy ? "Signing in…" : "Sign in"}
              </button>
              <button type="button" className="btn btn-ghost login-invite-link" onClick={onWantInvite}>
                Have an invite token?
              </button>
            </form>
          </>
        )}

        {step === "mfa_verify" && (
          <form onSubmit={handleMfaVerify} className="login-form">
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
        )}

        {step === "enrolling" && (
          <MfaEnrollmentFlow onComplete={onAuthenticated} />
        )}
      </div>
    </div>
  );
}
