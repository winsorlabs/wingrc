import { useEffect, useMemo, useState } from "react";
import { api, ApiError, SspConflictError } from "../api";
import {
  decidedLine,
  familyProgress,
  KIND_LABELS,
  overallProgress,
  rowState,
  shortHash,
  startingFamily,
} from "../lib/sspImport";
import type { SspConflict, SspDecisionIn, SspImportRow, SspImportSummary, SspImportView } from "../types";

// The SSP import review session (routers/ssp_imports.py,
// ssp_import_session.py). One family at a time; every decision is its own
// request, so a reload or a closed tab loses nothing, and nothing is
// written until a row is accepted. The source's status, Notes and RACI are
// reading material behind each row's "Source details", never imported.
interface Props {
  orgId: string;
  assessmentId: string;
}

export function SspImportPanel({ orgId, assessmentId }: Props) {
  const [sessions, setSessions] = useState<SspImportSummary[] | null>(null);
  const [view, setView] = useState<SspImportView | null>(null);
  const [family, setFamily] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [confirmDiscard, setConfirmDiscard] = useState(false);

  function load() {
    setError(null);
    api.listSspImports(orgId, assessmentId)
      .then((list) => {
        setSessions(list);
        const open = list.find((s) => s.status === "open");
        if (!open) {
          setView(null);
          return;
        }
        return api.getSspImport(orgId, assessmentId, open.id).then((v) => {
          setView(v);
          setFamily((f) => (f && v.families.some((x) => x.key === f) ? f : startingFamily(v)));
        });
      })
      .catch((e: Error) => setError(e.message));
  }

  useEffect(load, [orgId, assessmentId]);

  async function upload(file: File) {
    setUploading(true);
    setError(null);
    try {
      const v = await api.startSspImport(orgId, assessmentId, file);
      setView(v);
      setFamily(startingFamily(v));
      setSessions(await api.listSspImports(orgId, assessmentId));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setUploading(false);
    }
  }

  async function close(outcome: "completed" | "discarded") {
    if (!view) return;
    setError(null);
    try {
      await api.closeSspImport(orgId, assessmentId, view.session.id, outcome);
      setConfirmDiscard(false);
      load();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  const closed = (sessions ?? []).filter((s) => s.status !== "open");

  if (sessions === null) {
    return <div className="workspace-content">{error ? <div className="error">{error}</div> : <div className="empty">Loading…</div>}</div>;
  }

  return (
    <div className="workspace-content ssp-import">
      <div className="ssp-head">
        <h1>SSP Import</h1>
        <p className="ssp-lede">
          Bring a customer's existing SSP in as draft implementation statements, one family at a time.
          Every decision saves as you make it. Nothing is written until you accept a row, every statement
          lands as a draft, and no control's status or the SPRS score changes.
        </p>
      </div>

      {error && <div className="error" role="alert">{error}</div>}

      {view ? (
        <>
          <div className="card ssp-session">
            <div className="ssp-session-line">
              <div>
                <div className="ssp-file">{view.session.file_name}</div>
                <div className="ssp-meta">
                  SHA-256 {shortHash(view.session.file_sha256)}… · started
                  {view.session.started_by ? ` by ${view.session.started_by}` : ""} {fmt(view.session.started_at)} ·
                  the file itself was not kept
                </div>
              </div>
              <div className="ssp-session-actions">
                <button
                  className="btn btn-primary btn-sm"
                  disabled={view.session.decided < view.session.rows}
                  title={view.session.decided < view.session.rows ? "Decide every row first" : undefined}
                  onClick={() => close("completed")}
                >
                  Finish import
                </button>
                {confirmDiscard ? (
                  <span className="ssp-confirm">
                    Discard this import? Its {view.session.written} draft{view.session.written === 1 ? "" : "s"} stay; undecided rows are left.
                    <button className="btn btn-ghost btn-sm" onClick={() => close("discarded")}>Discard</button>
                    <button className="btn btn-ghost btn-sm" onClick={() => setConfirmDiscard(false)}>Keep working</button>
                  </span>
                ) : (
                  <button className="btn btn-ghost btn-sm" onClick={() => setConfirmDiscard(true)}>Discard…</button>
                )}
              </div>
            </div>
            <div className="ssp-progress" aria-live="polite">{overallProgress(view)}</div>
          </div>

          <div className="ssp-body">
            <nav className="ssp-families" aria-label="Families">
              {view.families.map((f) => (
                <button
                  key={f.key}
                  className={`ssp-family${f.key === family ? " active" : ""}`}
                  onClick={() => setFamily(f.key)}
                  aria-current={f.key === family ? "true" : undefined}
                >
                  <span className="ssp-family-name">{f.heading}</span>
                  <span className="ssp-family-count">
                    {f.decided === f.total ? "✓ " : ""}{f.decided}/{f.total}
                  </span>
                </button>
              ))}
            </nav>
            <section className="ssp-rows">
              {family && (
                <FamilyRows
                  orgId={orgId}
                  assessmentId={assessmentId}
                  view={view}
                  family={family}
                  onView={setView}
                />
              )}
            </section>
          </div>
        </>
      ) : (
        <div className="card ssp-empty">
          <h2>No import in progress</h2>
          <p>
            Upload the customer's SSP as a <code>.docx</code>. WinGRC reads its security-requirements table
            (columns Control, Status, Notes, Implementation Statement, RACI), matches each row to an objective
            by its exact 800-171A text, and keeps only what you will review: the statement text and the
            Control cell. The file itself is discarded once read; its SHA-256 is kept so it can be identified
            later. RACI names and Notes are not stored.
          </p>
          <label className="btn btn-primary ssp-upload">
            {uploading ? "Reading…" : "Choose SSP file"}
            <input
              type="file"
              accept=".docx"
              disabled={uploading}
              onChange={(e) => {
                const f = e.target.files?.[0];
                e.target.value = "";
                if (f) void upload(f);
              }}
            />
          </label>
        </div>
      )}

      {closed.length > 0 && (
        <details className="ssp-history">
          <summary>Earlier imports ({closed.length})</summary>
          <ul className="item-list">
            {closed.map((s) => (
              <li key={s.id} className="item-row">
                <span>
                  {s.file_name} · {s.status === "completed" ? "finished" : "discarded"} {fmt(s.closed_at)}
                  {s.closed_by ? ` by ${s.closed_by}` : ""}
                </span>
                <span className="ssp-meta">
                  {s.decided} of {s.rows} decided · {s.written} draft{s.written === 1 ? "" : "s"} written
                </span>
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}

function fmt(iso: string | null): string {
  if (!iso) return "";
  return new Date(iso).toLocaleString();
}

function FamilyRows({ orgId, assessmentId, view, family, onView }: {
  orgId: string;
  assessmentId: string;
  view: SspImportView;
  family: string;
  onView: (v: SspImportView) => void;
}) {
  const rows = useMemo(() => view.rows.filter((r) => r.family === family), [view, family]);
  const f = view.families.find((x) => x.key === family);
  return (
    <>
      {f && <h2 className="ssp-family-title">{f.heading} <span className="ssp-meta">{familyProgress(f)}</span></h2>}
      <ol className="ssp-row-list">
        {rows.map((r) => (
          <RowCard key={r.id} orgId={orgId} assessmentId={assessmentId} sessionId={view.session.id} row={r} onView={onView} />
        ))}
      </ol>
    </>
  );
}

function RowCard({ orgId, assessmentId, sessionId, row, onView }: {
  orgId: string;
  assessmentId: string;
  sessionId: string;
  row: SspImportRow;
  onView: (v: SspImportView) => void;
}) {
  const [pick, setPick] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(row.statement ?? "");
  const [conflict, setConflict] = useState<SspConflict["statement"] | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const state = rowState(row);
  const pickable = row.match_kind !== "exact" && row.acceptable_onto.length > 0;
  const objectiveId = row.match_kind === "exact" ? row.objective_id : pick;
  const shown = conflict ?? (row.existing && state === "conflict" ? row.existing : null);

  async function decide(body: SspDecisionIn) {
    setBusy(true);
    setErr(null);
    try {
      const v = await api.decideSspRow(orgId, assessmentId, sessionId, row.id, body);
      setConflict(null);
      setEditing(false);
      onView(v);
    } catch (e) {
      if (e instanceof SspConflictError) setConflict(e.conflict.statement);
      else setErr(e instanceof ApiError || e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  function writeBody(extra: Partial<SspDecisionIn> = {}): SspDecisionIn {
    const base: SspDecisionIn = editing
      ? { action: "edit", body: text }
      : { action: "accept" };
    return { ...base, ...(objectiveId ? { objective_id: objectiveId } : {}), ...extra };
  }

  const ref = row.match_kind === "exact" ? row.objective_ref : row.candidates.find((c) => c.objective_id === pick)?.ref;

  return (
    <li className={`card ssp-row ssp-row-${state}`}>
      <div className="ssp-row-head">
        <span className="ssp-row-num">Row {row.number}</span>
        <span className="ssp-row-target">{row.decided_ref ?? row.objective_ref ?? "No objective"}</span>
        <span className={`badge ${row.match_kind === "exact" ? "badge-met" : row.match_kind === "unmatched" ? "badge-na" : "badge-partial"}`}>
          {KIND_LABELS[row.match_kind]}
        </span>
        {state === "written" && <span className="badge badge-pending">Draft written</span>}
        {state === "rejected" && <span className="badge badge-na">Rejected</span>}
      </div>

      <div className="ssp-row-q">Source control text</div>
      <div className="ssp-row-control">{row.control_text}</div>

      <div className="ssp-row-q">Statement</div>
      {editing ? (
        <textarea
          className="ssp-edit"
          value={text}
          rows={Math.min(14, Math.max(5, text.split("\n").length + 1))}
          onChange={(e) => setText(e.target.value)}
          aria-label={`Edit the statement for row ${row.number}`}
        />
      ) : (
        <div className="ssp-row-statement">{row.statement ?? <em className="ssp-meta">The source row has no statement text.</em>}</div>
      )}

      {row.shares_objective_with.length > 0 && state !== "written" && (
        <div className="ssp-note">
          Row{row.shares_objective_with.length > 1 ? "s" : ""} {row.shares_objective_with.join(", ")} of this file name the same objective.
          Only one statement can sit on it.
        </div>
      )}

      {(state === "written" || state === "rejected") && (
        <div className="ssp-decided">
          {decidedLine(row)}
          {state === "rejected" && (
            <button className="btn btn-ghost btn-sm" disabled={busy} onClick={() => decide({ action: "reopen" })}>Reopen</button>
          )}
        </div>
      )}

      {state === "cannot_accept" && (
        <div className="ssp-note">
          {row.not_acceptable}
          {row.candidates.length > 0 && (
            <div className="ssp-meta">
              Closest objectives, for reading only: {row.candidates.map((c) => `${c.ref} (${c.score.toFixed(2)})`).join(", ")}
            </div>
          )}
          <div><button className="btn btn-ghost btn-sm" disabled={busy} onClick={() => decide({ action: "reject" })}>Reject</button></div>
        </div>
      )}

      {(state === "pending" || state === "conflict") && (
        <>
          {pickable && (
            <fieldset className="ssp-pick">
              <legend>Which objective is this row's?</legend>
              {row.candidates.map((c) => (
                <label key={c.objective_id}>
                  <input
                    type="radio"
                    name={`pick-${row.id}`}
                    checked={pick === c.objective_id}
                    onChange={() => setPick(c.objective_id)}
                  />{" "}
                  {c.ref}
                </label>
              ))}
            </fieldset>
          )}

          {shown && (
            <div className="ssp-conflict" role="group" aria-label="Existing statement">
              <div className="ssp-conflict-title">{ref ?? "This objective"} already has a statement ({shown.status})</div>
              <div className="ssp-row-statement">{shown.body}</div>
              <div className="ssp-conflict-actions">
                <button className="btn btn-ghost btn-sm" disabled={busy || !objectiveId}
                  onClick={() => decide({ action: "keep_existing", ...(objectiveId ? { objective_id: objectiveId } : {}) })}>
                  Keep the existing statement
                </button>
                <button className="btn btn-ghost btn-sm" disabled={busy || !objectiveId}
                  onClick={() => decide(writeBody({ replace_statement_id: shown.id, replace_updated_at: shown.updated_at }))}>
                  Replace it with this row's {editing ? "edited " : ""}text
                </button>
              </div>
            </div>
          )}

          {!shown && (
            <div className="ssp-actions">
              {editing ? (
                <>
                  <button className="btn btn-primary btn-sm" disabled={busy || !objectiveId || !text.trim()} onClick={() => decide(writeBody())}>
                    Save edit as draft{ref ? ` on ${ref}` : ""}
                  </button>
                  <button className="btn btn-ghost btn-sm" disabled={busy} onClick={() => { setEditing(false); setText(row.statement ?? ""); }}>Cancel edit</button>
                </>
              ) : (
                <>
                  <button className="btn btn-primary btn-sm" disabled={busy || !objectiveId} onClick={() => decide(writeBody())}>
                    Accept{ref ? ` onto ${ref}` : pickable ? " (choose an objective)" : ""}
                  </button>
                  <button className="btn btn-ghost btn-sm" disabled={busy} onClick={() => setEditing(true)}>Edit first</button>
                </>
              )}
              <button className="btn btn-ghost btn-sm" disabled={busy} onClick={() => decide({ action: "reject" })}>Reject</button>
            </div>
          )}
        </>
      )}

      {err && <div className="error" role="alert">{err}</div>}

      <details className="ssp-source">
        <summary>Source details</summary>
        <dl>
          <dt>Heading</dt><dd>{row.heading ?? "None above this row"}</dd>
          <dt>The source's status</dt><dd>{row.source_status ?? "(empty)"}: the source document's claim, never imported</dd>
          <dt>Notes</dt><dd>{row.notes_present ? "Present in the source; not imported" : "Empty"}</dd>
          <dt>RACI</dt><dd>{row.raci_present ? "Present in the source; not imported or stored" : "Empty"}</dd>
          {row.objective_text && <><dt>Catalog text</dt><dd>{row.objective_text}</dd></>}
        </dl>
      </details>
    </li>
  );
}
