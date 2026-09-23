from typing import Any

from app.chat.agents.inspection_agent.planner import Planner
from app.chat.agents.inspection_agent.workflow_state import OrchestratorState


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
        "region_model_version": "",
        "defect_model": "",
        "defect_model_version": "",
        "defect_label": "",
        "defect_confidence": 0.0,
        "defect_scores": {},
        "final_decision": "",
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


def test_plans_verify_image_first() -> None:
    decision = Planner().plan(_blank_state())
    assert decision.decision == "verify_image"


def test_aborts_immediately_on_recorded_error() -> None:
    decision = Planner().plan(_blank_state(error="boom"))
    assert decision.decision == "abort"


def test_plans_golden_lookup_after_image_verified() -> None:
    decision = Planner().plan(_blank_state(image_readable=True))
    assert decision.decision == "lookup_golden_image"


def test_plans_validate_measurements_when_xml_attached() -> None:
    decision = Planner().plan(
        _blank_state(image_readable=True, golden_lookup_done=True, inspection_xml_bytes=b"<x/>")
    )
    assert decision.decision == "validate_measurements"


def test_skips_validate_measurements_when_no_xml_attached() -> None:
    decision = Planner().plan(_blank_state(image_readable=True, golden_lookup_done=True))
    assert decision.decision == "classify_region"


def test_plans_alignment_check_when_golden_image_found() -> None:
    decision = Planner().plan(
        _blank_state(image_readable=True, golden_lookup_done=True, golden_image_id="g1")
    )
    assert decision.decision == "align_and_check_quality"


def test_skips_alignment_check_when_no_golden_image_found() -> None:
    decision = Planner().plan(
        _blank_state(image_readable=True, golden_lookup_done=True, golden_image_id=None)
    )
    assert decision.decision == "classify_region"


def test_plans_classify_defect_when_region_confident() -> None:
    decision = Planner().plan(
        _blank_state(
            image_readable=True,
            golden_lookup_done=True,
            region="Body",
            region_uncertain=False,
        )
    )
    assert decision.decision == "classify_defect"


def test_skips_classify_defect_when_region_uncertain() -> None:
    decision = Planner().plan(
        _blank_state(
            image_readable=True,
            golden_lookup_done=True,
            region="Body",
            region_uncertain=True,
        )
    )
    assert decision.decision == "finalize"


def test_plans_finalize_after_classification_complete() -> None:
    decision = Planner().plan(
        _blank_state(
            image_readable=True,
            golden_lookup_done=True,
            region="Body",
            defect_label="MissingPart",
        )
    )
    assert decision.decision == "finalize"


def test_plans_persist_case_directly_when_review_required() -> None:
    """REVIEW_REQUIRED is terminal for this agent - no hand-off step before persisting."""

    decision = Planner().plan(
        _blank_state(
            image_readable=True,
            golden_lookup_done=True,
            region="Body",
            defect_label="MissingPart",
            final_decision="REVIEW_REQUIRED",
        )
    )
    assert decision.decision == "persist_case"


def test_plans_persist_case_when_accepted() -> None:
    decision = Planner().plan(
        _blank_state(
            image_readable=True,
            golden_lookup_done=True,
            region="Body",
            defect_label="MissingPart",
            final_decision="ACCEPTED",
        )
    )
    assert decision.decision == "persist_case"


def test_plans_done_once_persisted() -> None:
    decision = Planner().plan(
        _blank_state(
            image_readable=True,
            golden_lookup_done=True,
            region="Body",
            defect_label="MissingPart",
            final_decision="ACCEPTED",
            case_persisted=True,
        )
    )
    assert decision.decision == "done"
