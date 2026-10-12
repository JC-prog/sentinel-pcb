"""What a chat turn leaves behind: the cards a reply showed, a reply cut short, and renaming.

The model is scripted (tests/chat/_llm.py); `run_turn` is swapped for a stand-in where a test needs
a reply that streams some text and then fails.
"""

from collections.abc import AsyncIterator
from typing import Annotated, Any

import pytest
from fastapi.testclient import TestClient
from langchain_core.tools import tool

from app.chat.agents import ToolRegistry
from app.chat.agents.supervisor import TextChunk
from app.chat.agents.toolkit import ChatTool, Runtime, returns_json
from app.chat.services import streaming
from tests.chat._llm import ScriptedModel, ai, install, says


def _send(client: TestClient, conversation_id: str, message: str) -> None:
    with client.stream(
        "POST",
        "/api/chat/stream",
        json={"conversation_id": conversation_id, "message": message, "image_ids": []},
    ) as response:
        response.read()
        assert response.status_code == 200


def _card_registry() -> ToolRegistry:
    @tool("get_drift_summary")
    @returns_json
    async def stand_in(
        runtime: Runtime, model: Annotated[str, "the model"] = ""
    ) -> dict[str, Any]:
        """A stand-in tool."""
        return {"model": model, "score": 0.5}

    return ToolRegistry([ChatTool(stand_in, label="Drift summary", shows_card=True)])


def test_the_cards_a_reply_showed_come_back_with_the_conversation(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(streaming, "tool_registry", _card_registry())
    install(
        monkeypatch,
        ScriptedModel(replies=[ai("", ("get_drift_summary", {"model": "m1"})), ai("Here you go.")]),
    )

    _send(authenticated_client, "c1", "show me")

    detail = authenticated_client.get("/api/conversations/c1").json()
    user, assistant = detail["messages"]
    assert user["tool_results"] == []
    assert assistant["content"] == "Here you go."
    assert assistant["interrupted"] is False
    assert assistant["tool_results"] == [
        {"name": "get_drift_summary", "result": {"model": "m1", "score": 0.5}}
    ]


def test_a_reply_that_fails_part_way_is_kept_and_flagged_interrupted(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def half_then_fail(**_: Any) -> AsyncIterator[TextChunk]:
        yield TextChunk("Half an ")
        yield TextChunk("answer")
        raise RuntimeError("upstream went away")

    monkeypatch.setattr(streaming, "run_turn", half_then_fail)

    _send(authenticated_client, "c1", "tell me")

    messages = authenticated_client.get("/api/conversations/c1").json()["messages"]
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert messages[1]["content"] == "Half an answer"
    assert messages[1]["interrupted"] is True


def test_a_turn_that_fails_before_anything_streamed_saves_no_reply(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fail(**_: Any) -> AsyncIterator[TextChunk]:
        raise RuntimeError("upstream went away")
        yield  # pragma: no cover - makes this an async generator

    monkeypatch.setattr(streaming, "run_turn", fail)

    _send(authenticated_client, "c1", "tell me")

    messages = authenticated_client.get("/api/conversations/c1").json()["messages"]
    assert [m["role"] for m in messages] == ["user"]


def test_rename_conversation(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    install(monkeypatch, says("ack"))
    _send(authenticated_client, "c1", "hello")

    response = authenticated_client.patch(
        "/api/conversations/c1", json={"title": "  Board   A7 reflow defects "}
    )

    assert response.status_code == 200
    assert response.json()["title"] == "Board A7 reflow defects"
    [summary] = authenticated_client.get("/api/conversations").json()
    assert summary["title"] == "Board A7 reflow defects"


def test_rename_rejects_a_blank_title(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    install(monkeypatch, says("ack"))
    _send(authenticated_client, "c1", "hello")

    response = authenticated_client.patch("/api/conversations/c1", json={"title": "   "})

    assert response.status_code == 422


def test_rename_is_404_for_another_users_conversation(
    authenticated_client: TestClient,
    other_authenticated_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install(monkeypatch, says("ack"))
    _send(authenticated_client, "c1", "private")

    response = other_authenticated_client.patch("/api/conversations/c1", json={"title": "mine now"})

    assert response.status_code == 404
