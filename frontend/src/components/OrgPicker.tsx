import { useEffect, useState } from "react";
import { api, getCachedAssessmentId, setCachedAssessmentId } from "../api";
import type { Assessment, Framework, Org } from "../types";

interface Props {
  onEnterBoard: (org: Org, assessment: Assessment) => void;
}

export function OrgPicker({ onEnterBoard }: Props) {
  const [orgs, setOrgs] = useState<Org[]>([]);
  const [selectedOrg, setSelectedOrg] = useState<Org | null>(null);
  const [assessments, setAssessments] = useState<Assessment[]>([]);
  const [frameworks, setFrameworks] = useState<Framework[]>([]);
  const [newOrgName, setNewOrgName] = useState("");
  const [creating, setCreating] = useState(false);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.getOrgs().then(setOrgs).catch(() => setError("Could not load orgs"));
    api.getFrameworks().then(setFrameworks).catch(() => {});
  }, []);

  function selectOrg(org: Org) {
    setSelectedOrg(org);
    setAssessments([]);
    api.getAssessments(org.id).then((list) => {
      setAssessments(list);
      const cachedId = getCachedAssessmentId(org.id);
      if (cachedId) {
        const cached = list.find((a) => a.id === cachedId);
        if (cached) onEnterBoard(org, cached);
      }
    });
  }

  async function createOrg() {
    if (!newOrgName.trim()) return;
    setCreating(true);
    try {
      const org = await api.createOrg(newOrgName.trim());
      setOrgs((prev) => [...prev, org]);
      setNewOrgName("");
      selectOrg(org);
    } catch {
      setError("Failed to create org");
    } finally {
      setCreating(false);
    }
  }

  async function startAssessment() {
    if (!selectedOrg || frameworks.length === 0) return;
    const fw = frameworks.find((f) => f.key === "cmmc_l2") ?? frameworks[0];
    setStarting(true);
    try {
      const name = `CMMC L2 Assessment ${new Date().toISOString().slice(0, 10)}`;
      const a = await api.createAssessment(selectedOrg.id, fw.id, name);
      setCachedAssessmentId(selectedOrg.id, a.id);
      onEnterBoard(selectedOrg, a);
    } catch {
      setError("Failed to start assessment");
    } finally {
      setStarting(false);
    }
  }

  function openAssessment(a: Assessment) {
    if (!selectedOrg) return;
    setCachedAssessmentId(selectedOrg.id, a.id);
    onEnterBoard(selectedOrg, a);
  }

  return (
    <div className="picker-grid">
      {/* Left: orgs */}
      <div className="card">
        <h2>Organizations</h2>
        {error && <p style={{ color: "#dc3545", marginBottom: "0.75rem" }}>{error}</p>}

        <ul className="item-list">
          {orgs.map((org) => (
            <li
              key={org.id}
              className={`item-row clickable ${selectedOrg?.id === org.id ? "selected" : ""}`}
              onClick={() => selectOrg(org)}
            >
              <span className="item-name">{org.name}</span>
            </li>
          ))}
          {orgs.length === 0 && (
            <li className="empty">No organizations yet</li>
          )}
        </ul>

        <div className="divider" />
        <div className="form-row">
          <input
            type="text"
            placeholder="New organization name"
            value={newOrgName}
            onChange={(e) => setNewOrgName(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && createOrg()}
          />
          <button className="btn-primary" onClick={createOrg} disabled={creating || !newOrgName.trim()}>
            {creating ? "…" : "Add"}
          </button>
        </div>
      </div>

      {/* Right: assessments for selected org */}
      <div className="card">
        <h2>{selectedOrg ? `Assessments — ${selectedOrg.name}` : "Select an organization"}</h2>

        {!selectedOrg && (
          <div className="empty">Click an organization to see its assessments.</div>
        )}

        {selectedOrg && (
          <>
            <ul className="item-list">
              {assessments.map((a) => (
                <li key={a.id} className="item-row">
                  <div>
                    <div className="item-name">{a.name}</div>
                    <div className="item-meta">
                      {a.status} · started {a.started_at.slice(0, 10)}
                      {a.sprs_score != null ? ` · SPRS ${a.sprs_score}` : ""}
                    </div>
                  </div>
                  <button className="btn-ghost btn-sm" onClick={() => openAssessment(a)}>
                    Open
                  </button>
                </li>
              ))}
              {assessments.length === 0 && (
                <li className="empty">No assessments yet — start one below.</li>
              )}
            </ul>

            <div className="divider" />
            <button
              className="btn-primary"
              onClick={startAssessment}
              disabled={starting || frameworks.length === 0}
            >
              {starting ? "Starting…" : "Start New Assessment"}
            </button>
          </>
        )}
      </div>
    </div>
  );
}
