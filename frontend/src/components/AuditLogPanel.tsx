import { Fragment, useEffect, useState } from "react";
import { api } from "../api";
import type { AuditLogRow } from "../types";

const PAGE_SIZE = 50;

// Non-exhaustive suggestions for the action datalist only — mirrors
// audit.py's module docstring list as of this writing. Not enforced: the
// backend's action column is free text and accepts any value, this is
// purely an autocomplete convenience. If audit.py's list changes, this one
// drifting stale is a UX nit, not a correctness bug (unlike lib/roles.ts's
// READ_ONLY_ROLES, which mirrors an actual enforcement boundary).
const KNOWN_ACTIONS = [
  "org_product.activate",
  "org_product.deactivate",
  "control_state.update",
  "evidence_state_link.archive",
  "evidence_task.update",
  "evidence_task.archive",
  "implementation_statement.upsert",
  "user.invite",
  "user.role_change",
  "user.activation_change",
  "user.deactivate",
  "user.mfa_reset",
  "user.unlock",
  "user.password_reset_issued",
  "user.delete",
  "user.anonymize",
  "api_user.create",
  "api_token.create",
  "api_token.revoke",
  "bundle.export",
];

function formatDateTime(iso: string): string {
  return new Date(iso).toLocaleString();
}

interface Props {
  orgId: string;
}

export function AuditLogPanel({ orgId }: Props) {
  const [rows, setRows] = useState<AuditLogRow[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expandedId, setExpandedId] = useState<string | null>(null);

  const [actionFilter, setActionFilter] = useState("");
  const [actorFilter, setActorFilter] = useState("");
  const [ipFilter, setIpFilter] = useState("");
  const [startFilter, setStartFilter] = useState("");
  const [endFilter, setEndFilter] = useState("");

  function load(
    nextOffset: number,
    overrides?: { action: string; actor: string; ip: string; start: string; end: string }
  ) {
    const f = overrides ?? { action: actionFilter, actor: actorFilter, ip: ipFilter, start: startFilter, end: endFilter };
    setLoading(true);
    setError(null);
    setExpandedId(null);
    api
      .listAuditLog(orgId, {
        offset: nextOffset,
        limit: PAGE_SIZE,
        action: f.action.trim() || undefined,
        actor: f.actor.trim() || undefined,
        ip_address: f.ip.trim() || undefined,
        start: f.start ? new Date(f.start).toISOString() : undefined,
        end: f.end ? new Date(f.end).toISOString() : undefined,
      })
      .then((page) => {
        setRows(page.items);
        setTotal(page.total);
        setOffset(page.offset);
      })
      .catch(() => setError("Could not load audit log"))
      .finally(() => setLoading(false));
  }

  useEffect(() => {
    load(0);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [orgId]);

  function clearFilters() {
    setActionFilter("");
    setActorFilter("");
    setIpFilter("");
    setStartFilter("");
    setEndFilter("");
    load(0, { action: "", actor: "", ip: "", start: "", end: "" });
  }

  const ipFilterActive = ipFilter.trim().length > 0;
  const page = Math.floor(offset / PAGE_SIZE) + 1;
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <div className="audit-log-panel">
      <div className="form-grid audit-log-filters">
        <div className="form-field">
          <label>Action</label>
          <input
            list="audit-log-known-actions"
            value={actionFilter}
            onChange={(e) => setActionFilter(e.target.value)}
            placeholder="e.g. user.deactivate"
          />
          <datalist id="audit-log-known-actions">
            {KNOWN_ACTIONS.map((a) => (
              <option key={a} value={a} />
            ))}
          </datalist>
        </div>
        <div className="form-field">
          <label>Actor</label>
          <input
            value={actorFilter}
            onChange={(e) => setActorFilter(e.target.value)}
            placeholder="user id or 'system'"
          />
        </div>
        <div className="form-field">
          <label>Source IP</label>
          <input
            value={ipFilter}
            onChange={(e) => setIpFilter(e.target.value)}
            placeholder="e.g. 10.0.0"
          />
        </div>
        <div className="form-field">
          <label>From</label>
          <input
            type="datetime-local"
            value={startFilter}
            onChange={(e) => setStartFilter(e.target.value)}
          />
        </div>
        <div className="form-field">
          <label>To</label>
          <input
            type="datetime-local"
            value={endFilter}
            onChange={(e) => setEndFilter(e.target.value)}
          />
        </div>
      </div>

      {ipFilterActive && (
        <div className="field-hint">
          Rows with no recorded source IP (everything logged before IP capture was
          added, or logged outside a normal request) never match this filter — they
          are hidden while it's active, not shown as a false match. Clear the filter
          to see them again.
        </div>
      )}

      <div className="audit-log-filter-actions">
        <button
          className="btn-primary btn-sm"
          onClick={() => load(0)}
          disabled={loading}
        >
          Apply Filters
        </button>
        <button className="btn-ghost btn-sm" onClick={clearFilters} disabled={loading}>
          Clear
        </button>
      </div>

      {error && <div className="form-error">{error}</div>}

      {loading ? (
        <div className="loading">Loading audit log…</div>
      ) : rows.length === 0 ? (
        <div className="contacts-empty">No audit log entries match these filters.</div>
      ) : (
        <>
          <table className="contacts-table audit-log-table">
            <thead>
              <tr>
                <th>Time</th>
                <th>Actor</th>
                <th>Action</th>
                <th>Entity</th>
                <th>Source IP</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <Fragment key={r.id}>
                  <tr>
                    <td>{formatDateTime(r.created_at)}</td>
                    <td>
                      {r.actor} <span className="no-roles">({r.actor_type})</span>
                    </td>
                    <td>{r.action}</td>
                    <td title={r.entity_id}>
                      {r.entity_type} · {r.entity_id.slice(0, 8)}…
                    </td>
                    <td>
                      {r.ip_address ?? <span className="no-roles">Unknown</span>}
                    </td>
                    <td>
                      <button
                        className="btn-ghost btn-xs"
                        onClick={() => setExpandedId(expandedId === r.id ? null : r.id)}
                      >
                        {expandedId === r.id ? "Hide" : "Details"}
                      </button>
                    </td>
                  </tr>
                  {expandedId === r.id && (
                    <tr>
                      <td colSpan={6}>
                        <pre className="audit-log-detail">
                          {JSON.stringify(
                            { before: r.before_value, after: r.after_value, context: r.context },
                            null,
                            2
                          )}
                        </pre>
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>

          <div className="audit-log-pagination">
            <button
              className="btn-ghost btn-sm"
              onClick={() => load(Math.max(0, offset - PAGE_SIZE))}
              disabled={offset === 0 || loading}
            >
              ← Previous
            </button>
            <span>
              Page {page} of {pageCount} ({total} total)
            </span>
            <button
              className="btn-ghost btn-sm"
              onClick={() => load(offset + PAGE_SIZE)}
              disabled={offset + PAGE_SIZE >= total || loading}
            >
              Next →
            </button>
          </div>
        </>
      )}
    </div>
  );
}
