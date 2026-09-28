/**
 * The document library screen (roadmap N.1/N.2) — list, create, edit,
 * diff, history.
 *
 * Lives under the Library nav category, whose Policies/Procedures/Plans
 * subitems were placeholders until this slice; `docType` is how they filter.
 * "Lists" and "Baselines" stay separate features and are still disabled in
 * the nav — Lists in particular is a *view over the scope graph*, not a
 * document, however much the `list` doc_type looks like it should be the
 * same thing.
 */

import { Suspense, lazy, useCallback, useEffect, useState } from "react";

import { api } from "../api";
import { deriveCanWrite } from "../lib/roles";
import { DocumentDiffView } from "./DocumentDiffView";
import { DocumentHistoryPanel } from "./DocumentHistoryPanel";
import type { Contact, DocumentDetail, DocumentRow, DocumentType } from "../types";

// TipTap and ProseMirror are ~700 kB of the production bundle, and every
// screen that is not this one pays for them on first load otherwise. Split
// out so the editor arrives only when someone actually opens a document.
// DocumentDiffView and DocumentHistoryPanel are NOT split: they are plain
// React with no heavy dependency, and read-only roles (c3pao_assessor) reach
// them without ever loading the editor at all.
const DocumentEditor = lazy(() =>
  import("./DocumentEditor").then((m) => ({ default: m.DocumentEditor })),
);

const DOC_TYPES: DocumentType[] = ["policy", "procedure", "plan", "list", "sop", "form", "other"];

type Tab = "edit" | "history" | "diff";

interface DiffTarget {
  fromVersionId?: string;
  toVersionId?: string;
}

function CreateForm({
  orgId,
  defaultType,
  onCreated,
  onCancel,
}: {
  orgId: string;
  defaultType: DocumentType;
  onCreated: (doc: DocumentDetail) => void;
  onCancel: () => void;
}) {
  const [docId, setDocId] = useState("");
  const [title, setTitle] = useState("");
  const [docType, setDocType] = useState<DocumentType>(defaultType);
  const [cadence, setCadence] = useState(12);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      const created = await api.createDocument(orgId, {
        doc_id: docId.trim(),
        doc_type: docType,
        title: title.trim(),
        cadence_months: cadence,
        body: "",
      });
      onCreated(created);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not create the document.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="doc-create">
      <h3>New document</h3>
      <label>
        Document ID
        <input
          value={docId}
          onChange={(e) => setDocId(e.target.value)}
          placeholder="AC-POL-001"
        />
        <span className="field-hint">
          Stable and human-readable. Appears in the evidence record once published, so it is
          treated like a filename: letters, digits, dot, underscore and hyphen only.
        </span>
      </label>
      <label>
        Title
        <input
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="Access Control Policy"
        />
      </label>
      <label>
        Type
        <select value={docType} onChange={(e) => setDocType(e.target.value as DocumentType)}>
          {DOC_TYPES.map((t) => (
            <option key={t} value={t}>
              {t}
            </option>
          ))}
        </select>
      </label>
      <label>
        Review cadence (months)
        <input
          type="number"
          min={1}
          value={cadence}
          onChange={(e) => setCadence(Number(e.target.value))}
        />
        <span className="field-hint">
          How often this document is due for re-approval. Separate from the org-wide
          user/device review cycle.
        </span>
      </label>
      {error && <div className="doc-notice doc-notice-error">{error}</div>}
      <div className="doc-create-actions">
        <button
          type="button"
          className="btn-primary"
          onClick={submit}
          disabled={busy || !docId.trim() || !title.trim()}
        >
          {busy ? "Creating…" : "Create"}
        </button>
        <button type="button" className="btn-ghost btn-sm" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </div>
  );
}

function PublishControl({
  orgId,
  doc,
  contacts,
  onPublished,
}: {
  orgId: string;
  doc: DocumentDetail;
  contacts: Contact[];
  onPublished: () => void;
}) {
  const [contactId, setContactId] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const current = doc.current_version;
  if (!current) return null;
  if (current.status === "approved") {
    return (
      <p className="field-hint">
        Version {current.version_number} is approved
        {current.approved_at ? ` (${new Date(current.approved_at).toLocaleDateString()})` : ""}.
      </p>
    );
  }
  if (current.status === "superseded") return null;

  const publish = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.publishDocument(orgId, doc.id, contactId);
      onPublished();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not publish.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="doc-publish">
      <label>
        Approved by
        <select value={contactId} onChange={(e) => setContactId(e.target.value)}>
          <option value="">Select a contact…</option>
          {contacts.map((c) => (
            <option key={c.id} value={c.id}>
              {c.name} ({c.affiliation})
            </option>
          ))}
        </select>
      </label>
      <button type="button" className="btn-primary" onClick={publish} disabled={busy || !contactId}>
        {busy ? "Publishing…" : `Publish version ${current.version_number}`}
      </button>
      <p className="field-hint">
        Publishing approves this version and attaches it as evidence to every objective the
        document is tagged with. It does <strong>not</strong> mark any control met — an engineer
        reviews and does that by hand.
      </p>
      {error && <div className="doc-notice doc-notice-error">{error}</div>}
    </div>
  );
}

export function DocumentsPanel({
  orgId,
  currentUserRole,
  docType,
}: {
  orgId: string;
  currentUserRole: string;
  docType?: DocumentType;
}) {
  const canEdit = deriveCanWrite(currentUserRole);

  const [docs, setDocs] = useState<DocumentRow[] | null>(null);
  const [selected, setSelected] = useState<DocumentDetail | null>(null);
  const [tab, setTab] = useState<Tab>("edit");
  const [diffTarget, setDiffTarget] = useState<DiffTarget>({});
  const [creating, setCreating] = useState(false);
  const [contacts, setContacts] = useState<Contact[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  const loadList = useCallback(async () => {
    setError(null);
    try {
      setDocs(await api.listDocuments(orgId, docType));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load documents.");
    }
  }, [orgId, docType]);

  useEffect(() => {
    void loadList();
  }, [loadList]);

  useEffect(() => {
    api.getContacts(orgId).then(setContacts).catch(() => setContacts([]));
  }, [orgId]);

  // Clear the selection when the type filter changes to one that excludes it,
  // rather than leaving a policy open under the "Procedures" heading.
  useEffect(() => {
    if (selected && docType && selected.doc_type !== docType) setSelected(null);
  }, [selected, docType]);

  const openDoc = useCallback(
    async (documentId: string) => {
      try {
        const detail = await api.getDocument(orgId, documentId);
        setSelected(detail);
        setTab("edit");
        setDiffTarget({});
      } catch (e) {
        setError(e instanceof Error ? e.message : "Could not open the document.");
      }
    },
    [orgId],
  );

  const refreshSelected = useCallback(async () => {
    if (!selected) return;
    const detail = await api.getDocument(orgId, selected.id);
    setSelected(detail);
    setReloadKey((k) => k + 1);
    void loadList();
  }, [orgId, selected, loadList]);

  if (error && !docs) return <div className="doc-notice doc-notice-error">{error}</div>;
  if (!docs) return <div className="empty">Loading documents…</div>;

  return (
    <div className="doc-library">
      <div className="doc-library-list">
        <div className="doc-library-head">
          <h2>{docType ? `${docType}s` : "Documents"}</h2>
          {canEdit && (
            <button type="button" className="btn-primary" onClick={() => setCreating(true)}>
              New
            </button>
          )}
        </div>
        {docs.length === 0 ? (
          <p className="field-hint">
            No {docType ? `${docType} documents` : "documents"} yet.
            {canEdit ? " Create one to get started." : ""}
          </p>
        ) : (
          <ul className="doc-list">
            {docs.map((d) => (
              <li key={d.id}>
                <button
                  type="button"
                  className={selected?.id === d.id ? "doc-list-item active" : "doc-list-item"}
                  onClick={() => void openDoc(d.id)}
                >
                  <span className="doc-list-id">{d.doc_id}</span>
                  <span className="doc-list-title">{d.title}</span>
                  {d.current_version && (
                    <span className={`doc-status doc-status-${d.current_version.status}`}>
                      {d.current_version.status.replace("_", " ")} v
                      {d.current_version.version_number}
                    </span>
                  )}
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="doc-library-detail">
        {creating ? (
          <CreateForm
            orgId={orgId}
            defaultType={docType ?? "policy"}
            onCreated={(created) => {
              setCreating(false);
              setSelected(created);
              setTab("edit");
              void loadList();
            }}
            onCancel={() => setCreating(false)}
          />
        ) : !selected ? (
          <div className="empty">Select a document.</div>
        ) : (
          <>
            <div className="doc-tabs" role="tablist">
              {(["edit", "history", "diff"] as Tab[]).map((t) => (
                <button
                  key={t}
                  type="button"
                  role="tab"
                  aria-selected={tab === t}
                  className={tab === t ? "doc-tab active" : "doc-tab"}
                  onClick={() => setTab(t)}
                >
                  {t === "edit" ? (canEdit ? "Edit" : "Read") : t === "history" ? "History" : "Compare"}
                </button>
              ))}
            </div>

            {tab === "edit" && (
              <>
                <Suspense fallback={<div className="empty">Loading editor…</div>}>
                  <DocumentEditor
                    orgId={orgId}
                    doc={selected}
                    canEdit={canEdit}
                    onSaved={() => void refreshSelected()}
                    onShowDiff={(from, to) => {
                      setDiffTarget({ fromVersionId: from, toVersionId: to });
                      setTab("diff");
                    }}
                  />
                </Suspense>
                {canEdit && (
                  <PublishControl
                    orgId={orgId}
                    doc={selected}
                    contacts={contacts}
                    onPublished={() => void refreshSelected()}
                  />
                )}
              </>
            )}

            {tab === "history" && (
              <DocumentHistoryPanel
                orgId={orgId}
                documentId={selected.id}
                reloadKey={reloadKey}
                onCompare={(from, to) => {
                  setDiffTarget({ fromVersionId: from, toVersionId: to });
                  setTab("diff");
                }}
              />
            )}

            {tab === "diff" && (
              <DocumentDiffView
                orgId={orgId}
                documentId={selected.id}
                fromVersionId={diffTarget.fromVersionId}
                toVersionId={diffTarget.toVersionId}
                onClose={() => setTab("edit")}
              />
            )}
          </>
        )}
      </div>
    </div>
  );
}
