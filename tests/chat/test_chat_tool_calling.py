"""Tool calling inside a chat turn, through POST /api/chat/stream: which tools the model is given,
the tool_call / tool_result frames, the round limit. The chat model is scripted (tests/chat/_llm.py);
the tools are the real ones unless a test swaps in a stand-in."""

import json
from typing import Annotated, Any

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import BaseMessage, ToolMessage
from langchain_core.tools import tool

from app.chat.agents import ToolRegistry
from app.chat.agents.toolkit import ChatTool, Runtime, returns_json
from app.chat.services import streaming
from app.shared.config.settings import settings
from tests.chat._llm import ScriptedModel, ai, always, install, says


def _parse_sse(body: str) -> list[tuple[str, dict[str, object]]]:
    frames = [f for f in body.split("\n\n") if f.strip()]
    parsed = []
    for frame in frames:
        event = "message"
        data = "{}"
        for line in frame.splitlines():
            if line.startswith("event:"):
                event = line.removeprefix("event:").strip()
            elif line.startswith("data:"):
                data = line.removeprefix("data:").strip()
        parsed.append((event, json.loads(data)))
    return parsed


def _stream(client: TestClient, message: str, image_ids: list[str] | None = None) -> str:
    with client.stream(
        "POST",
        "/api/chat/stream",
        json={"conversation_id": "c1", "message": message, "image_ids": image_ids or []},
    ) as response:
        return "".join(response.iter_text())


def _tool_results(model: ScriptedModel) -> list[ToolMessage]:
    return [m for m in model.seen[-1] if isinstance(m, ToolMessage)]


def test_the_model_is_offered_every_tool_but_inspect_image_by_default(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """authenticated_client is the first user registered in a fresh DB, which
    app/shared/auth/service.py auto-promotes to ADMIN regardless of the requested role - see
    tests/chat/test_role_gated_tools.py for the full role -> tool-visibility matrix."""

    model = says("ack")
    install(monkeypatch, model)

    _stream(authenticated_client, "hi")

    assert set(model.offered) == {
        "relabel_case",
        "confirm_relabel",
        "review_case",
        "confirm_review",
        "report_model_drift",
        "get_drift_summary",
        "draft_retraining_plan",
        "monitoring_status",
        "create_case",
        "get_sample",
        "list_review_cases",
    }


def test_inspect_image_is_offered_when_an_image_is_attached(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    model = says("ack")
    install(monkeypatch, model)

    _stream(authenticated_client, "check this board", image_ids=["some-upload-id"])

    assert "inspect_image" in model.offered


def test_tools_disabled_gives_the_model_no_tools_at_all(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "chat_tool_calling_enabled", False)
    model = says("ack")
    install(monkeypatch, model)

    _stream(authenticated_client, "hi")

    assert model.offered == []


def test_a_tool_call_round_trip_streams_progress_and_the_final_answer(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    model = ScriptedModel(
        replies=[
            ai("", ("get_drift_summary", {"model": "pcb_body_defect"})),
            ai("No drift detected."),
        ]
    )
    install(monkeypatch, model)

    frames = _parse_sse(_stream(authenticated_client, "has the model drifted?"))

    assert [event for event, _ in frames] == ["tool_call", "delta", "done"]
    assert frames[0][1] == {"name": "get_drift_summary", "label": "Drift summary"}
    assert frames[1][1] == {"text": "No drift detected."}

    # the model was handed the tool's real result before it answered
    [result] = _tool_results(model)
    assert result.name == "get_drift_summary"
    assert json.loads(str(result.content))["model"] == "pcb_body_defect"


def test_a_failed_tool_is_explained_to_the_model_not_shown_as_a_card(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """relabel_case with no case in the conversation is a refusal, and a refusal has no card."""

    model = ScriptedModel(
        replies=[
            ai("", ("relabel_case", {"correct_label": "Golden", "reason": "false positive"})),
            ai("There's no case yet - inspect an image first."),
        ]
    )
    install(monkeypatch, model)

    frames = _parse_sse(_stream(authenticated_client, "it should be golden"))

    assert [event for event, _ in frames] == ["tool_call", "delta", "done"]  # no tool_result
    [result] = _tool_results(model)
    assert "no case in this conversation" in json.loads(str(result.content))["error"]


def _card_registry() -> ToolRegistry:
    """A registry whose one tool has a card - so the frame the UI shows it with can be tested
    without needing a saved Case. Named after a real role-table entry so access.py permits it."""

    @tool("get_drift_summary")
    @returns_json
    async def stand_in(
        runtime: Runtime, model: Annotated[str, "the model"] = ""
    ) -> dict[str, Any]:
        """A stand-in tool."""
        if model == "bad":
            return {"error": "no such model"}
        return {"model": model, "score": 0.5}

    return ToolRegistry([ChatTool(stand_in, label="Drift summary", shows_card=True)])


def test_a_card_tool_result_is_sent_to_the_ui_as_a_tool_result_frame(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(streaming, "tool_registry", _card_registry())
    install(
        monkeypatch,
        ScriptedModel(replies=[ai("", ("get_drift_summary", {"model": "m1"})), ai("Here you go.")]),
    )

    frames = _parse_sse(_stream(authenticated_client, "show me"))

    assert [event for event, _ in frames] == ["tool_call", "tool_result", "delta", "done"]
    assert frames[1][1] == {"name": "get_drift_summary", "result": {"model": "m1", "score": 0.5}}


def test_an_error_result_from_a_card_tool_is_not_a_card(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(streaming, "tool_registry", _card_registry())
    install(
        monkeypatch,
        ScriptedModel(replies=[ai("", ("get_drift_summary", {"model": "bad"})), ai("Sorry.")]),
    )

    frames = _parse_sse(_stream(authenticated_client, "show me"))

    assert "tool_result" not in [event for event, _ in frames]


def test_a_model_that_never_stops_calling_tools_is_cut_off_with_an_apology(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forever(messages: list[BaseMessage]) -> Any:
        return ai("", ("get_drift_summary", {"model": "pcb_body_defect"}))

    model = always(forever)
    install(monkeypatch, model)

    body = _stream(authenticated_client, "keep calling tools forever")

    deltas = [str(data["text"]) for event, data in _parse_sse(body) if event == "delta"]
    assert "".join(deltas) == (
        "I wasn't able to finish that after several tool calls - could you rephrase or "
        "simplify the request?"
    )
    assert len(model.seen) == settings.chat_tool_max_rounds

    messages = authenticated_client.get("/api/conversations/c1").json()["messages"]
    assert messages[-1]["role"] == "assistant"
    assert "rephrase" in messages[-1]["content"]
