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

import { api, ListConflictError, TagRemovalRefusedError } from "../api";
import { AdoptTemplates } from "./AdoptTemplates";
import { TagRemovalDialog } from "./TagRemovalDialog";
import { FAMILY_NAMES } from "../lib/families";
import {
  adoptedFromText,
  causeLabel,
  changeCounts,
  distributionText,
  exampleRowsText,
  tagAuthor,
  updateAvailableText,
  columnLossWarning,
  columnsChanged,
  columnsFrom,
  draftFrom,
  droppedColumns,
  historyEntries,
  linkSentence,
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
  TakeTemplateResult,
  LibraryListSummary,
  LibraryListVersion,
  ListImportReport,
  ListEvidenceStatus,
  ListLinkEvent,
  ListTagEvent,
  TagRemovalCheck,
  ListViewSummary,
} from "../types";
import { ListBuilder } from "./ListBuilder";
import { ListRowsTable } from "./ListRows";
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
// backend list_library.EXAMPLE_TOKEN: a fill-in anywhere in the cell.
const FILL_IN = /\[[A-Z][A-Z0-9_]*\]/;

function CellText({ value }: { value: string }) {
  const reason = PLACEHOLDER_REASON.exec(value);
  if (reason) {
    return (
      <span className="list-cell-placeholder" title={`Known absent: ${reason[1]}`}>
        {value}
      </span>
    );
  }
  if (FILL_IN.test(value)) {
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

/** Where this list is evidence, and whether it can be (L.4). A link stays
 * on the version it named; the list moving on is said, not fixed here --
 * an objective's evidence is where a link is moved forward. */
function ListAsEvidence({ status, isTemplate }: { status: ListEvidenceStatus; isTemplate: boolean }) {
  if (isTemplate && status.links.length === 0) return null;
  const drifted = status.links.filter((l) => l.drift);
  return (
    <section className="list-evidence" aria-label="As evidence">
      {status.offered ? (
        status.links.length === 0 && (
          <p className="field-hint">
            Offered as evidence for the practices above. Nothing is attached until someone attaches it from an
            objective&apos;s evidence.
          </p>
        )
      ) : (
        <p className="field-hint">Not offered as evidence: {status.not_offered}.</p>
      )}
      {status.links.length > 0 && (
        <>
          <p>
            Evidence for {status.links.length} objective{status.links.length === 1 ? "" : "s"}:
          </p>
          <ul className="list-evidence-links">
            {status.links.map((l) => (
              <li key={`${l.control_state_id}-${l.linked_version_id}`}>
                {l.assessment_name}: {l.control_key}[{l.objective_key}], version {l.linked_version_number}
              </li>
            ))}
          </ul>
        </>
      )}
      {drifted.length > 0 && (
        <div className="doc-notice doc-notice-warn">
          {[...new Set(drifted.map((l) => l.drift))].map((d) => (
            <p key={d}>{d}</p>
          ))}
          <p className="field-hint">Each objective&apos;s evidence is where its link is moved forward.</p>
        </div>
      )}
    </section>
  );
}

function LibraryListView({
  orgId,
  listId,
  canWrite,
  canDefine,
  liveView,
  onOpenLive,
  onOpenList,
  onBack,
  onSaved,
}: {
  orgId: string;
  listId: string;
  canWrite: boolean;
  // Columns and tags define a list; a customer contact fills rows in.
  canDefine: boolean;
  liveView: ListViewSummary | null;
  onOpenLive: () => void;
  onOpenList: (listId: string) => void;
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
  // What the server says removing confirmRemove would do; null while asking.
  const [removal, setRemoval] = useState<TagRemovalCheck | "failed" | null>(null);
  const [linkEvents, setLinkEvents] = useState<ListLinkEvent[]>([]);
  const [take, setTake] = useState<TakeTemplateResult | null>(null);
  const [takeBusy, setTakeBusy] = useState(false);
  const [takeError, setTakeError] = useState<string | null>(null);
  const [asEvidence, setAsEvidence] = useState<ListEvidenceStatus | null>(null);

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
    // So do its evidence links (L.10).
    Promise.resolve()
      .then(() => api.getListLinkEvents(orgId, listId))
      .then(
        (es) => setLinkEvents(es ?? []),
        () => setLinkEvents([]),
      );
    // So does where it is evidence (L.4).
    Promise.resolve()
      .then(() => api.listEvidenceStatus(orgId, listId))
      .then(
        (st) => setAsEvidence(st ?? null),
        () => setAsEvidence(null),
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
    setTake(null);
    setTakeError(null);
    setAsEvidence(null);
    load();
  }, [load]);

  async function takeTemplate(apply: boolean, overwriteEdit?: string | null) {
    setTakeBusy(true);
    setTakeError(null);
    try {
      const r = await api.takeTemplateVersion(orgId, listId, apply, overwriteEdit);
      if (apply) {
        setTake(null);
        await load();
        onSaved();
      } else {
        setTake(r);
      }
    } catch (e) {
      setTakeError(e instanceof Error ? e.message : "Could not take the template version");
    } finally {
      setTakeBusy(false);
    }
  }

  async function copyIntoThisOrg() {
    setTakeBusy(true);
    setTakeError(null);
    try {
      const r = await api.adoptTemplates(orgId, orgId, [listId], true);
      onSaved();
      const id = r.items[0]?.list_id;
      if (id) onOpenList(id);
    } catch (e) {
      setTakeError(e instanceof Error ? e.message : "Could not copy this template");
    } finally {
      setTakeBusy(false);
    }
  }

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

  // The confirmation says what the server computed for this tag, never the
  // rule in the abstract: a screen must not state a result it never computed.
  function askRemove(key: string) {
    setTagError(null);
    setConfirmRemove(key);
    setRemoval(null);
    Promise.resolve()
      .then(() => api.checkTagRemoval(orgId, listId, key))
      .then(
        (r) => setRemoval(r ?? "failed"),
        () => setRemoval("failed"),
      );
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
      // Refused on the race between check and click: said as the check says it.
      if (e instanceof TagRemovalRefusedError) setRemoval(e.check);
      else setTagError(e instanceof Error ? e.message : "Could not change this list's practices");
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
  const history = historyEntries(versions.length ? versions : [base], tagEvents, linkEvents);

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
              {canDefine && detail.control_keys.length > 1 && (
                <button
                  type="button"
                  className="btn-ghost btn-sm list-tag-remove"
                  aria-label={`Remove ${k}`}
                  disabled={tagBusy}
                  onClick={() => askRemove(k)}
                >
                  ×
                </button>
              )}
            </span>
          ))}
        </span>
        {confirmRemove && (
          <TagRemovalDialog
            tag={confirmRemove}
            subject={base.title}
            consequence="This list will no longer claim to serve it, and the change is recorded in its history with your name."
            check={removal}
            busy={tagBusy}
            onConfirm={() => changeTag("remove", confirmRemove)}
            onCancel={() => setConfirmRemove(null)}
          />
        )}
        {canDefine && (
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

      {asEvidence && <ListAsEvidence status={asEvidence} isTemplate={detail.is_template} />}

      {/* Actionable first, then the rows: the rows are what this page is for (L.8). */}
      {detail.adopted_from && (detail.adopted_from.update_available || takeError) && (
        <section aria-label="Newer template version">
          {detail.adopted_from.update_available && (
            <div className="doc-notice doc-notice-warn">
              <p>{updateAvailableText(detail.adopted_from)}</p>
              {canDefine && !take && (
                <button
                  type="button"
                  className="btn-ghost btn-sm"
                  disabled={takeBusy || editing}
                  onClick={() => takeTemplate(false)}
                >
                  Review template version {detail.adopted_from.head_version_number}
                </button>
              )}
              {!canDefine && (
                <p className="field-hint">Someone who defines lists here and in the template&apos;s org takes it.</p>
              )}
              {take && (
                <div aria-label="What taking it changes">
                  <p>
                    Taking template version {take.to_template_version} writes a new version of this list.{" "}
                    {take.rows_from === "copy"
                      ? "This org's rows are kept, matched to the template's columns by name."
                      : "Its rows are still the template's examples, so the new examples replace them."}
                    {take.columns_added.length > 0 && ` Columns added: ${take.columns_added.join(", ")}.`}
                    {take.columns_removed.length > 0 && ` Columns removed: ${take.columns_removed.join(", ")}.`}
                    {take.tag_add.length > 0 && ` Practices added: ${take.tag_add.join(", ")}.`}
                    {take.tag_remove.length > 0 && ` Practices removed: ${take.tag_remove.join(", ")}.`}
                  </p>
                  {take.edit_ref && (
                    <p>
                      <strong>It replaces an edit made here</strong>
                      {take.person_changed.length > 0 && ` (${take.person_changed.join(", ")} changed since the copy took the template)`}
                      {take.dropped_cells > 0 &&
                        `${take.person_changed.length > 0 ? "; and" : ""} ${take.dropped_cells} filled cell${take.dropped_cells === 1 ? "" : "s"} in columns the template dropped will not be in the new version`}
                      . The current version keeps every value.
                    </p>
                  )}
                  <button
                    type="button"
                    className="btn-primary btn-sm"
                    disabled={takeBusy}
                    onClick={() => takeTemplate(true, take.edit_ref)}
                  >
                    {take.edit_ref
                      ? `Take version ${take.to_template_version}, replacing the edit in version ${take.edit_ref.split("@v")[1]}`
                      : `Take version ${take.to_template_version}`}
                  </button>{" "}
                  <button type="button" className="btn-ghost btn-sm" onClick={() => setTake(null)}>
                    Not now
                  </button>
                </div>
              )}
            </div>
          )}
          {takeError && <div className="form-error">{takeError}</div>}
        </section>
      )}

      {!detail.is_template && !isOld && exampleRowsText(base.example_rows, base.rows.length) && (
        <p className="doc-notice">{exampleRowsText(base.example_rows, base.rows.length)}</p>
      )}

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

      <ListRowsTable title={shown.title} rowCount={shown.rows.length} columnCount={columns.length}>
        <thead>
          <tr>
            {editing && draftCols && canDefine
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
      </ListRowsTable>
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
          {canDefine && (
            <button type="button" className="btn-ghost btn-sm" onClick={addColumn}>
              + Add column
            </button>
          )}
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
        reason; <span className="list-cell-example">[PLACEHOLDER]</span>, or another fill-in such
        as <span className="list-cell-example">[CUSTOMER_NAME]</span>, marks a template example to replace; —
        is empty.
      </div>

      {/* Reference about the rows: read once, so under them. */}
      {detail.adopted_from && (
        <section className="list-adoption" aria-label="Where this copy came from">
          <p>
            {adoptedFromText(detail.adopted_from)}{" "}
            <span className="field-hint">
              This list is this org&apos;s own; the template is unchanged by anything done here.
            </span>
          </p>
        </section>
      )}

      {detail.is_template && (
        <section className="list-adoption" aria-label="Copies of this template">
          <p>
            {distributionText(detail.distribution) ?? "No org has adopted this template yet."}{" "}
            <span className="field-hint">
              A copy moves to a newer version of this template only when someone takes it.
            </span>
          </p>
          {detail.own_copy_id ? (
            <button type="button" className="btn-ghost btn-sm" onClick={() => onOpenList(detail.own_copy_id!)}>
              Open this org&apos;s copy
            </button>
          ) : (
            canDefine && (
              <button type="button" className="btn-ghost btn-sm" disabled={takeBusy} onClick={copyIntoThisOrg}>
                Copy into this org&apos;s lists
              </button>
            )
          )}
          {takeError && <div className="form-error">{takeError}</div>}
        </section>
      )}

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

      <details className="list-history" aria-label="History">
        <summary>
          History: versions, practices and evidence ({history.length})
        </summary>
          <ol>
            {history.map((h) => {
              if (h.kind === "link") {
                const l = h.link;
                return (
                  <li key={l.id} className="list-history-link">
                    <span className={`cause-badge link-${l.change}`}>
                      {l.change === "linked" ? "attached" : l.change === "moved_forward" ? "moved forward" : "detached"}
                    </span>{" "}
                    <span className="field-hint">
                      {l.saved_by ?? "a person"} · {formatWhen(l.created_at)}
                    </span>
                    <div className="list-history-why">{linkSentence(l)}</div>
                  </li>
                );
              }
              if (h.kind === "tag") {
                const e = h.event;
                return (
                  <li key={e.id} className="list-history-tag">
                    <span className={`cause-badge tag-${e.change}`}>{e.change === "added" ? "tagged" : "untagged"}</span>{" "}
                    <span className="field-hint">
                      {tagAuthor(e)} · {formatWhen(e.created_at)}
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
        {(shown.provenance.changelogs ?? []).map((c) => (
          <details key={c.path} className="list-changelog">
            <summary>Changelog: {c.path}</summary>
            <pre>{c.text || "(the changelog file was not readable at import)"}</pre>
          </details>
        ))}
      </details>
    </div>
  );
}

export function ListLibraryPanel({
  orgId,
  canWrite,
  canDefine = false,
}: {
  orgId: string;
  canWrite: boolean;
  // Building a list, its columns and its tags are the MSP's (lib/roles.ts
  // canDefineLists); a customer contact fills rows in.
  canDefine?: boolean;
}) {
  const [lists, setLists] = useState<LibraryListSummary[] | null>(null);
  const [views, setViews] = useState<ListViewSummary[]>([]);
  const [report, setReport] = useState<ListImportReport | null>(null);
  const [family, setFamily] = useState<string | null>(null);
  const [practice, setPractice] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [selection, setSelection] = useState<Selection>(null);
  const [building, setBuilding] = useState(false);
  const [adopting, setAdopting] = useState(false);
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
    setAdopting(false);
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
  if (adopting) {
    return (
      <AdoptTemplates
        orgId={orgId}
        onCancel={() => setAdopting(false)}
        onOpen={async (id) => {
          setAdopting(false);
          await load();
          setSelection({ kind: "library", id });
        }}
        onDone={async () => {
          setAdopting(false);
          await load();
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
        canDefine={canDefine}
        liveView={live}
        onOpenLive={() => live && setSelection({ kind: "scope", id: live.id })}
        onOpenList={(id) => {
          load();
          setSelection({ kind: "library", id });
        }}
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
        {canDefine && (
          <div className="list-library-actions">
            <button type="button" className="btn-ghost btn-sm" onClick={() => setAdopting(true)}>
              Adopt templates
            </button>{" "}
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
                          {e.library?.adopted_from && (
                            <span className="affiliation-badge" title={adoptedFromText(e.library.adopted_from)}>
                              from template v{e.library.adopted_from.based_on_version_number}
                            </span>
                          )}
                          {e.library?.adopted_from?.update_available && (
                            <span className="affiliation-badge list-update-badge">
                              template v{e.library.adopted_from.head_version_number} available
                            </span>
                          )}
                          {e.library && !e.library.is_template && e.library.example_rows > 0 && (
                            <span className="affiliation-badge">
                              {e.library.example_rows} example row{e.library.example_rows === 1 ? "" : "s"} to fill in
                            </span>
                          )}
                          {e.library && e.library.adopted_copies > 0 && (
                            <span className="affiliation-badge">
                              adopted by {e.library.adopted_copies} org{e.library.adopted_copies === 1 ? "" : "s"}
                            </span>
                          )}
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
