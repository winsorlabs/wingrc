import { useEffect, useState } from "react";
import { api } from "../api";
import { ALL_ROLES, ROLE_LABELS } from "../lib/roles";
import type { InvitedUser, MspOrg, Org, UserDirectoryEntry } from "../types";

// Deployment-wide user directory + org-access grant/revoke (ADR 0009
// M.7/M.8, G.11). Distinct from the per-org Users tab (UsersPanel.tsx,
// under an org's own Security category) -- that screen answers "who has
// access to this org"; this one answers "every user on this deployment,
// and which orgs each one can reach," and is where access into a
// *different* org gets granted from.
//
// No bulk "grant to every org" action, for any role -- and if one is
// ever added, it must exclude consultant_admin specifically:
// org_membership.py's _AUTO_PROVISION_ROLES deliberately leaves that role
// out (per-engagement, granted explicitly per org), and a bulk-grant
// affordance on this screen is exactly where that intent would get
// accidentally undone.
export function UserDirectoryPanel() {
  const [users, setUsers] = useState<UserDirectoryEntry[]>([]);
  const [orgs, setOrgs] = useState<Org[]>([]);
  const [mspOrg, setMspOrg] = useState<MspOrg | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [grantTarget, setGrantTarget] = useState<UserDirectoryEntry | null>(null);
  const [grantOrgId, setGrantOrgId] = useState("");
  const [grantRole, setGrantRole] = useState("customer_poc");
  const [granting, setGranting] = useState(false);
  const [grantError, setGrantError] = useState<string | null>(null);

  const [confirmRevoke, setConfirmRevoke] = useState<{ userId: string; orgId: string } | null>(null);
  const [revoking, setRevoking] = useState(false);
  const [revokeError, setRevokeError] = useState<string | null>(null);

  const [showInviteMsp, setShowInviteMsp] = useState(false);
  const [inviteEmail, setInviteEmail] = useState("");
  const [inviteName, setInviteName] = useState("");
  const [inviteRole, setInviteRole] = useState("msp_engineer");
  const [inviting, setInviting] = useState(false);
  const [inviteError, setInviteError] = useState<string | null>(null);
  const [invitedResult, setInvitedResult] = useState<InvitedUser | null>(null);

  function load() {
    setLoading(true);
    setError(null);
    // api.getOrgs() returns every org the caller has membership in, not
    // every org on the deployment -- reused here rather than adding a new
    // cross-org "all orgs" endpoint, since an msp_admin (the only role
    // that reaches this screen) is auto-provisioned into every org by
    // org_membership.py already. A deployment where that invariant has
    // somehow broken would show a short org picker here rather than a
    // missing one -- an honest reflection of the caller's own reach, not
    // a bug in this screen.
    Promise.all([api.listUserDirectory(), api.getOrgs(), api.getMspOrg()])
      .then(([u, o, msp]) => {
        setUsers(u);
        setOrgs(o);
        setMspOrg(msp);
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  }

  useEffect(load, []);

  function openGrant(user: UserDirectoryEntry) {
    setGrantTarget(user);
    setGrantOrgId("");
    setGrantRole("customer_poc");
    setGrantError(null);
  }

  async function handleGrant() {
    if (!grantTarget || !grantOrgId) return;
    setGranting(true);
    setGrantError(null);
    try {
      await api.grantMembership(grantOrgId, { user_id: grantTarget.id, role: grantRole });
      setGrantTarget(null);
      load();
    } catch (e) {
      setGrantError(e instanceof Error ? e.message : "Could not grant access");
    } finally {
      setGranting(false);
    }
  }

  async function handleRevoke() {
    if (!confirmRevoke) return;
    setRevoking(true);
    setRevokeError(null);
    try {
      await api.revokeMembership(confirmRevoke.orgId, confirmRevoke.userId);
      setConfirmRevoke(null);
      load();
    } catch (e) {
      setRevokeError(e instanceof Error ? e.message : "Could not revoke access");
    } finally {
      setRevoking(false);
    }
  }

  function openInviteMsp() {
    setInviteEmail("");
    setInviteName("");
    setInviteRole("msp_engineer");
    setInviteError(null);
    setInvitedResult(null);
    setShowInviteMsp(true);
  }

  async function handleInviteMsp() {
    if (!mspOrg) return;
    if (!inviteEmail.trim() || !inviteName.trim()) {
      setInviteError("Email and display name are required");
      return;
    }
    setInviting(true);
    setInviteError(null);
    try {
      const result = await api.inviteUser(mspOrg.org_id, {
        email: inviteEmail.trim(),
        display_name: inviteName.trim(),
        role: inviteRole,
        login_method: "local",
      });
      setInvitedResult(result);
    } catch (e) {
      setInviteError(e instanceof Error ? e.message : "Could not invite user");
    } finally {
      setInviting(false);
    }
  }

  function closeInviteMsp() {
    setShowInviteMsp(false);
    const hadResult = invitedResult !== null;
    setInvitedResult(null);
    if (hadResult) load();
  }

  if (loading) return <div className="loading">Loading directory…</div>;

  return (
    <div className="user-directory-panel">
      <div className="products-panel-header" style={{ padding: 0, border: "none", marginBottom: "0.75rem" }}>
        <span className="products-panel-title">User Directory</span>
        {mspOrg ? (
          <button className="btn-primary btn-sm" onClick={openInviteMsp}>
            + Invite to {mspOrg.org_name}
          </button>
        ) : (
          <span className="field-hint">
            No MSP org designated yet — run <code>manage.py bootstrap-admin</code> to set one.
          </span>
        )}
      </div>

      {error && <div className="form-error">{error}</div>}

      {users.length === 0 ? (
        <div className="empty">No users on this deployment yet.</div>
      ) : (
        <div className="table-scroll">
          <table className="contacts-table">
            <thead>
              <tr>
                <th>Name</th>
                <th>Email</th>
                <th>Home Org</th>
                <th>Org Access</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {users.map((u) => (
                <tr key={u.id}>
                  <td>
                    <div className="contact-name">{u.display_name}</div>
                    {u.deleted_at && <div className="contact-sub">Anonymized</div>}
                  </td>
                  <td>{u.email}</td>
                  <td>{u.home_org_name}</td>
                  <td>
                    <div className="role-chips-inline">
                      {u.memberships.map((m) => (
                        <span key={m.org_id} className="affiliation-badge">
                          {m.org_name}: {ROLE_LABELS[m.role] ?? m.role}
                          <button
                            className="btn-ghost btn-xs"
                            style={{ marginLeft: "0.3rem" }}
                            onClick={() => setConfirmRevoke({ userId: u.id, orgId: m.org_id })}
                            title={`Revoke access to ${m.org_name}`}
                          >
                            ×
                          </button>
                        </span>
                      ))}
                      {u.memberships.length === 0 && <span className="no-roles">No org access</span>}
                    </div>
                  </td>
                  <td>
                    <button className="btn-ghost btn-xs" onClick={() => openGrant(u)}>
                      Grant access…
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {confirmRevoke && (
        <div className="drawer-overlay" onClick={() => setConfirmRevoke(null)}>
          <div className="drawer" onClick={(e) => e.stopPropagation()}>
            <div className="drawer-header">
              <h3>Revoke access?</h3>
              <button className="drawer-close" onClick={() => setConfirmRevoke(null)} aria-label="Close">×</button>
            </div>
            <div className="drawer-body">
              {revokeError && <div className="form-error">{revokeError}</div>}
              <p>This removes the user's access to this org. They will no longer be able to reach it.</p>
            </div>
            <div className="drawer-footer">
              <div style={{ flex: 1 }} />
              <button className="btn-ghost" onClick={() => setConfirmRevoke(null)}>Cancel</button>
              <button className="btn-danger" onClick={handleRevoke} disabled={revoking}>
                {revoking ? "Revoking…" : "Revoke"}
              </button>
            </div>
          </div>
        </div>
      )}

      {grantTarget && (
        <div className="drawer-overlay" onClick={() => setGrantTarget(null)}>
          <div className="drawer" onClick={(e) => e.stopPropagation()}>
            <div className="drawer-header">
              <h3>Grant access — {grantTarget.display_name}</h3>
              <button className="drawer-close" onClick={() => setGrantTarget(null)} aria-label="Close">×</button>
            </div>
            <div className="drawer-body">
              {grantError && <div className="form-error">{grantError}</div>}
              <div className="form-field">
                <label>Organization</label>
                <select value={grantOrgId} onChange={(e) => setGrantOrgId(e.target.value)}>
                  <option value="">Select an org…</option>
                  {orgs.map((o) => (
                    <option key={o.id} value={o.id}>{o.name}</option>
                  ))}
                </select>
              </div>
              <div className="form-field">
                <label>Role</label>
                <select value={grantRole} onChange={(e) => setGrantRole(e.target.value)}>
                  {ALL_ROLES.map((r) => (
                    <option key={r} value={r}>{ROLE_LABELS[r] ?? r}</option>
                  ))}
                </select>
              </div>
            </div>
            <div className="drawer-footer">
              <div style={{ flex: 1 }} />
              <button className="btn-ghost" onClick={() => setGrantTarget(null)}>Cancel</button>
              <button className="btn-primary" onClick={handleGrant} disabled={granting || !grantOrgId}>
                {granting ? "Granting…" : "Grant"}
              </button>
            </div>
          </div>
        </div>
      )}

      {showInviteMsp && mspOrg && (
        <div className="drawer-overlay" onClick={invitedResult ? undefined : closeInviteMsp}>
          <div className="drawer" onClick={(e) => e.stopPropagation()}>
            <div className="drawer-header">
              <h3>{invitedResult ? "User Invited" : `Invite to ${mspOrg.org_name}`}</h3>
              <button className="drawer-close" onClick={closeInviteMsp} aria-label="Close">×</button>
            </div>
            {invitedResult ? (
              <>
                <div className="drawer-body">
                  <div className="token-warning">
                    This is the only time this invite token will be shown. Copy it now and
                    deliver it to {invitedResult.email} out of band.
                  </div>
                  <div className="form-field">
                    <label>Invite Token</label>
                    <code className="token-value">{invitedResult.invite_token}</code>
                  </div>
                </div>
                <div className="drawer-footer">
                  <div style={{ flex: 1 }} />
                  <button className="btn-primary" onClick={closeInviteMsp}>Done</button>
                </div>
              </>
            ) : (
              <>
                <div className="drawer-body">
                  {inviteError && <div className="form-error">{inviteError}</div>}
                  <div className="form-field">
                    <label>Email <span className="required">*</span></label>
                    <input type="email" value={inviteEmail} onChange={(e) => setInviteEmail(e.target.value)} />
                  </div>
                  <div className="form-field">
                    <label>Display Name <span className="required">*</span></label>
                    <input type="text" value={inviteName} onChange={(e) => setInviteName(e.target.value)} />
                  </div>
                  <div className="form-field">
                    <label>Role</label>
                    <select value={inviteRole} onChange={(e) => setInviteRole(e.target.value)}>
                      {ALL_ROLES.map((r) => (
                        <option key={r} value={r}>{ROLE_LABELS[r] ?? r}</option>
                      ))}
                    </select>
                  </div>
                </div>
                <div className="drawer-footer">
                  <div style={{ flex: 1 }} />
                  <button className="btn-ghost" onClick={closeInviteMsp}>Cancel</button>
                  <button className="btn-primary" onClick={handleInviteMsp} disabled={inviting}>
                    {inviting ? "Inviting…" : "Invite"}
                  </button>
                </div>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
