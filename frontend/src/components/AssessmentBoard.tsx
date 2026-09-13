import { useEffect, useState } from "react";
import { api } from "../api";
import { FAMILY_ORDER } from "../lib/families";
import { applyFilters, clearFilters, filtersActive, toggleSetItem } from "../lib/filters";
import type { FilterOpts } from "../lib/filters";
import type { Assessment, ControlStateRow, Org } from "../types";
import { ControlDrawer } from "./ControlDrawer";
import { EvidenceTasksPanel } from "./EvidenceTasksPanel";
import { FamilySection } from "./FamilySection";

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

function computeTierSummary(rows: ControlStateRow[], weight: number): TierSummary {
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
      <span className="tier-bar-counts">{summary.met} / {summary.total}</span>
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
  canWrite: boolean;
  currentUserRole: string | null | undefined;
}

export function AssessmentBoard({ org, assessment, canWrite, currentUserRole }: Props) {
  const [rows, setRows] = useState<ControlStateRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [drawerControl, setDrawerControl] = useState<DrawerControl | null>(null);
  const [showTasks, setShowTasks] = useState(false);

  const [filters, setFilters] = useState<FilterOpts>(clearFilters());
  const [sortByWeight, setSortByWeight] = useState(false);
  const [generatingBundle, setGeneratingBundle] = useState(false);
  const [bundleError, setBundleError] = useState<string | null>(null);

  // Local override of status/submitted_at after Complete/Reopen -- same
  // "optimistic local update" pattern handleStatusChange already uses
  // for control-state rows, so a status change is reflected immediately
  // without waiting on the parent (App.tsx) to re-fetch the assessment
  // list. Seeded from the prop; only ever changed by this component's
  // own complete/reopen actions.
  const [status, setStatus] = useState(assessment.status);
  const [transitioning, setTransitioning] = useState(false);
  const [transitionError, setTransitionError] = useState<string | null>(null);
  const [showSubmissionPrompt, setShowSubmissionPrompt] = useState(false);

  async function handleComplete() {
    setTransitioning(true);
    setTransitionError(null);
    try {
      const updated = await api.completeAssessment(org.id, assessment.id);
      setStatus(updated.status);
      setShowSubmissionPrompt(true);
    } catch (err: unknown) {
      setTransitionError((err as Error).message);
    } finally {
      setTransitioning(false);
    }
  }

  async function handleReopen() {
    setTransitioning(true);
    setTransitionError(null);
    try {
      const updated = await api.reopenAssessment(org.id, assessment.id);
      setStatus(updated.status);
    } catch (err: unknown) {
      setTransitionError((err as Error).message);
    } finally {
      setTransitioning(false);
    }
  }

  useEffect(() => {
    setLoading(true);
    api
      .getControlStates(org.id, assessment.id)
      .then(setRows)
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  }, [org.id, assessment.id]);

  function handleStatusChange(id: string, newStatus: string) {
    setRows((prev) => prev.map((r) => (r.id === id ? { ...r, status: newStatus } : r)));
  }

  function handleOpenDrawer(dbId: string, controlId: string, title: string) {
    setDrawerControl({ dbId, controlId, title });
  }

  function handleEvidenceChanged() {
    api
      .getControlStates(org.id, assessment.id)
      .then(setRows)
      .catch((e: Error) => setError(e.message));
  }

  async function handleGenerateBundle() {
    setGeneratingBundle(true);
    setBundleError(null);
    try {
      await api.downloadBundle(org.id, assessment.id);
    } catch (err: unknown) {
      setBundleError((err as Error).message);
    } finally {
      setGeneratingBundle(false);
    }
  }

  function handleStatementSave(updates: Array<{ objectiveId: string; status: string }>) {
    const byId = Object.fromEntries(updates.map((u) => [u.objectiveId, u.status]));
    setRows((prev) =>
      prev.map((r) =>
        byId[r.objective_id] !== undefined ? { ...r, statement_status: byId[r.objective_id] } : r
      )
    );
  }

  if (loading) return <div className="loading">Loading control states…</div>;
  if (error) return <div className="error-msg">Error: {error}</div>;

  // SPRS and tier summaries always from ALL rows
  const sprs = rows.length > 0 ? computeSprsLive(rows) : (assessment.sprs_score ?? "—");
  const met = rows.filter((r) => r.status === "met").length;
  const tier5 = computeTierSummary(rows, 5);
  const tier3 = computeTierSummary(rows, 3);
  const tier1 = computeTierSummary(rows, 1);

  // Filtered + optionally sorted display rows
  const filtered = applyFilters(rows, filters);
  const displayRows = sortByWeight
    ? [...filtered].sort((a, b) => b.sprs_weight - a.sprs_weight || a.control_id.localeCompare(b.control_id))
    : filtered;

  const byFamily = displayRows.reduce<Record<string, ControlStateRow[]>>((acc, row) => {
    (acc[row.family] ??= []).push(row);
    return acc;
  }, {});
  const families = FAMILY_ORDER.filter((f) => byFamily[f]);

  const hasFilters = filtersActive(filters);

  return (
    <>
      <div className="board">
        {/* ── Topbar ── */}
        <div className="board-topbar">
          <div>
            <div className="board-title">{assessment.name}</div>
            <div className="board-meta">
              {org.name} · {status} · SPRS: {sprs}
            </div>
          </div>
          <div className="board-topbar-right">
            <div className="board-meta">{met} / {rows.length} objectives met</div>
            <button
              className="btn-ghost btn-sm"
              onClick={handleGenerateBundle}
              disabled={generatingBundle}
            >
              {generatingBundle ? "Generating…" : "Generate Assessor Bundle"}
            </button>
            <button className="btn-ghost btn-sm" onClick={() => setShowTasks(true)}>
              Tasks
            </button>
            {/* Gates nothing -- a recorded milestone only. Every action in
                this board keeps working identically regardless of status;
                see engine.py:complete_assessment's own docstring. */}
            {canWrite && status === "in_progress" && (
              <button className="btn-primary btn-sm" onClick={handleComplete} disabled={transitioning}>
                {transitioning ? "Completing…" : "Complete Assessment"}
              </button>
            )}
            {canWrite && status === "submitted" && (
              <button className="btn-ghost btn-sm" onClick={handleReopen} disabled={transitioning}>
                {transitioning ? "Reopening…" : "Reopen"}
              </button>
            )}
          </div>
        </div>

        {bundleError && (
          <div className="error-msg">Bundle export failed: {bundleError}</div>
        )}
        {transitionError && (
          <div className="error-msg">{transitionError}</div>
        )}

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
          {/* Weight: multi-select, OR within group */}
          <div className="filter-group">
            <span className="filter-group-label">Weight</span>
            {([5, 3, 1] as const).map((w) => (
              <Chip
                key={w}
                label={`${w}-pt`}
                active={filters.weights.has(w)}
                onClick={() => setFilters((f) => ({ ...f, weights: toggleSetItem(f.weights, w) }))}
              />
            ))}
          </div>

          {/* Level: single toggle — "L1" or all */}
          <div className="filter-group">
            <span className="filter-group-label">Level</span>
            <Chip
              label="L1 only"
              active={filters.l1Only}
              onClick={() => setFilters((f) => ({ ...f, l1Only: !f.l1Only }))}
            />
          </div>

          {/* Status: multi-select, OR within group */}
          <div className="filter-group">
            <span className="filter-group-label">Status</span>
            {(
              [
                ["not_met", "Not met"],
                ["partial", "Partial"],
                ["pending_evidence", "Pending"],
                ["met", "Met"],
              ] as const
            ).map(([val, label]) => (
              <Chip
                key={val}
                label={label}
                active={filters.statuses.has(val)}
                onClick={() =>
                  setFilters((f) => ({ ...f, statuses: toggleSetItem(f.statuses, val) }))
                }
              />
            ))}
          </div>

          {/* Responsibility: multi-select, OR within group */}
          <div className="filter-group">
            <span className="filter-group-label">Resp.</span>
            {(
              [
                ["customer_owns", "Customer"],
                ["provider_satisfies", "Provider"],
                ["shared", "Shared"],
              ] as const
            ).map(([val, label]) => (
              <Chip
                key={val}
                label={label}
                active={filters.resps.has(val)}
                onClick={() =>
                  setFilters((f) => ({ ...f, resps: toggleSetItem(f.resps, val) }))
                }
              />
            ))}
          </div>

          {/* Sort + Clear */}
          <div className="filter-group filter-group--sort">
            <button
              className={`filter-chip filter-chip--sort${sortByWeight ? " filter-chip--active" : ""}`}
              onClick={() => setSortByWeight((v) => !v)}
            >
              Weight ↓
            </button>
            {hasFilters && (
              <button
                className="filter-chip filter-chip--clear"
                onClick={() => setFilters(clearFilters())}
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
            canWrite={canWrite}
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
          <div className="empty">No controls match the active filters.</div>
        )}
      </div>

      {showTasks && (
        <EvidenceTasksPanel
          orgId={org.id}
          assessmentId={assessment.id}
          canWrite={canWrite}
          onClose={() => setShowTasks(false)}
        />
      )}

      {drawerControl && (
        <ControlDrawer
          orgId={org.id}
          assessmentId={assessment.id}
          controlDbId={drawerControl.dbId}
          controlId={drawerControl.controlId}
          controlTitle={drawerControl.title}
          canWrite={canWrite}
          currentUserRole={currentUserRole}
          onClose={() => setDrawerControl(null)}
          onSave={handleStatementSave}
          onEvidenceChanged={handleEvidenceChanged}
        />
      )}

      {showSubmissionPrompt && (
        <div className="drawer-overlay" onClick={() => setShowSubmissionPrompt(false)}>
          <div className="drawer" onClick={(e) => e.stopPropagation()}>
            <div className="drawer-header">
              <h3>Assessment completed</h3>
              <button
                className="drawer-close"
                onClick={() => setShowSubmissionPrompt(false)}
                aria-label="Close"
              >
                ×
              </button>
            </div>
            <div className="drawer-body">
              <p>
                This marks the WinGRC assessment cycle complete -- it does not file
                anything with SPRS. When you file this in SPRS, come back and
                record it under <strong>Scope → SPRS Submissions</strong>. You can
                do this any time; nothing here is time-sensitive.
              </p>
            </div>
            <div className="drawer-footer">
              <div style={{ flex: 1 }} />
              <button className="btn-primary" onClick={() => setShowSubmissionPrompt(false)}>
                Got it
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
