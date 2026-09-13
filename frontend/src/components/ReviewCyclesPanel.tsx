import { useEffect, useState } from "react";
import { api } from "../api";
import { canManageReviewCycles } from "../lib/roles";
import type { ReviewCycle, ReviewCycleDetail, ReviewCycleItem } from "../types";

// Periodic review & attestation (D.3's first half) -- the client's
// acknowledgement IS the artifact here, so this screen is reachable by
// every org member including customer_poc, not just MSP staff. Only
// "Open a cycle now" and resolving a flag are MSP-side actions (gated by
// canManageReviewCycles, matching routers/review_cycles.py's per-route
// role restriction -- everything else on this router is open to any org
// member, enforced server-side regardless of what this screen shows).
interface Props {
  orgId: string;
  currentUserId: string;
  currentUserRole: string;
}

function statusBadgeClass(status: string): string {
  if (status === "attested" || status === "completed") return "status-active";
  if (status === "no_response" || status === "closed_unattested") return "status-warning";
  return "status-inactive";
}

export function ReviewCyclesPanel({ orgId, currentUserId, currentUserRole }: Props) {
  const [cycles, setCycles] = useState<ReviewCycle[]>([]);
  const [selected, setSelected] = useState<ReviewCycleDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [opening, setOpening] = useState(false);
  const [openError, setOpenError] = useState<string | null>(null);

  const canManage = canManageReviewCycles(currentUserRole);

  function loadList() {
    setLoading(true);
    setError(null);
    api.listReviewCycles(orgId).then(setCycles).catch((e: Error) => setError(e.message)).finally(() => setLoading(false));
  }

  useEffect(loadList, [orgId]);

  function openDetail(cycleId: string) {
    setError(null);
    api.getReviewCycle(orgId, cycleId).then(setSelected).catch((e: Error) => setError(e.message));
  }

  async function handleOpenCycle() {
    setOpening(true);
    setOpenError(null);
    try {
      await api.openReviewCycle(orgId);
      loadList();
    } catch (e) {
      setOpenError((e as Error).message);
    } finally {
      setOpening(false);
    }
  }

  if (selected) {
    return (
      <ReviewCycleDetailView
        orgId={orgId}
        detail={selected}
        currentUserId={currentUserId}
        canManage={canManage}
        onBack={() => {
          setSelected(null);
          loadList();
        }}
        onChanged={() => openDetail(selected.id)}
      />
    );
  }

  return (
    <div className="review-cycles-panel">
      <div className="products-panel-header" style={{ padding: 0, border: "none", marginBottom: "0.75rem" }}>
        <span className="products-panel-title">Periodic Review</span>
        {canManage && (
          <button className="btn-primary btn-sm" onClick={handleOpenCycle} disabled={opening}>
            {opening ? "Opening…" : "Open a cycle now"}
          </button>
        )}
      </div>
      <p style={{ color: "#6b7280", fontSize: "0.85rem", marginTop: 0 }}>
        Periodic sign-off that the MSP and the client reviewed the org's authorized
        users and devices -- an assessor-showable record (AC.L2-3.1.1[a]/[c]), not a
        notification. Approving confirms the list; it never changes scope.
      </p>
      {openError && <div className="form-error">{openError}</div>}

      {loading ? (
        <div className="loading">Loading…</div>
      ) : error ? (
        <div className="form-error">{error}</div>
      ) : cycles.length === 0 ? (
        <div className="empty">No review cycles yet.</div>
      ) : (
        <div className="table-scroll">
          <table className="contacts-table">
            <thead>
              <tr>
                <th>Opened</th>
                <th>Due</th>
                <th>Closed</th>
                <th>Status</th>
                <th>Cadence</th>
              </tr>
            </thead>
            <tbody>
              {cycles.map((c) => (
                <tr key={c.id} onClick={() => openDetail(c.id)} style={{ cursor: "pointer" }}>
                  <td>{new Date(c.opened_at).toLocaleDateString()}</td>
                  <td>{new Date(c.due_at).toLocaleDateString()}</td>
                  <td>{c.closed_at ? new Date(c.closed_at).toLocaleDateString() : ""}</td>
                  <td><span className={`status-badge ${statusBadgeClass(c.status)}`}>{c.status}</span></td>
                  <td>Every {c.cadence_months} mo.</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

interface DetailProps {
  orgId: string;
  detail: ReviewCycleDetail;
  currentUserId: string;
  canManage: boolean;
  onBack: () => void;
  onChanged: () => void;
}

function ReviewCycleDetailView({ orgId, detail, currentUserId, canManage, onBack, onChanged }: DetailProps) {
  const [comment, setComment] = useState("");
  const [attesting, setAttesting] = useState(false);
  const [attestError, setAttestError] = useState<string | null>(null);
  const [flagTarget, setFlagTarget] = useState<ReviewCycleItem | null>(null);
  const [flagReason, setFlagReason] = useState("");
  const [flagging, setFlagging] = useState(false);
  const [flagError, setFlagError] = useState<string | null>(null);

  const myReviewer = detail.reviewers.find((r) => r.user_id === currentUserId);

  async function handleAttest() {
    setAttesting(true);
    setAttestError(null);
    try {
      await api.attestReviewCycle(orgId, detail.id, comment || undefined);
      onChanged();
    } catch (e) {
      setAttestError((e as Error).message);
    } finally {
      setAttesting(false);
    }
  }

  async function handleFlag() {
    if (!flagTarget || !flagReason.trim()) return;
    setFlagging(true);
    setFlagError(null);
    try {
      await api.flagReviewCycleItem(orgId, detail.id, flagTarget.id, flagReason.trim());
      setFlagTarget(null);
      onChanged();
    } catch (e) {
      setFlagError((e as Error).message);
    } finally {
      setFlagging(false);
    }
  }

  async function handleResolve(flagId: string) {
    const note = window.prompt("Resolution note:");
    if (!note) return;
    await api.resolveReviewCycleFlag(orgId, detail.id, flagId, note);
    onChanged();
  }

  const canAttest = detail.status === "open" && myReviewer && myReviewer.status !== "attested";

  return (
    <div className="review-cycle-detail">
      <button className="btn-ghost btn-sm" onClick={onBack}>&larr; Back</button>
      <h2>Review cycle -- opened {new Date(detail.opened_at).toLocaleDateString()}</h2>
      <p>
        Status: <span className={`status-badge ${statusBadgeClass(detail.status)}`}>{detail.status}</span>
        {" "}&middot; Due {new Date(detail.due_at).toLocaleDateString()}
        {" "}&middot; Cadence: every {detail.cadence_months} month(s)
      </p>

      {canAttest && (
        <div className="card">
          <h3>Attest to this review</h3>
          <p style={{ color: "#6b7280" }}>
            Confirms you reviewed the list below. It does not change anything in
            scope -- if something looks wrong, flag it instead.
          </p>
          <textarea
            placeholder="Optional comment"
            value={comment}
            onChange={(e) => setComment(e.target.value)}
          />
          {attestError && <div className="form-error">{attestError}</div>}
          <button className="btn-primary" onClick={handleAttest} disabled={attesting}>
            {attesting ? "Attesting…" : "I have reviewed this list"}
          </button>
        </div>
      )}

      <h3>Items presented for review ({detail.items.length})</h3>
      <div className="table-scroll">
        <table className="contacts-table">
          <thead>
            <tr><th>Type</th><th>Identifier</th><th>Category</th><th></th></tr>
          </thead>
          <tbody>
            {detail.items.map((i) => (
              <tr key={i.id}>
                <td>{i.subject_type}</td>
                <td>{i.natural_key}</td>
                <td>{i.scope_category ?? ""}</td>
                <td>
                  {detail.status === "open" && (
                    <button
                      className="btn-ghost btn-sm"
                      onClick={() => {
                        setFlagTarget(i);
                        setFlagReason("");
                        setFlagError(null);
                      }}
                    >
                      Flag
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <h3>Reviewers ({detail.reviewers.length})</h3>
      <div className="table-scroll">
        <table className="contacts-table">
          <thead>
            <tr><th>Name</th><th>Side</th><th>Status</th><th>Attested</th><th>Comment</th></tr>
          </thead>
          <tbody>
            {detail.reviewers.map((r) => (
              <tr key={r.id}>
                <td>{r.reviewer_name}</td>
                <td>{r.reviewer_side}</td>
                <td><span className={`status-badge ${statusBadgeClass(r.status)}`}>{r.status}</span></td>
                <td>{r.attested_at ? new Date(r.attested_at).toLocaleString() : ""}</td>
                <td>{r.comment ?? ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {detail.flags.length > 0 && (
        <>
          <h3>Flags</h3>
          <div className="table-scroll">
            <table className="contacts-table">
              <thead>
                <tr><th>Item</th><th>Reason</th><th>Flagged by</th><th>Status</th>{canManage && <th></th>}</tr>
              </thead>
              <tbody>
                {detail.flags.map((f) => (
                  <tr key={f.id}>
                    <td>{detail.items.find((i) => i.id === f.cycle_item_id)?.natural_key ?? ""}</td>
                    <td>{f.reason}</td>
                    <td>{f.flagged_by_name}</td>
                    <td>{f.resolved_at ? `Resolved: ${f.resolved_note}` : "Open"}</td>
                    {canManage && (
                      <td>
                        {!f.resolved_at && (
                          <button className="btn-ghost btn-sm" onClick={() => handleResolve(f.id)}>
                            Resolve
                          </button>
                        )}
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      {flagTarget && (
        <div className="drawer-overlay" onClick={() => setFlagTarget(null)}>
          <div className="drawer" onClick={(e) => e.stopPropagation()}>
            <div className="drawer-header">
              <h3>Flag {flagTarget.natural_key}</h3>
              <button className="drawer-close" onClick={() => setFlagTarget(null)} aria-label="Close">×</button>
            </div>
            <div className="drawer-body">
              <p style={{ color: "#6b7280" }}>
                This routes to the MSP for follow-up -- it does not change scope.
              </p>
              {flagError && <div className="form-error">{flagError}</div>}
              <div className="form-field">
                <label>
                  Reason
                  <textarea value={flagReason} onChange={(e) => setFlagReason(e.target.value)} />
                </label>
              </div>
            </div>
            <div className="drawer-footer">
              <div style={{ flex: 1 }} />
              <button className="btn-ghost" onClick={() => setFlagTarget(null)}>Cancel</button>
              <button className="btn-primary" disabled={flagging || !flagReason.trim()} onClick={handleFlag}>
                {flagging ? "Flagging…" : "Flag item"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
