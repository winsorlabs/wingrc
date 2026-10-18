/**
 * A document's tagged objectives, and the editor for them (item X.1).
 *
 * A published document is offered as evidence on each objective it is
 * tagged with (item X); before this no screen could tag one. A document is
 * tagged per objective, where a list is tagged per practice: a policy
 * paragraph can satisfy one objective and not its siblings. Typing a
 * practice ("3.1.1") tags each of its objectives, each its own tag,
 * removable alone. Removing one asks the server first (TagRemovalDialog,
 * L.10's rules) and is refused while evidence rests on it.
 *
 * Read-only roles see the tags and no controls.
 */

import { useState } from "react";

import { api, TagRemovalRefusedError } from "../api";
import type { DocumentDetail, TagRemovalCheck } from "../types";
import { TagRemovalDialog } from "./TagRemovalDialog";

export function DocumentTags({
  orgId,
  doc,
  canTag,
  onChanged,
}: {
  orgId: string;
  doc: DocumentDetail;
  canTag: boolean;
  onChanged: () => void | Promise<void>;
}) {
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<{ id: string; key: string } | null>(null);
  const [check, setCheck] = useState<TagRemovalCheck | "failed" | null>(null);

  async function add() {
    const typed = input.trim();
    if (!typed || busy) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const r = await api.addDocumentObjectiveTag(orgId, doc.id, typed);
      const said: string[] = [];
      if (r.added.length > 0) said.push(`Tagged ${r.added.map((t) => t.objective_key).join(", ")}.`);
      if (r.already_tagged.length > 0)
        said.push(`Already tagged: ${r.already_tagged.map((t) => t.objective_key).join(", ")}.`);
      setNotice(said.join(" "));
      setInput("");
      await onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not tag this document");
    } finally {
      setBusy(false);
    }
  }

  function askRemove(id: string, key: string) {
    setError(null);
    setNotice(null);
    setConfirm({ id, key });
    setCheck(null);
    Promise.resolve()
      .then(() => api.checkDocumentTagRemoval(orgId, doc.id, id))
      .then(
        (r) => setCheck(r ?? "failed"),
        () => setCheck("failed"),
      );
  }

  async function remove() {
    if (!confirm || busy) return;
    setBusy(true);
    try {
      await api.removeDocumentObjectiveTag(orgId, doc.id, confirm.id);
      setConfirm(null);
      await onChanged();
    } catch (e) {
      // Refused on the race between check and click: said as the check says it.
      if (e instanceof TagRemovalRefusedError) setCheck(e.check);
      else setError(e instanceof Error ? e.message : "Could not remove this tag");
    } finally {
      setBusy(false);
    }
  }

  const tags = doc.tagged_objectives;
  return (
    <section className="doc-tags" aria-label="Tagged objectives">
      <h3>Tagged objectives</h3>
      {tags.length === 0 ? (
        <p className="field-hint">
          Not tagged with any objective, so it is offered as evidence nowhere.
        </p>
      ) : (
        <>
          <p className="field-hint">
            Once a version is published, it is offered as evidence on each of these, and someone attaches it there.
          </p>
          <ul className="doc-tag-list">
            {tags.map((t) => (
              <li key={t.objective_id}>
                <span className="filter-chip list-practice">{t.objective_key}</span> {t.text}
                {canTag && (
                  <button
                    type="button"
                    className="btn-ghost btn-sm list-tag-remove"
                    aria-label={`Remove ${t.objective_key}`}
                    disabled={busy}
                    onClick={() => askRemove(t.objective_id, t.objective_key)}
                  >
                    ×
                  </button>
                )}
              </li>
            ))}
          </ul>
        </>
      )}
      {confirm && (
        <TagRemovalDialog
          tag={confirm.key}
          subject={doc.title}
          consequence="This document will no longer be offered as evidence for it, and the change is recorded in its history with your name."
          check={check}
          busy={busy}
          onConfirm={() => void remove()}
          onCancel={() => setConfirm(null)}
        />
      )}
      {canTag && (
        <div className="list-tag-add">
          <input
            aria-label="Objective to tag"
            placeholder="AC.L2-3.1.1[a], or 3.1.1 for all its objectives"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && input.trim()) {
                e.preventDefault();
                void add();
              }
            }}
          />{" "}
          <button type="button" className="btn-ghost btn-sm" disabled={busy || !input.trim()} onClick={() => void add()}>
            Add tag
          </button>
        </div>
      )}
      {notice && <p className="field-hint">{notice}</p>}
      {error && <div className="form-error">{error}</div>}
    </section>
  );
}
