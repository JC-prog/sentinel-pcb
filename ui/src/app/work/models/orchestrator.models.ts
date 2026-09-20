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
