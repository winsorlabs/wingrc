import { useState } from "react";
import type { ControlStateRow } from "../types";
import { ControlSection } from "./ControlSection";

const FAMILY_NAMES: Record<string, string> = {
  AC: "Access Control",
  AT: "Awareness and Training",
  AU: "Audit and Accountability",
  CM: "Configuration Management",
  IA: "Identification and Authentication",
  IR: "Incident Response",
  MA: "Maintenance",
  MP: "Media Protection",
  PS: "Personnel Security",
  PE: "Physical Protection",
  RA: "Risk Assessment",
  CA: "Security Assessment",
  SC: "System and Communications Protection",
  SI: "System and Information Integrity",
};

interface Props {
  family: string;
  rows: ControlStateRow[];
}

export function FamilySection({ family, rows }: Props) {
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
              title={objs[0].control_title}
              objectives={objs}
            />
          ))}
        </div>
      )}
    </div>
  );
}
