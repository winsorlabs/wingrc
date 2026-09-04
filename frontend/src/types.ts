export interface Org {
  id: string;
  name: string;
  created_at: string;
}

export interface Framework {
  id: string;
  key: string;
  name: string;
  version: string;
  published_at: string | null;
}

export interface Assessment {
  id: string;
  org_id: string;
  framework_id: string;
  name: string;
  assessment_type: string;
  status: string;
  started_at: string;
  sprs_score: number | null;
  // G.4: derived at read time (MAX of control_state/implementation_statement
  // updated_at for this assessment) — see backend/app/routers/assessments.py's
  // _last_activity_by_assessment for why this isn't a stored column.
  last_activity_at: string;
}

export interface ControlStateRow {
  id: string;
  objective_id: string;
  control_id: string;
  control_db_id: string;
  family: string;
  control_title: string;
  objective_key: string;
  objective_text: string;
  status: string;
  responsibility: string;
  sourced_from_product_id: string | null;
  sourced_from_product_key: string | null;
  statement_status: string | null;
  evidence_count: number;
  sprs_weight: number;
  is_level_1: boolean;
}

export interface ProductRow {
  id: string;
  key: string;
  name: string;
  provider: string;
  category: string;
  role: string;
  is_active: boolean;
  activated_at: string | null;
  provider_satisfies_count: number;
  shared_count: number;
  customer_owns_count: number;
}

export interface EvidenceTaskStateRef {
  control_state_id: string;
  objective_id: string;
  control_id: string;
  objective_key: string;
}

export interface EvidenceTaskRow {
  id: string;
  title: string;
  artifact_type: string;
  status: string;
  is_archived: boolean;
  archived_at: string | null;
  collection_session: string | null;
  baseline_spec_id: string | null;
  source_product_key: string | null;
  source_product_name: string | null;
  cadence: string | null;
  linked_states: EvidenceTaskStateRef[];
}

export interface StatementRow {
  id: string | null;
  objective_id: string;
  control_state_id: string | null;
  objective_key: string;
  objective_text: string;
  objective_guidance: string | null;
  body: string;
  status: string | null;
  control_discussion: string | null;
}

export interface EvidenceRow {
  id: string;
  title: string;
  artifact_type: string;
  kind: string;
  reference_location: string | null;
  note: string | null;
  mime_type: string | null;
  file_size_bytes: number | null;
  collected_at: string;
  download_url: string | null;
}

export interface OrgProfile {
  id: string;
  name: string;
  created_at: string;
  updated_at: string | null;
  cage_code: string | null;
  uei: string | null;
  year_established: number | null;
  industry: string | null;
  address_line1: string | null;
  address_line2: string | null;
  city: string | null;
  state_or_province: string | null;
  postal_code: string | null;
  country: string | null;
  phone_primary: string | null;
  phone_secondary: string | null;
  website: string | null;
  logo_storage_key: string | null;
  logo_url: string | null;
}

export interface StorageLocation {
  type: string;
  description: string;
}

export interface ExternalConnection {
  name: string;
  direction: string;
  purpose: string;
}

export interface SystemDescriptionData {
  id: string;
  org_id: string;
  system_name: string;
  system_type: string;
  operational_status: string;
  system_description: string | null;
  cui_categories: string[];
  cui_storage_locations: StorageLocation[];
  authorization_boundary_description: string | null;
  external_connections: ExternalConnection[];
  cui_flow_description: string | null;
  // Diagram slots (migration 0029) — mirrors backend/app/routers/orgs.py's
  // SystemDescriptionOut field-for-field.
  network_diagram_evidence_id: string | null;
  network_diagram_url: string | null;
  data_flow_diagram_evidence_id: string | null;
  data_flow_diagram_url: string | null;
  created_at: string;
  updated_at: string;
}

export interface DiagramUpload {
  evidence_id: string;
  url: string | null;
  mime_type: string;
}

export interface ContactDocRole {
  role: string;
  notes: string | null;
}

export interface Contact {
  id: string;
  org_id: string;
  name: string;
  email: string;
  affiliation: string;
  phone: string | null;
  role_title: string | null;
  contract_ref: string | null;
  notes: string | null;
  documentation_roles: ContactDocRole[];
  created_at: string;
}

export interface OnboardingStatus {
  profile: { complete: boolean; missing_fields: string[] };
  system_description: { complete: boolean };
  personnel: { complete: boolean; contact_count: number; roles_covered: string[] };
}

// GET /orgs/{org_id}/assessments/{assessment_id}/dashboard (G.3) — mirrors
// backend/app/routers/dashboard.py's DashboardOut and its nested models
// field-for-field.
export interface FamilyHeatmapEntry {
  family: string;
  controls_met: number;
  controls_total: number;
}

export interface SprsTrajectoryPoint {
  computed_at: string;
  score: number;
}

export interface SprsWidgetData {
  current_score: number | null;
  trajectory: SprsTrajectoryPoint[];
}

export interface StatementProgress {
  draft: number;
  reviewed: number;
  approved: number;
  not_started: number;
}

export interface EvidenceExpiringItem {
  task_id: string;
  title: string;
  expires_at: string;
}

export interface ReviewQueueItem {
  control_state_id: string;
  control_id: string;
  family: string;
  objective_key: string;
}

export interface BlockedObjectiveItem {
  control_state_id: string;
  control_id: string;
  family: string;
  objective_key: string;
}

export interface RaciBucket {
  contact_id: string | null;
  contact_name: string | null;
  open_task_count: number;
}

export interface PoamSummary {
  open: number;
  on_track: number;
  delayed: number;
  completed: number;
  cancelled: number;
}

export interface DashboardData {
  family_heatmap: FamilyHeatmapEntry[];
  sprs: SprsWidgetData;
  statement_progress: StatementProgress;
  evidence_expiring: EvidenceExpiringItem[];
  needs_review: ReviewQueueItem[];
  needs_review_count: number;
  blocked_objectives: BlockedObjectiveItem[];
  blocked_objectives_count: number;
  raci_open_tasks: RaciBucket[];
  poam_summary: PoamSummary;
}

export interface AuthUser {
  id: string;
  org_id: string;
  email: string;
  display_name: string;
  role: string;
  login_method: string;
  mfa_enrolled: boolean;
}

export interface MfaEnrollData {
  provisioning_uri: string;
  secret: string;
  // Server-rendered inline SVG data: URI (ADR 0008) — never a third-party
  // request; render directly as an <img src>.
  qr_data_uri: string;
}

// Step-up proof for a self-service account action (I.9) — current password
// or a current TOTP code, never both required. Mirrors the backend's
// StepUpIn (routers/auth.py).
export interface StepUpIn {
  current_password?: string;
  totp_code?: string;
}

export interface SessionRow {
  id: string;
  created_at: string;
  last_activity_at: string;
  expires_at: string;
  is_current: boolean;
}

export interface ApiTokenRow {
  id: string;
  name: string;
  role: string;
  created_at: string;
  expires_at: string | null;
  last_used_at: string | null;
}

export interface CreatedApiToken {
  id: string;
  name: string;
  role: string;
  expires_at: string | null;
  token: string;
}

// Resolved at read time from the current `user` table — never written into
// audit_log itself, which stores only the raw GUID (actor / entity_id).
// status:
//   "active"     — display_name/email are current
//   "anonymized" — user row survives (ADR 0006 anonymize) but PII is
//                  scrubbed; display_name/email are null, never the
//                  placeholder values
//   "deleted"    — no user row at all (ADR 0006 hard-delete); the
//                  expected, documented outcome, not a data bug
export interface ResolvedIdentity {
  id: string;
  status: "active" | "anonymized" | "deleted";
  display_name: string | null;
  email: string | null;
}

export interface AuditLogRow {
  id: string;
  created_at: string;
  actor: string;
  actor_type: string;
  // null when `actor` isn't a resolvable GUID (e.g. the literal "system").
  actor_user: ResolvedIdentity | null;
  action: string;
  entity_type: string;
  entity_id: string;
  // null unless entity_type === "user".
  entity_user: ResolvedIdentity | null;
  before_value: Record<string, unknown> | null;
  after_value: Record<string, unknown> | null;
  context: Record<string, unknown> | null;
  // NULL means "predates IP capture, or logged outside an HTTP request" —
  // never "hidden by a filter". See AuditLogPanel's own note to the admin.
  ip_address: string | null;
}

export interface AuditLogPage {
  items: AuditLogRow[];
  total: number;
  offset: number;
  limit: number;
}

export interface UserRow {
  id: string;
  org_id: string;
  contact_id: string | null;
  email: string;
  display_name: string;
  role: string;
  login_method: string;
  is_active: boolean;
  mfa_enrolled: boolean;
  requires_admin_reset: boolean;
  // I.5: locked_until alone can't tell an admin apart a 1st/2nd lockout
  // (requires_admin_reset stays false) from a never-locked account.
  // lockout_count is what makes requires_admin_reset legible.
  locked_until: string | null;
  lockout_count: number;
  last_login_at: string | null;
  created_at: string;
  // ADR 0006: permanent, irreversible anonymization marker — distinct from
  // is_active. Once set, the row can never be reactivated.
  deleted_at: string | null;
}

export interface InvitedUser {
  id: string;
  email: string;
  display_name: string;
  role: string;
  login_method: string;
  is_active: boolean;
  invite_token: string;
  invite_expires_at: string;
}

export interface PasswordResetIssued {
  reset_token: string;
  expires_at: string;
}

// ── Scope / Assets (G.5) — mirrors backend/app/routers/scope.py's schemas ──
export interface ScopeEntity {
  id: string;
  entity_type: string;
  natural_key: string;
  scope_category: string | null;
  status: string;
  in_boundary: boolean;
  source: string;
  source_ref: string | null;
  attributes: Record<string, unknown>;
}

export interface ScopeChangeIncoming {
  scope_category: string | null;
  status: string;
  in_boundary: boolean;
  source: string;
  source_ref: string | null;
  attributes: Record<string, unknown>;
}

export interface ScopeChange {
  change_type: "new" | "changed" | "missing" | "unchanged";
  entity_type: string;
  natural_key: string;
  field_diffs: Record<string, [unknown, unknown]>;
  incoming: ScopeChangeIncoming | null;
}

export interface DryRunResult {
  summary: Record<string, number>;
  changes: ScopeChange[];
}
