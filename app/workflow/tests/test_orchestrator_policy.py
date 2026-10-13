from state.workflow_state import WorkflowState
from policy.policy_engine import PolicyEngine
from planner.llm_planner import Planner


def test_deterministic_planner_starts_with_preparation():
    state = WorkflowState(status="RUNNING")
    plan = Planner(use_llm=False).plan(state)
    assert plan.decision == "dataset_preparation"


def test_policy_rejects_inference_without_verified_samples():
    state = WorkflowState(status="RUNNING", prepared_samples=[{"sample_id": "S1"}])
    allowed, _ = PolicyEngine().validate_action("multimodal_inference", state)
    assert allowed is False


def test_policy_rejects_early_abort_when_recoverable_step_exists():
    state = WorkflowState(status="RUNNING")
    allowed, _ = PolicyEngine().validate_action("abort", state)
    assert allowed is False
