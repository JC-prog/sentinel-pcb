/** Types mirroring app/agents/orchestrator_agent's SSE event payloads
 * (POST /api/orchestrator/run/stream) and upload responses. */

export type OrchestratorRunMode = 'prepare' | 'prepare_verify' | 'run_full';

export interface OrchestratorUploadRecord {
  id: string;
}

/** GET /api/orchestrator/status - mirrors the source tkinter app's "OpenAI key detected" label
 * next to its Use real LLM Planner checkbox, without exposing the key itself. */
export interface OrchestratorStatus {
  llm_configured: boolean;
}

export interface OrchestratorStatusEvent {
  status: string;
  input_samples: number;
  preparation_ready: number;
  verification_passed: number;
  inference_attempted: number;
  accepted: number;
  review_required: number;
}

export interface OrchestratorPlanStepEvent {
  plan_version: number;
  observation: string;
  constraint: string;
  decision: string;
  reason: string;
  planner_source: string;
  policy: { allowed: boolean; reason: string } | null;
  tool_status: string | null;
}

export interface OrchestratorRunRequest {
  mode: OrchestratorRunMode;
  dataset_id: string;
  xml_id: string;
  image_root_id?: string | null;
  feature_threshold: number;
  defect_threshold: number;
  use_llm: boolean;
  llm_model?: string | null;
  llm_fallback: boolean;
}

/** One line of the Execution Log panel - either a plain log line or a planner step, in the order
 * they arrived, so the log reads the same top-to-bottom sequence the tkinter source app's
 * Execution Log text widget showed. */
export type OrchestratorLogEntry =
  | { kind: 'log'; text: string }
  | { kind: 'plan_step'; step: OrchestratorPlanStepEvent }
  | { kind: 'error'; message: string };

/** One of orchestrator.py's per-sample stage results ("feature_classification"/
 * "defect_classification"), whichever of the two ran - see monitoring.py's module docstring for
 * why a sample stopping at stage 1 has no "routing"/"defect_classification". model_version is
 * absent (not null) on a run from before this field existed. */
export interface OrchestratorStageResult {
  prediction: string;
  confidence: number;
  probabilities?: Record<string, number>;
  model_version?: string | null;
}

/** One element of a finished run's `results` array (app/workflow/agents/orchestrator_agent/
 * orchestrator.py's state.inference_results) - untyped on the wire until now (the app only ever
 * JSON.stringify'd the whole result for download). Every field but sample_id/final_decision is
 * optional because the failure-path payload shape only has sample_id/status/errors/details/
 * final_decision - see OrchestratorRunResult below. */
export interface OrchestratorInferenceResult {
  sample_id: string;
  final_decision: 'ACCEPTED' | 'REVIEW_REQUIRED' | 'ABORTED';
  status?: string;
  source_feature?: string;
  machine_defect?: string;
  feature_classification?: OrchestratorStageResult;
  // service_model is the real inference-service model name ("pcb_body_defect") - what a drift
  // report/retraining ticket must be filed against; selected_model is only the internal routing
  // key ("body") and must never be sent as a model name.
  routing?: { selected_model: string; service_model: string };
  defect_classification?: OrchestratorStageResult;
  comparison?: { feature_agreement: boolean; defect_agreement: boolean };
  failed_inspections?: Record<string, unknown>;
  explainability_result?: Record<string, unknown>;
  errors?: string[];
  // On a failure path (never reached stage 2), the only stage result available is nested here.
  details?: { feature_classification?: OrchestratorStageResult; sample_id?: string };
}

/** The SSE `result` event's payload - the whole finished-run summary, previously untyped
 * (Signal<unknown>). Only `results` is used by the UI today; the rest is kept loose since nothing
 * reads it besides the raw JSON download. */
export interface OrchestratorRunResult {
  /** Identifies this run to the Agent 2 review routes - the server keeps what a review needs. */
  run_id?: string;
  workflow_status: string;
  termination_reason: string | null;
  results: OrchestratorInferenceResult[];
  [key: string]: unknown;
}

export interface WorkflowDriftReportRequest {
  model_name: string;
  description: string;
  model_version?: string | null;
  /** The run being reported on - the server then snapshots its own numbers for the model. */
  run_id?: string | null;
  samples: OrchestratorInferenceResult[];
}

export interface WorkflowDriftReportOut {
  id: string;
  model_name: string;
  model_version: string | null;
  status: string;
}

export interface WorkflowRetrainingTicketItem {
  sample: OrchestratorInferenceResult;
  reason: string;
  correct_label?: string | null;
}

export interface WorkflowRetrainingTicketsRequest {
  /** With a run id, a sample already flagged for that run is skipped instead of flagged twice. */
  run_id?: string | null;
  tickets: WorkflowRetrainingTicketItem[];
}

export interface WorkflowRetrainingTicketOut {
  id: string;
  sample_ref: string | null;
  model_name: string | null;
  status: string;
}

/** One model's numbers for a run, computed on the server from the stored samples and the
 * operator's decisions (GET /api/orchestrator/monitoring/run-drift). `model_name` is null for
 * samples that stopped at feature classification and reached no defect model. */
export interface WorkflowModelDrift {
  model_name: string | null;
  model_version: string | null;
  samples: number;
  review_required: number;
  decided: number;
  corrected: number;
  correction_rate: number | null;
  agent2_disagreed: number;
  low_confidence_rate: number | null;
  mean_confidence: number | null;
}

/** A sample where the operator's final result differs from Agent 1's label. `queueable` is false
 * when no model was recorded to file a retraining ticket against; `queued` when one exists. */
export interface WorkflowCorrection {
  sample_id: string;
  model_name: string | null;
  model_version: string | null;
  agent1_label: string | null;
  final_result: string;
  selected_source: string | null;
  operator_notes: string | null;
  queueable: boolean;
  queued: boolean;
}

export interface WorkflowRunDrift {
  run_id: string;
  /** False when the stored run could not be read; `message` says so. */
  available: boolean;
  message?: string | null;
  totals: { samples: number; review_required: number; decided: number; corrected: number };
  models: WorkflowModelDrift[];
  corrections: WorkflowCorrection[];
  /** Open (not yet planned) retraining tickets per model - what a plan drafted now would contain. */
  open_tickets?: Record<string, number>;
}

/** A drafted retraining plan: a job awaiting an Admin's approval in the Models tab. */
export interface WorkflowRetrainingPlanOut {
  job_id: string;
  model_name: string;
  status: string;
  base_version: string;
  sample_count: number;
  drift_reports_linked: number;
}

export interface WorkflowQueueCorrectionsRequest {
  run_id: string;
  sample_ids: string[];
}

export interface WorkflowQueueCorrectionsOut {
  created: WorkflowRetrainingTicketOut[];
  already_queued: string[];
}

/** One Agent 2 review next to Agent 1's verdict, pushed over the run stream as a `review` event and
 * returned inside a case by GET /api/orchestrator/reviews/cases. `conflict` only flags that the two
 * agents differ (or Agent 2's self-check failed) - every case still waits for the operator. */
export interface WorkflowReviewOut {
  run_id: string;
  sample_id: string;
  agent1_verdict: string;
  agent2_verdict: string;
  conflict: boolean;
  diagnosis: string;
  confidence: number;
  self_check_passed: boolean;
  contradiction_detected: boolean;
  ipc_citations: string[];
  visual_evidence: string;
  errors: string[];
}

export type WorkflowDecisionSource = 'MACHINE' | 'AI' | 'MANUAL';

export interface WorkflowReviewDecisionRequest {
  run_id: string;
  sample_id: string;
  selected_source: WorkflowDecisionSource;
  final_result: string;
  machine_result?: string | null;
  ai_result?: string | null;
  ai_diagnosis?: string | null;
  operator_notes?: string | null;
}

export interface WorkflowReviewDecisionOut {
  run_id: string;
  sample_id: string;
  selected_source: WorkflowDecisionSource;
  final_result: string;
  machine_result: string | null;
  ai_result: string | null;
  operator_notes: string | null;
  decided_by_user_id: string;
}

/** One row of the Review Console: a REVIEW_REQUIRED sample of a run, with Agent 2's review (null
 * while it is still pending) and the operator's decision (null until saved). */
export interface WorkflowReviewCaseOut {
  run_id: string;
  sample_id: string;
  board_id: string;
  component_ref: string;
  feature_type: string;
  agent1_verdict: string;
  agent1_confidence: number;
  has_golden_image: boolean;
  has_defect_image: boolean;
  review: WorkflowReviewOut | null;
  decision: WorkflowReviewDecisionOut | null;
}
