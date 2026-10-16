/**
 * Library › Lists › Adopt templates (roadmap L.7): copy templates into this
 * org's own lists.
 *
 * Templates stay in the org that holds them -- the MSP's, or this org when
 * the MSP is also its own tenant. Each copy is its own record: it says which
 * template and version it came from, and a later template version reaches it
 * only when someone takes it. The set is shown before anything is written.
 */

import { useEffect, useMemo, useState } from "react";

import { api } from "../api";
import type { AdoptResult, LibraryListSummary, Org } from "../types";

export function AdoptTemplates({
  orgId,
  onDone,
  onOpen,
  onCancel,
}: {
  orgId: string;
  onDone: () => void;
  onOpen: (listId: string) => void;
  onCancel: () => void;
}) {
  const [orgs, setOrgs] = useState<Org[]>([]);
  const [source, setSource] = useState(orgId);
  const [templates, setTemplates] = useState<LibraryListSummary[] | null>(null);
  const [chosen, setChosen] = useState<Set<string>>(new Set());
  const [search, setSearch] = useState("");
  const [review, setReview] = useState<AdoptResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.getOrgs().then(setOrgs, () => setOrgs([]));
  }, []);

  useEffect(() => {
    setTemplates(null);
    setChosen(new Set());
    setReview(null);
    setError(null);
    api.listLibrary(source).then(
      (ls) => setTemplates(ls.filter((l) => l.is_template)),
      (e) => {
        setTemplates([]);
        setError(e instanceof Error ? e.message : "Could not read that org's templates");
      },
    );
  }, [source]);

  const sameOrg = source === orgId;
  const sourceName = orgs.find((o) => o.id === source)?.name ?? "that org";
  const ordered = useMemo(
    () => [...orgs].sort((a, b) => (a.id === orgId ? -1 : b.id === orgId ? 1 : a.name.localeCompare(b.name))),
    [orgs, orgId],
  );
  const shown = (templates ?? []).filter((t) =>
    `${t.title} ${t.control_keys.join(" ")}`.toLowerCase().includes(search.trim().toLowerCase()),
  );

  function toggle(id: string) {
    setChosen((c) => {
      const next = new Set(c);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  async function run(apply: boolean) {
    setBusy(true);
    setError(null);
    try {
      const r = await api.adoptTemplates(orgId, source, [...chosen], apply);
      if (apply) onDone();
      else setReview(r);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not adopt these templates");
    } finally {
      setBusy(false);
    }
  }

  const toCreate = review?.items.filter((i) => i.action === "create") ?? [];
  const already = review?.items.filter((i) => i.action === "already_adopted") ?? [];

  return (
    <div className="contacts-panel list-adopt">
      <button type="button" className="btn-ghost btn-sm list-back" onClick={onCancel}>
        ← All lists
      </button>
      <h2>Adopt templates</h2>
      <p className="contact-sub">
        A copy becomes one of this org&apos;s own lists, with the template&apos;s columns, practices and example rows.
        It records which template and version it came from. The template is not changed, and a newer template
        version reaches the copy only when someone takes it.
      </p>

      {!review && (
        <>
          <label className="list-adopt-source">
            Templates held by{" "}
            <select value={source} onChange={(e) => setSource(e.target.value)}>
              {ordered.length === 0 && <option value={orgId}>this org</option>}
              {ordered.map((o) => (
                <option key={o.id} value={o.id}>
                  {o.name}
                  {o.id === orgId ? " (this org)" : ""}
                </option>
              ))}
            </select>
          </label>
          {sameOrg && (templates?.length ?? 0) > 0 && (
            <p className="field-hint">
              These are this org&apos;s own templates. A copy sits under This org&apos;s lists, beside the template it
              came from, and is the one to fill in.
            </p>
          )}
          {templates === null ? (
            <div className="loading">Loading templates…</div>
          ) : templates.length === 0 ? (
            <div className="contacts-empty">
              {sameOrg ? "This org holds no templates." : `${sourceName} holds no templates.`} Choose the org that holds
              them.
            </div>
          ) : (
            <>
              <input
                className="list-search"
                aria-label="Search templates"
                placeholder="Search templates"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
              <ul className="list-adopt-choices" aria-label="Templates">
                {shown.map((t) => (
                  <li key={t.id}>
                    <label>
                      <input type="checkbox" checked={chosen.has(t.id)} onChange={() => toggle(t.id)} /> {t.title}{" "}
                      <span className="field-hint">
                        {t.control_keys.join(", ")} · version {t.version_number}
                      </span>
                    </label>
                  </li>
                ))}
              </ul>
            </>
          )}
          {error && <div className="form-error">{error}</div>}
          <div className="list-edit-bar">
            <button
              type="button"
              className="btn-primary btn-sm"
              disabled={chosen.size === 0 || busy}
              onClick={() => run(false)}
            >
              {busy ? "Checking…" : `Review ${chosen.size} template${chosen.size === 1 ? "" : "s"}`}
            </button>
            <button type="button" className="btn-ghost btn-sm" onClick={onCancel}>
              Cancel
            </button>
          </div>
        </>
      )}

      {review && (
        <section aria-label="What adopting will write">
          {toCreate.length > 0 && (
            <>
              <h3>
                Will copy {toCreate.length} into this org&apos;s lists
                {sameOrg ? "" : `, from ${sourceName}`}
              </h3>
              <ul>
                {toCreate.map((i) => (
                  <li key={i.template_id}>
                    <strong>{i.template_title}</strong>, template version {i.template_version_number} —{" "}
                    {i.control_keys.join(", ")}.{" "}
                    <span className="field-hint">
                      {i.example_rows > 0
                        ? `${i.example_rows} example row${i.example_rows === 1 ? "" : "s"} to fill in.`
                        : `${i.row_count} row${i.row_count === 1 ? "" : "s"}, none of them marked as examples.`}
                    </span>
                  </li>
                ))}
              </ul>
            </>
          )}
          {already.length > 0 && (
            <>
              <h3>Already in this org&apos;s lists — not copied again</h3>
              <ul>
                {already.map((i) => (
                  <li key={i.template_id}>
                    {i.template_title}{" "}
                    <button type="button" className="link-button" onClick={() => i.list_id && onOpen(i.list_id)}>
                      open this org&apos;s copy
                    </button>
                  </li>
                ))}
              </ul>
            </>
          )}
          {error && <div className="form-error">{error}</div>}
          <div className="list-edit-bar">
            <button
              type="button"
              className="btn-primary btn-sm"
              disabled={toCreate.length === 0 || busy}
              onClick={() => run(true)}
            >
              {busy ? "Copying…" : `Copy ${toCreate.length} into this org's lists`}
            </button>
            <button type="button" className="btn-ghost btn-sm" disabled={busy} onClick={() => setReview(null)}>
              Back
            </button>
          </div>
        </section>
      )}
    </div>
  );
}
