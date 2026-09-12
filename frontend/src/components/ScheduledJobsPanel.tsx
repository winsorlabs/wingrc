import { useEffect, useState } from "react";
import { api } from "../api";
import type { ScheduledJob } from "../types";

// Read-only admin visibility into the job scheduler (scheduler.py) --
// D.3's second infrastructure prerequisite, after outbound email. Manual
// "run now" and enable/disable are NOT here by design -- see
// routers/scheduled_jobs.py's own docstring; they're their own slice with
// their own authorization questions.
export function ScheduledJobsPanel() {
  const [jobs, setJobs] = useState<ScheduledJob[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  function load() {
    setLoading(true);
    setError(null);
    api
      .listScheduledJobs()
      .then(setJobs)
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  }

  useEffect(load, []);

  function statusBadgeClass(status: string): string {
    if (status === "succeeded") return "status-active";
    if (status === "failed") return "status-warning";
    return "status-inactive"; // running
  }

  return (
    <div className="scheduled-jobs-panel">
      <div className="products-panel-header" style={{ padding: 0, border: "none", marginBottom: "0.75rem" }}>
        <span className="products-panel-title">Scheduled Jobs</span>
      </div>

      {loading ? (
        <div className="loading">Loading scheduled jobs…</div>
      ) : error ? (
        <div className="form-error">{error}</div>
      ) : jobs.length === 0 ? (
        <div className="empty">No jobs registered.</div>
      ) : (
        <div className="table-scroll">
          <table className="contacts-table">
            <thead>
              <tr>
                <th>Job</th>
                <th>Interval</th>
                <th>Last run</th>
                <th>Outcome</th>
                <th>Next due</th>
                <th>Error</th>
              </tr>
            </thead>
            <tbody>
              {jobs.map((j) => (
                <tr key={j.job_name}>
                  <td><strong>{j.job_name}</strong></td>
                  <td>{formatInterval(j.interval_seconds)}</td>
                  <td>{j.last_run ? new Date(j.last_run.started_at).toLocaleString() : "Never run"}</td>
                  <td>
                    {j.last_run ? (
                      <span className={`status-badge ${statusBadgeClass(j.last_run.status)}`}>
                        {j.last_run.status}
                      </span>
                    ) : (
                      <span className="status-badge status-inactive">never run</span>
                    )}
                  </td>
                  <td>{j.next_due_at ? new Date(j.next_due_at).toLocaleString() : "Due now"}</td>
                  <td>{j.last_run?.status === "failed" ? j.last_run.error : ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function formatInterval(seconds: number): string {
  if (seconds % 3600 === 0) {
    const hours = seconds / 3600;
    return `Every ${hours} hour${hours === 1 ? "" : "s"}`;
  }
  if (seconds % 60 === 0) {
    const minutes = seconds / 60;
    return `Every ${minutes} minute${minutes === 1 ? "" : "s"}`;
  }
  return `Every ${seconds}s`;
}
