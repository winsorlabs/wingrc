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
  created_at: string;
  updated_at: string;
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
