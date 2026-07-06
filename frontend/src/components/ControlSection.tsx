import { useState } from "react";
import type { ControlStateRow } from "../types";
import { ObjectiveRow } from "./ObjectiveRow";
import { StatementChip } from "./StatementChip";

interface Props {
  controlId: string;
  title: string;
  objectives: ControlStateRow[];
  defaultOpen?: boolean;
}

export function ControlSection({ controlId, title, objectives, defaultOpen = false }: Props) {
  const [open, setOpen] = useState(defaultOpen);
  const statementStatus = objectives[0]?.statement_status ?? null;

  return (
    <div className="control-section">
      <div className="control-header" onClick={() => setOpen((o) => !o)}>
        <span className={`chevron ${open ? "open" : ""}`}>▶</span>
        <span className="control-id">{controlId}</span>
        <span className="control-title">{title}</span>
        <StatementChip status={statementStatus} />
      </div>
      {open && (
        <div className="control-body">
          {objectives.map((row) => (
            <ObjectiveRow key={row.id} row={row} />
          ))}
        </div>
      )}
    </div>
  );
}
