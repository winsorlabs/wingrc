import type { Assessment, Contact, ControlStateRow, EvidenceRow, EvidenceTaskRow, Framework, OnboardingStatus, Org, OrgProfile, ProductRow, StatementRow, SystemDescriptionData } from "./types";

const BASE = "/api";

async function req<T>(path: string, opts?: RequestInit): Promise<T> {
  const r = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  return r.json() as Promise<T>;
}

export const api = {
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

  putSystemDescription: (orgId: string, data: Omit<SystemDescriptionData, "id" | "org_id" | "created_at" | "updated_at">) =>
    req<SystemDescriptionData>(`/orgs/${orgId}/system-description`, {
      method: "PUT",
      body: JSON.stringify(data),
    }),

  // ── Onboarding status ─────────────────────────────────────────────────────
  getOnboardingStatus: (orgId: string) =>
    req<OnboardingStatus>(`/orgs/${orgId}/onboarding-status`),

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
};

const CACHE_PREFIX = "wingrc_assessment_";

export function getCachedAssessmentId(orgId: string): string | null {
  return localStorage.getItem(`${CACHE_PREFIX}${orgId}`);
}

export function setCachedAssessmentId(orgId: string, assessmentId: string) {
  localStorage.setItem(`${CACHE_PREFIX}${orgId}`, assessmentId);
}
