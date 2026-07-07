import { useEffect, useState } from "react";
import { api } from "../api";
import type { Assessment, ControlStateRow, Org } from "../types";
import { FamilySection } from "./FamilySection";

const FAMILY_ORDER = ["AC", "AT", "AU", "CM", "IA", "IR", "MA", "MP", "PS", "PE", "RA", "CA", "SC", "SI"];

interface Props {
  org: Org;
  assessment: Assessment;
}

export function AssessmentBoard({ org, assessment }: Props) {
  const [rows, setRows] = useState<ControlStateRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    api
      .getControlStates(org.id, assessment.id)
      .then(setRows)
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  }, [org.id, assessment.id]);

  function handleStatusChange(id: string, newStatus: string) {
    setRows((prev) =>
      prev.map((r) => (r.id === id ? { ...r, status: newStatus } : r))
    );
  }

  if (loading) return <div className="loading">Loading control states…</div>;
  if (error) return <div className="error-msg">Error: {error}</div>;

  const byFamily = rows.reduce<Record<string, ControlStateRow[]>>((acc, row) => {
    (acc[row.family] ??= []).push(row);
    return acc;
  }, {});

  const met = rows.filter((r) => r.status === "met").length;
  const families = FAMILY_ORDER.filter((f) => byFamily[f]);

  return (
    <div className="board">
      <div className="board-topbar">
        <div>
          <div className="board-title">{assessment.name}</div>
          <div className="board-meta">{org.name} · {assessment.status} · SPRS: {assessment.sprs_score ?? "—"}</div>
        </div>
        <div className="board-meta">{met} / {rows.length} objectives met</div>
      </div>

      {families.map((family) => (
        <FamilySection
          key={family}
          family={family}
          rows={byFamily[family]}
          orgId={org.id}
          assessmentId={assessment.id}
          onStatusChange={handleStatusChange}
        />
      ))}

      {families.length === 0 && (
        <div className="empty">No control states found — start the assessment to seed them.</div>
      )}
    </div>
  );
}
