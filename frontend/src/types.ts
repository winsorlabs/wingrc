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
