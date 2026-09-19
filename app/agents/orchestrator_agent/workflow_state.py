"""Ported verbatim from orchestrator-agent/adc_agentic_project's state/workflow_state.py - the
batch-run state threaded through orchestrator.py's plan/policy/execute loop. Distinct from
app/agents/adc_inspection_agent/workflow_state.py's OrchestratorState (a TypedDict for one image
per run); this one keeps the original's per-run counters (input_samples, preparation_ready, etc.)
since a Work-tab run really does process many dataset rows at once.
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class WorkflowState:
    status: str = "CREATED"
    inputs: dict[str, Any] = field(default_factory=dict)

    prepared_samples: list[dict] = field(default_factory=list)
    verified_samples: list[dict] = field(default_factory=list)
    inference_results: list[dict] = field(default_factory=list)

    observations: list[str] = field(default_factory=list)
    plan_history: list[dict] = field(default_factory=list)
    tool_history: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    current_step: str | None = None
    replan_count: int = 0
    termination_reason: str | None = None

    # Run summary counters. These make partial workflows unambiguous.
    input_samples: int = 0
    preparation_ready: int = 0
    preparation_failed: int = 0
    verification_passed: int = 0
    verification_failed: int = 0
    inference_attempted: int = 0
    inference_completed: int = 0
    accepted: int = 0
    review_required: int = 0
    inference_aborted: int = 0

    # Planner metadata
    planner_backend: str = "deterministic"
    llm_enabled: bool = False
    planner_model: str | None = None

    def summary_for_planner(self) -> dict[str, Any]:
        """Return a compact state snapshot suitable for the LLM planner."""
        return {
            "workflow_status": self.status,
            "current_step": self.current_step,
            "input_samples": self.input_samples,
            "preparation_ready": self.preparation_ready,
            "preparation_failed": self.preparation_failed,
            "verification_passed": self.verification_passed,
            "verification_failed": self.verification_failed,
            "inference_attempted": self.inference_attempted,
            "inference_completed": self.inference_completed,
            "accepted": self.accepted,
            "review_required": self.review_required,
            "inference_aborted": self.inference_aborted,
            "has_prepared_samples": bool(self.prepared_samples),
            "has_verified_samples": bool(self.verified_samples),
            "has_inference_results": bool(self.inference_results),
            "recent_observations": self.observations[-5:],
            "recent_errors": self.errors[-5:],
            "replan_count": self.replan_count,
        }
