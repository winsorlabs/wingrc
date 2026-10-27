import { useState } from "react";
import { api } from "../api";
import { assetDisplayName } from "../lib/assetDisplay";
import {
  dashboardTaskRows,
  documentReviewTotal,
  TASK_SOURCE_LABELS,
} from "../lib/dashboardTasks";
import { canSeeAuditLog } from "../lib/roles";
import type {
  DashboardData,
  EnvironmentAssessmentRef,
  EnvironmentData,
  EnvironmentOrgInfo,
  ScopeEntity,
  ScopeListWidgetData,
} from "../types";
import { FamilyRadarCard, SprsCard } from "./dashboardCards";
import type { LibraryTab, NavCategory, ScopeTab, SecurityTab } from "./SideNav";

export interface DashboardNavTarget {
  category: NavCategory;
  scopeTab?: ScopeTab;
  securityTab?: SecurityTab;
  libraryTab?: LibraryTab;
}

interface Props {
  orgId: string;
  environment: EnvironmentData;
  // The selected assessment's dashboard, or null when the org has none:
  // the SPRS score, the radar and open evidence tasks are assessment data,
  // and everything else here is not (routers/environment.py).
  assessmentData: DashboardData | null;
  assessmentId: string | null;
  currentUserRole: string;
  onNavigate: (target: DashboardNavTarget) => void;
}

// The environment view (Q.0): what is in this org's boundary and what is
// waiting on a person. Layout in styles.css's `.env-grid`; the regions are
// named there. Every count is the server's COUNT(*), never rows.length.
export function EnvironmentDashboard({
  orgId,
  environment,
  assessmentData,
  assessmentId,
  currentUserRole,
  onNavigate,
}: Props) {
  const assessment = environment.assessments.find((a) => a.id === assessmentId) ?? null;
  return (
    <div className="env-grid">
      <div className="env-col env-col-left">
        {assessmentData ? (
          <SprsCard sprs={assessmentData.sprs} />
        ) : (
          <div className="card">
            <h2>SPRS Score</h2>
            <div className="dashboard-big-stat dashboard-big-stat--empty">—</div>
            <p className="field-hint">No assessment yet, so no score. Start one from the org picker.</p>
          </div>
        )}
        <FamilyRadarCard
          entries={assessmentData?.family_heatmap ?? []}
          emptyText={assessmentData ? undefined : "No assessment yet."}
        />
      </div>
      <div className="env-col env-col-centre">
        <PendingApprovalCard widget={environment.pending_approval} onNavigate={onNavigate} />
        <ScopeListCard
          orgId={orgId}
          title="People in boundary"
          hint="Scope people with access to this environment, not WinGRC logins."
          entityType="person"
          noun={["person", "people"]}
          widget={environment.people_in_boundary}
          outOfBoundary={environment.people_out_of_boundary}
          emptyText="No people in the boundary yet. Import the Authorized Users workbook or sync Liongard from Scope › Assets."
          onSeeAll={() => onNavigate({ category: "library", libraryTab: "lists" })}
          seeAllLabel="CUI Users list"
        />
        <ScopeListCard
          orgId={orgId}
          title="Devices in boundary"
          hint="Accepted into the CUI boundary. Pending and rejected devices are not counted here."
          entityType="device"
          noun={["device", "devices"]}
          widget={environment.devices_in_boundary}
          outOfBoundary={environment.devices_out_of_boundary}
          emptyText="No devices in the boundary yet. Add them from Scope › Assets, by workbook or Liongard."
          onSeeAll={() => onNavigate({ category: "scope", scopeTab: "assets" })}
          seeAllLabel="Assets"
        />
      </div>
      <div className="env-col env-col-org">
        <OrgInfoCard
          info={environment.org}
          assessment={assessment}
          onEditProfile={() => onNavigate({ category: "scope", scopeTab: "profile" })}
        />
      </div>
      <div className="env-area-tasks">
        <TasksCard environment={environment} assessmentData={assessmentData} />
      </div>
      <div className="env-area-links">
        <QuickLinks
          hasAssessment={assessmentId !== null}
          currentUserRole={currentUserRole}
          onNavigate={onNavigate}
        />
      </div>
    </div>
  );
}

function PendingApprovalCard({
  widget,
  onNavigate,
}: {
  widget: ScopeListWidgetData;
  onNavigate: (target: DashboardNavTarget) => void;
}) {
  return (
    <div className={`card env-pending${widget.total > 0 ? " env-pending--waiting" : ""}`}>
      <div className="env-pending-line">
        <h2>Waiting for approval ({widget.total})</h2>
        {widget.total === 0 ? (
          <span className="field-hint">Nothing waiting to be accepted.</span>
        ) : (
          <>
            <span className="env-pending-names">
              {widget.rows.slice(0, 3).map((r) => r.display_name).join(", ")}
            </span>
            {widget.total > 3 && <span className="env-pending-more">and {widget.total - 3} more</span>}
          </>
        )}
        <button
          type="button"
          className="btn-ghost btn-sm"
          onClick={() => onNavigate({ category: "scope", scopeTab: "liongard-sync" })}
        >
          Asset Approvals
        </button>
      </div>
    </div>
  );
}

function ScopeListCard({
  orgId,
  title,
  hint,
  entityType,
  noun,
  widget,
  outOfBoundary,
  emptyText,
  onSeeAll,
  seeAllLabel,
}: {
  orgId: string;
  title: string;
  hint: string;
  entityType: string;
  noun: [string, string];
  widget: ScopeListWidgetData;
  outOfBoundary: number;
  emptyText: string;
  onSeeAll: () => void;
  seeAllLabel: string;
}) {
  const [excluded, setExcluded] = useState<ScopeEntity[] | null>(null);
  const [excludedOpen, setExcludedOpen] = useState(false);
  const [excludedError, setExcludedError] = useState<string | null>(null);

  function toggleExcluded() {
    const next = !excludedOpen;
    setExcludedOpen(next);
    if (next && excluded === null) {
      api
        .getScopeFiltered(orgId, { entityType, status: "active", inBoundary: false, limit: 50 })
        .then(setExcluded)
        .catch((e: Error) => setExcludedError(e.message));
    }
  }

  return (
    <div className="card env-list-card">
      <div className="env-list-head">
        <h2>
          {title} <span className="env-count">{widget.total}</span>
        </h2>
        <p className="field-hint">{hint}</p>
      </div>
      <div className="env-list-scroll" tabIndex={0} aria-label={`${title}, ${widget.total}`}>
        {widget.rows.length === 0 ? (
          <div className="empty">{emptyText}</div>
        ) : (
          <ul className="env-list">
            {widget.rows.map((r) => (
              <li key={r.id}>
                <span className="env-list-name">{r.display_name}</span>
                {r.sub_label && <span className="env-list-sub">{r.sub_label}</span>}
                {r.scope_category && <span className="env-list-cat">{r.scope_category}</span>}
              </li>
            ))}
          </ul>
        )}
      </div>
      <div className="env-list-foot">
        {widget.total > widget.rows.length && (
          <span>
            Showing the first {widget.rows.length} of {widget.total}.{" "}
          </span>
        )}
        <button type="button" className="link-button" onClick={onSeeAll}>
          {seeAllLabel}
        </button>
        {outOfBoundary > 0 && (
          <>
            {" · "}
            <button
              type="button"
              className="link-button"
              aria-expanded={excludedOpen}
              onClick={toggleExcluded}
            >
              {outOfBoundary} {outOfBoundary === 1 ? noun[0] : noun[1]} outside the boundary
            </button>
          </>
        )}
      </div>
      {excludedOpen && (
        <div className="env-excluded">
          <p className="field-hint">
            Present in scope but not in the boundary: rejected at approval, or marked out of
            boundary. Not counted above.
          </p>
          {excludedError && <div className="error-msg">{excludedError}</div>}
          {excluded === null && !excludedError && <div className="loading">Loading…</div>}
          {excluded && (
            <ul className="env-list">
              {excluded.map((e) => (
                <li key={e.id}>
                  <span className="env-list-name">{assetDisplayName(e.attributes, e.natural_key)}</span>
                  <span className="env-list-sub">{e.natural_key}</span>
                </li>
              ))}
            </ul>
          )}
          {excluded && excluded.length < outOfBoundary && (
            <p className="field-hint">
              Showing {excluded.length} of {outOfBoundary}.
            </p>
          )}
        </div>
      )}
    </div>
  );
}

const ASSESSMENT_TYPE_LABELS: Record<string, string> = {
  self: "Self-assessment",
  third_party: "Third-party assessment",
  c3pao: "C3PAO assessment",
};

function OrgInfoCard({
  info,
  assessment,
  onEditProfile,
}: {
  info: EnvironmentOrgInfo;
  assessment: EnvironmentAssessmentRef | null;
  onEditProfile: () => void;
}) {
  const cityLine = [info.city, [info.state_or_province, info.postal_code].filter(Boolean).join(" ")]
    .filter(Boolean)
    .join(", ");
  const addressLines = [info.address_line1, info.address_line2, cityLine, info.country]
    .filter((l): l is string => !!l && l.trim() !== "");
  const hasAddress = [info.address_line1, info.address_line2, info.city, info.postal_code].some(Boolean);
  return (
    <div className="card env-org">
      <h2>Org Info</h2>
      <div className="env-org-name">{info.name}</div>
      <dl className="env-org-fields">
        <dt>Address</dt>
        <dd>
          {hasAddress ? (
            addressLines.map((l) => <div key={l}>{l}</div>)
          ) : (
            <span className="env-not-entered">Not entered</span>
          )}
        </dd>
        <OrgCode label="CAGE code" value={info.cage_code} required={info.missing_ssp_fields.includes("cage_code")} />
        <OrgCode label="UEI" value={info.uei} required={info.missing_ssp_fields.includes("uei")} />
        {info.phone_primary && (
          <>
            <dt>Phone</dt>
            <dd>{info.phone_primary}</dd>
          </>
        )}
        {info.website && (
          <>
            <dt>Website</dt>
            <dd className="env-org-wrap">{info.website}</dd>
          </>
        )}
        <dt>Assessment</dt>
        <dd>
          {assessment ? (
            <>
              <div>{assessment.framework_name}</div>
              <div className="env-org-sub">
                Version {assessment.framework_version}
                {" · "}
                {ASSESSMENT_TYPE_LABELS[assessment.assessment_type] ?? assessment.assessment_type}
              </div>
              <div className="env-org-sub">{assessment.name}</div>
            </>
          ) : (
            <span className="env-not-entered">No assessment yet</span>
          )}
        </dd>
      </dl>
      {info.missing_ssp_fields.length > 0 && (
        <p className="field-hint">CAGE code and UEI are required on the SSP header for a DoD submission.</p>
      )}
      <button type="button" className="btn-ghost btn-sm" onClick={onEditProfile}>
        {info.missing_ssp_fields.length > 0 || !hasAddress ? "Complete the org profile" : "Edit the org profile"}
      </button>
    </div>
  );
}

function OrgCode({ label, value, required }: { label: string; value: string | null; required: boolean }) {
  return (
    <>
      <dt>{label}</dt>
      <dd>
        {value ? (
          <span className="env-code">{value}</span>
        ) : (
          <span className={required ? "env-missing" : "env-not-entered"}>
            {required ? "Missing" : "Not entered"}
          </span>
        )}
      </dd>
    </>
  );
}

function TasksCard({
  environment,
  assessmentData,
}: {
  environment: EnvironmentData;
  assessmentData: DashboardData | null;
}) {
  const reviews = environment.document_reviews;
  const rows = dashboardTaskRows(reviews, assessmentData?.open_evidence_tasks ?? []);
  const reviewTotal = documentReviewTotal(reviews);
  const taskTotal = assessmentData?.open_evidence_tasks_count ?? 0;
  const total = reviewTotal + taskTotal;
  const shown = rows.length;
  return (
    <div className="card env-tasks">
      <div className="env-list-head">
        <h2>
          Document reviews and open evidence tasks <span className="env-count">{total}</span>
        </h2>
        <p className="field-hint">
          {reviewTotal} document {reviewTotal === 1 ? "review" : "reviews"} due or never approved
          {assessmentData
            ? `, ${taskTotal} open evidence ${taskTotal === 1 ? "task" : "tasks"} in this assessment.`
            : ". Evidence tasks appear once an assessment exists."}{" "}
          Not included here: POA&amp;M items and periodic review cycles.
        </p>
      </div>
      <div className="env-list-scroll" tabIndex={0} aria-label={`Document reviews and open evidence tasks, ${total}`}>
        {rows.length === 0 ? (
          <div className="empty">
            Nothing due: every document is within its review cadence
            {assessmentData ? " and no evidence task is open." : "."}
          </div>
        ) : (
          <ul className="env-list env-task-list">
            {rows.map((r) => (
              <li key={r.key}>
                <span className={`env-source env-source--${r.source}`}>{TASK_SOURCE_LABELS[r.source]}</span>
                <span className="env-list-name">{r.title}</span>
                <span className={r.source === "document_review" ? `doc-review doc-review-${r.status}` : "env-list-sub"}>
                  {r.detail}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>
      {shown < total && (
        <div className="env-list-foot">
          Showing {shown} of {total}: the first {reviews.items.length} reviews
          {assessmentData ? ` and ${assessmentData.open_evidence_tasks.length} tasks` : ""}. The
          Library and Assessments › Tasks list the rest.
        </div>
      )}
    </div>
  );
}

function QuickLinks({
  hasAssessment,
  currentUserRole,
  onNavigate,
}: {
  hasAssessment: boolean;
  currentUserRole: string;
  onNavigate: (target: DashboardNavTarget) => void;
}) {
  // A hidden link is a convenience, never a control: the Audit Log's own
  // endpoint refuses every role but msp_admin
  // (test_environment_dashboard.py, through HTTP).
  const links: { label: string; target: DashboardNavTarget; show: boolean }[] = [
    { label: "Document library", target: { category: "library" }, show: true },
    { label: "Tools", target: { category: "tools" }, show: hasAssessment },
    { label: "Assets", target: { category: "scope", scopeTab: "assets" }, show: true },
    { label: "Asset approvals", target: { category: "scope", scopeTab: "liongard-sync" }, show: true },
    { label: "Personnel & contacts", target: { category: "scope", scopeTab: "contacts" }, show: true },
    { label: "Org profile", target: { category: "scope", scopeTab: "profile" }, show: true },
    {
      label: "Audit log",
      target: { category: "security", securityTab: "audit-log" },
      show: canSeeAuditLog(currentUserRole),
    },
  ];
  return (
    <nav className="quick-links" aria-label="Quick links">
      <span className="quick-links-label">Quick links</span>
      {links
        .filter((l) => l.show)
        .map((l) => (
          <button key={l.label} type="button" className="quick-link" onClick={() => onNavigate(l.target)}>
            {l.label}
          </button>
        ))}
    </nav>
  );
}
