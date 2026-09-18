import { useEffect, useState } from "react";
import { api } from "../api";
import type { LiongardSyncResultChangeRow, LiongardSyncResultDetail, LiongardSyncResultRow } from "../types";

// D.3 second half: daily Liongard sync + asset/user onboarding approval.
// Reachable by every org member with write access -- the org's Security
// Officer/IT contact is exactly who approves here, same reasoning as
// ReviewCyclesPanel's own gate. Only change_type="new" rows go through
// approve/reject (see LiongardSyncResultChange's own docstring in
// models.py) -- "changed"/"missing" rows are shown for visibility only.
interface Props {
  orgId: string;
}

function statusBadgeClass(status: string): string {
  if (status === "pending_review") return "status-warning";
  if (status === "reviewed") return "status-active";
  if (status === "superseded") return "status-inactive";
  return "status-inactive";
}

export function LiongardSyncPanel({ orgId }: Props) {
  const [results, setResults] = useState<LiongardSyncResultRow[]>([]);
  const [selected, setSelected] = useState<LiongardSyncResultDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [syncing, setSyncing] = useState(false);
  const [syncError, setSyncError] = useState<string | null>(null);

  function loadList() {
    setLoading(true);
    setError(null);
    api.listLiongardSyncResults(orgId).then(setResults).catch((e: Error) => setError(e.message)).finally(() => setLoading(false));
  }

  useEffect(loadList, [orgId]);

  function openDetail(id: string) {
    setError(null);
    api.getLiongardSyncResult(orgId, id).then(setSelected).catch((e: Error) => setError(e.message));
  }

  async function handleSyncNow() {
    setSyncing(true);
    setSyncError(null);
    try {
      const result = await api.syncLiongardNow(orgId);
      loadList();
      openDetail(result.id);
    } catch (e) {
      setSyncError((e as Error).message);
    } finally {
      setSyncing(false);
    }
  }

  if (selected) {
    return (
      <SyncResultDetailView
        orgId={orgId}
        detail={selected}
        onBack={() => {
          setSelected(null);
          loadList();
        }}
        onChanged={() => openDetail(selected.id)}
      />
    );
  }

  return (
    <div className="liongard-sync-panel">
      <div className="products-panel-header" style={{ padding: 0, border: "none", marginBottom: "0.75rem" }}>
        <span className="products-panel-title">Asset Approvals</span>
        <button className="btn-primary btn-sm" onClick={handleSyncNow} disabled={syncing}>
          {syncing ? "Syncing…" : "Sync now"}
        </button>
      </div>
      <p style={{ color: "#6b7280", fontSize: "0.85rem", marginTop: 0 }}>
        Assets and users Liongard observes land here pending approval, never silently
        joining the CUI boundary. A daily sync also runs automatically; this button
        triggers one immediately.
      </p>
      {syncError && <div className="form-error">{syncError}</div>}
      {error && <div className="form-error">{error}</div>}

      {loading ? (
        <div className="loading">Loading…</div>
      ) : results.length === 0 ? (
        <div className="empty">No syncs yet. Click "Sync now" to pull from Liongard.</div>
      ) : (
        <div className="table-scroll">
          <table className="contacts-table">
            <thead>
              <tr>
                <th>Pulled</th>
                <th>Status</th>
                <th>New</th>
                <th>Changed</th>
                <th>Missing</th>
              </tr>
            </thead>
            <tbody>
              {results.map((r) => (
                <tr key={r.id} onClick={() => openDetail(r.id)} style={{ cursor: "pointer" }}>
                  <td>{new Date(r.pulled_at).toLocaleString()}</td>
                  <td>
                    <span className={`status-badge ${statusBadgeClass(r.status)}`}>{r.status}</span>
                  </td>
                  <td>{r.summary.new ?? 0}</td>
                  <td>{r.summary.changed ?? 0}</td>
                  <td>{r.summary.missing ?? 0}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function SyncResultDetailView({
  orgId,
  detail,
  onBack,
  onChanged,
}: {
  orgId: string;
  detail: LiongardSyncResultDetail;
  onBack: () => void;
  onChanged: () => void;
}) {
  const newChanges = detail.changes.filter((c) => c.change_type === "new");
  const otherChanges = detail.changes.filter((c) => c.change_type !== "new");

  return (
    <div className="liongard-sync-detail">
      <button className="btn-ghost btn-sm" onClick={onBack} style={{ marginBottom: "0.75rem" }}>
        ← Back to syncs
      </button>
      <h3>
        Sync from {new Date(detail.pulled_at).toLocaleString()}{" "}
        <span className={`status-badge ${statusBadgeClass(detail.status)}`}>{detail.status}</span>
      </h3>
      {detail.status === "superseded" && (
        <div className="field-hint">
          A later sync for this org has since run -- this result is kept for the record
          but is no longer the one to act on.
        </div>
      )}
      {detail.warnings.length > 0 && (
        <div className="field-hint">{detail.warnings.join(" ")}</div>
      )}

      <h4>Awaiting approval ({newChanges.length})</h4>
      {newChanges.length === 0 ? (
        <div className="empty">Nothing new from this sync.</div>
      ) : (
        newChanges.map((c) => (
          <ChangeApprovalRow
            key={c.id}
            orgId={orgId}
            syncResultId={detail.id}
            change={c}
            checklistProducts={detail.checklist_products}
            disabled={detail.status === "superseded"}
            onDecided={onChanged}
          />
        ))
      )}

      {otherChanges.length > 0 && (
        <>
          <h4>Other changes (informational only)</h4>
          <p style={{ color: "#6b7280", fontSize: "0.85rem" }}>
            These aren't new entities, so they don't need approval -- review and apply
            them, if wanted, through the ordinary Scope import screen.
          </p>
          <div className="table-scroll">
            <table className="contacts-table">
              <thead>
                <tr>
                  <th>Type</th>
                  <th>Entity</th>
                  <th>Natural key</th>
                </tr>
              </thead>
              <tbody>
                {otherChanges.map((c) => (
                  <tr key={c.id}>
                    <td>{c.change_type}</td>
                    <td>{c.entity_type}</td>
                    <td>{c.natural_key}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}

function ChangeApprovalRow({
  orgId,
  syncResultId,
  change,
  checklistProducts,
  disabled,
  onDecided,
}: {
  orgId: string;
  syncResultId: string;
  change: LiongardSyncResultChangeRow;
  checklistProducts: { product_key: string; product_name: string }[];
  disabled: boolean;
  onDecided: () => void;
}) {
  const [confirmations, setConfirmations] = useState<Record<string, boolean>>({});
  const [reason, setReason] = useState("");
  const [showReject, setShowReject] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const displayName =
    (change.incoming?.attributes.display_name as string | undefined) || change.natural_key;

  async function handleApprove() {
    setBusy(true);
    setError(null);
    try {
      await api.approveLiongardChange(orgId, syncResultId, change.id, confirmations);
      onDecided();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function handleReject() {
    setBusy(true);
    setError(null);
    try {
      await api.rejectLiongardChange(orgId, syncResultId, change.id, reason);
      onDecided();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  if (change.resolution && change.resolution !== "pending") {
    return (
      <div className="liongard-change-row liongard-change-resolved">
        <strong>{displayName}</strong> ({change.entity_type}) — {change.resolution}
      </div>
    );
  }

  return (
    <div className="liongard-change-row">
      <div>
        <strong>{displayName}</strong> ({change.entity_type}, {change.natural_key})
      </div>
      {checklistProducts.length > 0 && (
        <div className="liongard-checklist">
          <div className="field-hint">
            Confirm which of this org's activated tools are actually present on this
            asset -- not automatically verified, your own observation.
          </div>
          {checklistProducts.map((p) => (
            <label key={p.product_key} style={{ display: "block" }}>
              <input
                type="checkbox"
                checked={confirmations[p.product_key] ?? false}
                onChange={(e) =>
                  setConfirmations({ ...confirmations, [p.product_key]: e.target.checked })
                }
                disabled={disabled || busy}
              />{" "}
              {p.product_name}
            </label>
          ))}
        </div>
      )}
      {error && <div className="form-error">{error}</div>}
      {showReject ? (
        <div>
          <textarea
            placeholder="Reason for rejecting (required)"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            disabled={busy}
          />
          <div>
            <button className="btn-primary btn-sm" onClick={handleReject} disabled={busy || !reason.trim()}>
              Confirm reject
            </button>
            <button className="btn-ghost btn-sm" onClick={() => setShowReject(false)} disabled={busy}>
              Cancel
            </button>
          </div>
        </div>
      ) : (
        <div>
          <button className="btn-primary btn-sm" onClick={handleApprove} disabled={disabled || busy}>
            Approve
          </button>
          <button className="btn-ghost btn-sm" onClick={() => setShowReject(true)} disabled={disabled || busy}>
            Reject
          </button>
        </div>
      )}
    </div>
  );
}
