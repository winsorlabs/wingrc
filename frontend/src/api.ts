import type { ApiTokenRow, Assessment, AuditLogPage, AuthUser, BaselineImportPreview, BaselineImportResult, Contact, ControlStateRow, CreatedApiToken, DashboardData, DiagramUpload, DryRunResult, EvidenceRow, EvidenceTaskRow, Framework, IntegrationConnector, InvitedUser, LiongardEnvironmentMapping, LiongardEnvironmentOption, MembershipGrantResult, MfaEnrollData, MspOrg, OnboardingStatus, Org, OrgProfile, PasswordResetIssued, PractitionerNotesUpdate, ProductDetail, ProductDocumentItem, ProductFootprintRow, ProductLibraryItem, ProductPublishState, ProductRow, RaciAssignmentRow, ScopeChange, ScopeEntity, SessionRow, StatementRow, StepUpIn, SystemDescriptionData, UserDirectoryEntry, UserRow } from "./types";

const BASE = "/api";

// Carries the HTTP status alongside the server's detail message so callers
// can branch on it (e.g. UsersPanel distinguishing "blocked, offer
// anonymize" (409) from any other failure) without re-parsing the message.
export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

async function req<T>(path: string, opts?: RequestInit): Promise<T> {
  const r = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  return r.json() as Promise<T>;
}

export const api = {
  getMe: async (): Promise<AuthUser | null> => {
    const r = await fetch(`${BASE}/auth/me`, { credentials: "include" });
    if (r.status === 401) return null;
    if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
    return r.json() as Promise<AuthUser>;
  },

  logout: async (): Promise<void> => {
    await fetch(`${BASE}/auth/logout`, { method: "POST", credentials: "include" });
  },

  localLogin: async (email: string, password: string): Promise<{ next: string }> => {
    const r = await fetch(`${BASE}/auth/login`, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    });
    if (!r.ok) {
      const body = await r.json().catch(() => ({}));
      throw new Error(body.detail ?? `${r.status} ${r.statusText}`);
    }
    return r.json() as Promise<{ next: string }>;
  },

  mfaVerify: async (code: string): Promise<void> => {
    const r = await fetch(`${BASE}/auth/mfa/verify`, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code }),
    });
    if (!r.ok) {
      const body = await r.json().catch(() => ({}));
      throw new Error(body.detail ?? `${r.status} ${r.statusText}`);
    }
  },

  setPassword: async (token: string, password: string): Promise<{ next: string }> => {
    const r = await fetch(`${BASE}/auth/set-password`, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token, password }),
    });
    if (!r.ok) {
      const body = await r.json().catch(() => ({}));
      throw new Error(body.detail ?? `${r.status} ${r.statusText}`);
    }
    return r.json() as Promise<{ next: string }>;
  },

  mfaEnroll: async (): Promise<MfaEnrollData> => {
    const r = await fetch(`${BASE}/auth/mfa/enroll`, {
      method: "POST",
      credentials: "include",
    });
    if (!r.ok) {
      const body = await r.json().catch(() => ({}));
      throw new Error(body.detail ?? `${r.status} ${r.statusText}`);
    }
    return r.json() as Promise<MfaEnrollData>;
  },

  mfaEnrollConfirm: async (code: string): Promise<{ backup_codes: string[] }> => {
    const r = await fetch(`${BASE}/auth/mfa/enroll/confirm`, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code }),
    });
    if (!r.ok) {
      const body = await r.json().catch(() => ({}));
      throw new Error(body.detail ?? `${r.status} ${r.statusText}`);
    }
    return r.json() as Promise<{ backup_codes: string[] }>;
  },

  // --- Self-service account management (I.9) ---

  changePassword: async (currentPassword: string, newPassword: string): Promise<void> => {
    const r = await fetch(`${BASE}/auth/change-password`, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
    });
    if (!r.ok) {
      const body = await r.json().catch(() => ({}));
      throw new Error(body.detail ?? `${r.status} ${r.statusText}`);
    }
  },

  mfaReenroll: async (stepUp: StepUpIn): Promise<MfaEnrollData> => {
    const r = await fetch(`${BASE}/auth/mfa/reenroll`, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(stepUp),
    });
    if (!r.ok) {
      const body = await r.json().catch(() => ({}));
      throw new Error(body.detail ?? `${r.status} ${r.statusText}`);
    }
    return r.json() as Promise<MfaEnrollData>;
  },

  mfaReenrollConfirm: async (code: string): Promise<{ backup_codes: string[] }> => {
    const r = await fetch(`${BASE}/auth/mfa/reenroll/confirm`, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code }),
    });
    if (!r.ok) {
      const body = await r.json().catch(() => ({}));
      throw new Error(body.detail ?? `${r.status} ${r.statusText}`);
    }
    return r.json() as Promise<{ backup_codes: string[] }>;
  },

  regenerateBackupCodes: async (stepUp: StepUpIn): Promise<{ backup_codes: string[] }> => {
    const r = await fetch(`${BASE}/auth/mfa/backup-codes/regenerate`, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(stepUp),
    });
    if (!r.ok) {
      const body = await r.json().catch(() => ({}));
      throw new Error(body.detail ?? `${r.status} ${r.statusText}`);
    }
    return r.json() as Promise<{ backup_codes: string[] }>;
  },

  listSessions: async (): Promise<SessionRow[]> => {
    const r = await fetch(`${BASE}/auth/sessions`, { credentials: "include" });
    if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
    return r.json() as Promise<SessionRow[]>;
  },

  revokeAllSessions: async (): Promise<void> => {
    const r = await fetch(`${BASE}/auth/sessions/revoke-all`, {
      method: "POST",
      credentials: "include",
    });
    if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  },

  getOrgs: () => req<Org[]>("/orgs"),
  createOrg: (name: string) =>
    req<Org>("/orgs", { method: "POST", body: JSON.stringify({ name }) }),

  getFrameworks: () => req<Framework[]>("/frameworks"),

  getAssessments: (orgId: string) =>
    req<Assessment[]>(`/orgs/${orgId}/assessments`),
  createAssessment: (orgId: string, frameworkId: string, name: string) =>
    req<Assessment>(`/orgs/${orgId}/assessments`, {
      method: "POST",
      body: JSON.stringify({ framework_id: frameworkId, name }),
    }),

  getControlStates: (orgId: string, assessmentId: string) =>
    req<ControlStateRow[]>(
      `/orgs/${orgId}/assessments/${assessmentId}/control-states`
    ),

  patchControlState: (
    orgId: string,
    assessmentId: string,
    controlStateId: string,
    status: string
  ) =>
    req<{ id: string; status: string }>(
      `/orgs/${orgId}/assessments/${assessmentId}/control-states/${controlStateId}`,
      { method: "PATCH", body: JSON.stringify({ status }) }
    ),

  getStatements: (orgId: string, assessmentId: string, controlDbId: string) =>
    req<StatementRow[]>(
      `/orgs/${orgId}/assessments/${assessmentId}/controls/${controlDbId}/statements`
    ),

  putStatements: (
    orgId: string,
    assessmentId: string,
    controlDbId: string,
    items: Array<{ objective_id: string; body: string; status: string }>
  ) =>
    req<StatementRow[]>(
      `/orgs/${orgId}/assessments/${assessmentId}/controls/${controlDbId}/statements`,
      { method: "PUT", body: JSON.stringify(items) }
    ),

  getProducts: (orgId: string, assessmentId: string) =>
    req<ProductRow[]>(`/orgs/${orgId}/assessments/${assessmentId}/products`),

  activateProduct: (orgId: string, assessmentId: string, productId: string) =>
    req<{ objectives_updated: number; tasks_created: number }>(
      `/orgs/${orgId}/assessments/${assessmentId}/products/${productId}/activate`,
      { method: "POST", body: JSON.stringify({}) }
    ),

  deactivateProduct: (orgId: string, assessmentId: string, productId: string) =>
    req<{ controls_flagged: number; tasks_archived: number; evidence_links_archived: number }>(
      `/orgs/${orgId}/assessments/${assessmentId}/products/${productId}/deactivate`,
      { method: "POST", body: JSON.stringify({}) }
    ),

  getEvidenceTasks: (orgId: string, assessmentId: string) =>
    req<EvidenceTaskRow[]>(`/orgs/${orgId}/assessments/${assessmentId}/evidence-tasks`),

  patchEvidenceTask: (orgId: string, assessmentId: string, taskId: string, status: string) =>
    req<{ id: string; status: string; is_archived: boolean }>(
      `/orgs/${orgId}/assessments/${assessmentId}/evidence-tasks/${taskId}`,
      { method: "PATCH", body: JSON.stringify({ status }) }
    ),

  listEvidence: (orgId: string, assessmentId: string, controlStateId: string) =>
    req<EvidenceRow[]>(
      `/orgs/${orgId}/assessments/${assessmentId}/control-states/${controlStateId}/evidence`
    ),

  uploadEvidence: async (
    orgId: string,
    assessmentId: string,
    controlStateId: string,
    file: File,
    artifactType: string
  ): Promise<EvidenceRow> => {
    const form = new FormData();
    form.append("file", file);
    form.append("artifact_type", artifactType);
    const r = await fetch(
      `/api/orgs/${orgId}/assessments/${assessmentId}/control-states/${controlStateId}/evidence`,
      { method: "POST", body: form }
    );
    if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
    return r.json() as Promise<EvidenceRow>;
  },

  deleteEvidence: async (
    orgId: string,
    assessmentId: string,
    controlStateId: string,
    evidenceId: string
  ): Promise<void> => {
    const r = await fetch(
      `/api/orgs/${orgId}/assessments/${assessmentId}/control-states/${controlStateId}/evidence/${evidenceId}`,
      { method: "DELETE" }
    );
    if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  },

  addReferences: (
    orgId: string,
    assessmentId: string,
    controlStateId: string,
    refs: Array<{ title: string; location: string; artifact_type: string }>
  ) =>
    req<EvidenceRow[]>(
      `/orgs/${orgId}/assessments/${assessmentId}/control-states/${controlStateId}/evidence/references`,
      { method: "POST", body: JSON.stringify(refs) }
    ),

  uploadTaskEvidence: async (
    orgId: string,
    assessmentId: string,
    taskId: string,
    file: File,
    artifactType: string
  ): Promise<EvidenceRow> => {
    const form = new FormData();
    form.append("file", file);
    form.append("artifact_type", artifactType);
    const r = await fetch(
      `/api/orgs/${orgId}/assessments/${assessmentId}/evidence-tasks/${taskId}/collect`,
      { method: "POST", body: form }
    );
    if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
    return r.json() as Promise<EvidenceRow>;
  },

  addTaskReference: (
    orgId: string,
    assessmentId: string,
    taskId: string,
    ref: { title: string; location: string; artifact_type: string }
  ) =>
    req<EvidenceRow>(
      `/orgs/${orgId}/assessments/${assessmentId}/evidence-tasks/${taskId}/collect/reference`,
      { method: "POST", body: JSON.stringify(ref) }
    ),

  // ── Org profile ──────────────────────────────────────────────────────────
  getOrgProfile: (orgId: string) =>
    req<OrgProfile>(`/orgs/${orgId}/profile`),

  patchOrgProfile: (orgId: string, data: Partial<Omit<OrgProfile, "id" | "name" | "created_at" | "updated_at" | "logo_storage_key" | "logo_url">>) =>
    req<OrgProfile>(`/orgs/${orgId}/profile`, {
      method: "PATCH",
      body: JSON.stringify(data),
    }),

  uploadLogo: async (orgId: string, file: File): Promise<{ logo_storage_key: string; logo_url: string }> => {
    const form = new FormData();
    form.append("file", file);
    const r = await fetch(`/api/orgs/${orgId}/logo`, { method: "POST", body: form });
    if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
    return r.json() as Promise<{ logo_storage_key: string; logo_url: string }>;
  },

  // ── System description ────────────────────────────────────────────────────
  getSystemDescription: async (orgId: string): Promise<SystemDescriptionData | null> => {
    const r = await fetch(`/api/orgs/${orgId}/system-description`, {
      headers: { "Content-Type": "application/json" },
    });
    if (r.status === 404) return null;
    if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
    return r.json() as Promise<SystemDescriptionData>;
  },

  putSystemDescription: (
    orgId: string,
    data: Omit<
      SystemDescriptionData,
      | "id"
      | "org_id"
      | "created_at"
      | "updated_at"
      | "network_diagram_evidence_id"
      | "network_diagram_url"
      | "data_flow_diagram_evidence_id"
      | "data_flow_diagram_url"
    >
  ) =>
    req<SystemDescriptionData>(`/orgs/${orgId}/system-description`, {
      method: "PUT",
      body: JSON.stringify(data),
    }),

  uploadNetworkDiagram: async (orgId: string, file: File): Promise<DiagramUpload> => {
    const form = new FormData();
    form.append("file", file);
    const r = await fetch(`/api/orgs/${orgId}/system-description/network-diagram`, {
      method: "POST",
      body: form,
    });
    if (!r.ok) {
      const body = await r.json().catch(() => ({}));
      throw new Error(body.detail ?? `${r.status} ${r.statusText}`);
    }
    return r.json() as Promise<DiagramUpload>;
  },

  uploadDataFlowDiagram: async (orgId: string, file: File): Promise<DiagramUpload> => {
    const form = new FormData();
    form.append("file", file);
    const r = await fetch(`/api/orgs/${orgId}/system-description/data-flow-diagram`, {
      method: "POST",
      body: form,
    });
    if (!r.ok) {
      const body = await r.json().catch(() => ({}));
      throw new Error(body.detail ?? `${r.status} ${r.statusText}`);
    }
    return r.json() as Promise<DiagramUpload>;
  },

  // ── Onboarding status ─────────────────────────────────────────────────────
  getOnboardingStatus: (orgId: string) =>
    req<OnboardingStatus>(`/orgs/${orgId}/onboarding-status`),

  // ── Org dashboard (G.3) ───────────────────────────────────────────────────
  getDashboard: (orgId: string, assessmentId: string) =>
    req<DashboardData>(`/orgs/${orgId}/assessments/${assessmentId}/dashboard`),

  // ── Contacts ──────────────────────────────────────────────────────────────
  getContacts: (orgId: string) =>
    req<Contact[]>(`/orgs/${orgId}/contacts`),

  createContact: (
    orgId: string,
    data: { name: string; email: string; affiliation: string; phone?: string | null; role_title?: string | null; contract_ref?: string | null; notes?: string | null }
  ) =>
    req<Contact>(`/orgs/${orgId}/contacts`, { method: "POST", body: JSON.stringify(data) }),

  patchContact: (
    orgId: string,
    contactId: string,
    data: Partial<{ name: string; email: string; affiliation: string; phone: string | null; role_title: string | null; contract_ref: string | null; notes: string | null }>
  ) =>
    req<Contact>(`/orgs/${orgId}/contacts/${contactId}`, { method: "PATCH", body: JSON.stringify(data) }),

  deleteContact: async (orgId: string, contactId: string): Promise<void> => {
    const r = await fetch(`/api/orgs/${orgId}/contacts/${contactId}`, { method: "DELETE" });
    if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  },

  addContactRole: (orgId: string, contactId: string, role: string) =>
    req<{ id: string; contact_id: string; role: string; notes: string | null; created_at: string }>(
      `/orgs/${orgId}/contacts/${contactId}/roles`,
      { method: "POST", body: JSON.stringify({ role }) }
    ),

  removeContactRole: async (orgId: string, contactId: string, role: string): Promise<void> => {
    const r = await fetch(`/api/orgs/${orgId}/contacts/${contactId}/roles/${role}`, { method: "DELETE" });
    if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  },

  // ── RACI (G.7) ────────────────────────────────────────────────────────────
  getRaciAssignments: (orgId: string, assessmentId: string) =>
    req<RaciAssignmentRow[]>(`/orgs/${orgId}/assessments/${assessmentId}/raci`),

  createRaciAssignment: (
    orgId: string,
    assessmentId: string,
    data: { control_state_id: string; contact_id: string; raci_letter: string }
  ) =>
    req<RaciAssignmentRow>(`/orgs/${orgId}/assessments/${assessmentId}/raci`, {
      method: "POST",
      body: JSON.stringify(data),
    }),

  deleteRaciAssignment: async (orgId: string, assessmentId: string, raciId: string): Promise<void> => {
    const r = await fetch(`/api/orgs/${orgId}/assessments/${assessmentId}/raci/${raciId}`, {
      method: "DELETE",
    });
    if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  },

  bulkAssignRaci: (
    orgId: string,
    assessmentId: string,
    data: { family: string; contact_id: string; raci_letter: string }
  ) =>
    req<{ assigned: number; skipped: number }>(`/orgs/${orgId}/assessments/${assessmentId}/raci/bulk`, {
      method: "POST",
      body: JSON.stringify(data),
    }),

  // ── Users ─────────────────────────────────────────────────────────────────
  listUsers: (orgId: string) =>
    req<UserRow[]>(`/orgs/${orgId}/users`),

  inviteUser: (
    orgId: string,
    data: { email: string; display_name: string; role: string; login_method: string }
  ) =>
    req<InvitedUser>(`/orgs/${orgId}/users`, {
      method: "POST",
      body: JSON.stringify(data),
    }),

  patchUser: (
    orgId: string,
    userId: string,
    data: Partial<{ role: string; is_active: boolean; display_name: string }>
  ) =>
    req<UserRow>(`/orgs/${orgId}/users/${userId}`, {
      method: "PATCH",
      body: JSON.stringify(data),
    }),

  deactivateUser: async (orgId: string, userId: string): Promise<void> => {
    const r = await fetch(`/api/orgs/${orgId}/users/${userId}`, { method: "DELETE" });
    if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  },

  resetUserMfa: (orgId: string, userId: string) =>
    req<{ ok: boolean }>(`/orgs/${orgId}/users/${userId}/reset-mfa`, {
      method: "POST",
      body: JSON.stringify({}),
    }),

  unlockUser: (orgId: string, userId: string) =>
    req<{ ok: boolean }>(`/orgs/${orgId}/users/${userId}/unlock`, {
      method: "POST",
      body: JSON.stringify({}),
    }),

  resetUserPassword: (orgId: string, userId: string) =>
    req<PasswordResetIssued>(`/orgs/${orgId}/users/${userId}/reset-password`, {
      method: "POST",
      body: JSON.stringify({}),
    }),

  // ADR 0006 — permanent deletion. Custom fetch (not req<T>) because the
  // 409 "blocked, history exists" response's detail message is the exact
  // copy UsersPanel shows the admin as the anonymize offer; ApiError.status
  // lets the caller distinguish that case from any other failure.
  deleteUserPermanent: async (orgId: string, userId: string): Promise<{ ok: boolean; deleted: boolean }> => {
    const r = await fetch(`${BASE}/orgs/${orgId}/users/${userId}/delete`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
    });
    if (!r.ok) {
      const body = await r.json().catch(() => ({}));
      throw new ApiError(body.detail ?? `${r.status} ${r.statusText}`, r.status);
    }
    return r.json();
  },

  anonymizeUser: async (orgId: string, userId: string): Promise<UserRow> => {
    const r = await fetch(`${BASE}/orgs/${orgId}/users/${userId}/anonymize`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
    });
    if (!r.ok) {
      const body = await r.json().catch(() => ({}));
      throw new ApiError(body.detail ?? `${r.status} ${r.statusText}`, r.status);
    }
    return r.json();
  },

  // ── API tokens ────────────────────────────────────────────────────────────
  listApiTokens: (orgId: string) =>
    req<ApiTokenRow[]>(`/orgs/${orgId}/api-tokens`),

  createApiToken: (
    orgId: string,
    data: { name: string; role: string; expires_in_days?: number | null }
  ) =>
    req<CreatedApiToken>(`/orgs/${orgId}/api-tokens`, {
      method: "POST",
      body: JSON.stringify(data),
    }),

  revokeApiToken: async (orgId: string, tokenId: string): Promise<void> => {
    const r = await fetch(`/api/orgs/${orgId}/api-tokens/${tokenId}`, { method: "DELETE" });
    if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  },

  // ── Org-access grant/revoke (ADR 0009 M.8) — org-scoped; msp_admin only
  // (backend/app/routers/users.py). ─────────────────────────────────────
  grantMembership: (orgId: string, data: { user_id: string; role: string }) =>
    req<MembershipGrantResult>(`/orgs/${orgId}/memberships`, {
      method: "POST",
      body: JSON.stringify(data),
    }),

  revokeMembership: async (orgId: string, userId: string): Promise<void> => {
    const r = await fetch(`${BASE}/orgs/${orgId}/memberships/${userId}`, { method: "DELETE" });
    if (!r.ok) {
      const body = await r.json().catch(() => ({}));
      throw new Error(body.detail ?? `${r.status} ${r.statusText}`);
    }
  },

  // ── Audit log (read-only, msp_admin only — see backend/app/routers/audit_log.py) ──
  listAuditLog: (
    orgId: string,
    filters: {
      offset?: number;
      limit?: number;
      action?: string;
      actor?: string;
      ip_address?: string;
      start?: string;
      end?: string;
    }
  ) => {
    const params = new URLSearchParams();
    for (const [key, value] of Object.entries(filters)) {
      if (value !== undefined && value !== "") params.set(key, String(value));
    }
    return req<AuditLogPage>(`/orgs/${orgId}/audit-log?${params.toString()}`);
  },

  // ── Scope / Assets (G.5) ──────────────────────────────────────────────────
  getScope: (orgId: string, entityType?: string) =>
    req<ScopeEntity[]>(
      `/orgs/${orgId}/scope${entityType ? `?entity_type=${encodeURIComponent(entityType)}` : ""}`
    ),

  createScopeEntity: (
    orgId: string,
    data: {
      entity_type: string;
      natural_key: string;
      scope_category?: string | null;
      status?: string;
      in_boundary?: boolean;
      attributes?: Record<string, unknown>;
    }
  ) => req<ScopeEntity>(`/orgs/${orgId}/scope`, { method: "POST", body: JSON.stringify(data) }),

  patchScopeEntity: (
    orgId: string,
    entityId: string,
    data: Partial<{
      scope_category: string | null;
      status: string;
      in_boundary: boolean;
      attributes: Record<string, unknown>;
    }>
  ) =>
    req<ScopeEntity>(`/orgs/${orgId}/scope/${entityId}`, {
      method: "PATCH",
      body: JSON.stringify(data),
    }),

  deleteScopeEntity: async (orgId: string, entityId: string): Promise<void> => {
    const r = await fetch(`/api/orgs/${orgId}/scope/${entityId}`, { method: "DELETE" });
    if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  },

  dryRunWorkbookImport: async (orgId: string, file: File): Promise<DryRunResult> => {
    const form = new FormData();
    form.append("file", file);
    const r = await fetch(`/api/orgs/${orgId}/imports/workbook/dry-run`, {
      method: "POST",
      body: form,
    });
    if (!r.ok) {
      const body = await r.json().catch(() => ({}));
      throw new Error(body.detail ?? `${r.status} ${r.statusText}`);
    }
    return r.json() as Promise<DryRunResult>;
  },

  applyWorkbookImport: (orgId: string, changes: ScopeChange[]) =>
    req<{ applied: number }>(`/orgs/${orgId}/imports/workbook/apply`, {
      method: "POST",
      body: JSON.stringify({ changes }),
    }),

  // ── Liongard connector (D.2) — org-scoped; see routers/scope.py's own
  // module docstring for why apply below is applyWorkbookImport, not a
  // second endpoint: its body doesn't actually depend on the source.
  // Errors here (wrong Environment id, expired key, rate limit) carry a
  // specific, real message in `detail` -- parsed explicitly below rather
  // than via the plain req() helper, matching dryRunWorkbookImport's own
  // pattern, so the UI shows Liongard's actual error, not "400 Bad
  // Request". ──────────────────────────────────────────────────────────
  getLiongardEnvironmentMapping: (orgId: string) =>
    req<LiongardEnvironmentMapping | null>(`/orgs/${orgId}/integrations/liongard/environment`),

  listLiongardEnvironments: async (orgId: string): Promise<LiongardEnvironmentOption[]> => {
    const r = await fetch(`${BASE}/orgs/${orgId}/integrations/liongard/environments`);
    if (!r.ok) {
      const body = await r.json().catch(() => ({}));
      throw new Error(body.detail ?? `${r.status} ${r.statusText}`);
    }
    return r.json() as Promise<LiongardEnvironmentOption[]>;
  },

  setLiongardEnvironmentMapping: async (
    orgId: string,
    liongardEnvironmentId: number
  ): Promise<LiongardEnvironmentMapping> => {
    const r = await fetch(`${BASE}/orgs/${orgId}/integrations/liongard/environment`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ liongard_environment_id: liongardEnvironmentId }),
    });
    if (!r.ok) {
      const body = await r.json().catch(() => ({}));
      throw new Error(body.detail ?? `${r.status} ${r.statusText}`);
    }
    return r.json() as Promise<LiongardEnvironmentMapping>;
  },

  liongardSyncDryRun: async (orgId: string): Promise<DryRunResult> => {
    const r = await fetch(`${BASE}/orgs/${orgId}/integrations/liongard/sync/dry-run`, {
      method: "POST",
    });
    if (!r.ok) {
      const body = await r.json().catch(() => ({}));
      throw new Error(body.detail ?? `${r.status} ${r.statusText}`);
    }
    return r.json() as Promise<DryRunResult>;
  },

  // ── Integrations (D.1) — deployment-wide, not org-scoped; msp_admin only
  // (backend/app/routers/integrations.py). ──────────────────────────────
  listIntegrations: () => req<IntegrationConnector[]>("/integrations"),

  setIntegrationCredential: (
    connectorKey: string,
    data: { config: Record<string, string>; credential: Record<string, string> }
  ) =>
    req<IntegrationConnector>(`/integrations/${connectorKey}/credential`, {
      method: "PUT",
      body: JSON.stringify(data),
    }),

  deleteIntegrationCredential: async (connectorKey: string): Promise<void> => {
    const r = await fetch(`/api/integrations/${connectorKey}/credential`, { method: "DELETE" });
    if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  },

  testIntegrationConnection: (connectorKey: string) =>
    req<IntegrationConnector>(`/integrations/${connectorKey}/test`, { method: "POST" }),

  // ── Tools library (G.9) — deployment-wide, not org-scoped; msp_admin /
  // consultant_admin only (backend/app/routers/admin_products.py). Never
  // writes OrgProduct -- activation stays inside the org (getProducts /
  // activateProduct above). ────────────────────────────────────────────
  listToolsLibrary: () => req<ProductLibraryItem[]>("/admin/products"),

  getToolDetail: (productId: string) => req<ProductDetail>(`/admin/products/${productId}`),

  getToolFootprint: (productId: string) =>
    req<ProductFootprintRow[]>(`/admin/products/${productId}/footprint`),

  publishTool: (productId: string) =>
    req<ProductPublishState>(`/admin/products/${productId}/publish`, { method: "POST" }),

  unpublishTool: (productId: string) =>
    req<ProductPublishState>(`/admin/products/${productId}/unpublish`, { method: "POST" }),

  dryRunBaselineImport: async (file: File): Promise<BaselineImportPreview> => {
    const form = new FormData();
    form.append("file", file);
    const r = await fetch(`${BASE}/admin/products/import/dry-run`, { method: "POST", body: form });
    if (!r.ok) {
      const body = await r.json().catch(() => ({}));
      throw new Error(body.detail ?? `${r.status} ${r.statusText}`);
    }
    return r.json() as Promise<BaselineImportPreview>;
  },

  applyBaselineImport: async (file: File): Promise<BaselineImportResult> => {
    const form = new FormData();
    form.append("file", file);
    const r = await fetch(`${BASE}/admin/products/import/apply`, { method: "POST", body: form });
    if (!r.ok) {
      const body = await r.json().catch(() => ({}));
      const detail = body.detail;
      const msg = detail && typeof detail === "object" && Array.isArray(detail.problems)
        ? detail.problems.join("; ")
        : detail ?? `${r.status} ${r.statusText}`;
      throw new Error(msg);
    }
    return r.json() as Promise<BaselineImportResult>;
  },

  listToolDocuments: (productId: string) =>
    req<ProductDocumentItem[]>(`/admin/products/${productId}/documents`),

  uploadToolDocument: async (
    productId: string,
    file: File,
    data: { title?: string; kind: string; source_docs_ref?: string }
  ): Promise<ProductDocumentItem> => {
    const form = new FormData();
    form.append("file", file);
    if (data.title) form.append("title", data.title);
    form.append("kind", data.kind);
    if (data.source_docs_ref) form.append("source_docs_ref", data.source_docs_ref);
    const r = await fetch(`${BASE}/admin/products/${productId}/documents`, { method: "POST", body: form });
    if (!r.ok) {
      const body = await r.json().catch(() => ({}));
      throw new Error(body.detail ?? `${r.status} ${r.statusText}`);
    }
    return r.json() as Promise<ProductDocumentItem>;
  },

  deleteToolDocument: async (productId: string, documentId: string): Promise<void> => {
    const r = await fetch(`${BASE}/admin/products/${productId}/documents/${documentId}`, { method: "DELETE" });
    if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  },

  toolDocumentDownloadUrl: (productId: string, documentId: string): string =>
    `${BASE}/admin/products/${productId}/documents/${documentId}/download`,

  // ── User directory (ADR 0009 M.7, G.11) — deployment-wide, not
  // org-scoped; msp_admin only, NOT consultant_admin (backend/app/
  // routers/admin_users.py — identity administration, not compliance-data
  // configuration; see that router's own docstring for the distinction
  // from Integrations/Tools). Grant/revoke itself is org-scoped —
  // grantMembership/revokeMembership above. ─────────────────────────────
  listUserDirectory: () => req<UserDirectoryEntry[]>("/admin/users"),

  getMspOrg: () => req<MspOrg | null>("/admin/users/msp-org"),

  // ── Practitioner notes (migration 0032) — deployment-wide, not
  // org-scoped; msp_admin only (backend/app/routers/objectives.py). ────
  editPractitionerNotes: (objectiveId: string, text: string) =>
    req<PractitionerNotesUpdate>(`/objectives/${objectiveId}/practitioner-notes`, {
      method: "PATCH",
      body: JSON.stringify({ text }),
    }),

  revertPractitionerNotes: (objectiveId: string) =>
    req<PractitionerNotesUpdate>(`/objectives/${objectiveId}/practitioner-notes/revert`, {
      method: "POST",
    }),

  downloadBundle: async (orgId: string, assessmentId: string): Promise<void> => {
    const r = await fetch(`/api/orgs/${orgId}/assessments/${assessmentId}/bundle`);
    if (!r.ok) {
      let msg = `${r.status} ${r.statusText}`;
      try { const body = await r.json(); if (body.detail) msg = body.detail; } catch { /* ignore */ }
      throw new Error(msg);
    }
    const blob = await r.blob();
    const cd = r.headers.get("Content-Disposition") ?? "";
    const match = cd.match(/filename="([^"]+)"/);
    const filename = match?.[1] ?? `bundle_${assessmentId}.zip`;
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  },
};

const CACHE_PREFIX = "wingrc_assessment_";

export function getCachedAssessmentId(orgId: string): string | null {
  return localStorage.getItem(`${CACHE_PREFIX}${orgId}`);
}

export function setCachedAssessmentId(orgId: string, assessmentId: string) {
  localStorage.setItem(`${CACHE_PREFIX}${orgId}`, assessmentId);
}
