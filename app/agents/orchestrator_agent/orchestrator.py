"""Adapted from orchestrator-agent/adc_agentic_project's agents/orchestrator.py.

Loop:
  Workflow State -> LLM Planner -> Observation/Constraint/Decision/Reason
  -> Policy Engine -> Tool Execution -> State Update -> Re-plan

The LLM decides WHAT should happen next. Domain services and policy remain
deterministic so model inference and safety constraints are reproducible.

Two changes from the source, both needed to stream progress to the Work-tab UI as the workflow
runs rather than only once at the end (the source's tkinter UI only updated between separate
button clicks, never mid-run - see ui.py's `_run_full`, which calls `agent.run()` as a single
blocking call):

1. `run()` is an `async def` generator, `yield`ing a `{"event": ..., "data": ...}` dict after each
   planner iteration and state-changing step, instead of `return`ing a `WorkflowState` once at the
   end. `runner.py` turns these into SSE frames.
2. Every blocking call (the sync `Planner.plan()` - which may make a sync OpenAI SDK call - and
   the sync pandas/opencv dataset services) is wrapped in `asyncio.to_thread(...)` so it doesn't
   block the event loop other requests are running on. `_execute_inference` instead awaits
   `TwoStageInferenceService.infer_sample()` directly, since that one's already async (it awaits
   the inference microservice's httpx client - see services/multimodal_inference.py).

Everything else - the loop's structure, the loop-detection/max-replans safety net, the per-step
methods - is unchanged from the source.
"""

import asyncio
import json
import logging

from app.agents.orchestrator_agent.planner import Planner
from app.agents.orchestrator_agent.policy_engine import PolicyEngine
from app.agents.orchestrator_agent.workflow_state import WorkflowState
from app.agents.orchestrator_agent.services.dataset_preparation import DatasetPreparationService
from app.agents.orchestrator_agent.services.dataset_verification import DatasetVerificationService
from app.agents.orchestrator_agent.services.model_lifecycle import ModelLifecycleService
from app.agents.orchestrator_agent.services.multimodal_inference import TwoStageInferenceService
from app.agents.orchestrator_agent.services.result_comparison import final_decision
from app.config.settings import settings

logger = logging.getLogger(__name__)


class OrchestratorAgent:
    """
    Agentic ADC workflow controller.

    Loop:
      Workflow State -> LLM Planner -> Observation/Constraint/Decision/Reason
      -> Policy Engine -> Tool Execution -> State Update -> Re-plan

    The LLM decides WHAT should happen next. Domain services and policy remain
    deterministic so model inference and safety constraints are reproducible.
    """

    def __init__(
        self,
        username,
        feature_threshold=0.70,
        defect_threshold=0.70,
        use_llm=False,
        planner_model=None,
        allow_llm_fallback=True,
        max_replans=8,
    ):
        self.planner = Planner(
            use_llm=use_llm,
            model=planner_model,
            allow_fallback=allow_llm_fallback,
        )
        self.policy = PolicyEngine()
        self.preparation = DatasetPreparationService()
        self.verification = DatasetVerificationService()
        self.lifecycle = ModelLifecycleService()
        self.inference = TwoStageInferenceService(self.lifecycle, username, feature_threshold)
        self.defect_threshold = defect_threshold
        self.max_replans = max_replans
        self.use_llm = use_llm

    async def run(self, dataset_csv, inspection_xml, image_root=None):
        state = WorkflowState(
            status="RUNNING",
            inputs={
                "dataset_csv": dataset_csv,
                "inspection_xml": inspection_xml,
                "image_root": image_root,
            },
            planner_backend="openai" if self.use_llm else "deterministic",
            llm_enabled=self.use_llm,
            planner_model=self.planner.model if self.use_llm else None,
        )

        yield {"event": "log", "data": {"text": "=== AGENTIC ADC WORKFLOW ==="}}
        yield {
            "event": "log",
            "data": {
                "text": f"Planner: {'OpenAI LLM' if self.use_llm else 'Deterministic'} "
                f"| Model: {self.planner.model if self.use_llm else 'N/A'}"
            },
        }

        last_action = None
        repeated_action_count = 0

        while state.status == "RUNNING":
            if state.replan_count >= self.max_replans:
                state.status = "ABORTED"
                state.termination_reason = "MAX_REPLAN_LIMIT_REACHED"
                state.observations.append(
                    f"Stopped after {self.max_replans} planner iterations to prevent loops."
                )
                break

            plan = await asyncio.to_thread(self.planner.plan, state)
            state.replan_count += 1

            plan_record = {
                "plan_version": state.replan_count,
                "observation": plan.observation,
                "constraint": plan.constraint,
                "decision": plan.decision,
                "reason": plan.reason,
                "planner_source": plan.source,
                "policy": None,
                "tool_status": None,
            }

            state.observations.append(plan.observation)

            if plan.decision == last_action:
                repeated_action_count += 1
            else:
                repeated_action_count = 0
            last_action = plan.decision

            if repeated_action_count >= 2:
                plan_record["policy"] = {
                    "allowed": False,
                    "reason": "Repeated planner action detected; loop prevention activated.",
                }
                state.plan_history.append(plan_record)
                yield {"event": "plan_step", "data": plan_record}
                state.status = "ABORTED"
                state.termination_reason = "PLANNER_LOOP_DETECTED"
                break

            allowed, policy_reason = self.policy.validate_action(plan.decision, state)
            plan_record["policy"] = {"allowed": allowed, "reason": policy_reason}

            if not allowed:
                state.plan_history.append(plan_record)
                state.observations.append(f"Policy rejected {plan.decision}: {policy_reason}")
                yield {"event": "plan_step", "data": plan_record}
                # Bounded re-plan: planner sees the new observation on next iteration.
                continue

            state.current_step = plan.decision

            try:
                if plan.decision == "dataset_preparation":
                    result = await self._execute_preparation(state)
                elif plan.decision == "dataset_verification":
                    result = await self._execute_verification(state)
                elif plan.decision == "multimodal_inference":
                    result = await self._execute_inference(state)
                elif plan.decision == "finalize":
                    self._finalize(state)
                    result = {"status": state.status, "success": state.status != "ABORTED"}
                elif plan.decision == "abort":
                    state.status = "ABORTED"
                    state.termination_reason = "PLANNER_ABORTED_WORKFLOW"
                    result = {"status": "ABORTED", "success": False}
                else:
                    result = {"status": "UNKNOWN_ACTION", "success": False}
            except Exception as exc:
                error = f"{plan.decision} failed: {type(exc).__name__}: {exc}"
                state.errors.append(error)
                state.observations.append(error)
                result = {"status": "TOOL_EXECUTION_FAILED", "success": False, "error": error}

            plan_record["tool_status"] = result.get("status")
            state.plan_history.append(plan_record)
            state.tool_history.append({
                "step": plan.decision,
                "status": result.get("status"),
                "success": result.get("success", False),
            })

            yield {"event": "plan_step", "data": plan_record}
            yield {"event": "status", "data": self._status_payload(state)}

            # If an unrecoverable tool execution produced no path forward, abort.
            if result.get("fatal"):
                state.status = "ABORTED"
                state.termination_reason = result.get("termination_reason", "UNRECOVERABLE_TOOL_FAILURE")

        state.current_step = None

        summary = {
            "workflow_status": state.status,
            "termination_reason": state.termination_reason,
            "planner_backend": state.planner_backend,
            "planner_model": state.planner_model,
            "input_samples": state.input_samples,
            "preparation_ready": state.preparation_ready,
            "preparation_failed": state.preparation_failed,
            "verification_passed": state.verification_passed,
            "verification_failed": state.verification_failed,
            "inference_attempted": state.inference_attempted,
            "inference_completed": state.inference_completed,
            "accepted": state.accepted,
            "review_required": state.review_required,
            "inference_aborted": state.inference_aborted,
            "plan_history": state.plan_history,
            "observations": state.observations,
            "results": state.inference_results,
        }
        yield {"event": "log", "data": {"text": json.dumps(summary, indent=2)}}
        yield {"event": "result", "data": summary}

    @staticmethod
    def _status_payload(state):
        return {
            "status": state.status,
            "input_samples": state.input_samples,
            "preparation_ready": state.preparation_ready,
            "verification_passed": state.verification_passed,
            "inference_attempted": state.inference_attempted,
            "accepted": state.accepted,
            "review_required": state.review_required,
        }

    async def _execute_preparation(self, state):
        prep = await asyncio.to_thread(
            self.preparation.prepare,
            state.inputs["dataset_csv"],
            state.inputs["inspection_xml"],
            state.inputs.get("image_root"),
        )
        state.prepared_samples = prep.data.get("samples", [])
        state.input_samples = prep.metrics.get("total_samples", len(state.prepared_samples))
        state.preparation_ready = prep.metrics.get("ready_samples", 0)
        state.preparation_failed = prep.metrics.get("failed_samples", 0)

        state.observations.append(
            f"Dataset preparation: {state.preparation_ready}/{state.input_samples} READY; "
            f"{state.preparation_failed} failed. Status={prep.status}."
        )
        state.errors.extend(prep.errors[:20])

        if not state.prepared_samples:
            return {
                "status": prep.status,
                "success": False,
                "fatal": True,
                "termination_reason": "NO_INPUT_SAMPLES",
            }

        # Partial preparation is recoverable; verification decides what can proceed.
        return {"status": prep.status, "success": prep.success}

    async def _execute_verification(self, state):
        verify = await asyncio.to_thread(self.verification.verify, state.prepared_samples)
        state.verified_samples = verify.data.get("verified_samples", [])
        state.verification_passed = verify.metrics.get("passed_samples", 0)
        state.verification_failed = verify.metrics.get("failed_samples", 0)

        state.observations.append(
            f"Dataset verification: {state.verification_passed}/{verify.metrics.get('total_samples', 0)} "
            f"PASSED; {state.verification_failed} failed. Status={verify.status}."
        )

        allowed, reason = self.policy.allow_inference(verify)
        state.observations.append(reason)

        if not allowed:
            return {
                "status": verify.status,
                "success": False,
                "fatal": True,
                "termination_reason": "NO_VERIFIED_SAMPLES",
            }

        return {"status": verify.status, "success": verify.success}

    async def _execute_inference(self, state):
        state.inference_attempted = len(state.verified_samples)

        for sample in state.verified_samples:
            result = await self.inference.infer_sample(sample)

            if result.success:
                payload = result.data
                payload["status"] = result.status
                payload["final_decision"] = final_decision(result, self.defect_threshold)
                if payload["final_decision"] == "REVIEW_REQUIRED":
                    payload["explainability_result"] = await self._escalate_review(sample, payload)
                state.inference_results.append(payload)
                state.inference_completed += 1
            else:
                decision = "REVIEW_REQUIRED" if result.recoverable else "ABORTED"
                payload = {
                    "sample_id": sample.get("sample_id"),
                    "status": result.status,
                    "errors": result.errors,
                    "details": result.data,
                    "final_decision": decision,
                }
                if decision == "REVIEW_REQUIRED":
                    payload["explainability_result"] = await self._escalate_review(sample, payload)
                state.inference_results.append(payload)
                if decision == "ABORTED":
                    state.inference_aborted += 1

        state.accepted = sum(
            1 for r in state.inference_results if r.get("final_decision") == "ACCEPTED"
        )
        state.review_required = sum(
            1 for r in state.inference_results if r.get("final_decision") == "REVIEW_REQUIRED"
        )
        state.inference_aborted = sum(
            1 for r in state.inference_results if r.get("final_decision") == "ABORTED"
        )

        state.observations.append(
            f"Inference attempted {state.inference_attempted} sample(s): "
            f"{state.accepted} accepted, {state.review_required} review-required, "
            f"{state.inference_aborted} aborted."
        )

        return {
            "status": "INFERENCE_EXECUTED",
            "success": bool(state.inference_results),
            "fatal": not bool(state.inference_results),
            "termination_reason": "NO_INFERENCE_RESULTS",
        }

    async def _escalate_review(self, sample, payload):
        """Hands a REVIEW_REQUIRED sample off to explainability_review_agent (ported as-is from
        pcb_agentic_inspector's Agent 2 - see that package's graph.py). Never raises or blocks the
        run: any failure here (kill switch off, pipeline exception) is recorded as a skipped
        result and the sample is still appended to inference_results regardless. Mirrors
        adc_inspection_agent/graph.py's _escalate_review_node for the chat pipeline's equivalent
        hand-off to case_review_agent. Deferred import so a routine orchestrator run never pays
        for this agent's (heavier) dependency chain unless a sample actually needs review."""

        if not settings.explainability_review_agent_enabled:
            return {"skipped": True, "reason": "explainability review agent disabled"}

        try:
            from app.agents.explainability_review_agent import execute_explainability_review

            classification_data = payload if "feature_classification" in payload else (payload.get("details") or {})
            feature_stage = classification_data.get("feature_classification") or {}
            defect_stage = classification_data.get("defect_classification") or {}

            input_data = {
                "board_id": sample.get("board", "UNKNOWN"),
                "component_ref": sample.get("component", "UNKNOWN"),
                "defect_image_path": sample.get("defect_image", ""),
                "golden_image_path": sample.get("golden_image"),
                "feature_type": feature_stage.get("prediction"),
                "preliminary_defect": defect_stage.get("prediction"),
                "confidence": defect_stage.get("confidence", 0.0),
                "aoi_measurements": sample.get("failed_inspections") or {},
            }
            result = await asyncio.to_thread(execute_explainability_review, input_data)
            return {
                "predicted_defect": result.get("predicted_defect"),
                "confidence": result.get("final_confidence", 0.0),
                "diagnosis": result.get("diagnosis", ""),
                "self_check_passed": result.get("self_check_passed", False),
                "contradiction_detected": result.get("contradiction_detected", False),
                "ipc_citations": result.get("ipc_citations", []),
                "visual_evidence": result.get("visual_evidence", ""),
            }
        except Exception as exc:
            logger.exception("Escalation to explainability_review_agent failed.")
            return {"skipped": True, "reason": str(exc)}

    def _finalize(self, state):
        if not state.inference_results:
            state.status = "ABORTED"
            state.termination_reason = "NO_INFERENCE_RESULTS"
            return

        if state.inference_aborted > 0:
            state.status = "ABORTED"
            state.termination_reason = "INFERENCE_ABORTED"
        elif state.review_required > 0:
            state.status = "REVIEW_REQUIRED"
            uncertain = [
                r.get("status") for r in state.inference_results
                if r.get("final_decision") == "REVIEW_REQUIRED"
            ]
            state.termination_reason = uncertain[0] if uncertain else "HUMAN_REVIEW_REQUIRED"
        else:
            state.status = "COMPLETED"
            state.termination_reason = "ALL_INFERENCE_RESULTS_ACCEPTED"

        state.observations.append(
            f"Workflow finalized as {state.status}. Reason={state.termination_reason}."
        )
