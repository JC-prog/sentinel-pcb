from typing import Any

from PIL import Image

from app.agents.case_review_agent.graph import (
    PCBInspectionState,
    _heuristic_self_check,
    build_graph,
)


def _state_with_measurements(measurements: dict[str, Any]) -> PCBInspectionState:
    state: PCBInspectionState = {
        "image": Image.new("RGB", (4, 4)),
        "image_name": "board.png",
        "board_id": "B1",
        "component_ref": "R131",
        "issue_symptom": "looks off",
        "inspection_xml_bytes": None,
        "package": None,
        "feature": None,
        "historical_context": "",
        "reference_standards": "",
        "similar_cases": [],
        "visual_bounding_boxes": [],
        "visual_description": "",
        "measurements": measurements,
        "defect_location": None,
        "final_defect_category": "unknown",
        "final_diagnosis_text": "",
        "grounding_confidence": 0.0,
        "self_check_passed": False,
        "errors": [],
    }
    return state


def test_flags_missing_part_on_near_zero_laser_height() -> None:
    result = _heuristic_self_check(_state_with_measurements({"laser_profile_height_um": 1.2}))
    assert result["defect_category"] == "missing part"
    assert result["self_check_passed"] is True


def test_flags_shifted_on_high_overhang() -> None:
    result = _heuristic_self_check(_state_with_measurements({"side_overhang_percent": 72.0}))
    assert result["defect_category"] == "shifted"
    assert result["self_check_passed"] is True


def test_laser_height_takes_priority_over_overhang() -> None:
    result = _heuristic_self_check(
        _state_with_measurements({"laser_profile_height_um": 1.2, "side_overhang_percent": 72.0})
    )
    assert result["defect_category"] == "missing part"


def test_falls_back_to_unknown_when_nothing_indicates_a_defect() -> None:
    result = _heuristic_self_check(
        _state_with_measurements({"laser_profile_height_um": 42.5, "side_overhang_percent": 32.0})
    )
    assert result["defect_category"] == "unknown"
    assert result["self_check_passed"] is False


def test_falls_back_to_unknown_when_measurements_are_empty() -> None:
    result = _heuristic_self_check(_state_with_measurements({}))
    assert result["defect_category"] == "unknown"


class _RaisingReasoningLlm:
    def query(self, prompt: str, require_json: bool = True) -> str:
        raise RuntimeError("OpenAI is unreachable")


class _FakeVisionInspector:
    def query(self, image: Image.Image, prompt: str) -> str:
        return "The pads look empty."


class _FakeBoundingBoxDetector:
    def detect(self, image: Image.Image) -> dict[str, Any]:
        return {"defects": []}


class _FakeRegistry:
    def __init__(self) -> None:
        self.reasoning_llm = _RaisingReasoningLlm()
        self.llava = _FakeVisionInspector()
        self.pcb_detector = _FakeBoundingBoxDetector()


class _FakeMcpClient:
    def search_historical(self, image: Image.Image, component_ref: str | None = None) -> list[Any]:
        return []

    def get_standards(self, component_ref: str) -> dict[str, str]:
        return {"standard_id": "IPC-A-610 General Workmanship Criteria"}

    def get_measurements(self, board_id: str, component_ref: str, **kwargs: Any) -> dict[str, Any]:
        return {"laser_profile_height_um": 1.0, "side_overhang_percent": 10.0, "ict_status": "FAIL"}


def test_pipeline_falls_back_to_heuristic_when_reasoning_llm_raises() -> None:
    graph = build_graph(_FakeRegistry(), _FakeMcpClient())  # type: ignore[arg-type]
    initial_state = _state_with_measurements({})
    final_state = graph.invoke(initial_state)

    assert final_state["final_defect_category"] == "missing part"
    assert final_state["self_check_passed"] is True
    assert any("ReasoningGrounding" in e for e in final_state["errors"])
