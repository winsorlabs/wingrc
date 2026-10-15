/**
 * Library › Lists (roadmap L.2): every list an org keeps, in one place.
 *
 * Two kinds of list share this screen, and only this screen -- an earlier
 * nav had "Lists" in two places meaning two different things:
 *
 * - Library lists (L.1's list_definition): a definition plus its own rows,
 *   edited here. Every save creates a version; nothing mutates one (the
 *   database refuses an UPDATE of list_version).
 * - Lists drawn live from the scope graph (the four ListViews -- 3.1.1a/b/c
 *   and External Services). They follow Scope › Assets and are edited
 *   there; they appear here so there is one place called Lists.
 *
 * The index keeps the kinds apart (L.2.1). A live list and a template can
 * share a title and a practice -- "3.1.1c Authorized Devices" is real
 * devices in a real boundary, the device template is examples -- so each
 * kind is its own section, with a third waiting for the lists an org keeps
 * for itself (a copy adopted from a template lands there).
 *
 * Family is the first axis: a list tagged with three practices appears once
 * in each of their family views, and once overall. Its tags say it serves
 * all three, which is the model's central claim.
 *
 * Lists can be built here by hand (L.2.2, ListBuilder) and tagged to
 * further practices. Tags are not version content, so their changes have
 * their own record -- shown in the same History as the versions, because
 * that is where a person looks first. Removing a tag is refused while
 * evidence rests on it; the refusal names the link.
 *
 * Columns can be added, renamed and removed while editing. Rows are
 * positional, so a rename keeps its cells; a removal drops them from the
 * new version, and the save bar says exactly what before it saves.
 *
 * A save that lost a race is not an error path. The 409 says who saved the
 * newer version; the editor's draft stays, and their changes are re-applied
 * onto that version for them to review (lib/listLibrary.ts's rebase) --
 * never silently onto it, and never dropped.
 */

import { useCallback, useEffect, useMemo, useState } from "react";

import { api, ListConflictError } from "../api";
import { FAMILY_NAMES } from "../lib/families";
import {
  causeLabel,
  changeCounts,
  columnLossWarning,
  columnsChanged,
  columnsFrom,
  draftFrom,
  droppedColumns,
  historyEntries,
  renamedColumns,
  tagSentence,
  type DraftColumn,
  draftRows,
  familiesOf,
  familyCounts,
  familyOf,
  filterLists,
  isDirty,
  kindOf,
  sortLists,
  rebase,
  type DraftRow,
  type ListKind,
  type RebaseResult,
  versionAuthor,
  whyVersion,
} from "../lib/listLibrary";
import type {
  LibraryListDetail,
  LibraryListSaveConflict,
  LibraryListSummary,
  LibraryListVersion,
  ListImportReport,
  ListTagEvent,
  ListViewSummary,
} from "../types";
import { ListBuilder } from "./ListBuilder";
import { ListsPanel } from "./ListsPanel";

type Selection = { kind: "library"; id: string } | { kind: "scope"; id: string } | null;

interface Entry {
  kind: "library" | "scope";
  id: string;
  title: string;
  control_keys: string[];
  library?: LibraryListSummary;
  scope?: ListViewSummary;
}

const SECTIONS: { kind: ListKind; title: string; hint: string; empty?: string }[] = [
  {
    kind: "sourced",
    title: "Live from scope",
    hint: "Real records in this org's boundary, drawn from Scope › Assets and edited there.",
  },
  {
    kind: "kept",
    title: "This org's lists",
    hint: "Kept by hand for this org. Every save creates a new version.",
    empty: "None yet. A list this org keeps for itself, including one copied from a template, goes here.",
  },
  {
    kind: "template",
    title: "Templates",
    hint: "MSP templates. Their rows are illustrative examples, not this org's records.",
  },
];

const READ_ONLY_HINT = "Your role can read lists but not change them.";

function sheetOf(ref: string): string {
  return ref.split("#")[1] ?? ref;
}

function fileOf(ref: string): string {
  return ref.split("#")[0] ?? ref;
}

function formatWhen(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

const PLACEHOLDER_REASON = /^\[PLACEHOLDER - (.+)\]$/;

function CellText({ value }: { value: string }) {
  const reason = PLACEHOLDER_REASON.exec(value);
  if (reason) {
    return (
      <span className="list-cell-placeholder" title={`Known absent: ${reason[1]}`}>
        {value}
      </span>
    );
  }
  if (value === "[PLACEHOLDER]") {
    return (
      <span className="list-cell-example" title="Template example: replace with real data">
        {value}
      </span>
    );
  }
  return value ? <>{value}</> : <span className="list-cell-empty">—</span>;
}

function PracticeChips({
  keys,
  onPick,
  active,
}: {
  keys: string[];
  onPick?: (k: string) => void;
  active?: string | null;
}) {
  return (
    <span className="list-practices">
      {keys.map((k) =>
        onPick ? (
          <button
            key={k}
            type="button"
            className={`filter-chip list-practice${active === k ? " filter-chip--active" : ""}`}
            title={`${FAMILY_NAMES[familyOf(k)] ?? familyOf(k)}: show every list serving ${k}`}
            onClick={() => onPick(k)}
          >
            {k}
          </button>
        ) : (
          <span key={k} className="filter-chip list-practice" title={FAMILY_NAMES[familyOf(k)] ?? ""}>
            {k}
          </span>
        ),
      )}
    </span>
  );
}

const STATUS_LABELS: Record<string, string> = {
  needs_normalization: "Needs normalization (multi-section or wide sheets; reported, not flattened)",
  non_conforming: "Non-conforming files (older generation; to be normalized by hand)",
  superseded: "Superseded by another file",
};

function ImportReportNote({ report }: { report: ListImportReport }) {
  if (!report.recorded_at) return null;
  const missing = report.not_imported.filter((n) => n.status !== "merged");
  const merged = report.not_imported.filter((n) => n.status === "merged");
  if (missing.length === 0 && report.not_ingested.length === 0 && merged.length === 0) return null;
  const groups = Object.keys(STATUS_LABELS)
    .map((status) => [status, missing.filter((n) => n.status === status)] as const)
    .filter(([, items]) => items.length > 0);
  const other = missing.filter((n) => !(n.status in STATUS_LABELS));
  return (
    <details className="list-import-report">
      <summary>
        Not in this library: {missing.length} template sheet{missing.length === 1 ? "" : "s"} not
        imported
        {report.not_ingested.length > 0 &&
          `, and ${report.not_ingested.length} archived file${report.not_ingested.length === 1 ? "" : "s"} deliberately not ingested`}
        {" "}(last import {formatWhen(report.recorded_at)})
      </summary>
      {groups.map(([status, items]) => (
        <div key={status}>
          <h4>
            {STATUS_LABELS[status]} — {items.length}
          </h4>
          <ul>
            {items.map((n) => (
              <li key={n.source_ref}>
                <code>{n.source_ref}</code> — {n.reason}
              </li>
            ))}
          </ul>
        </div>
      ))}
      {other.length > 0 && (
        <ul>
          {other.map((n) => (
            <li key={n.source_ref}>
              <code>{n.source_ref}</code> ({n.status}) — {n.reason}
            </li>
          ))}
        </ul>
      )}
      {merged.length > 0 && (
        <div>
          <h4>Folded into another list — {merged.length}</h4>
          <ul>
            {merged.map((n) => (
              <li key={n.source_ref}>
                <code>{n.source_ref}</code> — {n.reason}
              </li>
            ))}
          </ul>
        </div>
      )}
      {report.not_ingested.length > 0 && (
        <div>
          <h4>Archive: prior versions, not ingested by decision — {report.not_ingested.length}</h4>
          <ul>
            {report.not_ingested.map((p) => (
              <li key={p}>
                <code>{p}</code>
              </li>
            ))}
          </ul>
        </div>
      )}
    </details>
  );
}

interface ConflictState {
  info: LibraryListSaveConflict;
  theirs: LibraryListDetail;
  baseNumber: number;
  columnsChanged: boolean;
  result: RebaseResult | null;
  theirChanges: { added: number; removed: number };
  keepMine: Set<number>;
}

function LibraryListView({
  orgId,
  listId,
  canWrite,
  liveView,
  onOpenLive,
  onBack,
  onSaved,
}: {
  orgId: string;
  listId: string;
  canWrite: boolean;
  liveView: ListViewSummary | null;
  onOpenLive: () => void;
  onBack: () => void;
  onSaved: () => void;
}) {
  const [detail, setDetail] = useState<LibraryListDetail | null>(null);
  const [versions, setVersions] = useState<LibraryListVersion[]>([]);
  const [viewing, setViewing] = useState<number | null>(null);
  const [draft, setDraft] = useState<DraftRow[] | null>(null);
  const [note, setNote] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [conflict, setConflict] = useState<ConflictState | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);
  const [draftCols, setDraftCols] = useState<DraftColumn[] | null>(null);
  const [tagEvents, setTagEvents] = useState<ListTagEvent[]>([]);
  const [tagInput, setTagInput] = useState("");
  const [tagBusy, setTagBusy] = useState(false);
  const [tagError, setTagError] = useState<string | null>(null);
  const [confirmRemove, setConfirmRemove] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [d, vs] = await Promise.all([
        api.getLibraryList(orgId, listId),
        api.getLibraryListVersions(orgId, listId),
      ]);
      setDetail(d);
      setVersions(vs);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load list");
    }
    // The tag record adds to the history; the list reads without it.
    Promise.resolve(api.getListTagEvents(orgId, listId)).then(
      (es) => setTagEvents(es ?? []),
      () => setTagEvents([]),
    );
  }, [orgId, listId]);

  useEffect(() => {
    setDetail(null);
    setViewing(null);
    setDraft(null);
    setDraftCols(null);
    setConflict(null);
    setNotice(null);
    setConfirmRemove(null);
    setTagError(null);
    load();
  }, [load]);

  const base = detail?.current ?? null;
  const colsChanged = draftCols !== null && base !== null && columnsChanged(base.columns, draftCols);
  const dirty = draft !== null && base !== null && (isDirty(base.rows, draft) || colsChanged);

  // There is no autosave: say so before a tab close loses an edit.
  useEffect(() => {
    if (!dirty) return;
    const warn = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  if (error && !detail) return <div className="form-error">{error}</div>;
  if (!detail || !base) return <div className="loading">Loading list…</div>;

  const shown: LibraryListVersion =
    viewing !== null ? versions.find((v) => v.version_number === viewing) ?? base : base;
  const isOld = shown.version_number !== base.version_number;
  const addedByMerge = new Set(detail.merge?.columns.filter((c) => c.added).map((c) => c.target_column));
  const families = familiesOf(detail);
  const editing = draft !== null;

  function startEdit() {
    setDraft(draftFrom(base!.rows));
    setDraftCols(columnsFrom(base!.columns));
    setNote("");
    setNotice(null);
    setError(null);
  }

  function setCell(i: number, c: number, value: string) {
    setDraft((d) => d && d.map((r, k) => (k === i ? { ...r, cells: r.cells.map((x, m) => (m === c ? value : x)) } : r)));
  }

  function removeColumn(i: number) {
    setDraftCols((cols) => cols && cols.filter((_, k) => k !== i));
    setDraft((d) => d && d.map((r) => ({ ...r, cells: r.cells.filter((_, k) => k !== i) })));
  }

  function addColumn() {
    setDraftCols((cols) => cols && [...cols, { name: "", from: null }]);
    setDraft((d) => d && d.map((r) => ({ ...r, cells: [...r.cells, ""] })));
  }

  async function changeTag(action: "add" | "remove", key: string) {
    if (tagBusy) return;
    setTagBusy(true);
    setTagError(null);
    try {
      if (action === "add") {
        await api.addListTag(orgId, listId, key);
        setTagInput("");
      } else {
        await api.removeListTag(orgId, listId, key);
      }
      setConfirmRemove(null);
      await load();
      onSaved();
    } catch (e) {
      setTagError(e instanceof Error ? e.message : "Could not change this list's practices");
    } finally {
      setTagBusy(false);
    }
  }

  async function save() {
    if (!draft || !base || saving) return;
    setSaving(true);
    setError(null);
    try {
      if (colsChanged && draftCols) {
        await api.createLibraryListVersion(
          orgId, listId, draftRows(draft), base.id, note, draftCols.map((c) => c.name.trim()),
        );
      } else {
        await api.createLibraryListVersion(orgId, listId, draftRows(draft), base.id, note);
      }
      setDraft(null);
      setDraftCols(null);
      setConflict(null);
      setNotice(null);
      await load();
      onSaved();
    } catch (e) {
      if (e instanceof ListConflictError) {
        try {
          const theirs = await api.getLibraryList(orgId, listId);
          // Either side changing the columns leaves rows that cannot be
          // matched up cell by cell, so nothing is re-applied automatically.
          const columnsChanged =
            JSON.stringify(theirs.current.columns) !== JSON.stringify(base.columns) || colsChanged;
          setConflict({
            info: e.conflict,
            theirs,
            baseNumber: base.version_number,
            columnsChanged,
            result: columnsChanged ? null : rebase(base.rows, draft, theirs.current.rows),
            theirChanges: changeCounts(base.rows, theirs.current.rows),
            keepMine: new Set(),
          });
        } catch (inner) {
          setError(inner instanceof Error ? inner.message : "Could not load the newer version");
        }
      } else {
        setError(e instanceof Error ? e.message : "Could not save this version");
      }
    } finally {
      setSaving(false);
    }
  }

  function reapply() {
    if (!conflict?.result) return;
    const width = conflict.theirs.current.columns.length;
    const extra: DraftRow[] = conflict.result.conflicts
      .filter((c, i) => c.mine && conflict.keepMine.has(i))
      .map((c) => ({ origin: null, cells: c.mine!.slice(0, width) }));
    setDetail(conflict.theirs);
    setDraft([...conflict.result.rows, ...extra]);
    setNotice(
      `Your changes are now on top of version ${conflict.theirs.current.version_number}. ` +
        `Review them, then save as version ${conflict.theirs.current.version_number + 1}.`,
    );
    setConflict(null);
    api.getLibraryListVersions(orgId, listId).then(setVersions, () => undefined);
  }

  function discardAndLoad() {
    setDraft(null);
    setDraftCols(null);
    setConflict(null);
    setNotice(null);
    load();
  }

  const rows: DraftRow[] | null = editing ? draft : null;
  const columns = editing && draftCols ? draftCols.map((c) => c.name) : shown.columns;
  const dropped = editing && draftCols ? droppedColumns(base.columns, base.rows, draftCols) : [];
  const renamed = editing && draftCols ? renamedColumns(base.columns, draftCols) : [];
  const lossWarning = columnLossWarning(dropped, base.version_number + 1);
  const droppedCells = dropped.reduce((a, d) => a + d.filled, 0);
  const badColumns =
    editing &&
    draftCols !== null &&
    (draftCols.some((c) => !c.name.trim()) ||
      new Set(draftCols.map((c) => c.name.trim().toLowerCase())).size !== draftCols.length);
  const history = historyEntries(versions.length ? versions : [base], tagEvents);

  return (
    <div className="contacts-panel list-library-detail">
      <button type="button" className="btn-ghost btn-sm list-back" onClick={onBack}>
        ← All lists
      </button>

      <div className="list-library-header">
        <div>
          <h2>
            {shown.title}{" "}
            {detail.is_template && (
              <span className="affiliation-badge" title="An MSP template: its rows are illustrative examples">
                template
              </span>
            )}
          </h2>
          <div className="contact-sub">
            Version {shown.version_number}
            {isOld ? ` of ${base.version_number}` : ""} ·{" "}
            {causeLabel(shown.cause)} by {versionAuthor(shown)} on {formatWhen(shown.created_at)}
          </div>
        </div>
        <div className="list-library-actions">
          {versions.length > 1 && (
            <label className="field-hint">
              Version{" "}
              <select
                value={shown.version_number}
                disabled={editing}
                onChange={(e) => {
                  const n = Number(e.target.value);
                  setViewing(n === base.version_number ? null : n);
                }}
              >
                {[...versions].reverse().map((v) => (
                  <option key={v.id} value={v.version_number}>
                    {v.version_number} · {causeLabel(v.cause)}
                    {v.version_number === base.version_number ? " (current)" : ""}
                  </option>
                ))}
              </select>
            </label>
          )}
          <button
            type="button"
            className="btn-ghost btn-sm"
            disabled={editing || exporting}
            title={`Download version ${shown.version_number} as .xlsx, named by its version id -- the same file a bundle embeds`}
            onClick={async () => {
              setExporting(true);
              setError(null);
              try {
                await api.exportLibraryList(orgId, listId, shown.version_number);
              } catch (e) {
                setError(e instanceof Error ? e.message : "Could not export this list");
              } finally {
                setExporting(false);
              }
            }}
          >
            {exporting ? "Exporting…" : `Export v${shown.version_number} (.xlsx)`}
          </button>
          {!editing && !isOld && (
            <button
              type="button"
              className="btn-primary btn-sm"
              disabled={!canWrite}
              title={canWrite ? "Edit rows; saving creates a new version" : READ_ONLY_HINT}
              onClick={startEdit}
            >
              Edit rows
            </button>
          )}
        </div>
      </div>

      {!canWrite && <p className="field-hint">{READ_ONLY_HINT}</p>}

      <section className="list-serves" aria-label="Practices this list serves">
        {detail.control_keys.length > 1 ? (
          <p>
            <strong>
              One list serving {detail.control_keys.length} practices
              {families.length > 1 ? ` in ${families.length} families` : ""}.
            </strong>{" "}
            It appears under each of them and is kept once.
          </p>
        ) : (
          <p>Serves one practice.</p>
        )}
        <span className="list-practices">
          {detail.control_keys.map((k) => (
            <span key={k} className="filter-chip list-practice" title={FAMILY_NAMES[familyOf(k)] ?? ""}>
              {k}
              {canWrite && detail.control_keys.length > 1 && (
                <button
                  type="button"
                  className="btn-ghost btn-sm list-tag-remove"
                  aria-label={`Remove ${k}`}
                  disabled={tagBusy}
                  onClick={() => {
                    setTagError(null);
                    setConfirmRemove(k);
                  }}
                >
                  ×
                </button>
              )}
            </span>
          ))}
        </span>
        {confirmRemove && (
          <div className="doc-notice doc-notice-warn list-tag-confirm">
            <p>
              Remove <strong>{confirmRemove}</strong>? This list will no longer claim to serve it, and the change is
              recorded in its history with your name. It is refused while evidence rests on this list for{" "}
              {confirmRemove}.
            </p>
            <button
              type="button"
              className="btn-primary btn-sm"
              disabled={tagBusy}
              onClick={() => changeTag("remove", confirmRemove)}
            >
              Yes, remove {confirmRemove}
            </button>{" "}
            <button type="button" className="btn-ghost btn-sm" onClick={() => setConfirmRemove(null)}>
              Keep it
            </button>
          </div>
        )}
        {canWrite && (
          <div className="list-tag-add">
            <input
              aria-label="Practice to tag"
              placeholder="CM.L2-3.4.2 or 3.4.2"
              value={tagInput}
              onChange={(e) => setTagInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && tagInput.trim()) {
                  e.preventDefault();
                  changeTag("add", tagInput.trim());
                }
              }}
            />{" "}
            <button
              type="button"
              className="btn-ghost btn-sm"
              disabled={tagBusy || !tagInput.trim()}
              onClick={() => changeTag("add", tagInput.trim())}
            >
              Add tag
            </button>
          </div>
        )}
        {tagError && <div className="form-error">{tagError}</div>}
      </section>

      {detail.merge && (
        <section className="list-merge" aria-label="How this list was merged">
          <p>
            <strong>Merged from two template sheets.</strong> {sheetOf(detail.merge.target_ref)}{" "}
            (<code>{fileOf(detail.merge.target_ref)}</code>) and {sheetOf(detail.merge.absorbed_ref)}{" "}
            (<code>{fileOf(detail.merge.absorbed_ref)}</code>) held the same facts under different
            practices, so they are kept as this one list. {sheetOf(detail.merge.absorbed_ref)}'s columns map
            onto it as recorded below, and its example rows were not carried.
          </p>
          {addedByMerge.size > 0 && (
            <p className="field-hint">
              The example rows come from the {sheetOf(detail.merge.target_ref)} sheet of the source workbook,
              so {[...addedByMerge].join(" and ")} {addedByMerge.size === 1 ? "is" : "are"} empty in every one
              of them. WinGRC does not write content into a template; whoever keeps it can fill them
              with Edit rows.
            </p>
          )}
          <table className="contacts-table list-merge-map">
            <thead>
              <tr>
                <th>{sheetOf(detail.merge.absorbed_ref)} column</th>
                <th>Column in this list</th>
              </tr>
            </thead>
            <tbody>
              {detail.merge.columns.map((c) => (
                <tr key={c.source_column}>
                  <td>{c.source_column}</td>
                  <td>
                    {c.target_column}
                    {c.added && <span className="field-hint"> (added by the merge)</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

      {liveView && (
        <p className="doc-notice">
          This org also has a live <strong>{liveView.sheet_title}</strong> drawn from Scope › Assets. That
          one follows the scope graph; this one is kept by hand. Joining them is a later slice.{" "}
          <button type="button" className="btn-ghost btn-sm" onClick={onOpenLive}>
            Open the live list
          </button>
        </p>
      )}

      <dl className="list-meta">
        {shown.description && (
          <>
            <dt>What goes here</dt>
            <dd>{shown.description}</dd>
          </>
        )}
        {shown.responsible && (
          <>
            <dt>Responsible</dt>
            <dd>{shown.responsible}</dd>
          </>
        )}
        {shown.review_cadence && (
          <>
            <dt>Review cadence</dt>
            <dd>{shown.review_cadence}</dd>
          </>
        )}
        {detail.source_ref && (
          <>
            <dt>Imported from</dt>
            <dd>
              <code>{detail.source_ref}</code>
            </dd>
          </>
        )}
        {versions[0]?.provenance.origin_note && (
          <>
            <dt>Where it came from</dt>
            <dd>
              {versions[0].provenance.origin_note}{" "}
              <span className="field-hint">
                — a note by {versions[0].saved_by ?? "the person who built it"}, not an import source
              </span>
            </dd>
          </>
        )}
        {shown.provenance.via === "api" && shown.provenance.note && (
          <>
            <dt>Change note</dt>
            <dd>{shown.provenance.note}</dd>
          </>
        )}
      </dl>

      <section className="list-history" aria-label="History">
        <h3>History: versions and practices</h3>
        <ol>
          {history.map((h) => {
            if (h.kind === "tag") {
              const e = h.event;
              return (
                <li key={e.id} className="list-history-tag">
                  <span className={`cause-badge tag-${e.change}`}>{e.change === "added" ? "tagged" : "untagged"}</span>{" "}
                  <span className="field-hint">
                    {e.via === "api" ? (e.saved_by ?? "a person") : "WinGRC importer"} · {formatWhen(e.created_at)}
                  </span>
                  <div className="list-history-why">
                    {tagSentence(e)}
                    {e.note ? ` ${e.note}` : ""}
                  </div>
                </li>
              );
            }
            const v = h.version;
            return (
            <li key={v.id} className={v.version_number === shown.version_number ? "is-shown" : undefined}>
              <button
                type="button"
                className="btn-ghost btn-sm"
                disabled={editing || v.version_number === shown.version_number}
                onClick={() => setViewing(v.version_number === base.version_number ? null : v.version_number)}
              >
                v{v.version_number}
              </button>{" "}
              <span className={`cause-badge cause-${v.cause}`}>{causeLabel(v.cause)}</span>{" "}
              <span className="field-hint">
                {versionAuthor(v)} · {formatWhen(v.created_at)}
              </span>
              <div className="list-history-why">{whyVersion(v)}</div>
            </li>
            );
          })}
        </ol>
      </section>

      {(shown.provenance.changelogs ?? []).map((c) => (
        <details key={c.path} className="list-changelog">
          <summary>Changelog: {c.path}</summary>
          <pre>{c.text || "(the changelog file was not readable at import)"}</pre>
        </details>
      ))}

      {isOld && (
        <p className="doc-notice">
          Version {shown.version_number} is a fixed record. Edits start from the current version (
          {base.version_number}).
        </p>
      )}

      {conflict && (
        <div className="doc-notice doc-notice-error list-conflict" role="alert">
          <p>
            <strong>
              Version {conflict.info.current_version_number} was saved by{" "}
              {conflict.info.saved_by ?? "another editor"} at {formatWhen(conflict.info.saved_at)}
            </strong>{" "}
            while you were editing version {conflict.baseNumber}. Your edit was not saved, and it is
            still here.
          </p>
          <p>
            Compared with version {conflict.baseNumber}, theirs adds {conflict.theirChanges.added} row
            {conflict.theirChanges.added === 1 ? "" : "s"} and removes {conflict.theirChanges.removed}.
          </p>
          {conflict.result ? (
            <>
              <p>
                {conflict.result.applied} {conflict.result.applied === 1 ? "change" : "changes"} of yours can
                be re-applied onto version {conflict.info.current_version_number} exactly.
              </p>
              {conflict.result.conflicts.length > 0 && (
                <ul className="list-conflict-rows">
                  {conflict.result.conflicts.map((c, i) => (
                    <li key={i}>
                      {c.kind === "edited" ? (
                        <>
                          You changed <code>{c.original.join(" | ")}</code> to{" "}
                          <code>{c.mine!.join(" | ")}</code>, but the other editor changed or removed that
                          row too. It is left as they have it.{" "}
                          <label>
                            <input
                              type="checkbox"
                              checked={conflict.keepMine.has(i)}
                              onChange={(e) => {
                                const next = new Set(conflict.keepMine);
                                if (e.target.checked) next.add(i);
                                else next.delete(i);
                                setConflict({ ...conflict, keepMine: next });
                              }}
                            />{" "}
                            add my version as a new row
                          </label>
                        </>
                      ) : (
                        <>
                          You removed <code>{c.original.join(" | ")}</code>, but the other editor changed
                          or removed it too. It is left as they have it.
                        </>
                      )}
                    </li>
                  ))}
                </ul>
              )}
              <button type="button" className="btn-primary btn-sm" onClick={reapply}>
                Re-apply my changes onto version {conflict.info.current_version_number}
              </button>{" "}
            </>
          ) : (
            <p>
              The columns changed in version {conflict.info.current_version_number}, so your rows cannot be
              re-applied automatically. Your draft stays below until you leave this list.
            </p>
          )}
          <button type="button" className="btn-ghost btn-sm" onClick={discardAndLoad}>
            Discard my edit and load version {conflict.info.current_version_number}
          </button>
        </div>
      )}

      {notice && <p className="doc-notice doc-notice-warn">{notice}</p>}
      {error && <div className="form-error">{error}</div>}

      <div className="table-scroll">
        <table className="contacts-table list-rows">
          <thead>
            <tr>
              {editing && draftCols
                ? draftCols.map((c, i) => (
                    <th key={i}>
                      <input
                        aria-label={`Column ${i + 1} name`}
                        value={c.name}
                        onChange={(e) =>
                          setDraftCols((cols) => cols && cols.map((x, k) => (k === i ? { ...x, name: e.target.value } : x)))
                        }
                      />
                      <button
                        type="button"
                        className="btn-ghost btn-sm"
                        aria-label={`Remove column ${c.name || i + 1}`}
                        disabled={draftCols.length === 1}
                        onClick={() => removeColumn(i)}
                      >
                        Remove column
                      </button>
                    </th>
                  ))
                : columns.map((c) => (
                <th key={c}>
                  {c}
                  {addedByMerge.has(c) && (
                    <span
                      className="list-merge-col"
                      title={`Added by the merge with ${sheetOf(detail.merge!.absorbed_ref)}`}
                    >
                      {" "}
                      (from {sheetOf(detail.merge!.absorbed_ref).split(" ")[0]})
                    </span>
                  )}
                </th>
              ))}
              {editing && <th aria-label="Row actions" />}
            </tr>
          </thead>
          <tbody>
            {rows
              ? rows.map((r, i) => (
                  <tr key={i} className={r.origin === null ? "list-row-new" : undefined}>
                    {r.cells.map((cell, c) => (
                      <td key={c}>
                        <input
                          aria-label={`${columns[c]}, row ${i + 1}`}
                          value={cell}
                          onChange={(e) => setCell(i, c, e.target.value)}
                        />
                      </td>
                    ))}
                    <td>
                      <button
                        type="button"
                        className="btn-ghost btn-sm"
                        aria-label={`Remove row ${i + 1}`}
                        onClick={() => setDraft((d) => d && d.filter((_, k) => k !== i))}
                      >
                        Remove
                      </button>
                    </td>
                  </tr>
                ))
              : shown.rows.map((r, i) => (
                  <tr key={i}>
                    {r.map((cell, c) => (
                      <td key={c}>
                        <CellText value={cell} />
                      </td>
                    ))}
                  </tr>
                ))}
          </tbody>
        </table>
      </div>
      {!editing && shown.rows.length === 0 && <div className="contacts-empty">No rows in this version.</div>}

      {editing && (
        <div className="list-edit-bar">
          <button
            type="button"
            className="btn-ghost btn-sm"
            onClick={() => setDraft((d) => d && [...d, { origin: null, cells: columns.map(() => "") }])}
          >
            + Add row
          </button>
          <button type="button" className="btn-ghost btn-sm" onClick={addColumn}>
            + Add column
          </button>
          <input
            className="list-note"
            aria-label="Change note"
            placeholder="Change note (optional)"
            value={note}
            onChange={(e) => setNote(e.target.value)}
          />
          {/* Not while a conflict is open: the base is stale until the editor
              re-applies or discards, so a save could only 409 again. */}
          <button
            type="button"
            className="btn-primary btn-sm"
            disabled={!dirty || saving || conflict !== null || badColumns}
            onClick={save}
          >
            {saving
              ? "Saving…"
              : !dirty
                ? "No changes"
                : droppedCells > 0
                  ? `Save as version ${base.version_number + 1}, removing ${droppedCells} filled cell${droppedCells === 1 ? "" : "s"}`
                  : `Save as version ${base.version_number + 1}`}
          </button>
          <button
            type="button"
            className="btn-ghost btn-sm"
            disabled={saving}
            onClick={() => {
              setDraft(null);
              setDraftCols(null);
              setConflict(null);
              setNotice(null);
            }}
          >
            Cancel
          </button>
        </div>
      )}
      {editing && (lossWarning || renamed.length > 0 || badColumns) && (
        <div className="doc-notice doc-notice-warn list-column-changes" aria-label="Column changes">
          {lossWarning && <p>{lossWarning}</p>}
          {renamed.length > 0 && (
            <p>
              Renamed: {renamed.map((r) => `${r.from} → ${r.to}`).join(", ")}. A renamed column keeps its cells.
            </p>
          )}
          {badColumns && <p>Every column needs a name, and no two can share one.</p>}
        </div>
      )}

      <div className="contact-sub list-legend">
        <span className="list-cell-placeholder">[PLACEHOLDER - reason]</span> is known absent for the stated
        reason; <span className="list-cell-example">[PLACEHOLDER]</span> is a template example to replace; —
        is empty.
      </div>
    </div>
  );
}

export function ListLibraryPanel({
  orgId,
  canWrite,
  canBuild = false,
}: {
  orgId: string;
  canWrite: boolean;
  // Building a list is the MSP's (lib/roles.ts canBuildLists); every writer tags.
  canBuild?: boolean;
}) {
  const [lists, setLists] = useState<LibraryListSummary[] | null>(null);
  const [views, setViews] = useState<ListViewSummary[]>([]);
  const [report, setReport] = useState<ListImportReport | null>(null);
  const [family, setFamily] = useState<string | null>(null);
  const [practice, setPractice] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [selection, setSelection] = useState<Selection>(null);
  const [building, setBuilding] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [ls, vs] = await Promise.all([api.listLibrary(orgId), api.listViews(orgId)]);
      setLists(ls);
      setViews(vs);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load lists");
    }
    // The report only adds context; the screen works without it.
    api.getListImportReport(orgId).then(setReport, () => setReport(null));
  }, [orgId]);

  useEffect(() => {
    setSelection(null);
    setBuilding(false);
    load();
  }, [load]);

  const entries: Entry[] = useMemo(
    () => [
      ...(lists ?? []).map((l) => ({
        kind: "library" as const, id: l.id, title: l.title, control_keys: l.control_keys, library: l,
      })),
      ...views.map((v) => ({
        kind: "scope" as const, id: v.id, title: v.sheet_title, control_keys: v.control_ids, scope: v,
      })),
    ],
    [lists, views],
  );

  if (error && !lists) return <div className="form-error">{error}</div>;
  if (!lists) return <div className="loading">Loading lists…</div>;

  if (building) {
    return (
      <ListBuilder
        orgId={orgId}
        onCancel={() => setBuilding(false)}
        onCreated={async (created) => {
          setBuilding(false);
          await load();
          setSelection({ kind: "library", id: created.id });
        }}
      />
    );
  }
  if (selection?.kind === "library") {
    const summary = lists.find((l) => l.id === selection.id);
    const live = views.find((v) => v.id === summary?.projection_view_id) ?? null;
    return (
      <LibraryListView
        orgId={orgId}
        listId={selection.id}
        canWrite={canWrite}
        liveView={live}
        onOpenLive={() => live && setSelection({ kind: "scope", id: live.id })}
        onBack={() => setSelection(null)}
        onSaved={load}
      />
    );
  }
  if (selection?.kind === "scope") {
    return (
      <div>
        <button type="button" className="btn-ghost btn-sm list-back" onClick={() => setSelection(null)}>
          ← All lists
        </button>
        <ListsPanel orgId={orgId} canWrite={canWrite} viewId={selection.id} />
      </div>
    );
  }

  const counts = familyCounts(entries);
  const families = familiesOf({ control_keys: [...counts.keys()] });
  const practices = [...new Set(entries.flatMap((e) => e.control_keys))].sort();
  const shown = sortLists(filterLists(entries, { family, practice, search }));

  return (
    <div className="contacts-panel list-library">
      <div className="list-library-header">
        <div>
          <h2>Lists</h2>
          <div className="contact-sub">
            Every list this org keeps, in three kinds: lists drawn live from scope, lists kept by hand for this
            org, and templates whose rows are examples.
          </div>
        </div>
        {canBuild && (
          <div className="list-library-actions">
            <button type="button" className="btn-primary btn-sm" onClick={() => setBuilding(true)}>
              + Add list
            </button>
          </div>
        )}
      </div>

      <div className="list-filters">
        <div className="list-families" role="group" aria-label="Control family">
          <button
            type="button"
            className={`filter-chip${family === null ? " filter-chip--active" : ""}`}
            onClick={() => setFamily(null)}
          >
            All ({entries.length})
          </button>
          {families.map((f) => (
            <button
              key={f}
              type="button"
              title={FAMILY_NAMES[f] ?? f}
              className={`filter-chip${family === f ? " filter-chip--active" : ""}`}
              onClick={() => {
                setFamily(family === f ? null : f);
                setPractice(null);
              }}
            >
              {f} ({counts.get(f)})
            </button>
          ))}
        </div>
        <label className="field-hint">
          Practice{" "}
          <select
            aria-label="Practice"
            value={practice ?? ""}
            onChange={(e) => setPractice(e.target.value || null)}
          >
            <option value="">All practices</option>
            {practices
              .filter((k) => family === null || familyOf(k) === family)
              .map((k) => (
                <option key={k} value={k}>
                  {k}
                </option>
              ))}
          </select>
        </label>
        <input
          type="search"
          aria-label="Search lists"
          placeholder="Search titles or practices"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
      </div>

      {family && (
        <p className="field-hint">
          {FAMILY_NAMES[family] ?? family}: lists serving any {family} practice. A list that also serves
          other families appears in each of them, once.
        </p>
      )}

      {lists.length === 0 && (
        <p className="field-hint">This org&apos;s list library is empty: nothing has been imported or created here.</p>
      )}

      {shown.length === 0 ? (
        <div className="contacts-empty">No lists match.</div>
      ) : (
        <div className="table-scroll">
          <table className="contacts-table list-index">
            <thead>
              <tr>
                <th>List</th>
                <th>Serves</th>
                <th>Version</th>
                <th>Last changed</th>
                <th>Rows</th>
              </tr>
            </thead>
            {SECTIONS.map((sec) => {
              const inSection = shown.filter((e) => kindOf(e) === sec.kind);
              const filtering = family !== null || practice !== null || search.trim() !== "";
              if (inSection.length === 0 && (filtering || !sec.empty)) return null;
              return (
                <tbody key={sec.kind} className={`list-section list-section--${sec.kind}`}>
                  <tr className="list-section-head">
                    <th colSpan={5} scope="rowgroup">
                      <span className="list-section-title">
                        {sec.title} ({inSection.length})
                      </span>{" "}
                      <span className="field-hint">{inSection.length === 0 ? sec.empty : sec.hint}</span>
                    </th>
                  </tr>
                  {inSection.map((e) => (
                    <tr key={`${e.kind}:${e.id}`}>
                      <td>
                        <button
                          type="button"
                          className="link-button"
                          onClick={() => setSelection({ kind: e.kind, id: e.id })}
                        >
                          {e.title}
                        </button>
                        <div className="list-badges">
                          {e.library && e.library.merged_from.length > 0 && (
                            <span
                              className="affiliation-badge"
                              title={`Also replaces ${e.library.merged_from.join(", ")}`}
                            >
                              merged: {e.library.merged_from.map(sheetOf).join(", ")}
                            </span>
                          )}
                          {e.library?.has_changelog && <span className="affiliation-badge">changelog</span>}
                        </div>
                      </td>
                      <td>
                        <PracticeChips keys={e.control_keys} onPick={setPractice} active={practice} />
                      </td>
                      <td>{e.library ? `v${e.library.version_number}` : "live"}</td>
                      <td>
                        {e.library ? (
                          <>
                            <span title={formatWhen(e.library.updated_at)}>
                              {new Date(e.library.updated_at).toLocaleDateString()}
                            </span>
                            <div className="field-hint">
                              {e.library.cause === "edit"
                                ? (e.library.saved_by ?? "edited")
                                : causeLabel(e.library.cause)}
                            </div>
                          </>
                        ) : (
                          <span className="field-hint">follows Scope</span>
                        )}
                      </td>
                      <td>{e.library ? e.library.row_count : e.scope?.row_count}</td>
                    </tr>
                  ))}
                </tbody>
              );
            })}
          </table>
        </div>
      )}

      {report && <ImportReportNote report={report} />}
    </div>
  );
}
