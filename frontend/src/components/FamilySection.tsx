import { useState } from "react";
import { FAMILY_NAMES } from "../lib/families";
import type { ControlStateRow } from "../types";
import { ControlSection } from "./ControlSection";

interface Props {
  family: string;
  rows: ControlStateRow[];
  orgId: string;
  assessmentId: string;
  canWrite: boolean;
  onStatusChange: (id: string, newStatus: string) => void;
  onOpenDrawer: (controlDbId: string, controlId: string, title: string) => void;
}

export function FamilySection({
  family,
  rows,
  orgId,
  assessmentId,
  canWrite,
  onStatusChange,
  onOpenDrawer,
}: Props) {
  const [open, setOpen] = useState(true);

  const met = rows.filter((r) => r.status === "met").length;
  const total = rows.length;

  const byControl = rows.reduce<Record<string, ControlStateRow[]>>((acc, row) => {
    (acc[row.control_id] ??= []).push(row);
    return acc;
  }, {});

  const controls = Object.entries(byControl);

  return (
    <div className="family-section">
      <div className="family-header" onClick={() => setOpen((o) => !o)}>
        <span className={`chevron ${open ? "open" : ""}`}>▶</span>
        <span className="family-key">{family}</span>
        <span className="family-label">{FAMILY_NAMES[family] ?? ""}</span>
        <span className="family-stats">
          <span>{met}/{total} objectives met</span>
          <span>· {controls.length} controls</span>
        </span>
      </div>
      {open && (
        <div className="family-body">
          {controls.map(([controlId, objs]) => (
            <ControlSection
              key={controlId}
              controlId={controlId}
              controlDbId={objs[0].control_db_id}
              title={objs[0].control_title}
              objectives={objs}
              orgId={orgId}
              assessmentId={assessmentId}
              canWrite={canWrite}
              onStatusChange={onStatusChange}
              onOpenDrawer={onOpenDrawer}
            />
          ))}
        </div>
      )}
    </div>
  );
}
