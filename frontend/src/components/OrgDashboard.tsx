import { useEffect, useState } from "react";
import { api } from "../api";
import { canSeeAuditLog } from "../lib/roles";
import type {
  AuditLogRow,
  BlockedObjectiveItem,
  DashboardData,
  EvidenceExpiringItem,
  FamilyHeatmapEntry,
  PoamSummary,
  RaciBucket,
  ReviewQueueItem,
  SprsWidgetData,
  StatementProgress,
} from "../types";

interface Props {
  orgId: string;
  assessmentId: string;
  currentUserRole: string;
}

// Landing screen once an org + assessment are both resolved (G.3). Nine
// widgets from one GET call, except "Recent activity" — that one calls
// api.listAuditLog directly, unmodified, so it keeps its existing
// msp_admin-only gate (routers/audit_log.py) rather than needing this
// endpoint to leak or conditionally omit audit data per role. See
// backend/app/routers/dashboard.py's own module docstring for the same
// reasoning on the backend side.
export function OrgDashboard({ orgId, assessmentId, currentUserRole }: Props) {
  const [data, setData] = useState<DashboardData | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setData(null);
    setError(null);
    api.getDashboard(orgId, assessmentId).then(setData).catch((e: Error) => setError(e.message));
  }, [orgId, assessmentId]);

  if (error) {
    return (
      <div className="workspace-content">
        <div className="error-msg">Error loading dashboard: {error}</div>
      </div>
    );
  }
  if (!data) {
    return (
      <div className="workspace-content">
        <div className="loading">Loading dashboard…</div>
      </div>
    );
  }

  return (
    <div className="workspace-content dashboard-grid">
      <FamilyHeatmapCard entries={data.family_heatmap} />
      <SprsCard sprs={data.sprs} />
      <StatementProgressCard progress={data.statement_progress} />
      <EvidenceExpiringCard items={data.evidence_expiring} />
      <NeedsReviewCard items={data.needs_review} count={data.needs_review_count} />
      <BlockedObjectivesCard items={data.blocked_objectives} count={data.blocked_objectives_count} />
      <RaciOpenTasksCard buckets={data.raci_open_tasks} />
      <PoamSummaryCard summary={data.poam_summary} />
      {canSeeAuditLog(currentUserRole) && <RecentActivityCard orgId={orgId} />}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Widgets
// ---------------------------------------------------------------------------

function FamilyHeatmapCard({ entries }: { entries: FamilyHeatmapEntry[] }) {
  return (
    <div className="card">
      <h2>Family Completion</h2>
      {entries.length === 0 && <div className="empty">No controls in this assessment yet.</div>}
      <div className="heatmap-rows">
        {entries.map((e) => {
          const pct = e.controls_total > 0 ? (e.controls_met / e.controls_total) * 100 : 0;
          return (
            <div className="tier-bar" key={e.family}>
              <span className="tier-bar-label">{e.family}</span>
              <span className="tier-bar-counts">{e.controls_met} / {e.controls_total}</span>
              <div className="tier-bar-track">
                <div className="tier-bar-fill" style={{ width: `${pct}%` }} />
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function SprsCard({ sprs }: { sprs: SprsWidgetData }) {
  const first = sprs.trajectory[0]?.score;
  const last = sprs.trajectory[sprs.trajectory.length - 1]?.score;
  const trend = first !== undefined && last !== undefined ? last - first : null;
  return (
    <div className="card">
      <h2>SPRS Score</h2>
      <div className="dashboard-big-stat">{sprs.current_score ?? "—"}</div>
      {trend !== null && sprs.trajectory.length > 1 && (
        <div className="item-meta">
          {trend > 0 ? "▲" : trend < 0 ? "▼" : "—"} {trend > 0 ? "+" : ""}{trend} over {sprs.trajectory.length} recomputes
        </div>
      )}
      {sprs.trajectory.length === 0 && <div className="empty">No score history yet.</div>}
    </div>
  );
}

function StatementProgressCard({ progress }: { progress: StatementProgress }) {
  return (
    <div className="card">
      <h2>Statement Authoring</h2>
      <ul className="stat-list">
        <li><span>Approved</span><span>{progress.approved}</span></li>
        <li><span>Reviewed</span><span>{progress.reviewed}</span></li>
        <li><span>Draft</span><span>{progress.draft}</span></li>
        <li><span>Not started</span><span>{progress.not_started}</span></li>
      </ul>
    </div>
  );
}

function EvidenceExpiringCard({ items }: { items: EvidenceExpiringItem[] }) {
  return (
    <div className="card">
      <h2>Evidence Expiring (30 days)</h2>
      {items.length === 0 && <div className="empty">Nothing expiring soon.</div>}
      <ul className="item-list">
        {items.map((item) => (
          <li className="item-row" key={item.task_id}>
            <div>
              <div className="item-name">{item.title}</div>
              <div className="item-meta">Expires {new Date(item.expires_at).toLocaleDateString()}</div>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

function NeedsReviewCard({ items, count }: { items: ReviewQueueItem[]; count: number }) {
  return (
    <div className="card">
      <h2>Needs Review ({count})</h2>
      {items.length === 0 && <div className="empty">Nothing waiting on review.</div>}
      <ul className="item-list">
        {items.map((item) => (
          <li className="item-row" key={item.control_state_id}>
            <div>
              <div className="item-name">{item.control_id}[{item.objective_key}]</div>
              <div className="item-meta">{item.family}</div>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

function BlockedObjectivesCard({ items, count }: { items: BlockedObjectiveItem[]; count: number }) {
  return (
    <div className="card">
      <h2>Blocked Objectives ({count})</h2>
      {items.length === 0 && <div className="empty">Nothing pending evidence with no artifact attached.</div>}
      <ul className="item-list">
        {items.map((item) => (
          <li className="item-row" key={item.control_state_id}>
            <div>
              <div className="item-name">{item.control_id}[{item.objective_key}]</div>
              <div className="item-meta">{item.family} — awaiting first evidence</div>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

function RaciOpenTasksCard({ buckets }: { buckets: RaciBucket[] }) {
  return (
    <div className="card">
      <h2>Open Tasks by Owner</h2>
      {buckets.length === 0 && <div className="empty">No open evidence tasks.</div>}
      <ul className="item-list">
        {buckets.map((b) => (
          <li className="item-row" key={b.contact_id ?? "unassigned"}>
            <div className="item-name">{b.contact_name ?? "Unassigned"}</div>
            <span className="role-badge">{b.open_task_count}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function PoamSummaryCard({ summary }: { summary: PoamSummary }) {
  return (
    <div className="card">
      <h2>POA&amp;M Summary</h2>
      <ul className="stat-list">
        <li><span>Open</span><span>{summary.open}</span></li>
        <li><span>On track</span><span>{summary.on_track}</span></li>
        <li><span>Delayed</span><span>{summary.delayed}</span></li>
        <li><span>Completed</span><span>{summary.completed}</span></li>
        <li><span>Cancelled</span><span>{summary.cancelled}</span></li>
      </ul>
    </div>
  );
}

function RecentActivityCard({ orgId }: { orgId: string }) {
  const [rows, setRows] = useState<AuditLogRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.listAuditLog(orgId, { limit: 10 })
      .then((page) => setRows(page.items))
      .catch((e: Error) => setError(e.message));
  }, [orgId]);

  return (
    <div className="card">
      <h2>Recent Activity</h2>
      {error && <div className="error-msg">{error}</div>}
      {rows === null && !error && <div className="loading">Loading…</div>}
      {rows !== null && rows.length === 0 && <div className="empty">No activity yet.</div>}
      {rows !== null && rows.length > 0 && (
        <ul className="item-list">
          {rows.map((r) => (
            <li className="item-row" key={r.id}>
              <div>
                <div className="item-name">{r.action}</div>
                <div className="item-meta">{new Date(r.created_at).toLocaleString()}</div>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
