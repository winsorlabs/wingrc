/**
 * Library › Lists › Add list (roadmap L.2.2): build a list by hand.
 *
 * The fields are the template header block's, and only those -- title,
 * row 2's practices, What goes here, Responsible and Review cadence, row
 * 10's columns -- so a list built here and one imported from a workbook are
 * the same definition reached two ways. Version 1 records that a person
 * built it from nothing ("built by hand"), never "imported".
 *
 * "Where it came from" is a person's note and is labelled as one. An
 * imported list's source is a file WinGRC read and hashed; the two must
 * never be confusable, so the note is never shown under "Imported from".
 */

import { useState } from "react";

import { api } from "../api";
import type { LibraryListDetail } from "../types";

export function ListBuilder({
  orgId,
  onCreated,
  onCancel,
}: {
  orgId: string;
  onCreated: (created: LibraryListDetail) => void;
  onCancel: () => void;
}) {
  const [title, setTitle] = useState("");
  const [practices, setPractices] = useState<string[]>([]);
  const [practice, setPractice] = useState("");
  const [description, setDescription] = useState("");
  const [responsible, setResponsible] = useState("");
  const [cadence, setCadence] = useState("");
  const [columns, setColumns] = useState<string[]>([""]);
  const [originNote, setOriginNote] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function addPractice() {
    const p = practice.trim();
    if (p && !practices.includes(p)) setPractices([...practices, p]);
    setPractice("");
  }

  const ready = title.trim() !== "" && practices.length > 0 && columns.some((c) => c.trim() !== "");

  async function create() {
    if (!ready || saving) return;
    setSaving(true);
    setError(null);
    try {
      const created = await api.createLibraryList(orgId, {
        title,
        control_keys: practices,
        description,
        responsible,
        review_cadence: cadence,
        columns: columns.filter((c) => c.trim() !== ""),
        origin_note: originNote,
      });
      onCreated(created);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not create this list");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="contacts-panel list-builder">
      <button type="button" className="btn-ghost btn-sm list-back" onClick={onCancel}>
        ← All lists
      </button>
      <h2>Add a list</h2>
      <p className="contact-sub">
        The same fields a list template&apos;s header holds. Creating it saves version 1, recorded as built by
        hand by you. Rows are added afterwards with Edit rows.
      </p>

      <div className="list-builder-form">
        <label>
          Title
          <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Visitor Log" />
        </label>

        <fieldset className="list-builder-practices">
          <legend>Serves (CMMC practices)</legend>
          <div className="list-practices">
            {practices.map((p) => (
              <span key={p} className="filter-chip list-practice">
                {p}{" "}
                <button
                  type="button"
                  className="btn-ghost btn-sm"
                  aria-label={`Remove ${p}`}
                  onClick={() => setPractices(practices.filter((x) => x !== p))}
                >
                  ×
                </button>
              </span>
            ))}
          </div>
          <input
            aria-label="Practice to add"
            placeholder="CM.L2-3.4.1 or 3.4.1"
            value={practice}
            onChange={(e) => setPractice(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                addPractice();
              }
            }}
          />{" "}
          <button type="button" className="btn-ghost btn-sm" onClick={addPractice} disabled={!practice.trim()}>
            Add practice
          </button>
          <p className="field-hint">At least one. A list appears under each practice it serves.</p>
        </fieldset>

        <label>
          What goes here
          <textarea value={description} onChange={(e) => setDescription(e.target.value)} rows={2} />
        </label>
        <label>
          Responsible
          <input value={responsible} onChange={(e) => setResponsible(e.target.value)} />
        </label>
        <label>
          Review cadence
          <input value={cadence} onChange={(e) => setCadence(e.target.value)} placeholder="Annually" />
        </label>

        <fieldset className="list-builder-columns">
          <legend>Columns</legend>
          {columns.map((c, i) => (
            <div key={i} className="list-builder-column">
              <input
                aria-label={`Column ${i + 1}`}
                value={c}
                onChange={(e) => setColumns(columns.map((x, k) => (k === i ? e.target.value : x)))}
              />
              {columns.length > 1 && (
                <button
                  type="button"
                  className="btn-ghost btn-sm"
                  aria-label={`Remove column ${i + 1}`}
                  onClick={() => setColumns(columns.filter((_, k) => k !== i))}
                >
                  Remove
                </button>
              )}
            </div>
          ))}
          <button type="button" className="btn-ghost btn-sm" onClick={() => setColumns([...columns, ""])}>
            + Add column
          </button>
        </fieldset>

        <label>
          Where it came from <span className="field-hint">(your note, optional)</span>
          <textarea
            value={originNote}
            onChange={(e) => setOriginNote(e.target.value)}
            rows={2}
            placeholder="Lifted from our old SharePoint sheet"
          />
          <span className="field-hint">
            Kept as you write it. It is not an import source: a list imported from a template records the
            workbook WinGRC read and its hash, and this list has none.
          </span>
        </label>
      </div>

      {error && <div className="form-error">{error}</div>}
      <div className="list-edit-bar">
        <button type="button" className="btn-primary btn-sm" disabled={!ready || saving} onClick={create}>
          {saving ? "Creating…" : "Create list (version 1)"}
        </button>
        <button type="button" className="btn-ghost btn-sm" disabled={saving} onClick={onCancel}>
          Cancel
        </button>
      </div>
    </div>
  );
}
