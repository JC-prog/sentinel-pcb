import json
from collections.abc import Callable

import httpx
import pytest
from fastapi.testclient import TestClient

# Captured before any test patches httpx.AsyncClient, so the mock factory below can still
# construct a real client (just wired to a MockTransport instead of the network).
_RealAsyncClient = httpx.AsyncClient


def _mock_async_client(monkeypatch: pytest.MonkeyPatch, handler: Callable[[httpx.Request], httpx.Response]) -> None:
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


def test_chat_stream_requires_login(client: TestClient) -> None:
    response = client.post(
        "/api/chat/stream", json={"conversation_id": "c1", "message": "hi", "image_ids": []}
    )
    assert response.status_code == 401


def test_chat_stream_rejects_empty_message(authenticated_client: TestClient) -> None:
    response = authenticated_client.post(
        "/api/chat/stream", json={"conversation_id": "c1", "message": "", "image_ids": []}
    )
    assert response.status_code == 422


def test_chat_stream_requires_key_for_openai_provider(authenticated_client: TestClient) -> None:
    response = authenticated_client.post(
        "/api/chat/stream",
        json={"conversation_id": "c1", "message": "hi", "image_ids": [], "provider": "openai"},
    )
    assert response.status_code == 422


def test_chat_stream_uses_ollama_by_default(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/chat"
        body = (
            json.dumps({"message": {"content": "Hello "}, "done": False})
            + "\n"
            + json.dumps({"message": {"content": "from Ollama"}, "done": False})
            + "\n"
            + json.dumps({"message": {"content": ""}, "done": True})
        )
        return httpx.Response(200, text=body)

    _mock_async_client(monkeypatch, handler)

    with authenticated_client.stream(
        "POST",
        "/api/chat/stream",
        json={"conversation_id": "c1", "message": "hi", "image_ids": []},
    ) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        body = "".join(response.iter_text())

    frames = _parse_sse(body)
    assert frames[-1] == ("done", {})
    deltas = [str(data["text"]) for event, data in frames if event == "delta"]
    assert "".join(deltas) == "Hello from Ollama"


def test_chat_stream_uses_openai_when_selected(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer sk-test-key"
        sse = (
            'data: {"choices":[{"delta":{"content":"Hi "}}]}\n\n'
            'data: {"choices":[{"delta":{"content":"there"}}]}\n\n'
            "data: [DONE]\n\n"
        )
        return httpx.Response(200, text=sse)

    _mock_async_client(monkeypatch, handler)

    with authenticated_client.stream(
        "POST",
        "/api/chat/stream",
        json={
            "conversation_id": "c1",
            "message": "hi",
            "image_ids": [],
            "provider": "openai",
            "openai_api_key": "sk-test-key",
        },
    ) as response:
        body = "".join(response.iter_text())

    deltas = [str(data["text"]) for event, data in _parse_sse(body) if event == "delta"]
    assert "".join(deltas) == "Hi there"


def test_chat_stream_surfaces_upstream_error_without_leaking_the_key(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text='{"error": "invalid api key"}')

    _mock_async_client(monkeypatch, handler)

    with authenticated_client.stream(
        "POST",
        "/api/chat/stream",
        json={
            "conversation_id": "c1",
            "message": "hi",
            "image_ids": [],
            "provider": "openai",
            "openai_api_key": "sk-super-secret",
        },
    ) as response:
        body = "".join(response.iter_text())

    frames = _parse_sse(body)
    assert frames[0][0] == "error"
    assert "sk-super-secret" not in body
