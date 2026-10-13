/** Types mirroring app/chat/services/case_views.py (GET /api/cases, GET /api/cases/{case_number}). */

export interface CaseSummary {
  case_number: string;
  status: string;
  board_id: string;
  component_ref: string;
  region: string | null;
  defect_model: string | null;
  defect_model_version: string | null;
  defect_label: string | null;
  defect_confidence: number | null;
  corrected_label: string | null;
  created_by: string | null;
  created_at: string;
}

export interface CaseTicket {
  id: string;
  status: string;
  job_id: string | null;
  model_name: string | null;
  observed_label: string | null;
  correct_label: string | null;
}

export interface CaseDetail extends CaseSummary {
  package: string | null;
  feature: string | null;
  issue_symptom: string | null;
  region_confidence: number | null;
  region_model_version: string | null;
  defect_scores: Record<string, number>;
  measurement_validation: { valid?: boolean; issues?: string[] } | null;
  observations: string[];
  image_id: string;
  inspection_xml_id: string | null;
  correction_reason: string | null;
  corrected_by: string | null;
  corrected_at: string | null;
  resolved_by: string | null;
  resolved_at: string | null;
  resolution_note: string | null;
  pending_label: string | null;
  pending_reason: string | null;
  ticket: CaseTicket | null;
  /** Set only for the user who owns the conversation. */
  conversation_id: string | null;
}

export interface CaseList {
  total: number;
  limit: number;
  offset: number;
  cases: CaseSummary[];
}

export interface CaseQuery {
  q: string;
  status: string;
  model: string;
  corrected: '' | 'yes' | 'no';
  offset: number;
  limit: number;
}
