"""The per-case run state threaded through graph.py's cyclic StateGraph. Adapted from
orchestrator-agent/adc_agentic_project's state/workflow_state.py's WorkflowState dataclass - this
is a TypedDict instead (matches every other agent's LangGraph state convention here), and its
batch-only fields (input_samples, preparation_ready, verification_passed, etc. - counters that
only make sense across many dataset rows) are dropped, since exactly one image (and optionally one
inspection XML) flows through a single run of this graph.
"""

from typing import Any, TypedDict


class OrchestratorState(TypedDict):
    # Request/context - supplied once, up front, never mutated by a node.
    image_bytes: bytes
    image_name: str
    inspection_xml_bytes: bytes | None
    inspection_xml_id: str | None
    username: str
    user_id: str
    conversation_id: str
    board_id: str
    component_ref: str
    package: str | None
    feature: str | None
    issue_symptom: str | None

    # Step-completion facts - what planner.py and policy_engine.py read to decide what's next and
    # what's allowed, replacing the fixed conditional-edge routing the old case_agent/graph.py used.
    image_readable: bool | None
    golden_lookup_done: bool
    golden_image_id: str | None
    measurement_validation: dict[str, Any] | None
    alignment_done: bool
    alignment_result: dict[str, Any] | None
    quality_issues: list[str]
    region: str
    region_confidence: float
    region_scores: dict[str, float]
    region_uncertain: bool
    region_model_version: str
    defect_model: str
    defect_model_version: str
    defect_label: str
    defect_confidence: float
    defect_scores: dict[str, float]
    final_decision: str
    case_persisted: bool
    case_id: str | None
    case_number: str | None

    # Orchestrator bookkeeping - the single-case-run equivalent of WorkflowState's plan_history/
    # tool_history/replan_count/termination_reason/status.
    status: str  # "RUNNING" -> "COMPLETED" | "REVIEW_REQUIRED" | "ABORTED"
    current_step: str | None
    plan_history: list[dict[str, Any]]
    tool_history: list[dict[str, Any]]
    replan_count: int
    termination_reason: str | None
    observations: list[str]
    error: str | None
