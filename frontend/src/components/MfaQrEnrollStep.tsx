import { useState } from "react";
import type { MfaEnrollData } from "../types";

// Extracted from MfaEnrollmentFlow (I.9) so pre-auth enrollment and
// self-service re-enrollment (MfaReenrollFlow) share one QR/code-entry
// form instead of drifting copies — same reasoning I.5 used to extract
// MfaVerifyFlow: "an auth-critical form duplicated across pages drifts."

interface Props {
  enrollData: MfaEnrollData;
  busy: boolean;
  error: string;
  onSubmit: (code: string) => void;
  title?: string;
  description?: string;
}

export function MfaQrEnrollStep({ enrollData, busy, error, onSubmit, title, description }: Props) {
  const [code, setCode] = useState("");

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    onSubmit(code);
  }

  return (
    <div className="login-form">
      <h2>{title ?? "Set up two-factor authentication"}</h2>
      <p>
        {description ?? (
          "Scan the QR code below with your authenticator app (Google Authenticator, "
          + "Authy, Microsoft Authenticator, etc.), then enter the 6-digit code to confirm."
        )}
      </p>
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
      <form onSubmit={handleSubmit}>
        <label>
          Authenticator code
          <input
            type="text"
            inputMode="numeric"
            value={code}
            onChange={e => setCode(e.target.value)}
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
  );
}
