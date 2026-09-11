import { useEffect, useState } from "react";
import { api } from "../api";
import type { DryRunResult, LiongardEnvironmentMapping, LiongardEnvironmentOption, ScopeChange } from "../types";
import { ScopeChangeDiffTable } from "./ScopeChangeDiffTable";

// D.2: pull devices + users from this org's mapped Liongard Environment.
// Reuses the exact same dry-run -> review -> apply shape as
// AssetImportWizard (workbook import) -- apply is literally the same
// endpoint (api.applyWorkbookImport), since its body doesn't depend on the
// source. The only genuinely new step here is picking/confirming which
// Liongard Environment this org syncs from before a pull can run at all.

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
    if (environments) return;
    setEnvironmentsLoading(true);
    api
      .listLiongardEnvironments(orgId)
      .then(setEnvironments)
      .catch((e) => setMappingError(e instanceof Error ? e.message : "Could not load environments"))
      .finally(() => setEnvironmentsLoading(false));
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
    return dryRun.changes.filter((c, idx) => {
      if (c.change_type !== "new" && c.change_type !== "changed") return false;
      return !excluded.has(idx);
    });
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
          </button>
        </div>
        {error && <div className="form-error">{error}</div>}

        {!dryRun ? (
          <div className="field-hint">
            Pulls devices and users from this Environment and compares them against the current
            scope graph. Nothing is written until you review and confirm the changes below.
          </div>
        ) : (
          <>
            <div className="field-hint">
              {dryRun.changes.length === 0
                ? "No changes detected — the scope graph already matches Liongard."
                : "Review the changes below. Uncheck any row you don't want applied, then confirm."}
            </div>
            {dryRun.warnings.length > 0 && (
              <div className="form-error">
                {dryRun.warnings.map((w, i) => (
                  <div key={i}>⚠ {w}</div>
                ))}
              </div>
            )}
            {dryRun.changes.length > 0 && (
              <ScopeChangeDiffTable changes={dryRun.changes} excluded={excluded} onToggle={toggleExcluded} />
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
