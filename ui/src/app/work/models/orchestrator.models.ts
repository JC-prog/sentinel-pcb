/** Types mirroring app/agents/orchestrator_agent's SSE event payloads
 * (POST /api/orchestrator/run/stream) and upload responses. */

export type OrchestratorRunMode = 'prepare' | 'prepare_verify' | 'run_full';

export interface OrchestratorUploadRecord {
  id: string;
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
  workflow_status: string;
  termination_reason: string | null;
  results: OrchestratorInferenceResult[];
  [key: string]: unknown;
}

export interface WorkflowDriftReportRequest {
  model_name: string;
  description: string;
  model_version?: string | null;
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
  tickets: WorkflowRetrainingTicketItem[];
}

export interface WorkflowRetrainingTicketOut {
  id: string;
  sample_ref: string | null;
  model_name: string | null;
  status: string;
}
