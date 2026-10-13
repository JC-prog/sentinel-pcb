"""Agent 2's explanation served over MCP: the guarded tool, and the three things the ported server
does that must not reach a user - reading an arbitrary path, inventing telemetry, and failing to load
under a real FastMCP. The ported functions (which call OpenAI) are replaced by fakes."""

import sys
from pathlib import Path
from typing import Any

import pytest
from fastmcp import Client

from app.shared.config.settings import settings
from app.workflow.services import agent2_mcp_service as service
from app.workflow.services.agent2_mcp_service import Tools


@pytest.fixture
def uploads(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(settings, "chat_upload_dir", str(tmp_path))
    (tmp_path / "board.png").write_bytes(b"png")
    (tmp_path / "inspection.xml").write_text("<x/>")
    return tmp_path


class Calls:
    def __init__(self) -> None:
        self.measurement: list[dict[str, Any]] = []
        self.grounding_prompts: list[str] = []


def fake_tools(
    calls: Calls,
    *,
    visual: str = "The part is present but lifted at one end; the left fillet is not wetted.",
    measurement: dict[str, Any] | None = None,
    grounded: dict[str, Any] | None = None,
    similar: list[dict[str, Any]] | None = None,
) -> Tools:
    def precedents(**kwargs: Any) -> dict[str, Any]:
        return {"ipc_standard": "IPC-A-610 criteria", "similar_cases": similar or []}

    def visual_tool(**kwargs: Any) -> dict[str, Any]:
        return {"visual_description": visual, "bounding_boxes": [{"box": [1, 2, 3, 4]}]}

    def measurement_tool(**kwargs: Any) -> dict[str, Any]:
        calls.measurement.append(kwargs)
        return measurement if measurement is not None else {"source": "fallback_nominal"}

    def grounding_tool(**kwargs: Any) -> dict[str, Any]:
        calls.grounding_prompts.append(kwargs["reasoning_prompt"])
        return grounded or {
            "guardrail_status": "PASSED",
            "hallucination_flagged": False,
            "output": {
                "defect_classification": "TOMBSTONE",
                "confidence": 0.8,
                "visual_evidence_cited": "lifted at one end, left fillet not wetted",
                "ipc_precedent_cited": "IPC-A-610 tombstone criteria",
                "explanation_summary": "One end of the part is lifted off its pad.",
            },
        }

    return Tools(
        precedents=precedents, visual=visual_tool, measurement=measurement_tool, grounding=grounding_tool
    )


def test_the_ported_tools_load_under_a_real_fastmcp_without_leaving_a_stand_in_behind() -> None:
    before = sys.modules.get("fastmcp")

    tools = service.load_ported_tools()

    assert sys.modules.get("fastmcp") is before  # the stand-in is only for the import
    # the plain function works and the static criteria need no network
    result = tools.precedents(component_ref="U7", board_id="B1")
    assert "IPC-A-610" in result["ipc_standard"]


@pytest.mark.parametrize("name", ["../etc/passwd", "/etc/passwd", "a/b.png", "..", "", "sub\\x.png"])
def test_a_name_is_never_a_path(uploads: Path, name: str) -> None:
    assert service.resolve_upload(name) is None


def test_only_a_file_in_the_uploads_directory_resolves(uploads: Path) -> None:
    assert service.resolve_upload("board.png") == uploads.resolve() / "board.png"
    assert service.resolve_upload("missing.png") is None


def test_it_explains_an_image_and_maps_the_label(uploads: Path) -> None:
    calls = Calls()

    result = service.explain(
        "board.png",
        board_id="BOARD-1",
        component_ref="C12",
        issue_symptom="one end lifted",
        model_label="Tombstone",
        model_confidence=0.71,
        tools=fake_tools(calls),
    )

    assert result["status"] == "ok" and result["advisory"] is True
    assert result["label"] == "tombstone" and result["model_label"] == "tombstone"
    assert result["agrees_with_model"] is True
    assert result["summary"] == "One end of the part is lifted off its pad."
    assert result["guardrail"]["status"] == "PASSED"
    # the model's call is given as context only
    assert "judge the image independently" in calls.grounding_prompts[0]
    assert "Tombstone (confidence 71%)" in calls.grounding_prompts[0]


def test_a_disagreement_is_reported_not_hidden(uploads: Path) -> None:
    result = service.explain("board.png", model_label="MissingPart", tools=fake_tools(Calls()))

    assert result["label"] == "tombstone" and result["model_label"] == "missing part"
    assert result["agrees_with_model"] is False


def test_unresolved_has_no_label_and_no_agreement(uploads: Path) -> None:
    grounded = {
        "guardrail_status": "PASSED",
        "hallucination_flagged": False,
        "output": {
            "defect_classification": "UNRESOLVED",
            "confidence": 0.2,
            "visual_evidence_cited": "image too small to judge",
            "ipc_precedent_cited": "IPC-A-610",
            "explanation_summary": "The evidence is not enough to decide.",
        },
    }

    result = service.explain("board.png", model_label="Golden", tools=fake_tools(Calls(), grounded=grounded))

    assert result["label"] is None and result["agrees_with_model"] is None


def test_an_image_outside_the_uploads_is_unavailable_and_nothing_is_called(uploads: Path) -> None:
    calls = Calls()

    result = service.explain("../secret.png", tools=fake_tools(calls))

    assert result["status"] == "unavailable" and "uploads" in result["reason"]
    assert calls.grounding_prompts == []


def test_a_vision_step_that_could_not_look_is_unavailable(uploads: Path) -> None:
    tools = fake_tools(
        Calls(),
        visual="Optical feature extraction timed out or failed. Defaulting to physical sensor analysis.",
    )

    result = service.explain("board.png", tools=tools)

    assert result["status"] == "unavailable" and "vision" in result["reason"]


def test_invented_telemetry_is_never_used_and_without_an_xml_the_tool_is_not_called(
    uploads: Path,
) -> None:
    calls = Calls()

    result = service.explain("board.png", component_ref="C12", tools=fake_tools(calls))

    assert calls.measurement == []  # no XML given: nothing to read
    assert result["evidence"]["telemetry_recorded"] is False
    assert "NOT RECORDED" in calls.grounding_prompts[0]


def test_fallback_values_from_the_ported_tool_are_dropped_even_with_an_xml(uploads: Path) -> None:
    calls = Calls()

    result = service.explain(
        "board.png",
        component_ref="C12",
        xml_name="inspection.xml",
        tools=fake_tools(calls, measurement={"source": "fallback_missing", "laser_height_um": 0.4}),
    )

    assert len(calls.measurement) == 1
    assert calls.measurement[0]["image_path"] == ""  # no file-name guessing
    assert result["evidence"]["telemetry_recorded"] is False
    assert "0.4" not in calls.grounding_prompts[0]


def test_telemetry_read_from_the_xml_is_given_as_secondary_context(uploads: Path) -> None:
    calls = Calls()

    result = service.explain(
        "board.png",
        component_ref="C12",
        xml_name="inspection.xml",
        tools=fake_tools(calls, measurement={"source": "xml", "laser_height_um": 55.0}),
    )

    assert result["evidence"]["telemetry_recorded"] is True
    assert "laser_height_um=55.0" in calls.grounding_prompts[0]


def test_a_flagged_explanation_says_so(uploads: Path) -> None:
    grounded = {
        "guardrail_status": "FLAGGED",
        "hallucination_flagged": True,
        "error": "Visual evidence citation is too vague.",
        "output": {
            "defect_classification": "MISSING",
            "confidence": 0.5,
            "visual_evidence_cited": "looks normal",
            "ipc_precedent_cited": "IPC",
            "explanation_summary": "Probably a missing part.",
        },
    }

    result = service.explain("board.png", tools=fake_tools(Calls(), grounded=grounded))

    assert result["status"] == "flagged"
    assert result["guardrail"]["hallucination_flagged"] is True
    assert "too vague" in result["guardrail"]["note"]


def test_a_schema_failure_is_unavailable_not_a_made_up_answer(uploads: Path) -> None:
    grounded = {"guardrail_status": "FAILED", "hallucination_flagged": True, "error": "Schema violation"}

    result = service.explain("board.png", tools=fake_tools(Calls(), grounded=grounded))

    assert result["status"] == "unavailable"


def test_an_exception_in_a_step_is_unavailable_not_a_crash(uploads: Path) -> None:
    tools = fake_tools(Calls())

    def boom(**kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("openai down")

    result = service.explain(
        "board.png", tools=Tools(tools.precedents, tools.visual, tools.measurement, boom)
    )

    assert result["status"] == "unavailable" and "reasoning" in result["reason"]


async def test_the_served_tool_is_the_only_one_and_works_through_a_real_mcp_client(
    uploads: Path,
) -> None:
    server = service.build_server(fake_tools(Calls()))

    async with Client(server) as client:
        listed = [tool.name for tool in await client.list_tools()]
        result = await client.call_tool(
            "explain_inspection",
            {"image_name": "board.png", "model_label": "Tombstone", "component_ref": "C12"},
        )

    # the raw ported tools - including the one that reads any path - are not reachable
    assert listed == ["explain_inspection"]
    assert result.data["label"] == "tombstone" and result.data["agrees_with_model"] is True
