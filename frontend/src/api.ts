import type { Assessment, ControlStateRow, EvidenceRow, Framework, Org, ProductRow, StatementRow } from "./types";

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
};

const CACHE_PREFIX = "wingrc_assessment_";

export function getCachedAssessmentId(orgId: string): string | null {
  return localStorage.getItem(`${CACHE_PREFIX}${orgId}`);
}

export function setCachedAssessmentId(orgId: string, assessmentId: string) {
  localStorage.setItem(`${CACHE_PREFIX}${orgId}`, assessmentId);
}
