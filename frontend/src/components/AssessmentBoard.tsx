import { useEffect, useState } from "react";
import { api } from "../api";
import type { Assessment, ControlStateRow, Org } from "../types";
import { ControlDrawer } from "./ControlDrawer";
import { FamilySection } from "./FamilySection";
import { ProductsPanel } from "./ProductsPanel";

const FAMILY_ORDER = [
  "AC", "AT", "AU", "CM", "IA", "IR", "MA", "MP", "PS", "PE", "RA", "CA", "SC", "SI",
];

function computeSprsLive(rows: ControlStateRow[]): number {
  const controls: Record<string, { weight: number; statuses: string[] }> = {};
  for (const row of rows) {
    if (!controls[row.control_id]) {
      controls[row.control_id] = { weight: row.sprs_weight, statuses: [] };
    }
    controls[row.control_id].statuses.push(row.status);
  }
  let deductions = 0;
  for (const { weight, statuses } of Object.values(controls)) {
    if (!statuses.every((s) => s === "met" || s === "inherited")) {
      deductions += weight;
    }
  }
  return 110 - deductions;
}

interface DrawerControl {
  dbId: string;
  controlId: string;
  title: string;
}

interface Props {
  org: Org;
  assessment: Assessment;
}

export function AssessmentBoard({ org, assessment }: Props) {
  const [rows, setRows] = useState<ControlStateRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [drawerControl, setDrawerControl] = useState<DrawerControl | null>(null);
  const [showTools, setShowTools] = useState(false);

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

  function handleOpenDrawer(dbId: string, controlId: string, title: string) {
    setDrawerControl({ dbId, controlId, title });
  }

  function handleProductActivated() {
    setShowTools(false);
    setLoading(true);
    api
      .getControlStates(org.id, assessment.id)
      .then(setRows)
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  }

  function handleEvidenceChanged() {
    api
      .getControlStates(org.id, assessment.id)
      .then(setRows)
      .catch((e: Error) => setError(e.message));
  }

  function handleStatementSave(updates: Array<{ objectiveId: string; status: string }>) {
    const byId = Object.fromEntries(updates.map((u) => [u.objectiveId, u.status]));
    setRows((prev) =>
      prev.map((r) =>
        byId[r.objective_id] !== undefined
          ? { ...r, statement_status: byId[r.objective_id] }
          : r
      )
    );
  }

  if (loading) return <div className="loading">Loading control states…</div>;
  if (error) return <div className="error-msg">Error: {error}</div>;

  const byFamily = rows.reduce<Record<string, ControlStateRow[]>>((acc, row) => {
    (acc[row.family] ??= []).push(row);
    return acc;
  }, {});

  const met = rows.filter((r) => r.status === "met").length;
  const sprs = rows.length > 0 ? computeSprsLive(rows) : (assessment.sprs_score ?? "—");
  const families = FAMILY_ORDER.filter((f) => byFamily[f]);

  return (
    <>
      <div className="board">
        <div className="board-topbar">
          <div>
            <div className="board-title">{assessment.name}</div>
            <div className="board-meta">
              {org.name} · {assessment.status} · SPRS: {sprs}
            </div>
          </div>
          <div className="board-topbar-right">
            <div className="board-meta">{met} / {rows.length} objectives met</div>
            <button className="btn-ghost btn-sm" onClick={() => setShowTools(true)}>
              Tools &#x2699;
            </button>
          </div>
        </div>

        {families.map((family) => (
          <FamilySection
            key={family}
            family={family}
            rows={byFamily[family]}
            orgId={org.id}
            assessmentId={assessment.id}
            onStatusChange={handleStatusChange}
            onOpenDrawer={handleOpenDrawer}
          />
        ))}

        {families.length === 0 && (
          <div className="empty">
            No control states found — start the assessment to seed them.
          </div>
        )}
      </div>

      {showTools && (
        <ProductsPanel
          orgId={org.id}
          assessmentId={assessment.id}
          onClose={() => setShowTools(false)}
          onActivated={handleProductActivated}
        />
      )}

      {drawerControl && (
        <ControlDrawer
          orgId={org.id}
          assessmentId={assessment.id}
          controlDbId={drawerControl.dbId}
          controlId={drawerControl.controlId}
          controlTitle={drawerControl.title}
          onClose={() => setDrawerControl(null)}
          onSave={handleStatementSave}
          onEvidenceChanged={handleEvidenceChanged}
        />
      )}
    </>
  );
}
