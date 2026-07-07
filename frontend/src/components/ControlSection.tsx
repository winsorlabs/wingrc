import { useState } from "react";
import type { ControlStateRow } from "../types";
import { ObjectiveRow } from "./ObjectiveRow";
import { StatementChip } from "./StatementChip";

interface Props {
  controlId: string;
  controlDbId: string;
  title: string;
  objectives: ControlStateRow[];
  orgId: string;
  assessmentId: string;
  onStatusChange: (id: string, newStatus: string) => void;
  onOpenDrawer: (controlDbId: string, controlId: string, title: string) => void;
  defaultOpen?: boolean;
}

export function ControlSection({
  controlId,
  controlDbId,
  title,
  objectives,
  orgId,
  assessmentId,
  onStatusChange,
  onOpenDrawer,
  defaultOpen = false,
}: Props) {
  const [open, setOpen] = useState(defaultOpen);
  const statementStatus = objectives[0]?.statement_status ?? null;

  return (
    <div className="control-section">
      <div className="control-header" onClick={() => setOpen((o) => !o)}>
        <span className={`chevron ${open ? "open" : ""}`}>▶</span>
        <span className="control-id">{controlId}</span>
        <span className="control-title">{title}</span>
        <StatementChip status={statementStatus} />
        <button
          className="edit-stmt-btn"
          aria-label="Edit implementation statement"
          onClick={(e) => {
            e.stopPropagation();
            onOpenDrawer(controlDbId, controlId, title);
          }}
        >
          ✎
        </button>
      </div>
      {open && (
        <div className="control-body">
          {objectives.map((row) => (
            <ObjectiveRow
              key={row.id}
              row={row}
              orgId={orgId}
              assessmentId={assessmentId}
              onStatusChange={onStatusChange}
            />
          ))}
        </div>
      )}
    </div>
  );
}
