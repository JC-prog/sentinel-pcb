"""Role -> tool-visibility matrix (app/chat/agents/access.py), exercised through /api/chat/stream, plus
a defense-in-depth check that a tool merely not being offered also can't be dispatched.

Every fixture here registers a *second* user in the test's DB - app/shared/auth/service.py auto-promotes
the first registered user in an empty DB to ADMIN regardless of requested role (see
tests/conftest.py's qa_authenticated_client docstring), so `authenticated_client` itself is used
as the ADMIN case rather than a dedicated fixture.
"""

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

_RealAsyncClient = httpx.AsyncClient


def _mock_async_client(
    monkeypatch: pytest.MonkeyPatch, handler: Callable[[httpx.Request], httpx.Response]
) -> None:
    def factory(*args: object, **kwargs: object) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(handler)
        return _RealAsyncClient(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(httpx, "AsyncClient", factory)


def _ollama_reply(text: str) -> httpx.Response:
    return httpx.Response(200, text=json.dumps({"message": {"content": text}, "done": True}))


def _ollama_tool_call_reply(name: str, arguments: dict[str, object]) -> httpx.Response:
    lines = [
        json.dumps(
            {
                "message": {
                    "content": "",
                    "tool_calls": [{"function": {"name": name, "arguments": arguments}}],
                },
                "done": False,
            }
        ),
        json.dumps({"message": {"content": ""}, "done": True}),
    ]
    return httpx.Response(200, text="\n".join(lines))


def _tool_names(payload: dict[str, Any]) -> set[str]:
    return {t["function"]["name"] for t in payload["tools"]}


def _stream(client: TestClient, message: str, image_ids: list[str] | None = None) -> str:
    with client.stream(
        "POST",
        "/api/chat/stream",
        json={"conversation_id": "c1", "message": message, "image_ids": image_ids or []},
    ) as response:
        return "".join(response.iter_text())


def _offered_tools(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, *, image_attached: bool
) -> set[str]:
    requests: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return _ollama_reply("ack")

    _mock_async_client(monkeypatch, handler)
    _stream(client, "check this board", image_ids=["some-upload-id"] if image_attached else None)
    return _tool_names(requests[0])


def test_qa_sees_orchestrator_explainability_review_and_retraining_tools(
    qa_authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    offered = _offered_tools(qa_authenticated_client, monkeypatch, image_attached=True)
    assert offered == {
        "current_time",
        "get_weather",
        "inspect_image",
        "explainability_review",
        "list_cases",
        "get_case",
        "review_case",
        "find_similar_cases",
        "investigate_case",
        "flag_case_for_retraining",
        "report_model_drift",
        "get_drift_summary",
        "draft_retraining_plan",
    }


def test_qa_sees_investigate_and_retraining_tools_without_an_image_attached(
    qa_authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every case and model-health tool works from a case number (or the conversation's latest
    case) - unlike inspect_image/explainability_review, they need no image attached to this
    message."""

    offered = _offered_tools(qa_authenticated_client, monkeypatch, image_attached=False)
    assert offered == {
        "current_time",
        "get_weather",
        "list_cases",
        "get_case",
        "review_case",
        "find_similar_cases",
        "investigate_case",
        "flag_case_for_retraining",
        "report_model_drift",
        "get_drift_summary",
        "draft_retraining_plan",
    }


def test_admin_sees_every_tool(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    offered = _offered_tools(authenticated_client, monkeypatch, image_attached=True)
    assert offered == {
        "current_time",
        "get_weather",
        "inspect_image",
        "explainability_review",
        "list_cases",
        "get_case",
        "review_case",
        "find_similar_cases",
        "investigate_case",
        "flag_case_for_retraining",
        "report_model_drift",
        "get_drift_summary",
        "draft_retraining_plan",
        "monitoring_status",
    }


def test_qa_tool_call_naming_a_disallowed_tool_is_rejected_at_dispatch(
    qa_authenticated_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Defense in depth: _run_tool_call re-checks role access even for a tool name the model
    was never offered - simulates a client crafting a tool-call request directly rather than
    relying on the LLM to only request what _available_tool_specs offered. monitoring_status is
    the one tool QA never sees (Admin-only)."""

    calls: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content))
        if len(calls) == 1:
            return _ollama_tool_call_reply("monitoring_status", {})
        return _ollama_reply("done")

    _mock_async_client(monkeypatch, handler)
    _stream(qa_authenticated_client, "what's the model status", image_ids=["some-upload-id"])

    second_call_messages = calls[1]["messages"]
    assert isinstance(second_call_messages, list)
    tool_result_message = second_call_messages[-1]
    assert tool_result_message["role"] == "tool"
    tool_result = json.loads(tool_result_message["content"])
    assert tool_result == {"error": "tool 'monitoring_status' is not permitted for role 'qa'"}
