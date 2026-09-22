import { useEffect, useState } from "react";
import { api } from "../api";
import type { DryRunResult, LiongardEnvironmentMapping, LiongardEnvironmentOption, LiongardUnmapResult, ScopeChange } from "../types";
import { ScopeChangeDiffTable } from "./ScopeChangeDiffTable";

// D.2: pull devices + users from this org's mapped Liongard Environment.
// Reuses the same dry-run -> review -> apply shape as AssetImportWizard
// (workbook import) for CHANGED rows -- apply is literally the same
// endpoint (api.applyWorkbookImport), since its body doesn't depend on the
// source. NEW rows are different since the 2026-09-22 fix: the backend
// refuses to apply one directly (it must go through Asset Approvals
// instead, see routers/scope.py:liongard_sync_dry_run's own docstring for
// the incident this closes), and this same dry-run call already queues
// any NEW row into that same approval workflow behind the scenes -- see
// `sync_result_id` on the dry-run response. So NEW rows here are shown
// for visibility only (ScopeChangeDiffTable's newRequiresApproval), never
// selectable for apply.

interface Props {
  orgId: string;
  onClose: () => void;
  onApplied: () => void;
}

export function LiongardSyncWizard({ orgId, onClose, onApplied }: Props) {
  const [mapping, setMapping] = useState<LiongardEnvironmentMapping | null | undefined>(undefined);
  const [mappingError, setMappingError] = useState<string | null>(null);

  const [pickingEnvironment, setPickingEnvironment] = useState(false);
  const [environments, setEnvironments] = useState<LiongardEnvironmentOption[] | null>(null);
  const [environmentsLoading, setEnvironmentsLoading] = useState(false);
  const [selectedEnvId, setSelectedEnvId] = useState<number | "">("");
  const [savingMapping, setSavingMapping] = useState(false);

  const [confirmUnmap, setConfirmUnmap] = useState(false);
  const [unmapping, setUnmapping] = useState(false);
  const [unmapError, setUnmapError] = useState<string | null>(null);
  const [justUnmapped, setJustUnmapped] = useState<LiongardUnmapResult | null>(null);

  const [dryRun, setDryRun] = useState<DryRunResult | null>(null);
  const [excluded, setExcluded] = useState<Set<number>>(new Set());
  const [syncing, setSyncing] = useState(false);
  const [applying, setApplying] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [appliedCount, setAppliedCount] = useState<number | null>(null);

  useEffect(() => {
    api
      .getLiongardEnvironmentMapping(orgId)
      .then(setMapping)
      .catch((e) => {
        setMapping(null);
        setMappingError(e instanceof Error ? e.message : "Could not load Liongard configuration");
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [orgId]);

  function startPickingEnvironment() {
    setPickingEnvironment(true);
    setMappingError(null);
    setJustUnmapped(null);
    if (environments) return;
    setEnvironmentsLoading(true);
    api
      .listLiongardEnvironments(orgId)
      .then(setEnvironments)
      .catch((e) => setMappingError(e instanceof Error ? e.message : "Could not load environments"))
      .finally(() => setEnvironmentsLoading(false));
  }

  async function handleUnmap() {
    setUnmapping(true);
    setUnmapError(null);
    try {
      const result = await api.unmapLiongardEnvironment(orgId);
      setMapping(null);
      setConfirmUnmap(false);
      setJustUnmapped(result);
      // An unmap invalidates any diff already pulled from the now-removed
      // Environment -- same reasoning as a re-map in saveMapping() below.
      setDryRun(null);
      setExcluded(new Set());
    } catch (e) {
      setUnmapError(e instanceof Error ? e.message : "Could not unmap this Environment");
    } finally {
      setUnmapping(false);
    }
  }

  async function saveMapping() {
    if (selectedEnvId === "") return;
    setSavingMapping(true);
    setMappingError(null);
    try {
      const updated = await api.setLiongardEnvironmentMapping(orgId, selectedEnvId);
      setMapping(updated);
      setPickingEnvironment(false);
      // A re-map invalidates any diff already pulled from the prior
      // Environment -- start clean rather than showing a stale review.
      setDryRun(null);
      setExcluded(new Set());
    } catch (e) {
      setMappingError(e instanceof Error ? e.message : "Could not save the environment mapping");
    } finally {
      setSavingMapping(false);
    }
  }

  async function handleSync() {
    setSyncing(true);
    setError(null);
    try {
      const result = await api.liongardSyncDryRun(orgId);
      setDryRun(result);
      setExcluded(new Set());
    } catch (e) {
      setError(e instanceof Error ? e.message : "Sync failed");
    } finally {
      setSyncing(false);
    }
  }

  function toggleExcluded(idx: number) {
    setExcluded((prev) => {
      const next = new Set(prev);
      if (next.has(idx)) next.delete(idx);
      else next.add(idx);
      return next;
    });
  }

  function selectedChanges(): ScopeChange[] {
    if (!dryRun) return [];
    // "new" is excluded here on purpose -- a NEW liongard-sourced row
    // can't be applied directly (the backend refuses it, 409), only
    // approved or rejected in Asset Approvals, where this same dry-run
    // call already queued it (dryRun.sync_result_id).
    return dryRun.changes.filter((c, idx) => {
      if (c.change_type !== "changed") return false;
      return !excluded.has(idx);
    });
  }

  function newEntityCount(): number {
    if (!dryRun) return 0;
    return dryRun.changes.filter((c) => c.change_type === "new").length;
  }

  async function handleApply() {
    setApplying(true);
    setError(null);
    try {
      const result = await api.applyWorkbookImport(orgId, selectedChanges());
      setAppliedCount(result.applied);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Apply failed");
    } finally {
      setApplying(false);
    }
  }

  const applicableCount = selectedChanges().length;

  function renderBody() {
    if (mapping === undefined) {
      return <div className="loading">Loading Liongard configuration…</div>;
    }

    if (appliedCount !== null) {
      return (
        <div className="wizard-complete-msg">
          Applied {appliedCount} change{appliedCount === 1 ? "" : "s"} to the scope graph.
        </div>
      );
    }

    if (mapping === null || pickingEnvironment) {
      return (
        <>
          <div className="field-hint">
            {mapping === null
              ? "This org isn't mapped to a Liongard Environment yet."
              : "Choose a different Liongard Environment for this org."}
          </div>
          {justUnmapped && (
            <div className="field-hint">
              Unmapped {justUnmapped.liongard_environment_name ?? `Environment ${justUnmapped.liongard_environment_id}`}.{" "}
              {justUnmapped.orphaned_scope_entity_count > 0
                ? `${justUnmapped.orphaned_scope_entity_count} scope entities previously imported from it remain in scope, now managed manually.`
                : "No previously-synced scope entities existed to leave behind."}
            </div>
          )}
          {mappingError && <div className="form-error">{mappingError}</div>}
          {!environments && !environmentsLoading && (
            <button className="btn-ghost btn-sm" onClick={startPickingEnvironment}>
              Load Liongard Environments
            </button>
          )}
          {environmentsLoading && <div className="loading">Loading environments…</div>}
          {environments && environments.length === 0 && (
            <div className="field-hint">Liongard returned no environments for this account.</div>
          )}
          {environments && environments.length > 0 && (
            <div className="form-field">
              <label>Liongard Environment</label>
              <select
                value={selectedEnvId}
                onChange={(e) => setSelectedEnvId(e.target.value ? Number(e.target.value) : "")}
              >
                <option value="">Choose…</option>
                {environments.map((env) => (
                  <option key={env.id} value={env.id}>
                    {env.name}
                  </option>
                ))}
              </select>
            </div>
          )}
        </>
      );
    }

    return (
      <>
        <div className="field-hint">
          Mapped to Liongard Environment <strong>{mapping.liongard_environment_name}</strong>.{" "}
          <button className="btn-ghost btn-xs" onClick={startPickingEnvironment}>
            Change
          </button>{" "}
          {!confirmUnmap && (
            <button className="btn-ghost btn-xs btn-destructive" onClick={() => setConfirmUnmap(true)}>
              Unmap
            </button>
          )}
        </div>
        {confirmUnmap && (
          <div className="delete-confirm">
            <span>
              Unmap this Environment?{" "}
              {mapping.liongard_sourced_scope_count > 0
                ? `${mapping.liongard_sourced_scope_count} scope entities already imported from it will remain in scope, now managed manually.`
                : "No scope entities have been imported from it yet."}
            </span>
            <button className="btn-danger btn-sm" onClick={handleUnmap} disabled={unmapping}>
              {unmapping ? "Unmapping…" : "Yes, unmap"}
            </button>
            <button className="btn-ghost btn-sm" onClick={() => setConfirmUnmap(false)} disabled={unmapping}>
              Keep mapped
            </button>
          </div>
        )}
        {unmapError && <div className="form-error">{unmapError}</div>}
        {error && <div className="form-error">{error}</div>}

        {!dryRun ? (
          <div className="field-hint">
            Pulls devices and users from this Environment and compares them against the current
            scope graph. Nothing is written until you review and confirm the changes below.
          </div>
        ) : (
          <>
            {dryRun.pull_status.length > 0 ? (
              dryRun.pull_status.map((p) => (
                <div className="field-hint" key={p.entity_label}>
                  {p.message}
                </div>
              ))
            ) : (
              <div className="field-hint">
                {dryRun.changes.length === 0
                  ? "No changes detected — the scope graph already matches Liongard."
                  : null}
              </div>
            )}
            {newEntityCount() > 0 && (
              <div className="field-hint">
                {newEntityCount()} new device{newEntityCount() === 1 ? "" : "s"}/identit
                {newEntityCount() === 1 ? "y" : "ies"} queued for review in Asset Approvals —
                they can't be applied from here.
              </div>
            )}
            {dryRun.changes.some((c) => c.change_type === "changed") && (
              <div className="field-hint">
                Review the changed rows below. Uncheck any row you don't want applied, then
                confirm.
              </div>
            )}
            {dryRun.warnings.length > 0 && (
              <div className="form-error">
                {dryRun.warnings.map((w, i) => (
                  <div key={i}>⚠ {w}</div>
                ))}
              </div>
            )}
            {dryRun.changes.length > 0 && (
              <ScopeChangeDiffTable
                changes={dryRun.changes}
                excluded={excluded}
                onToggle={toggleExcluded}
                newRequiresApproval
              />
            )}
          </>
        )}
      </>
    );
  }

  function renderFooter() {
    if (mapping === undefined) return null;

    if (appliedCount !== null) {
      return <button className="btn-primary" onClick={onApplied}>Done</button>;
    }

    if (mapping === null || pickingEnvironment) {
      return (
        <>
          <button className="btn-ghost" onClick={mapping === null ? onClose : () => setPickingEnvironment(false)}>
            Cancel
          </button>
          <button
            className="btn-primary"
            onClick={saveMapping}
            disabled={selectedEnvId === "" || savingMapping}
          >
            {savingMapping ? "Saving…" : "Save Environment"}
          </button>
        </>
      );
    }

    if (!dryRun) {
      return (
        <>
          <button className="btn-ghost" onClick={onClose}>Cancel</button>
          <button className="btn-primary" onClick={handleSync} disabled={syncing}>
            {syncing ? "Syncing…" : "Sync Now"}
          </button>
        </>
      );
    }

    return (
      <>
        <button className="btn-ghost" onClick={() => setDryRun(null)}>Back</button>
        <button className="btn-primary" onClick={handleApply} disabled={applying || applicableCount === 0}>
          {applying ? "Applying…" : `Apply ${applicableCount} Change${applicableCount === 1 ? "" : "s"}`}
        </button>
      </>
    );
  }

  return (
    <div className="wizard-overlay" onClick={onClose}>
      <div className="wizard" onClick={(e) => e.stopPropagation()}>
        <div className="wizard-header">
          <span className="wizard-title">Sync from Liongard</span>
          <button className="wizard-close" onClick={onClose} aria-label="Close">×</button>
        </div>
        <div className="wizard-body">{renderBody()}</div>
        <div className="wizard-footer">
          <div style={{ flex: 1 }} />
          {renderFooter()}
        </div>
      </div>
    </div>
  );
}
