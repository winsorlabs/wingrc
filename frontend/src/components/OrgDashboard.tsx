import { useEffect, useState } from "react";
import { api } from "../api";
import { canSeeAuditLog } from "../lib/roles";
import { familyRadarPoints } from "../lib/radarChart";
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
      <FamilyRadarCard entries={data.family_heatmap} />
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

// 100 SVG user units = 100% completion. Padding leaves room for the axis
// labels (family abbreviations) outside the outer ring. Kept as plain
// constants, not props — this chart always plots the same fixed 14 axes,
// no configurability needed (per the request: "no need for complex
// interactivity").
const RADAR_SIZE = 100;
const RADAR_PADDING = 26;
const RADAR_CENTER = RADAR_SIZE + RADAR_PADDING;
const RADAR_RINGS = [25, 50, 75, 100];

function FamilyRadarCard({ entries }: { entries: FamilyHeatmapEntry[] }) {
  const points = familyRadarPoints(entries);
  const toScreen = (unitX: number, unitY: number) =>
    `${RADAR_CENTER + unitX * RADAR_SIZE},${RADAR_CENTER + unitY * RADAR_SIZE}`;
  const ringPoints = (pct: number) =>
    points
      .map((p) => toScreen((pct / 100) * Math.cos(p.angle), (pct / 100) * Math.sin(p.angle)))
      .join(" ");
  const dataPolygon = points.map((p) => toScreen(p.unitX, p.unitY)).join(" ");

  return (
    <div className="card">
      <h2>Family Completion (Radar)</h2>
      {entries.length === 0 && <div className="empty">No controls in this assessment yet.</div>}
      <svg
        viewBox={`0 0 ${RADAR_CENTER * 2} ${RADAR_CENTER * 2}`}
        className="radar-chart"
        role="img"
        aria-label="Per-family completion percentage, plotted as a radar chart across all 14 control families"
      >
        {RADAR_RINGS.map((pct) => (
          <polygon key={pct} points={ringPoints(pct)} className="radar-grid-ring" />
        ))}
        {points.map((p) => (
          <line
            key={p.family}
            x1={RADAR_CENTER}
            y1={RADAR_CENTER}
            x2={RADAR_CENTER + Math.cos(p.angle) * RADAR_SIZE}
            y2={RADAR_CENTER + Math.sin(p.angle) * RADAR_SIZE}
            className="radar-axis-line"
          />
        ))}
        <polygon points={dataPolygon} className="radar-data-polygon" />
        {points.map((p) => (
          <circle
            key={p.family}
            cx={RADAR_CENTER + p.unitX * RADAR_SIZE}
            cy={RADAR_CENTER + p.unitY * RADAR_SIZE}
            r={3}
            className="radar-data-point"
          >
            <title>{p.family}: {Math.round(p.pct)}%</title>
          </circle>
        ))}
        {points.map((p) => {
          const labelR = RADAR_SIZE + 14;
          return (
            <text
              key={p.family}
              x={RADAR_CENTER + Math.cos(p.angle) * labelR}
              y={RADAR_CENTER + Math.sin(p.angle) * labelR}
              className="radar-label"
              textAnchor="middle"
              dominantBaseline="middle"
            >
              {p.family}
            </text>
          );
        })}
      </svg>
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
