"""Unit tests for explainability_review_agent's pure, dependency-free graph nodes (ported as-is
from pcb_agentic_inspector's Agent 2 - see app/workflow/agents/explainability_review_agent/graph.py).
Only the nodes that need no Ollama/OpenAI call are covered here; inspect_visuals_node and
grounding_self_check_node need network/LLM mocking and are exercised at the pipeline level
elsewhere."""

from typing import Any

from app.workflow.agents.explainability_review_agent.graph import (
    ReviewState,
    _heuristic_self_check,
    extract_telemetry_node,
    normalize_label,
    retrieve_precedents_node,
)


def _state(
    aoi_measurements: dict[str, Any] | None = None,
    telemetry_data: dict[str, Any] | None = None,
    preliminary_defect: str = "",
    feature_type: str = "Body",
) -> ReviewState:
    return {
        "board_id": "B1",
        "component_ref": "C978",
        "defect_image_path": "defect.jpg",
        "golden_image_path": None,
        "feature_type": feature_type,
        "preliminary_defect": preliminary_defect,
        "baseline_confidence": 0.5,
        "aoi_measurements": aoi_measurements or {},
        "retrieved_precedents": [],
        "telemetry_data": telemetry_data or {},
        "visual_evidence": "",
        "predicted_defect": "",
        "final_confidence": 0.0,
        "diagnosis": "",
        "contradiction_detected": False,
        "self_check_passed": False,
        "ipc_citations": [],
        "errors": [],
    }


def test_retrieve_precedents_returns_hardcoded_ipc_precedents() -> None:
    result = retrieve_precedents_node(_state(preliminary_defect="MissingPart"))
    precedents = result["retrieved_precedents"]
    assert len(precedents) == 2
    assert all("ipc_clause" in p and "rule" in p for p in precedents)


def test_normalize_label_converts_pascal_case() -> None:
    assert normalize_label("MissingPart") == "missing part"


def test_normalize_label_handles_snake_case_and_blank() -> None:
    assert normalize_label("solder_insufficient") == "solder insufficient"
    assert normalize_label("") == "no defect"


def test_extract_telemetry_flattens_nested_inspection_blocks() -> None:
    telemetry = extract_telemetry_node(
        _state(aoi_measurements={"BlobDetection": {"side_overhang_percent": 62.0}})
    )["telemetry_data"]
    assert telemetry["side_overhang_percent"] == 62.0
    assert telemetry["board_id"] == "B1"
    assert telemetry["component_ref"] == "C978"


def test_extract_telemetry_resolves_key_aliases() -> None:
    telemetry = extract_telemetry_node(
        _state(aoi_measurements={"height_um": 3.0, "side_overhang": 10.0, "coplanarity": 5.0})
    )["telemetry_data"]
    assert telemetry["laser_profile_height_um"] == 3.0
    assert telemetry["side_overhang_percent"] == 10.0
    assert telemetry["coplanarity_um"] == 5.0


def test_extract_telemetry_defaults_when_no_measurements_given() -> None:
    telemetry = extract_telemetry_node(_state())["telemetry_data"]
    assert telemetry["laser_profile_height_um"] == 45.0
    assert telemetry["side_overhang_percent"] == 0.0
    assert telemetry["ict_status"] == "PASS"


def test_heuristic_flags_missing_part_on_ict_fail() -> None:
    result = _heuristic_self_check(
        _state(
            telemetry_data={"ict_status": "FAIL", "laser_profile_height_um": 1.0},
            preliminary_defect="MissingPart",
        )
    )
    assert result["predicted_defect"] == "missing part"
    assert result["contradiction_detected"] is False


def test_heuristic_flags_contradiction_when_preliminary_defect_disagrees() -> None:
    result = _heuristic_self_check(
        _state(
            telemetry_data={"ict_status": "FAIL", "laser_profile_height_um": 1.0},
            preliminary_defect="Shifted",
        )
    )
    assert result["predicted_defect"] == "missing part"
    assert result["contradiction_detected"] is True


def test_heuristic_flags_shifted_on_high_overhang() -> None:
    result = _heuristic_self_check(
        _state(
            telemetry_data={
                "ict_status": "PASS",
                "laser_profile_height_um": 45.0,
                "side_overhang_percent": 72.0,
            }
        )
    )
    assert result["predicted_defect"] == "shifted"


def test_heuristic_falls_back_to_nominal_within_tolerances() -> None:
    result = _heuristic_self_check(
        _state(
            telemetry_data={
                "ict_status": "PASS",
                "laser_profile_height_um": 45.0,
                "side_overhang_percent": 10.0,
            },
            preliminary_defect="NoDefect",
        )
    )
    assert result["predicted_defect"] == "no defect"
    assert result["contradiction_detected"] is False
