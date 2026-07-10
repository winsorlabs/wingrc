import { useEffect, useState } from "react";
import { api } from "../api";
import type { OnboardingStatus } from "../types";
import { ContactsPanel } from "./ContactsPanel";
import { OrgProfileForm } from "./OrgProfileForm";
import { SystemDescriptionForm } from "./SystemDescriptionForm";

type Step = 0 | 1 | 2;

const STEPS = [
  { label: "Org Profile", key: "profile" as const },
  { label: "System Description", key: "system_description" as const },
  { label: "Personnel & Contacts", key: "personnel" as const },
];

interface Props {
  orgId: string;
  orgName: string;
  onClose: () => void;
}

export function OnboardingWizard({ orgId, orgName, onClose }: Props) {
  const [step, setStep] = useState<Step>(0);
  const [status, setStatus] = useState<OnboardingStatus | null>(null);

  function loadStatus() {
    api.getOnboardingStatus(orgId).then(setStatus).catch(() => {});
  }

  useEffect(() => {
    loadStatus();
  }, [orgId]);

  function stepComplete(s: Step): boolean {
    if (!status) return false;
    if (s === 0) return status.profile.complete;
    if (s === 1) return status.system_description.complete;
    return status.personnel.complete;
  }

  const allComplete = status && status.profile.complete && status.system_description.complete && status.personnel.complete;

  return (
    <div className="wizard-overlay">
      <div className="wizard">
        <div className="wizard-header">
          <div className="wizard-org">{orgName}</div>
          <div className="wizard-title">Get started</div>
          <button className="wizard-close" onClick={onClose} aria-label="Skip setup and close">×</button>
        </div>

        <div className="wizard-steps">
          {STEPS.map((s, i) => (
            <button
              key={s.key}
              className={`wizard-step-tab${step === i ? " active" : ""}${stepComplete(i as Step) ? " done" : ""}`}
              onClick={() => setStep(i as Step)}
            >
              <span className="wizard-step-num">
                {stepComplete(i as Step) ? "✓" : i + 1}
              </span>
              {s.label}
            </button>
          ))}
        </div>

        <div className="wizard-body">
          {step === 0 && <OrgProfileForm orgId={orgId} onSaved={loadStatus} />}
          {step === 1 && <SystemDescriptionForm orgId={orgId} onSaved={loadStatus} />}
          {step === 2 && <ContactsPanel orgId={orgId} onChanged={loadStatus} />}
        </div>

        <div className="wizard-footer">
          {allComplete ? (
            <div className="wizard-complete-msg">All sections complete!</div>
          ) : (
            <button className="btn-ghost btn-sm" onClick={onClose}>
              Skip setup — complete later in Settings
            </button>
          )}
          <div style={{ flex: 1 }} />
          {step > 0 && (
            <button className="btn-ghost" onClick={() => setStep((step - 1) as Step)}>
              ← Back
            </button>
          )}
          {step < 2 ? (
            <button className="btn-primary" onClick={() => setStep((step + 1) as Step)}>
              Continue →
            </button>
          ) : (
            <button className="btn-primary" onClick={onClose}>
              {allComplete ? "Finish" : "Done for now"}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
