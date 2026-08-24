import { useEffect, useState } from "react";
import { api, getCachedAssessmentId, setCachedAssessmentId } from "../api";
import { canCreateOrg } from "../lib/roles";
import type { Assessment, AuthUser, Framework, Org } from "../types";

interface Props {
  currentUser: AuthUser;
  canWrite: boolean;
  // See App.tsx's call site: true when the user explicitly navigated back
  // to this screen rather than landing here fresh, so the cached-assessment
  // auto-resume below doesn't bounce them straight back to where they came
  // from — that would make the assessment list / "Start New Assessment"
  // permanently unreachable for any org that's ever been opened.
  skipAutoResume?: boolean;
  onEnterBoard: (org: Org, assessment: Assessment) => void;
  onEnterOnboarding: (org: Org) => void;
  onOpenSettings: (org: Org) => void;
}

export function OrgPicker({ currentUser, canWrite, skipAutoResume = false, onEnterBoard, onEnterOnboarding, onOpenSettings }: Props) {
  const canCreate = canCreateOrg(currentUser.role);

  const [orgs, setOrgs] = useState<Org[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [selectedOrg, setSelectedOrg] = useState<Org | null>(null);
  const [assessments, setAssessments] = useState<Assessment[]>([]);
  const [frameworks, setFrameworks] = useState<Framework[]>([]);
  const [newOrgName, setNewOrgName] = useState("");
  const [creating, setCreating] = useState(false);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    // ADR 0009 M.5/M.6: GET /orgs is membership-scoped for every role now
    // (a customer_poc gets back exactly their one org, an MSP user gets
    // back everything they've been provisioned into) — always call the
    // same endpoint, then decide picker-vs-auto-select from the result's
    // length below, not from role.
    api.getOrgs()
      .then((list) => {
        setOrgs(list);
        setLoaded(true);
        if (list.length === 1 && !canCreate) {
          selectOrg(list[0]);
        }
      })
      .catch(() => setError("Could not load your organizations."));
    api.getFrameworks().then(setFrameworks).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function selectOrg(org: Org) {
    setSelectedOrg(org);
    setAssessments([]);
    api.getAssessments(org.id).then((list) => {
      setAssessments(list);
      if (skipAutoResume) return;
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
      // After creation, launch onboarding wizard
      onEnterOnboarding(org);
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

  // Whether to show the full cross-org picker (list + optional "create
  // org" affordance) or skip straight to the caller's one org. This is a
  // per-user fact now (do they have more than one org_membership row, or
  // can they create new ones), not a role one — ADR 0009 M.6. A
  // customer_poc/c3pao_assessor with exactly one membership (the common
  // case) still gets the fast single-org path exactly as before; a role
  // that can create orgs always gets the full picker even with only one
  // org today, since "create my first customer org" has to be reachable
  // from somewhere. A non-MSP role with more than one membership (ADR
  // 0009's own example: a c3pao_assessor across two engagements) gets the
  // picker too, just without the create-org affordance below.
  const showPicker = canCreate || orgs.length > 1;

  if (!showPicker) {
    return (
      <div className="picker-grid picker-grid-single">
        <div className="card">
          <h2>{selectedOrg ? `Assessments — ${selectedOrg.name}` : "Your organization"}</h2>
          {error && <p style={{ color: "#dc3545", marginBottom: "0.75rem" }}>{error}</p>}
          {!selectedOrg && !error && !loaded && (
            <div className="empty">Loading your organization…</div>
          )}
          {!selectedOrg && !error && loaded && (
            <div className="empty">
              No organization is available for your account. Contact your administrator.
            </div>
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
                  <li className="empty">
                    {canWrite ? "No assessments yet — start one below." : "No assessments yet."}
                  </li>
                )}
              </ul>

              {canWrite && (
                <>
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
            </>
          )}
        </div>
      </div>
    );
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
              <div className="org-row-actions">
                <button
                  className="btn-ghost btn-xs"
                  title="Org settings"
                  onClick={(e) => { e.stopPropagation(); onOpenSettings(org); }}
                  aria-label="Settings"
                >
                  ⚙
                </button>
              </div>
            </li>
          ))}
          {orgs.length === 0 && (
            <li className="empty">No organizations yet</li>
          )}
        </ul>

        {canCreate && (
          <>
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
          </>
        )}
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
                <li className="empty">
                  {canWrite ? "No assessments yet — start one below." : "No assessments yet."}
                </li>
              )}
            </ul>

            {canWrite && (
              <>
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
          </>
        )}
      </div>
    </div>
  );
}
