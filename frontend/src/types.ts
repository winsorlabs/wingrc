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
  statement_status: string | null;
  evidence_count: number;
}

export interface StatementRow {
  id: string | null;
  objective_id: string;
  objective_key: string;
  objective_text: string;
  objective_guidance: string | null;
  body: string;
  status: string | null;
}
