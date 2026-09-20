/** Wire shapes of the Models tab's API (app/modelops/services/schemas.py). Kept in the API's own
 * snake_case, like work/models/orchestrator.models.ts, rather than mapped to camelCase. */

export interface InferenceStatus {
  configured: boolean;
  reachable: boolean;
  error: string | null;
}

export type ModelVersionStatus = 'live' | 'previous' | 'candidate' | 'retired';

export interface ModelVersion {
  id: string;
  model_name: string;
  /** "<repo_id>@<revision>", as the inference service reports it. */
  version: string;
  repo_id: string;
  revision: string;
  status: ModelVersionStatus;
  source_job_id: string | null;
  first_seen_at: string;
  activated_at: string | null;
  activated_by: string | null;
}

export interface ModelSummary {
  name: string;
  live: ModelVersion | null;
  previous: ModelVersion | null;
  versions: ModelVersion[];
  /** Only known while the inference service is reachable. */
  labels: string[] | null;
  loaded_at: string | null;
  open_drift_reports: number;
  open_tickets: number;
  /** Pending approval, approved, queued or running. */
  active_jobs: number;
}

export interface ModelsOverview {
  inference: InferenceStatus;
  models: ModelSummary[];
}

export interface DriftReport {
  id: string;
  model_name: string;
  model_version: string | null;
  description: string;
  case_numbers: string[];
  /** The drift numbers as they were when the report was filed (app/chat/services/drift.py). */
  stats: DriftStats;
  status: 'open' | 'resolved';
  reported_by: string;
  created_at: string;
  resolved_at: string | null;
}

export interface DriftWindowStats {
  cases: number;
  needs_review: number;
  approved: number;
  overridden: number;
  review_rate: number | null;
  override_rate: number | null;
  low_confidence_rate: number | null;
  mean_confidence: number | null;
}

export interface DriftStats {
  window_days?: number;
  recent?: DriftWindowStats;
  previous?: DriftWindowStats;
  enough_data?: boolean;
  signals?: string[];
}

export interface DriftOverview {
  window_days: number;
  open_by_model: Record<string, number>;
  reported_in_window_by_model: Record<string, number>;
  reports: DriftReport[];
}

export type JobStatus =
  | 'pending_approval'
  | 'approved'
  | 'queued'
  | 'running'
  | 'succeeded'
  | 'failed'
  | 'cancelled';

export interface RetrainingJob {
  id: string;
  model_name: string;
  base_version: string;
  status: JobStatus;
  rationale: string;
  sample_count: number;
  drift_report_ids: string[];
  created_by: string;
  approved_by: string | null;
  approved_at: string | null;
  external_job_id: string | null;
  progress: number;
  error: string | null;
  artifact_repo_id: string | null;
  artifact_revision: string | null;
  /** True when the run was the stub trainer: nothing was actually trained. */
  simulated: boolean | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface JobSample {
  case_id: string;
  case_number: string | null;
  ticket_id: string | null;
  observed_label: string | null;
  expected_label: string | null;
}

export interface RetrainingJobDetail extends RetrainingJob {
  samples: JobSample[];
}

export interface TicketCounts {
  open: number;
  acknowledged: number;
  resolved: number;
}

export interface QueueOverview {
  inference: InferenceStatus;
  tickets: TicketCounts;
  jobs: RetrainingJob[];
}

/** Statuses where the job is still moving - what the page keeps polling for. */
export const ACTIVE_JOB_STATUSES: readonly JobStatus[] = ['approved', 'queued', 'running'];

export const FINISHED_JOB_STATUSES: readonly JobStatus[] = ['succeeded', 'failed', 'cancelled'];
