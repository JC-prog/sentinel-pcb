import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config.settings import settings

_RealAsyncClient = httpx.AsyncClient


def _mock_async_client(
    monkeypatch: pytest.MonkeyPatch, handler: Callable[[httpx.Request], httpx.Response]
) -> None:
    def factory(*args: object, **kwargs: object) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(handler)
        return _RealAsyncClient(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(httpx, "AsyncClient", factory)


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


def _ollama_reply(text: str) -> httpx.Response:
    return httpx.Response(200, text=json.dumps({"message": {"content": text}, "done": True}))


def _ollama_tool_call_reply(name: str, arguments: dict[str, object]) -> httpx.Response:
    body = {
        "message": {
            "content": "",
            "tool_calls": [{"function": {"name": name, "arguments": arguments}}],
        },
        "done": True,
    }
    return httpx.Response(200, text=json.dumps(body))


def _tool_names(payload: dict[str, Any]) -> set[str]:
    return {t["function"]["name"] for t in payload["tools"]}


def _stream(client: TestClient, message: str, image_ids: list[str] | None = None) -> str:
    with client.stream(
        "POST",
        "/api/chat/stream",
        json={"conversation_id": "c1", "message": message, "image_ids": image_ids or []},
    ) as response:
        return "".join(response.iter_text())


def test_tools_field_sent_by_default_excluding_explainability(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    requests: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return _ollama_reply("ack")

    _mock_async_client(monkeypatch, handler)
    _stream(authenticated_client, "hi")

    assert _tool_names(requests[0]) == {"current_time", "get_weather"}


def test_tools_field_includes_explainability_when_image_attached(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    requests: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return _ollama_reply("ack")

    _mock_async_client(monkeypatch, handler)
    _stream(authenticated_client, "check this board", image_ids=["some-upload-id"])

    assert _tool_names(requests[0]) == {"current_time", "get_weather", "explainability_review"}


def test_tools_disabled_sends_no_tools_field(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "chat_tool_calling_enabled", False)
    requests: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return _ollama_reply("ack")

    _mock_async_client(monkeypatch, handler)
    _stream(authenticated_client, "hi")

    assert "tools" not in requests[0]


def test_ollama_tool_call_round_trip(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content))
        if len(calls) == 1:
            return _ollama_tool_call_reply("current_time", {})
        return _ollama_reply("It is currently noon UTC.")

    _mock_async_client(monkeypatch, handler)
    body = _stream(authenticated_client, "what time is it?")

    deltas = [str(data["text"]) for event, data in _parse_sse(body) if event == "delta"]
    assert "".join(deltas) == "It is currently noon UTC."
    assert len(calls) == 2

    second_call_messages = calls[1]["messages"]
    assert isinstance(second_call_messages, list)
    tool_result_message = second_call_messages[-1]
    assert tool_result_message["role"] == "tool"
    assert tool_result_message["name"] == "current_time"

    detail = authenticated_client.get("/api/conversations/c1")
    messages = detail.json()["messages"]
    assert [(m["role"], m["content"]) for m in messages] == [
        ("user", "what time is it?"),
        ("assistant", "It is currently noon UTC."),
    ]


def test_openai_tool_call_round_trip(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content))
        if len(calls) == 1:
            sse = (
                'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_abc",'
                '"type":"function","function":{"name":"current_time","arguments":""}}]}}]}\n\n'
                'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":'
                '{"arguments":"{}"}}]}}]}\n\n'
                'data: {"choices":[{"delta":{},"finish_reason":"tool_calls"}]}\n\n'
                "data: [DONE]\n\n"
            )
        else:
            sse = (
                'data: {"choices":[{"delta":{"content":"It is currently noon UTC."}}]}\n\n'
                "data: [DONE]\n\n"
            )
        return httpx.Response(200, text=sse)

    _mock_async_client(monkeypatch, handler)

    with authenticated_client.stream(
        "POST",
        "/api/chat/stream",
        json={
            "conversation_id": "c1",
            "message": "what time is it?",
            "image_ids": [],
            "provider": "openai",
            "openai_api_key": "sk-test-key",
        },
    ) as response:
        body = "".join(response.iter_text())

    deltas = [str(data["text"]) for event, data in _parse_sse(body) if event == "delta"]
    assert "".join(deltas) == "It is currently noon UTC."
    assert len(calls) == 2

    second_call_messages = calls[1]["messages"]
    assert isinstance(second_call_messages, list)
    tool_result_message = second_call_messages[-1]
    assert tool_result_message["role"] == "tool"
    assert tool_result_message["tool_call_id"] == "call_abc"


def test_max_rounds_exceeded_falls_back(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content))
        return _ollama_tool_call_reply("current_time", {})

    _mock_async_client(monkeypatch, handler)
    body = _stream(authenticated_client, "keep calling tools forever")

    deltas = [str(data["text"]) for event, data in _parse_sse(body) if event == "delta"]
    assert "".join(deltas) == (
        "I wasn't able to finish that after several tool calls - could you rephrase or "
        "simplify the request?"
    )
    assert len(calls) == settings.chat_tool_max_rounds

    detail = authenticated_client.get("/api/conversations/c1")
    messages = detail.json()["messages"]
    assert messages[-1]["role"] == "assistant"
    assert "rephrase" in messages[-1]["content"]
