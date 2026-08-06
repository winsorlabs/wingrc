import { useEffect, useState } from "react";
import { api } from "../api";
import type { MfaEnrollData } from "../types";
import { BackupCodesDisplay } from "./BackupCodesDisplay";
import { MfaQrEnrollStep } from "./MfaQrEnrollStep";

// Shared between LoginPage (first login after an already-set password,
// mfa_enrolled=false) and InviteAcceptPage (right after set-password for a
// brand-new invite redemption) — both land here needing the exact same
// enroll -> confirm -> backup-codes sequence, so it lives in one place
// rather than being duplicated per caller.
//
// The QR/code-entry step and the backup-codes step are extracted into
// MfaQrEnrollStep/BackupCodesDisplay (I.9) so self-service re-enrollment
// (MfaReenrollFlow) can reuse the identical markup — this component keeps
// only the pre-auth-specific orchestration (mfaEnroll/mfaEnrollConfirm,
// onComplete meaning "now authenticated, enter the app").

type Step = "loading" | "enroll" | "backup_codes";

interface Props {
  onComplete: () => void;
}

export function MfaEnrollmentFlow({ onComplete }: Props) {
  const [step, setStep] = useState<Step>("loading");
  const [enrollData, setEnrollData] = useState<MfaEnrollData | null>(null);
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

  async function handleEnrollConfirm(code: string) {
    setError("");
    setBusy(true);
    try {
      const result = await api.mfaEnrollConfirm(code);
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
        <MfaQrEnrollStep
          enrollData={enrollData}
          busy={busy}
          error={error}
          onSubmit={handleEnrollConfirm}
        />
      )}
      {step === "backup_codes" && (
        <BackupCodesDisplay codes={backupCodes} onContinue={onComplete} />
      )}
    </>
  );
}
