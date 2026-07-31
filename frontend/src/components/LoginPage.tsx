import { useState } from "react";
import { api } from "../api";
import { MfaEnrollmentFlow } from "./MfaEnrollmentFlow";
import { MfaVerifyFlow } from "./MfaVerifyFlow";

type LoginStep = "credentials" | "mfa_verify" | "enrolling";

interface Props {
  onAuthenticated: () => void;
  onWantInvite: () => void;
}

export function LoginPage({ onAuthenticated, onWantInvite }: Props) {
  const [step, setStep] = useState<LoginStep>("credentials");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
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

  return (
    <div className="login-page">
      <div className="login-card">
        <div className="login-brand">
          <span className="login-brand-mark">W</span>
          <span className="login-brand-name">WinGRC</span>
        </div>

        {step === "credentials" && (
          <>
            <h2>Welcome back</h2>
            <p>Sign in to your MSP workspace.</p>

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
          <MfaVerifyFlow onComplete={onAuthenticated} />
        )}

        {step === "enrolling" && (
          <MfaEnrollmentFlow onComplete={onAuthenticated} />
        )}
      </div>
    </div>
  );
}
