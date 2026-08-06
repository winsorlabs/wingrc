// Extracted from MfaEnrollmentFlow (I.9), same reasoning as
// MfaQrEnrollStep — shared verbatim between first-time enrollment and
// self-service re-enrollment/backup-code regeneration.

interface Props {
  codes: string[];
  onContinue: () => void;
  continueLabel?: string;
}

export function BackupCodesDisplay({ codes, onContinue, continueLabel }: Props) {
  return (
    <div className="login-form">
      <h2>Save your backup codes</h2>
      <p>
        Store these codes somewhere safe. Each can be used once if you lose
        access to your authenticator. They will not be shown again.
      </p>
      <ul className="backup-codes">
        {codes.map(c => (
          <li key={c}><code>{c}</code></li>
        ))}
      </ul>
      <button className="btn btn-primary" onClick={onContinue}>
        {continueLabel ?? "I have saved my codes — continue"}
      </button>
    </div>
  );
}
