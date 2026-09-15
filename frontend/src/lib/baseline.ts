// Mirrors backend/app/baseline_import.py's _VALID_* frozensets for the
// per-control review table's dropdowns (ToolImportWizard.tsx, documents
// mode). Kept in sync by hand -- there is no runtime enum-discovery
// endpoint for four small, rarely-changing sets, matching this codebase's
// existing precedent (lib/raci.ts's RACI_LETTERS mirrors a backend enum
// the same way). If backend_import.py's sets ever change, update here too.

export const CLASSIFICATIONS = ["provider_satisfies", "shared", "customer_owns"] as const;
export const CLASSIFICATION_LABELS: Record<string, string> = {
  provider_satisfies: "Provider satisfies",
  shared: "Shared",
  customer_owns: "Customer owns",
};

// coverage_basis is moot for customer_owns (see baseline.py:ControlEntry's
// own docstring) -- ToolImportWizard.tsx disables this dropdown for those
// rows rather than offering a meaningless choice.
export const COVERAGE_BASES = ["customer_system", "platform_only", "assists"] as const;
export const COVERAGE_BASIS_LABELS: Record<string, string> = {
  customer_system: "Customer system",
  platform_only: "Platform only",
  assists: "Assists",
};

export const CANDIDATE_STATES = ["pending_evidence", "not_satisfied_by_product"] as const;
export const CANDIDATE_STATE_LABELS: Record<string, string> = {
  pending_evidence: "Pending evidence",
  not_satisfied_by_product: "Not satisfied by product",
};

export const EVIDENCE_TYPES = ["screenshot", "export", "document", "link", "policy"] as const;
