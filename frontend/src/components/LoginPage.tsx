import { useState } from "react";
import { api } from "../api";

type LoginStep =
  | "credentials"
  | "mfa_verify"
  | "mfa_enroll"
  | "mfa_enroll_confirm"
  | "backup_codes";

interface Props {
  onAuthenticated: () => void;
}

export function LoginPage({ onAuthenticated }: Props) {
  const [step, setStep] = useState<LoginStep>("credentials");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [mfaCode, setMfaCode] = useState("");
  const [enrollData, setEnrollData] = useState<{ provisioning_uri: string; secret: string } | null>(null);
  const [backupCodes, setBackupCodes] = useState<string[]>([]);
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
      if (result.next === "enroll") {
        const data = await api.mfaEnroll();
        setEnrollData(data);
        setStep("mfa_enroll");
      } else {
        setStep("mfa_verify");
      }
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

  async function handleEnrollConfirm(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setBusy(true);
    try {
      const result = await api.mfaEnrollConfirm(mfaCode);
      setBackupCodes(result.backup_codes);
      setStep("backup_codes");
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

        {step === "mfa_enroll" && enrollData && (
          <div className="login-form">
            <h2>Set up two-factor authentication</h2>
            <p>
              Scan the QR code below with your authenticator app (Google Authenticator,
              Authy, Microsoft Authenticator, etc.), then enter the 6-digit code to confirm.
            </p>
            <img
              src={`https://api.qrserver.com/v1/create-qr-code/?size=200x200&data=${encodeURIComponent(enrollData.provisioning_uri)}`}
              alt="TOTP QR code"
              width={200}
              height={200}
              className="totp-qr"
            />
            <details className="totp-manual">
              <summary>Can't scan? Enter manually</summary>
              <code>{enrollData.secret}</code>
            </details>
            <form onSubmit={handleEnrollConfirm}>
              <label>
                Authenticator code
                <input
                  type="text"
                  inputMode="numeric"
                  value={mfaCode}
                  onChange={e => setMfaCode(e.target.value)}
                  required
                  maxLength={6}
                  placeholder="000000"
                />
              </label>
              {error && <p className="login-error">{error}</p>}
              <button type="submit" disabled={busy} className="btn btn-primary">
                {busy ? "Confirming…" : "Confirm and continue"}
              </button>
            </form>
          </div>
        )}

        {step === "backup_codes" && (
          <div className="login-form">
            <h2>Save your backup codes</h2>
            <p>
              Store these codes somewhere safe. Each can be used once if you lose
              access to your authenticator. They will not be shown again.
            </p>
            <ul className="backup-codes">
              {backupCodes.map(c => (
                <li key={c}><code>{c}</code></li>
              ))}
            </ul>
            <button
              className="btn btn-primary"
              onClick={onAuthenticated}
            >
              I have saved my codes — continue
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
