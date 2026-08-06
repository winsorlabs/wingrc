import { useEffect, useState } from "react";
import { api } from "../api";
import type { MfaEnrollData } from "../types";

// Shared between LoginPage (first login after an already-set password,
// mfa_enrolled=false) and InviteAcceptPage (right after set-password for a
// brand-new invite redemption) — both land here needing the exact same
// enroll -> confirm -> backup-codes sequence, so it lives in one place
// rather than being duplicated per caller.

type Step = "loading" | "enroll" | "backup_codes";

interface Props {
  onComplete: () => void;
}

export function MfaEnrollmentFlow({ onComplete }: Props) {
  const [step, setStep] = useState<Step>("loading");
  const [enrollData, setEnrollData] = useState<MfaEnrollData | null>(null);
  const [mfaCode, setMfaCode] = useState("");
  const [backupCodes, setBackupCodes] = useState<string[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.mfaEnroll()
      .then((data) => {
        setEnrollData(data);
        setStep("enroll");
      })
      .catch((err) => {
        setError(err instanceof Error ? err.message : "Could not start MFA enrollment");
      });
  }, []);

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

  if (step === "loading") {
    return (
      <div className="login-form">
        {error ? <p className="login-error">{error}</p> : <p>Loading…</p>}
      </div>
    );
  }

  return (
    <>
      {step === "enroll" && enrollData && (
        <div className="login-form">
          <h2>Set up two-factor authentication</h2>
          <p>
            Scan the QR code below with your authenticator app (Google Authenticator,
            Authy, Microsoft Authenticator, etc.), then enter the 6-digit code to confirm.
          </p>
          {/* Rendered entirely server-side (ADR 0008) — never a third-party
              request. The old api.qrserver.com <img> sent the live TOTP
              secret to an external service in its query string. */}
          <img
            src={enrollData.qr_data_uri}
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
            onClick={onComplete}
          >
            I have saved my codes — continue
          </button>
        </div>
      )}
    </>
  );
}
