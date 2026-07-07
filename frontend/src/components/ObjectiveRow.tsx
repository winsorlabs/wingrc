import { useRef, useState } from "react";
import { api } from "../api";
import type { ControlStateRow } from "../types";

export const STATUSES = [
  { value: "met", label: "Met" },
  { value: "not_met", label: "Not Met" },
  { value: "partial", label: "Partial" },
  { value: "pending_evidence", label: "Pending Evidence" },
  { value: "not_applicable", label: "Not Applicable" },
  { value: "inherited", label: "Inherited" },
] as const;

const STATUS_CLASS: Record<string, string> = {
  met: "badge-met",
  not_met: "badge-not-met",
  partial: "badge-partial",
  pending_evidence: "badge-pending",
  not_applicable: "badge-na",
  inherited: "badge-inherited",
};

const RESP_LABEL: Record<string, string> = {
  provider_satisfies: "MSP",
  shared: "Shared",
  customer_owns: "Customer",
  inherited: "Inherited",
};

const RESP_CLASS: Record<string, string> = {
  provider_satisfies: "resp-msp",
  shared: "resp-shared",
  customer_owns: "resp-customer",
  inherited: "resp-msp",
};

interface Props {
  row: ControlStateRow;
  orgId: string;
  assessmentId: string;
  onStatusChange: (id: string, newStatus: string) => void;
}

export function ObjectiveRow({ row, orgId, assessmentId, onStatusChange }: Props) {
  const [status, setStatus] = useState(row.status);
  const [open, setOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);

  async function selectStatus(newStatus: string) {
    setOpen(false);
    if (newStatus === status) return;
    setSaving(true);
    try {
      await api.patchControlState(orgId, assessmentId, row.id, newStatus);
      setStatus(newStatus);
      onStatusChange(row.id, newStatus);
    } finally {
      setSaving(false);
    }
  }

  const badgeClass = STATUS_CLASS[status] ?? "badge-na";
  const badgeLabel = STATUSES.find((s) => s.value === status)?.label ?? status;

  return (
    <div className="obj-row">
      <span className="obj-key">[{row.objective_key}]</span>
      <span className="obj-text">{row.objective_text}</span>
      <span className="obj-badges">
        <span className="status-picker" ref={dropdownRef}>
          <button
            className={`badge ${badgeClass} badge-btn${saving ? " badge-saving" : ""}`}
            onClick={() => !saving && setOpen((o) => !o)}
            aria-haspopup="listbox"
            aria-expanded={open}
            disabled={saving}
          >
            {saving ? "…" : badgeLabel}
          </button>
          {open && (
            <div className="status-dropdown" role="listbox">
              {STATUSES.map((s) => (
                <button
                  key={s.value}
                  role="option"
                  aria-selected={s.value === status}
                  className={`status-option ${STATUS_CLASS[s.value]}${s.value === status ? " current" : ""}`}
                  onClick={() => selectStatus(s.value)}
                >
                  {s.label}
                </button>
              ))}
            </div>
          )}
        </span>
        {RESP_LABEL[row.responsibility] && (
          <span className={`resp ${RESP_CLASS[row.responsibility] ?? ""}`}>
            {RESP_LABEL[row.responsibility]}
          </span>
        )}
      </span>
    </div>
  );
}
