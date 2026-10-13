"""
Agent 1 Orchestrator Agent.
Coordinates dataset preparation, verification, fast ONNX inference,
policy validation, vector database indexing, and A2A escalation to Agent 2.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Optional

from planner.llm_planner import Planner
from policy.policy_engine import PolicyEngine
from state.workflow_state import WorkflowState
from services.dataset_preparation import DatasetPreparationService
from services.dataset_verification import DatasetVerificationService
from services.model_lifecycle import ModelLifecycleService
from services.multimodal_inference import TwoStageInferenceService
from services.result_comparison import final_decision

# Import Agent 2 A2A Dispatcher
try:
    from agents.a2a_dispatcher import Agent2ReviewClient
except ImportError:
    try:
        from src.agent1_orchestrator.agents.a2a_dispatcher import Agent2ReviewClient
    except ImportError:
        Agent2ReviewClient = None

# Import Qdrant Vector DB Indexer
try:
    from src.data.qdrant_indexer import populate_qdrant_db
except ImportError:
    try:
        from data.qdrant_indexer import populate_qdrant_db
    except ImportError:
        populate_qdrant_db = None

logger = logging.getLogger("OrchestratorAgent")


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
        project_root: str,
        feature_threshold: float = 0.70,
        defect_threshold: float = 0.70,
        use_llm: bool = False,
        planner_model: Optional[str] = None,
        allow_llm_fallback: bool = True,
        max_replans: int = 8,
        agent2_url: str = "http://127.0.0.1:8001",
        enable_a2a: bool = True,
        populate_vector_db: bool = True,
    ):
        self.planner = Planner(
            use_llm=use_llm,
            model=planner_model,
            allow_fallback=allow_llm_fallback,
        )
        self.policy = PolicyEngine()
        self.preparation = DatasetPreparationService()
        self.verification = DatasetVerificationService()
        self.lifecycle = ModelLifecycleService(project_root)
        self.inference = TwoStageInferenceService(self.lifecycle, feature_threshold)
        self.defect_threshold = defect_threshold
        self.max_replans = max_replans
        self.use_llm = use_llm

        # A2A Review Client Configuration
        self.enable_a2a = enable_a2a
        self.a2a_client = Agent2ReviewClient(agent2_url=agent2_url) if (enable_a2a and Agent2ReviewClient) else None

        # Vector Database Population
        self.populate_vector_db = populate_vector_db

    def run(
        self,
        dataset_csv: str,
        inspection_xml: str,
        image_root: Optional[str] = None,
        on_step: Optional[Callable[[WorkflowState], None]] = None,
    ) -> WorkflowState:
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

            plan = self.planner.plan(state)
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
                state.status = "ABORTED"
                state.termination_reason = "PLANNER_LOOP_DETECTED"
                break

            allowed, policy_reason = self.policy.validate_action(plan.decision, state)
            plan_record["policy"] = {"allowed": allowed, "reason": policy_reason}

            if not allowed:
                state.plan_history.append(plan_record)
                state.observations.append(f"Policy rejected {plan.decision}: {policy_reason}")
                # Bounded re-plan: planner sees the new observation on next iteration.
                continue

            state.current_step = plan.decision

            try:
                if plan.decision == "dataset_preparation":
                    result = self._execute_preparation(state)
                elif plan.decision == "dataset_verification":
                    result = self._execute_verification(state)
                elif plan.decision == "multimodal_inference":
                    result = self._execute_inference(state)
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

            # If an unrecoverable tool execution produced no path forward, abort.
            if result.get("fatal"):
                state.status = "ABORTED"
                state.termination_reason = result.get("termination_reason", "UNRECOVERABLE_TOOL_FAILURE")

            if on_step:
                on_step(state)

        state.current_step = None
        return state

    def _execute_preparation(self, state: WorkflowState) -> Dict[str, Any]:
        prep = self.preparation.prepare(
            state.inputs["dataset_csv"],
            state.inputs["inspection_xml"],
            state.inputs.get("image_root"),
        )
        state.prepared_samples = prep.data.get("samples", [])
        state.input_samples = prep.metrics.get("total_samples", len(state.prepared_samples))
        state.preparation_ready = prep.metrics.get("ready_samples", 0)
        state.preparation_failed = prep.metrics.get("failed_samples", 0)

        # Automatically populate the local Qdrant Vector Database with the prepared data
        if state.prepared_samples and self.populate_vector_db and populate_qdrant_db:
            try:
                indexed_count = populate_qdrant_db(state.prepared_samples, db_path="qdrant_db")
                state.observations.append(
                    f"Vector Database: Indexed {indexed_count} historical records into qdrant_db."
                )
            except Exception as ex:
                state.observations.append(f"Vector Database indexing notice: {ex}")

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

        return {"status": prep.status, "success": prep.success}

    def _execute_verification(self, state: WorkflowState) -> Dict[str, Any]:
        verify = self.verification.verify(state.prepared_samples)
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

    def _execute_inference(self, state: WorkflowState) -> Dict[str, Any]:
        state.inference_attempted = len(state.verified_samples)

        for sample in state.verified_samples:
            result = self.inference.infer_sample(sample)

            if result.success:
                payload = result.data
                payload["status"] = result.status
                payload["final_decision"] = final_decision(result, self.defect_threshold)
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
                if decision == "ABORTED":
                    state.inference_aborted += 1

            # Check if escalation to Agent 2 is needed
            if payload.get("final_decision") == "REVIEW_REQUIRED" and self.enable_a2a:
                self._execute_review_escalation(state, sample, payload)

            state.inference_results.append(payload)

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
            f"Inference completed {state.inference_attempted} sample(s): "
            f"{state.accepted} accepted, {state.review_required} review-required, "
            f"{state.inference_aborted} aborted."
        )

        return {
            "status": "INFERENCE_EXECUTED",
            "success": bool(state.inference_results),
            "fatal": not bool(state.inference_results),
            "termination_reason": "NO_INFERENCE_RESULTS",
        }

    def _execute_review_escalation(
        self, state: WorkflowState, sample: Dict[str, Any], inference_result: Dict[str, Any]
    ) -> None:
        """
        Delegates an ambiguous/low-confidence sample to Agent 2 over A2A protocol.
        """
        if not self.a2a_client:
            state.observations.append("A2A Client is disabled or unconfigured; skipping escalation.")
            return

        sample_id = sample.get("sample_id", "UNKNOWN")

        # Discover Agent 2 availability
        if not self.a2a_client.discover():
            inference_result["resolved_by"] = "AGENT2_UNAVAILABLE"
            state.observations.append(
                f"Agent 2 server offline; sample {sample_id} retained as REVIEW_REQUIRED."
            )
            return

        state.observations.append(f"Escalating sample {sample_id} to Agent 2 via A2A protocol...")

        # Transmit A2A Review Task
        review_response = self.a2a_client.delegate_review(sample, inference_result)
        artifact = review_response.get("artifact") or review_response.get("result", {})

        inference_result["agent2_review"] = artifact

        if artifact.get("self_check_passed"):
            # Ambiguity successfully grounded with physical telemetry and VLM
            inference_result["final_defect"] = artifact.get("predicted_defect")
            inference_result["diagnosis"] = artifact.get("diagnosis")
            inference_result["resolved_by"] = "Agent2_Multimodal"
            inference_result["final_decision"] = "ACCEPTED"

            state.observations.append(
                f"Agent 2 RESOLVED {sample_id} as '{inference_result['final_defect']}'. Diagnosis: {inference_result['diagnosis']}"
            )
            if hasattr(state, "mark_accepted"):
                state.mark_accepted(sample_id)
        else:
            # Physical or visual contradiction persists; flag for Human-in-the-Loop review
            inference_result["diagnosis"] = artifact.get("diagnosis", "Multimodal self-check contradiction.")
            inference_result["resolved_by"] = "HUMAN_QA_REQUIRED"
            inference_result["final_decision"] = "REVIEW_REQUIRED"

            state.observations.append(
                f"Agent 2 FLAGGED {sample_id} for Human Review. Reason: {inference_result['diagnosis']}"
            )
            if hasattr(state, "mark_for_human_review"):
                state.mark_for_human_review(sample_id)

    def _finalize(self, state: WorkflowState) -> None:
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
