import { useState } from "react";
import { api } from "../api";
import type { MfaEnrollData } from "../types";
import { BackupCodesDisplay } from "./BackupCodesDisplay";
import { MfaQrEnrollStep } from "./MfaQrEnrollStep";

// Self-service MFA re-enrollment (I.9) — not the same trust model as
// MfaEnrollmentFlow (pre-auth, keyed by a signed state cookie that already
// proved password + one-time token). This runs from an existing session,
// which isn't equivalent proof for something this sensitive, so it starts
// with a step-up prompt (current password or current TOTP code) before the
// backend will even stage a new secret — see routers/auth.py's
// mfa_reenroll/_verify_step_up. Shares the QR/code-entry and
// backup-codes-display markup with the pre-auth flow via
// MfaQrEnrollStep/BackupCodesDisplay.

type Step = "step_up" | "enroll" | "backup_codes";

interface Props {
  onDone: () => void;
  onCancel: () => void;
}

export function MfaReenrollFlow({ onDone, onCancel }: Props) {
  const [step, setStep] = useState<Step>("step_up");
  const [password, setPassword] = useState("");
  const [totpCode, setTotpCode] = useState("");
  const [enrollData, setEnrollData] = useState<MfaEnrollData | null>(null);
  const [backupCodes, setBackupCodes] = useState<string[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function handleStepUp(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setBusy(true);
    try {
      const data = await api.mfaReenroll({
        current_password: password || undefined,
        totp_code: totpCode || undefined,
      });
      setEnrollData(data);
      setStep("enroll");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not verify your identity");
    } finally {
      setBusy(false);
    }
  }

  async function handleEnrollConfirm(code: string) {
    setError("");
    setBusy(true);
    try {
      const result = await api.mfaReenrollConfirm(code);
      setBackupCodes(result.backup_codes);
      setStep("backup_codes");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Invalid code");
    } finally {
      setBusy(false);
    }
  }

  if (step === "step_up") {
    return (
      <div className="login-form">
        <h2>Re-enroll two-factor authentication</h2>
        <p>
          Confirm it's you before setting up a new authenticator. Use your
          password — this works even if you've lost your current
          authenticator device — or a code from it if you still have it.
        </p>
        <form onSubmit={handleStepUp}>
          <label>
            Current password
            <input
              type="password"
              value={password}
              onChange={e => setPassword(e.target.value)}
              autoComplete="current-password"
            />
          </label>
          <p style={{ textAlign: "center", color: "#6b7280", margin: ".25rem 0" }}>— or —</p>
          <label>
            Current authenticator code
            <input
              type="text"
              inputMode="numeric"
              value={totpCode}
              onChange={e => setTotpCode(e.target.value)}
              maxLength={6}
              placeholder="000000"
            />
          </label>
          {error && <p className="login-error">{error}</p>}
          <div className="form-row">
            <button
              type="submit"
              disabled={busy || (!password && !totpCode)}
              className="btn btn-primary"
            >
              {busy ? "Verifying…" : "Continue"}
            </button>
            <button type="button" className="btn-ghost" onClick={onCancel}>
              Cancel
            </button>
          </div>
        </form>
      </div>
    );
  }

  return (
    <>
      {step === "enroll" && enrollData && (
        <MfaQrEnrollStep
          enrollData={enrollData}
          busy={busy}
          error={error}
          onSubmit={handleEnrollConfirm}
          title="Set up your new authenticator"
          description="Scan the QR code with your new authenticator app, then enter the 6-digit code to confirm the switch."
        />
      )}
      {step === "backup_codes" && (
        <BackupCodesDisplay codes={backupCodes} onContinue={onDone} continueLabel="Done" />
      )}
    </>
  );
}
