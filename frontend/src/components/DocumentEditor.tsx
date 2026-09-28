/**
 * Browser editing for a document body (roadmap N.2 section 1).
 *
 * Three behaviours here are decisions, not implementation details:
 *
 * **Saving creates a version; it never mutates one.** `document_version`
 * is append-only from migration 0059 precisely so this screen could not
 * quietly become an in-place edit, and an approved version is not editable
 * at all -- editing one starts a new draft and leaves the approved record
 * exactly as approved.
 *
 * **No autosave.** A version per keystroke would be absurd, and the
 * alternative (a mutable scratch version) would break append-only. So the
 * working copy lives in this component and in `sessionStorage` for crash/
 * reload recovery only, and a version is written when the author asks for
 * one. The trade-off is accepted deliberately: version history is noisier
 * than a system with autosave-into-a-draft, and the diff view is what makes
 * that navigable (compare any two versions, not just adjacent ones).
 *
 * **Conflicts are detected, not prevented.** See `createDocumentVersion`
 * and the backend's own docstring for why this is optimistic concurrency
 * rather than a lock.
 *
 * The preview pane renders through `MarkdownView` -- the same renderer the
 * ZIP bundle and the PDF use, not the editor's own display -- so what the
 * author signs off on is what the assessor receives.
 */

import { EditorContent, useEditor } from "@tiptap/react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { DocumentConflictError, api } from "../api";
import { documentEditorExtensions } from "../lib/documentSchema";
import { MarkdownView } from "../lib/markdown";
import { docToMarkdown, markdownToDoc } from "../lib/tiptapMarkdown";
import type { DocumentDetail, DocumentSaveConflict, DocumentVersionRow } from "../types";

type Mode = "edit" | "preview" | "split";

/**
 * Per-author, per-version recovery key. Scoped to the version being edited
 * so a recovered draft can never be silently applied on top of a *different*
 * base than the one it was written against.
 */
function draftKey(documentId: string, baseVersionId: string): string {
  return `wingrc.docdraft.${documentId}.${baseVersionId}`;
}

function readDraft(documentId: string, baseVersionId: string): string | null {
  try {
    return sessionStorage.getItem(draftKey(documentId, baseVersionId));
  } catch {
    return null;
  }
}

function writeDraft(documentId: string, baseVersionId: string, body: string): void {
  try {
    sessionStorage.setItem(draftKey(documentId, baseVersionId), body);
  } catch {
    /* private mode / quota -- recovery is a convenience, never the record */
  }
}

function clearDraft(documentId: string, baseVersionId: string): void {
  try {
    sessionStorage.removeItem(draftKey(documentId, baseVersionId));
  } catch {
    /* see writeDraft */
  }
}

export function DocumentEditor({
  orgId,
  doc,
  onSaved,
  onShowDiff,
  canEdit,
}: {
  orgId: string;
  doc: DocumentDetail;
  onSaved: (version: DocumentVersionRow) => void;
  onShowDiff: (fromVersionId: string, toVersionId: string) => void;
  canEdit: boolean;
}) {
  const baseVersion = doc.current_version;
  const baseVersionId = baseVersion?.id ?? "";
  const isApproved = baseVersion?.status === "approved";

  const [mode, setMode] = useState<Mode>("split");
  const [markdown, setMarkdown] = useState<string>(baseVersion?.body ?? "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [conflict, setConflict] = useState<DocumentSaveConflict | null>(null);
  const [recovered, setRecovered] = useState(false);
  const savedBody = useRef<string>(baseVersion?.body ?? "");

  const editor = useEditor(
    {
      extensions: documentEditorExtensions,
      editable: canEdit,
      content: markdownToDoc(baseVersion?.body ?? ""),
      onUpdate: ({ editor: instance }) => {
        const next = docToMarkdown(instance.getJSON() as never);
        setMarkdown(next);
        if (baseVersionId) writeDraft(doc.id, baseVersionId, next);
      },
    },
    [doc.id, baseVersionId],
  );

  // Offer a recovered draft rather than applying it: silently replacing what
  // the server holds with something found in browser storage is exactly the
  // kind of invisible substitution this codebase refuses elsewhere.
  useEffect(() => {
    if (!baseVersionId) return;
    const stored = readDraft(doc.id, baseVersionId);
    if (stored !== null && stored !== (baseVersion?.body ?? "")) setRecovered(true);
  }, [doc.id, baseVersionId, baseVersion?.body]);

  const applyRecovered = useCallback(() => {
    const stored = readDraft(doc.id, baseVersionId);
    if (stored === null || !editor) return;
    editor.commands.setContent(markdownToDoc(stored) as never);
    setMarkdown(stored);
    setRecovered(false);
  }, [doc.id, baseVersionId, editor]);

  const discardRecovered = useCallback(() => {
    clearDraft(doc.id, baseVersionId);
    setRecovered(false);
  }, [doc.id, baseVersionId]);

  const dirty = markdown !== savedBody.current;

  // Unsaved work is lost on navigation because there is no autosave, so say
  // so rather than letting the tab close quietly.
  useEffect(() => {
    if (!dirty) return;
    const warn = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  const save = useCallback(async () => {
    if (!baseVersionId || saving) return;
    setSaving(true);
    setError(null);
    setConflict(null);
    try {
      const version = await api.createDocumentVersion(orgId, doc.id, markdown, baseVersionId);
      savedBody.current = markdown;
      clearDraft(doc.id, baseVersionId);
      onSaved(version);
    } catch (err) {
      if (err instanceof DocumentConflictError) {
        setConflict(err.conflict);
      } else {
        setError(err instanceof Error ? err.message : "Could not save this version.");
      }
    } finally {
      setSaving(false);
    }
  }, [orgId, doc.id, baseVersionId, markdown, saving, onSaved]);

  const toolbar = useMemo(() => {
    if (!editor || !canEdit) return null;
    const btn = (label: string, action: () => void, active = false, title?: string) => (
      <button
        key={label}
        type="button"
        className={active ? "doc-tbtn active" : "doc-tbtn"}
        onClick={action}
        title={title ?? label}
      >
        {label}
      </button>
    );
    return (
      <div className="doc-toolbar" role="toolbar" aria-label="Formatting">
        {btn("B", () => editor.chain().focus().toggleBold().run(), editor.isActive("bold"), "Bold")}
        {btn("I", () => editor.chain().focus().toggleItalic().run(), editor.isActive("italic"), "Italic")}
        {btn("S", () => editor.chain().focus().toggleStrike().run(), editor.isActive("strike"), "Strikethrough")}
        {btn("Code", () => editor.chain().focus().toggleCode().run(), editor.isActive("code"), "Inline code")}
        <span className="doc-toolbar-sep" />
        {btn("H1", () => editor.chain().focus().toggleHeading({ level: 1 }).run(), editor.isActive("heading", { level: 1 }))}
        {btn("H2", () => editor.chain().focus().toggleHeading({ level: 2 }).run(), editor.isActive("heading", { level: 2 }))}
        {btn("H3", () => editor.chain().focus().toggleHeading({ level: 3 }).run(), editor.isActive("heading", { level: 3 }))}
        <span className="doc-toolbar-sep" />
        {btn("• List", () => editor.chain().focus().toggleBulletList().run(), editor.isActive("bulletList"))}
        {btn("1. List", () => editor.chain().focus().toggleOrderedList().run(), editor.isActive("orderedList"))}
        {btn("Quote", () => editor.chain().focus().toggleBlockquote().run(), editor.isActive("blockquote"))}
        {btn("Block", () => editor.chain().focus().toggleCodeBlock().run(), editor.isActive("codeBlock"), "Code block")}
        {btn("—", () => editor.chain().focus().setHorizontalRule().run(), false, "Horizontal rule")}
        <span className="doc-toolbar-sep" />
        {btn("Table", () => editor.chain().focus().insertTable({ rows: 3, cols: 3, withHeaderRow: true }).run(), false, "Insert table (GFM cannot express merged cells, so there is no merge command)")}
        {btn("Link", () => {
          const url = window.prompt("Link URL (http, https or mailto):");
          if (!url) return;
          editor.chain().focus().setLink({ href: url }).run();
        }, editor.isActive("link"))}
        {btn("Unlink", () => editor.chain().focus().unsetLink().run())}
      </div>
    );
  }, [editor, canEdit]);

  if (!baseVersion) {
    return <div className="empty">This document has no version to edit.</div>;
  }

  return (
    <div className="doc-editor">
      <div className="doc-editor-head">
        <div>
          <strong>
            {doc.doc_id} — {doc.title}
          </strong>{" "}
          <span className={`doc-status doc-status-${baseVersion.status}`}>{baseVersion.status.replace("_", " ")}</span>{" "}
          <span className="field-hint">version {baseVersion.version_number}</span>
        </div>
        <div className="doc-editor-actions">
          <div className="doc-mode-switch" role="group" aria-label="View mode">
            {(["edit", "split", "preview"] as Mode[]).map((m) => (
              <button
                key={m}
                type="button"
                className={mode === m ? "doc-tbtn active" : "doc-tbtn"}
                onClick={() => setMode(m)}
              >
                {m}
              </button>
            ))}
          </div>
          {canEdit && (
            <button type="button" className="btn-primary" onClick={save} disabled={saving || !dirty}>
              {saving ? "Saving…" : dirty ? "Save as new version" : "No changes"}
            </button>
          )}
        </div>
      </div>

      {isApproved && canEdit && (
        <p className="doc-notice">
          Version {baseVersion.version_number} is <strong>approved</strong>. Editing does not change
          it — saving creates a new draft version, and the approved record stays exactly as
          approved.
        </p>
      )}

      {recovered && (
        <div className="doc-notice doc-notice-warn">
          An unsaved draft for this version was recovered from this browser.{" "}
          <button type="button" className="btn-ghost btn-sm" onClick={applyRecovered}>
            Restore it
          </button>{" "}
          or{" "}
          <button type="button" className="btn-ghost btn-sm" onClick={discardRecovered}>
            discard it
          </button>
          .
        </div>
      )}

      {conflict && (
        <div className="doc-notice doc-notice-error">
          <p>{conflict.message}</p>
          <p className="field-hint">
            Your text is still here and has not been lost. Version{" "}
            {conflict.current_version_number} is now current.
          </p>
          {conflict.current_version_id && (
            <button
              type="button"
              className="btn-ghost btn-sm"
              onClick={() => onShowDiff(conflict.base_version_id, conflict.current_version_id!)}
            >
              See what changed
            </button>
          )}
        </div>
      )}

      {error && <div className="doc-notice doc-notice-error">{error}</div>}

      <div className={`doc-panes doc-panes-${mode}`}>
        {mode !== "preview" && (
          <div className="doc-pane doc-pane-edit">
            {toolbar}
            <EditorContent editor={editor} className="doc-editor-surface" />
          </div>
        )}
        {mode !== "edit" && (
          <div className="doc-pane doc-pane-preview">
            <div className="doc-pane-label">
              Preview — rendered exactly as the SSP bundle and PDF will render it
            </div>
            <MarkdownView source={markdown} className="doc-body" />
          </div>
        )}
      </div>
    </div>
  );
}
