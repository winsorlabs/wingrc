import type { ControlStateRow } from "../types";

const STATUS_LABEL: Record<string, string> = {
  met: "Met",
  not_met: "Not Met",
  partial: "Partial",
  pending_evidence: "Pending",
  na: "N/A",
  inherited: "Inherited",
};

const STATUS_CLASS: Record<string, string> = {
  met: "badge-met",
  not_met: "badge-not-met",
  partial: "badge-partial",
  pending_evidence: "badge-pending",
  na: "badge-na",
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
}

export function ObjectiveRow({ row }: Props) {
  return (
    <div className="obj-row">
      <span className="obj-key">[{row.objective_key}]</span>
      <span className="obj-text">{row.objective_text}</span>
      <span className="obj-badges">
        <span className={`badge ${STATUS_CLASS[row.status] ?? "badge-na"}`}>
          {STATUS_LABEL[row.status] ?? row.status}
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
