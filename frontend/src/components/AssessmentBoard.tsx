import { useEffect, useState } from "react";
import { api } from "../api";
import type { Assessment, ControlStateRow, Org } from "../types";
import { ControlDrawer } from "./ControlDrawer";
import { FamilySection } from "./FamilySection";
import { ProductsPanel } from "./ProductsPanel";

const FAMILY_ORDER = [
  "AC", "AT", "AU", "CM", "IA", "IR", "MA", "MP", "PS", "PE", "RA", "CA", "SC", "SI",
];

// ---------------------------------------------------------------------------
// SPRS: always computed from ALL rows, never from the filtered view
// ---------------------------------------------------------------------------

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

// ---------------------------------------------------------------------------
// Weight-tier summary: control-level rollup (same logic as SPRS)
// ---------------------------------------------------------------------------

interface TierSummary {
  met: number;
  total: number;
}

function computeTierSummary(
  rows: ControlStateRow[],
  weight: number,
): TierSummary {
  const controls: Record<string, string[]> = {};
  for (const row of rows) {
    if (row.sprs_weight !== weight) continue;
    (controls[row.control_id] ??= []).push(row.status);
  }
  let met = 0;
  for (const statuses of Object.values(controls)) {
    if (statuses.every((s) => s === "met" || s === "inherited")) met++;
  }
  return { met, total: Object.keys(controls).length };
}

// ---------------------------------------------------------------------------
// Filter helpers
// ---------------------------------------------------------------------------

type WeightFilter = 1 | 3 | 5 | null;
type LevelFilter = "l1" | "l2only" | null;

function applyFilters(
  rows: ControlStateRow[],
  weight: WeightFilter,
  level: LevelFilter,
  status: string | null,
  resp: string | null,
): ControlStateRow[] {
  return rows.filter((r) => {
    if (weight !== null && r.sprs_weight !== weight) return false;
    if (level === "l1" && !r.is_level_1) return false;
    if (level === "l2only" && r.is_level_1) return false;
    if (status !== null && r.status !== status) return false;
    if (resp !== null && r.responsibility !== resp) return false;
    return true;
  });
}

// ---------------------------------------------------------------------------
// Small presentational components
// ---------------------------------------------------------------------------

interface ChipProps {
  label: string;
  active: boolean;
  onClick: () => void;
}

function Chip({ label, active, onClick }: ChipProps) {
  return (
    <button
      className={`filter-chip${active ? " filter-chip--active" : ""}`}
      onClick={onClick}
    >
      {label}
    </button>
  );
}

interface TierBarProps {
  label: string;
  summary: TierSummary;
}

function TierBar({ label, summary }: TierBarProps) {
  const pct = summary.total > 0 ? (summary.met / summary.total) * 100 : 0;
  return (
    <div className="tier-bar">
      <span className="tier-bar-label">{label}</span>
      <span className="tier-bar-counts">
        {summary.met} / {summary.total}
      </span>
      <div className="tier-bar-track">
        <div className="tier-bar-fill" style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main board
// ---------------------------------------------------------------------------

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

  // Filters
  const [weightFilter, setWeightFilter] = useState<WeightFilter>(null);
  const [levelFilter, setLevelFilter] = useState<LevelFilter>(null);
  const [statusFilter, setStatusFilter] = useState<string | null>(null);
  const [respFilter, setRespFilter] = useState<string | null>(null);
  const [sortByWeight, setSortByWeight] = useState(false);

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

  // SPRS always from all rows
  const sprs = rows.length > 0 ? computeSprsLive(rows) : (assessment.sprs_score ?? "—");
  const met = rows.filter((r) => r.status === "met").length;

  // Tier summaries always from all rows
  const tier5 = computeTierSummary(rows, 5);
  const tier3 = computeTierSummary(rows, 3);
  const tier1 = computeTierSummary(rows, 1);

  // Filtered + sorted display rows
  const filtered = applyFilters(rows, weightFilter, levelFilter, statusFilter, respFilter);
  const displayRows = sortByWeight
    ? [...filtered].sort((a, b) => b.sprs_weight - a.sprs_weight || a.control_id.localeCompare(b.control_id))
    : filtered;

  const byFamily = displayRows.reduce<Record<string, ControlStateRow[]>>((acc, row) => {
    (acc[row.family] ??= []).push(row);
    return acc;
  }, {});

  const families = FAMILY_ORDER.filter((f) => byFamily[f]);

  const filtersActive =
    weightFilter !== null || levelFilter !== null || statusFilter !== null || respFilter !== null;

  // Toggle helpers for single-select chip groups
  function toggleWeight(w: WeightFilter) {
    setWeightFilter((prev) => (prev === w ? null : w));
  }
  function toggleLevel(l: LevelFilter) {
    setLevelFilter((prev) => (prev === l ? null : l));
  }
  function toggleStatus(s: string) {
    setStatusFilter((prev) => (prev === s ? null : s));
  }
  function toggleResp(r: string) {
    setRespFilter((prev) => (prev === r ? null : r));
  }

  return (
    <>
      <div className="board">
        {/* ── Topbar ── */}
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

        {/* ── Tier summary ── */}
        {rows.length > 0 && (
          <div className="tier-summary">
            <TierBar label="5-pt" summary={tier5} />
            <TierBar label="3-pt" summary={tier3} />
            <TierBar label="1-pt" summary={tier1} />
          </div>
        )}

        {/* ── Filter bar ── */}
        <div className="filter-bar">
          <div className="filter-group">
            <span className="filter-group-label">Weight</span>
            <Chip label="5-pt" active={weightFilter === 5} onClick={() => toggleWeight(5)} />
            <Chip label="3-pt" active={weightFilter === 3} onClick={() => toggleWeight(3)} />
            <Chip label="1-pt" active={weightFilter === 1} onClick={() => toggleWeight(1)} />
          </div>
          <div className="filter-group">
            <span className="filter-group-label">Level</span>
            <Chip label="L1 only" active={levelFilter === "l1"} onClick={() => toggleLevel("l1")} />
            <Chip label="L2 above L1" active={levelFilter === "l2only"} onClick={() => toggleLevel("l2only")} />
          </div>
          <div className="filter-group">
            <span className="filter-group-label">Status</span>
            <Chip label="Not met" active={statusFilter === "not_met"} onClick={() => toggleStatus("not_met")} />
            <Chip label="Partial" active={statusFilter === "partial"} onClick={() => toggleStatus("partial")} />
            <Chip label="Pending" active={statusFilter === "pending_evidence"} onClick={() => toggleStatus("pending_evidence")} />
            <Chip label="Met" active={statusFilter === "met"} onClick={() => toggleStatus("met")} />
          </div>
          <div className="filter-group">
            <span className="filter-group-label">Resp.</span>
            <Chip label="Customer" active={respFilter === "customer_owns"} onClick={() => toggleResp("customer_owns")} />
            <Chip label="Provider" active={respFilter === "provider_satisfies"} onClick={() => toggleResp("provider_satisfies")} />
            <Chip label="Shared" active={respFilter === "shared"} onClick={() => toggleResp("shared")} />
          </div>
          <div className="filter-group filter-group--sort">
            <button
              className={`filter-chip filter-chip--sort${sortByWeight ? " filter-chip--active" : ""}`}
              onClick={() => setSortByWeight((v) => !v)}
            >
              Weight ↓
            </button>
            {filtersActive && (
              <button
                className="filter-chip filter-chip--clear"
                onClick={() => {
                  setWeightFilter(null);
                  setLevelFilter(null);
                  setStatusFilter(null);
                  setRespFilter(null);
                }}
              >
                Clear
              </button>
            )}
          </div>
        </div>

        {/* ── Family sections ── */}
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

        {rows.length === 0 && (
          <div className="empty">
            No control states found — start the assessment to seed them.
          </div>
        )}

        {rows.length > 0 && families.length === 0 && (
          <div className="empty">
            No controls match the active filters.
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
