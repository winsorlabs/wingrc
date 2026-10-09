/**
 * Scope > Lists: the CMMC lists (3.1.1a Authorized Users, 3.1.1b Auth
 * Processes, 3.1.1c Authorized Devices, External Services) as live views
 * over the scope graph, with the same .xlsx export an assessor receives.
 *
 * A list is a projection, not a grid: rows are edited where entities are
 * edited (Assets, the asset drawer). The one exception is adding an
 * authorized process -- nothing discovers those, so without a manual entry
 * point 3.1.1b would stay empty forever.
 *
 * Cell values are decided server-side (backend list_projection.py), the
 * same function the export uses, so this screen and the workbook cannot
 * disagree about what a list says.
 */

import { useCallback, useEffect, useState } from "react";

import { api } from "../api";
import type { ListCell, ListViewData, ListViewSummary } from "../types";

const PROCESS_VIEW_ID = "3.1.1b-auth-processes";

function Cell({ cell }: { cell: ListCell }) {
  if (cell.placeholder_reason !== null) {
    return (
      <td className="list-cell-placeholder" title={`Known absent: ${cell.placeholder_reason}`}>
        {cell.value}
      </td>
    );
  }
  return <td>{cell.value || <span className="list-cell-empty">—</span>}</td>;
}

function AddProcessForm({
  orgId,
  onAdded,
  onCancel,
}: {
  orgId: string;
  onAdded: () => void;
  onCancel: () => void;
}) {
  const [name, setName] = useState("");
  const [runningOn, setRunningOn] = useState("");
  const [account, setAccount] = useState("");
  const [purpose, setPurpose] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit() {
    if (!name.trim()) {
      setError("Process name is required");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      // Keyed by the workbook's own headers, which is what the 3.1.1b view
      // reads -- the same shape a workbook import of that tab produces.
      await api.createScopeEntity(orgId, {
        entity_type: "process",
        natural_key: name.trim(),
        attributes: {
          "Process Name": name.trim(),
          "Running On": runningOn.trim() || null,
          "Associated Account": account.trim() || null,
          "Description / Purpose": purpose.trim() || null,
        },
      });
      onAdded();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not add process");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="list-add-process">
      <label>
        Process Name
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Liongard inspector agent" />
      </label>
      <label>
        Running On
        <input value={runningOn} onChange={(e) => setRunningOn(e.target.value)} placeholder="SRV-UTIL01" />
      </label>
      <label>
        Associated Account
        <input value={account} onChange={(e) => setAccount(e.target.value)} placeholder="svc-liongard" />
      </label>
      <label>
        Description / Purpose
        <input value={purpose} onChange={(e) => setPurpose(e.target.value)} />
      </label>
      {error && <div className="form-error">{error}</div>}
      <div className="list-add-process-actions">
        <button className="btn-primary btn-sm" disabled={busy} onClick={submit}>
          Add process
        </button>
        <button className="btn-ghost btn-sm" disabled={busy} onClick={onCancel}>
          Cancel
        </button>
      </div>
    </div>
  );
}

export function ListsPanel({ orgId, canWrite }: { orgId: string; canWrite: boolean }) {
  const [views, setViews] = useState<ListViewSummary[] | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [data, setData] = useState<ListViewData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);
  const [adding, setAdding] = useState(false);

  const loadViews = useCallback(async () => {
    try {
      const vs = await api.listViews(orgId);
      setViews(vs);
      setSelectedId((cur) => cur ?? vs[0]?.id ?? null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load lists");
    }
  }, [orgId]);

  const loadView = useCallback(async () => {
    if (!selectedId) return;
    setData(null);
    try {
      setData(await api.getListView(orgId, selectedId));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load list");
    }
  }, [orgId, selectedId]);

  useEffect(() => {
    loadViews();
  }, [loadViews]);

  useEffect(() => {
    setAdding(false);
    loadView();
  }, [loadView]);

  async function handleExport() {
    if (!selectedId) return;
    setExporting(true);
    setError(null);
    try {
      await api.exportListView(orgId, selectedId);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Export failed");
    } finally {
      setExporting(false);
    }
  }

  if (error && !views) return <div className="form-error">{error}</div>;
  if (!views) return <div className="loading">Loading lists…</div>;

  return (
    <div className="contacts-panel">
      <div className="list-view-tabs" role="tablist">
        {views.map((v) => (
          <button
            key={v.id}
            role="tab"
            aria-selected={v.id === selectedId}
            className={`btn-ghost btn-sm${v.id === selectedId ? " active" : ""}`}
            onClick={() => setSelectedId(v.id)}
          >
            {v.sheet_title} <span className="list-view-count">({v.row_count})</span>
          </button>
        ))}
      </div>

      {error && <div className="form-error">{error}</div>}

      {!data ? (
        <div className="loading">Loading list…</div>
      ) : (
        <>
          <div className="contacts-panel-header">
            <div>
              <h2>{data.title}</h2>
              <div className="contact-sub">
                {data.control_ids.join(" / ")} — projected from the live scope graph. Edit
                entries under Assets; this list follows.
              </div>
            </div>
            <div>
              {canWrite && data.id === PROCESS_VIEW_ID && !adding && (
                <button className="btn-ghost btn-sm" onClick={() => setAdding(true)}>
                  + Add process
                </button>
              )}
              <button className="btn-primary btn-sm" disabled={exporting} onClick={handleExport}>
                {exporting ? "Exporting…" : "Export .xlsx"}
              </button>
            </div>
          </div>

          {adding && (
            <AddProcessForm
              orgId={orgId}
              onCancel={() => setAdding(false)}
              onAdded={() => {
                setAdding(false);
                loadView();
                loadViews();
              }}
            />
          )}

          {data.excluded_note && (
            <div className="contact-sub list-excluded-note" role="note">
              {data.excluded_note}
            </div>
          )}

          {data.rows.length === 0 ? (
            <div className="contacts-empty">
              {data.empty_explanation || `No entries in ${data.sheet_title} yet.`}
            </div>
          ) : (
            <div className="table-scroll">
              <table className="contacts-table">
                <thead>
                  <tr>
                    {data.columns.map((c) => (
                      <th key={c}>{c}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {data.rows.map((r) => (
                    <tr key={r.natural_key}>
                      {r.cells.map((cell, i) => (
                        <Cell key={data.columns[i]} cell={cell} />
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <div className="contact-sub list-legend">
            <span className="list-cell-placeholder">[PLACEHOLDER - reason]</span> cells are known
            absent for the stated reason; — means nothing has been recorded.
          </div>
        </>
      )}
    </div>
  );
}
