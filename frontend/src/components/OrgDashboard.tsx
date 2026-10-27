import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { defaultDashboardView, type DashboardView } from "../lib/dashboardView";
import { canSeeAuditLog } from "../lib/roles";
import { donutSlices } from "../lib/raciLoad";
import type {
  Assessment,
  AuditLogRow,
  BlockedObjectiveItem,
  DashboardData,
  DocumentReviewWidgetData,
  EnvironmentData,
  EvidenceExpiringItem,
  FamilyHeatmapEntry,
  PoamSummary,
  RaciBucket,
  RaciLoadWidgetData,
  ReviewQueueItem,
  SprsSubmission,
  StatementProgress,
} from "../types";
import { SprsCard } from "./dashboardCards";
import { EnvironmentDashboard, type DashboardNavTarget } from "./EnvironmentDashboard";

interface Props {
  orgId: string;
  // null when the caller (App.tsx) hasn't already established a specific
  // assessment for this org — e.g. entering via the header's settings
  // gear, which never selects one. In that case this component resolves
  // its own default (see the auto-select effect below) rather than
  // showing a dead end.
  assessmentId: string | null;
  currentUserRole: string;
  // Lifts the switcher's selection back up to App.tsx's own `assessment`
  // state (the same state AssessmentBoard/ProductsPanel/the breadcrumb
  // read) — switching here keeps every nav category showing the same
  // assessment, rather than the dashboard silently diverging from what
  // "Assessments"/"Tools" would show if the user clicked over.
  onSwitchAssessment: (assessment: Assessment) => void;
  // Quick links and the environment view's "see all" links (Q.0).
  onNavigate: (target: DashboardNavTarget) => void;
}

function mostRecentFirst(assessments: Assessment[]): Assessment[] {
  return [...assessments].sort(
    (a, b) => new Date(b.last_activity_at).getTime() - new Date(a.last_activity_at).getTime()
  );
}

function mostRecentlyActive(assessments: Assessment[]): Assessment {
  return mostRecentFirst(assessments)[0];
}

// Landing screen once an org is open (G.3; two views since Q.0). The
// assessment view is G.3's widgets on a deliberate grid; the environment
// view (EnvironmentDashboard.tsx) is the engineer's, with its own
// org-scoped GET. Which one a role lands on: lib/dashboardView.ts.
//
// The assessment view: nine widgets from one GET call,
// except "Recent activity" — that one calls api.listAuditLog directly,
// unmodified, so it keeps its existing msp_admin-only gate
// (routers/audit_log.py) rather than needing this endpoint to leak or
// conditionally omit audit data per role. See
// backend/app/routers/dashboard.py's own module docstring for the same
// reasoning on the backend side.
//
// G.4: defaults to the most-recently-active assessment ONLY when App.tsx
// didn't already have one selected (assessmentId === null) — it never
// overrides an assessment already established by OrgPicker (an explicit
// click, or OrgPicker's own separate "last opened in this browser"
// localStorage cache). Confirmed with the user before implementing rather
// than picking silently, since "always default to freshest on mount"
// would have silently swapped away from a choice the user just made.
export function OrgDashboard({
  orgId,
  assessmentId,
  currentUserRole,
  onSwitchAssessment,
  onNavigate,
}: Props) {
  // Q.0: a default per role (lib/dashboardView.ts), switchable by anyone,
  // not remembered. Per-user layout persistence is roadmap Q, not Q.0.
  // `chosenView` is null until someone clicks the toggle.
  const [chosenView, setView] = useState<DashboardView | null>(null);
  const [assessments, setAssessments] = useState<Assessment[] | null>(null);
  // An org with no assessment opens on the environment view whatever the
  // role: the assessment view would have nothing on it but a sentence.
  const view: DashboardView =
    chosenView ??
    (assessments !== null && assessments.length === 0
      ? "environment"
      : defaultDashboardView(currentUserRole));
  const [data, setData] = useState<DashboardData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [environment, setEnvironment] = useState<EnvironmentData | null>(null);
  const [environmentError, setEnvironmentError] = useState<string | null>(null);
  // Guards the auto-default effect from firing more than once per org
  // mount — without it, a user who explicitly switches away right after
  // the auto-default runs could get overridden again on the next
  // assessments-list refetch/re-render.
  const autoSelectedRef = useRef(false);

  useEffect(() => {
    setAssessments(null);
    setView(null);
    autoSelectedRef.current = false;
    api.getAssessments(orgId).then(setAssessments).catch(() => setAssessments([]));
  }, [orgId]);

  useEffect(() => {
    if (assessmentId !== null || assessments === null || autoSelectedRef.current) return;
    if (assessments.length === 0) return;
    autoSelectedRef.current = true;
    onSwitchAssessment(mostRecentlyActive(assessments));
  }, [assessmentId, assessments, onSwitchAssessment]);

  useEffect(() => {
    if (assessmentId === null) {
      setData(null);
      return;
    }
    setData(null);
    setError(null);
    api.getDashboard(orgId, assessmentId).then(setData).catch((e: Error) => setError(e.message));
  }, [orgId, assessmentId]);

  useEffect(() => {
    if (view !== "environment") return;
    setEnvironment(null);
    setEnvironmentError(null);
    api.getEnvironment(orgId).then(setEnvironment).catch((e: Error) => setEnvironmentError(e.message));
  }, [orgId, view]);

  const header = (
    <div className="dashboard-view-switch">
      <div className="view-toggle" role="group" aria-label="Dashboard view">
        <button type="button" aria-pressed={view === "environment"} onClick={() => setView("environment")}>
          Environment
        </button>
        <button type="button" aria-pressed={view === "assessment"} onClick={() => setView("assessment")}>
          Assessment
        </button>
      </div>
      {assessments && assessments.length > 1 && assessmentId !== null && (
        <AssessmentSwitcher
          assessments={assessments}
          selectedId={assessmentId}
          onSelect={onSwitchAssessment}
        />
      )}
    </div>
  );

  // Still resolving which assessment this is, if there is one: the
  // auto-select effect above runs after render.
  const resolving =
    assessments === null || (assessmentId === null && assessments.length > 0);

  if (view === "environment") {
    // The environment view needs no assessment (routers/environment.py);
    // it waits only for one that exists, so a tenant with an assessment
    // never flashes "no assessment yet".
    const waitingForAssessment = assessmentId !== null && data === null && error === null;
    return (
      <div className="workspace-content">
        {header}
        {environmentError && (
          <div className="error-msg">Error loading dashboard: {environmentError}</div>
        )}
        {error && <div className="error-msg">Error loading the assessment: {error}</div>}
        {!environmentError && (environment === null || resolving || waitingForAssessment) ? (
          <div className="loading">Loading dashboard…</div>
        ) : (
          environment && (
            <EnvironmentDashboard
              orgId={orgId}
              environment={environment}
              assessmentData={data}
              assessmentId={assessmentId}
              currentUserRole={currentUserRole}
              onNavigate={onNavigate}
            />
          )
        )}
      </div>
    );
  }

  if (assessmentId === null) {
    return (
      <div className="workspace-content">
        {header}
        {assessments !== null && assessments.length === 0 ? (
          <div className="empty">
            No assessments yet — start one from the org picker. The Environment view shows this
            org's boundary without one.
          </div>
        ) : (
          <div className="loading">Loading dashboard…</div>
        )}
      </div>
    );
  }

  if (error) {
    return (
      <div className="workspace-content">
        {header}
        <div className="error-msg">Error loading dashboard: {error}</div>
      </div>
    );
  }
  if (!data) {
    return (
      <div className="workspace-content">
        {header}
        <div className="loading">Loading dashboard…</div>
      </div>
    );
  }

  const showActivity = canSeeAuditLog(currentUserRole);
  // The assessment view (Q.0's layout, styles.css `.dash-assessment`).
  // The family radar moved to the environment view; the bar list stays
  // here, where its per-family numbers are read.
  return (
    <div className="workspace-content">
      {header}
      <div className={`dash-assessment${showActivity ? " dash-assessment--with-activity" : ""}`}>
        <div className="dash-col dash-col-left">
          <SprsCard sprs={data.sprs} />
          <SprsSubmissionCard orgId={orgId} />
          <StatementProgressCard progress={data.statement_progress} />
        </div>
        <div className="dash-area-family"><FamilyHeatmapCard entries={data.family_heatmap} /></div>
        <div className="dash-col dash-col-right">
          <NeedsReviewCard items={data.needs_review} count={data.needs_review_count} />
          <BlockedObjectivesCard items={data.blocked_objectives} count={data.blocked_objectives_count} />
          <EvidenceExpiringCard items={data.evidence_expiring} />
        </div>
        <div className="dash-area-docs"><DocumentReviewCard data={data.document_reviews} /></div>
        <div className="dash-area-poam"><PoamSummaryCard summary={data.poam_summary} /></div>
        <div className="dash-area-owners"><RaciOpenTasksCard buckets={data.raci_open_tasks} /></div>
        <div className="dash-area-racisplit"><RaciLoadSplitCard load={data.raci_load} /></div>
        <div className="dash-area-racicontact"><RaciLoadByContactCard load={data.raci_load} /></div>
        {showActivity && (
          <div className="dash-area-activity"><RecentActivityCard orgId={orgId} /></div>
        )}
      </div>
    </div>
  );
}

function AssessmentSwitcher({
  assessments,
  selectedId,
  onSelect,
}: {
  assessments: Assessment[];
  selectedId: string;
  onSelect: (assessment: Assessment) => void;
}) {
  const sorted = mostRecentFirst(assessments);
  return (
    <div className="assessment-switcher">
      <label htmlFor="assessment-switcher-select">Assessment</label>
      <select
        id="assessment-switcher-select"
        value={selectedId}
        onChange={(e) => {
          const next = assessments.find((a) => a.id === e.target.value);
          if (next) onSelect(next);
        }}
      >
        {sorted.map((a) => (
          <option key={a.id} value={a.id}>{a.name}</option>
        ))}
      </select>
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

// Donut geometry (G.7 Part 4 — "MSP vs customer, one number people care
// about" is the headline; see RaciLoadWidgetData's own comment for why
// this counts only Responsible assignments, not every RACI letter).
const DONUT_RADIUS = 40;
const DONUT_STROKE = 14;
const DONUT_SIZE = (DONUT_RADIUS + DONUT_STROKE) * 2;
const DONUT_CIRCUMFERENCE = 2 * Math.PI * DONUT_RADIUS;

function RaciLoadSplitCard({ load }: { load: RaciLoadWidgetData }) {
  const total = load.msp_count + load.customer_count + load.other_count;
  const counts = [
    { key: "msp", label: "MSP", count: load.msp_count },
    { key: "customer", label: "Customer", count: load.customer_count },
    { key: "other", label: "Other", count: load.other_count },
  ].filter((c) => c.count > 0);
  const slices = donutSlices(counts, DONUT_CIRCUMFERENCE);
  const center = DONUT_SIZE / 2;

  return (
    <div className="card">
      <h2>MSP vs Customer Load</h2>
      <p className="field-hint">Responsible (R) assignments only — an Informed or Consulted role isn't the same effort as doing the work.</p>
      {total === 0 ? (
        <div className="empty">No Responsible assignments yet.</div>
      ) : (
        <>
          <svg
            viewBox={`0 0 ${DONUT_SIZE} ${DONUT_SIZE}`}
            className="donut-chart"
            role="img"
            aria-label={
              `Responsible-assignment split: ${load.msp_count} MSP, ` +
              `${load.customer_count} customer, ${load.other_count} other`
            }
          >
            <circle
              cx={center} cy={center} r={DONUT_RADIUS}
              className="donut-ring-bg" strokeWidth={DONUT_STROKE} fill="none"
            />
            {slices.map((s) => (
              <circle
                key={s.key}
                cx={center}
                cy={center}
                r={DONUT_RADIUS}
                fill="none"
                strokeWidth={DONUT_STROKE}
                strokeDasharray={s.dashArray}
                strokeDashoffset={s.dashOffset}
                transform={`rotate(-90 ${center} ${center})`}
                className={`donut-slice donut-slice--${s.key}`}
              />
            ))}
            <text
              x={center} y={center} textAnchor="middle" dominantBaseline="middle"
              className="donut-center-label"
            >
              {total}
            </text>
          </svg>
          <ul className="donut-legend">
            {counts.map((c) => (
              <li className="donut-legend-item" key={c.key}>
                <span className={`donut-legend-swatch donut-legend-swatch--${c.key}`} />
                {c.label}: {c.count}
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}

function RaciLoadByContactCard({ load }: { load: RaciLoadWidgetData }) {
  // by_contact is already sorted desc by the backend (routers/dashboard.py's
  // own "server does the rollup" convention) -- the first entry is the max.
  const maxCount = load.by_contact[0]?.count ?? 0;
  return (
    <div className="card">
      <h2>Responsible Load by Contact</h2>
      <p className="field-hint">
        Horizontal bars, not a pie — a dozen contacts in a pie can't be ranked by eye.
      </p>
      {load.by_contact.length === 0 && <div className="empty">No Responsible assignments yet.</div>}
      <div className="heatmap-rows">
        {load.by_contact.map((c) => {
          const pct = maxCount > 0 ? (c.count / maxCount) * 100 : 0;
          return (
            <div className="tier-bar" key={c.contact_id}>
              <span className="tier-bar-label tier-bar-label--wide" title={c.contact_name}>
                {c.contact_name}
              </span>
              <span className="tier-bar-counts">{c.count}</span>
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

function DocumentReviewCard({ data }: { data: DocumentReviewWidgetData }) {
  const total = data.overdue_count + data.due_soon_count + data.never_approved_count;
  return (
    <div className="card">
      <h2>Document Reviews</h2>
      {total === 0 ? (
        <p className="field-hint">Every document is within its review cadence.</p>
      ) : (
        <>
          <ul className="stat-list">
            <li><span>Overdue</span><span>{data.overdue_count}</span></li>
            <li><span>Due soon</span><span>{data.due_soon_count}</span></li>
            <li><span>Never approved</span><span>{data.never_approved_count}</span></li>
          </ul>
          <ul className="mini-list">
            {data.items.map((i) => (
              <li key={i.document_id}>
                <span className={`doc-review doc-review-${i.status}`}>
                  {i.status.replace(/_/g, " ")}
                </span>{" "}
                <strong>{i.doc_id}</strong> {i.title}
              </li>
            ))}
          </ul>
          <p className="field-hint">
            An overdue review is a finding, not an expiry: the document stays approved and
            its evidence stays attached until a person decides otherwise.
          </p>
        </>
      )}
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

// Org-scoped, not assessment-scoped -- fetched via its own call rather
// than folded into the one assessment-scoped dashboard GET, same
// reasoning RecentActivityCard below already established (a widget with
// a genuinely different scope from the other nine gets its own fetch,
// not a forced fit into the shared round-trip). What was actually FILED
// with SPRS, never to be confused with the SprsCard above (WinGRC's
// computed score).
function SprsSubmissionCard({ orgId }: { orgId: string }) {
  const [submission, setSubmission] = useState<SprsSubmission | null | undefined>(undefined);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.getCurrentSprsSubmission(orgId)
      .then(setSubmission)
      .catch((e: Error) => setError(e.message));
  }, [orgId]);

  return (
    <div className="card">
      <h2>SPRS Submission</h2>
      {error && <div className="error-msg">{error}</div>}
      {submission === undefined && !error && <div className="loading">Loading…</div>}
      {submission === null && (
        <div className="empty">No SPRS submission on file yet.</div>
      )}
      {submission && (
        <ul className="stat-list">
          <li><span>Score</span><span>{submission.score}</span></li>
          <li><span>Filed</span><span>{submission.submitted_date}</span></li>
          <li><span>Submitted by</span><span>{submission.submitted_by_name}</span></li>
        </ul>
      )}
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
