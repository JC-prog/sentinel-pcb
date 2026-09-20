from typing import Any

from app.chat.agents.adc_inspection_agent.policy_engine import PolicyEngine
from app.chat.agents.adc_inspection_agent.workflow_state import OrchestratorState


def _blank_state(**overrides: Any) -> OrchestratorState:
    state: OrchestratorState = {
        "image_bytes": b"",
        "image_name": "image",
        "inspection_xml_bytes": None,
        "inspection_xml_id": None,
        "username": "u",
        "user_id": "u1",
        "conversation_id": "c1",
        "board_id": "BOARD-1",
        "component_ref": "U7",
        "package": None,
        "feature": None,
        "issue_symptom": None,
        "image_readable": None,
        "golden_lookup_done": False,
        "golden_image_id": None,
        "measurement_validation": None,
        "alignment_done": False,
        "alignment_result": None,
        "quality_issues": [],
        "region": "",
        "region_confidence": 0.0,
        "region_scores": {},
        "region_uncertain": False,
        "defect_model": "",
        "defect_label": "",
        "defect_confidence": 0.0,
        "defect_scores": {},
        "final_decision": "",
        "escalation_done": False,
        "explainability_result": None,
        "case_persisted": False,
        "case_id": None,
        "case_number": None,
        "status": "RUNNING",
        "current_step": None,
        "plan_history": [],
        "tool_history": [],
        "replan_count": 0,
        "termination_reason": None,
        "observations": [],
        "error": None,
    }
    state.update(overrides)  # type: ignore[typeddict-item]
    return state


def test_rejects_rerunning_a_completed_step() -> None:
    allowed, _ = PolicyEngine().validate_action("verify_image", _blank_state(image_readable=True))
    assert allowed is False


def test_rejects_golden_lookup_before_image_verified() -> None:
    allowed, _ = PolicyEngine().validate_action("lookup_golden_image", _blank_state())
    assert allowed is False


def test_rejects_finalize_before_classification() -> None:
    allowed, _ = PolicyEngine().validate_action("finalize", _blank_state(image_readable=True))
    assert allowed is False


def test_allows_finalize_when_region_uncertain_even_without_defect() -> None:
    allowed, _ = PolicyEngine().validate_action(
        "finalize", _blank_state(region="Body", region_uncertain=True)
    )
    assert allowed is True


def test_rejects_escalate_review_unless_review_required() -> None:
    allowed, _ = PolicyEngine().validate_action(
        "escalate_review", _blank_state(final_decision="ACCEPTED")
    )
    assert allowed is False


def test_allows_escalate_review_when_review_required() -> None:
    allowed, _ = PolicyEngine().validate_action(
        "escalate_review", _blank_state(final_decision="REVIEW_REQUIRED")
    )
    assert allowed is True


def test_rejects_persist_case_for_review_required_before_escalation() -> None:
    allowed, _ = PolicyEngine().validate_action(
        "persist_case", _blank_state(final_decision="REVIEW_REQUIRED", escalation_done=False)
    )
    assert allowed is False


def test_allows_persist_case_for_review_required_after_escalation() -> None:
    allowed, _ = PolicyEngine().validate_action(
        "persist_case", _blank_state(final_decision="REVIEW_REQUIRED", escalation_done=True)
    )
    assert allowed is True


def test_rejects_abort_without_a_recorded_error() -> None:
    allowed, _ = PolicyEngine().validate_action("abort", _blank_state())
    assert allowed is False


def test_allows_abort_with_a_recorded_error() -> None:
    allowed, _ = PolicyEngine().validate_action("abort", _blank_state(error="boom"))
    assert allowed is True


def test_rejects_unsupported_action() -> None:
    allowed, reason = PolicyEngine().validate_action("not_a_real_action", _blank_state())
    assert allowed is False
    assert "Unsupported action" in reason
