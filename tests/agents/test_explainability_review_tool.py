import json
from typing import Any

import pytest
from PIL import Image

from app.agents.explainability_review_agent import ExplainabilityReviewTool
from app.agents.explainability_review_agent.graph import PCBInspectionState


def _final_state(**overrides: Any) -> PCBInspectionState:
    state: PCBInspectionState = {
        "image": Image.new("RGB", (4, 4)),
        "image_name": "board.png",
        "board_id": "B1",
        "component_ref": "R131",
        "issue_symptom": "AOI flagged anomaly",
        "historical_context": "",
        "reference_standards": "",
        "visual_bounding_boxes": [],
        "visual_description": "",
        "measurements": {},
        "defect_location": {"landmark": "left pad", "bounding_box": [0, 0, 10, 10]},
        "final_defect_category": "tombstone",
        "final_diagnosis_text": "Component lifted on one side.",
        "grounding_confidence": 0.87,
        "self_check_passed": True,
        "errors": [],
    }
    state.update(overrides)  # type: ignore[typeddict-item]
    return state


class _FakePipeline:
    def __init__(self, final_state: PCBInspectionState) -> None:
        self._final_state = final_state

    def invoke(self, _initial_state: PCBInspectionState) -> PCBInspectionState:
        return self._final_state


def test_tool_metadata_shape() -> None:
    tool = ExplainabilityReviewTool()
    assert tool.name == "explainability_review"
    assert tool.parameters["required"] == ["image_id", "board_id", "component_ref"]
    assert set(tool.parameters["properties"]) == {
        "image_id",
        "board_id",
        "component_ref",
        "issue_symptom",
    }


async def test_run_returns_diagnosis_from_pipeline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.agents.explainability_review_agent.tool.get_pipeline",
        lambda api_key: _FakePipeline(_final_state()),
    )

    result = await ExplainabilityReviewTool().run(
        image=Image.new("RGB", (4, 4)),
        image_name="board.png",
        board_id="B1",
        component_ref="R131",
        issue_symptom="looks lifted",
        openai_api_key="sk-test",
    )

    payload = json.loads(result)
    assert payload == {
        "defect_category": "tombstone",
        "defect_location": {"landmark": "left pad", "bounding_box": [0, 0, 10, 10]},
        "explanation": "Component lifted on one side.",
        "confidence_score": 0.87,
        "self_check_passed": True,
        "errors": [],
    }


async def test_run_normalizes_unrecognized_category_to_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.agents.explainability_review_agent.tool.get_pipeline",
        lambda api_key: _FakePipeline(_final_state(final_defect_category="not_a_real_category")),
    )

    result = await ExplainabilityReviewTool().run(
        image=Image.new("RGB", (4, 4)),
        board_id="B1",
        component_ref="R131",
        openai_api_key="sk-test",
    )

    assert json.loads(result)["defect_category"] == "unknown"


async def test_run_defaults_issue_symptom_when_omitted(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    class _CapturingPipeline:
        def invoke(self, initial_state: PCBInspectionState) -> PCBInspectionState:
            captured["issue_symptom"] = initial_state["issue_symptom"]
            return _final_state()

    monkeypatch.setattr(
        "app.agents.explainability_review_agent.tool.get_pipeline",
        lambda api_key: _CapturingPipeline(),
    )

    await ExplainabilityReviewTool().run(
        image=Image.new("RGB", (4, 4)),
        board_id="B1",
        component_ref="R131",
        openai_api_key="sk-test",
    )

    assert captured["issue_symptom"] == "AOI flagged anomaly"
