import json
from collections.abc import Callable

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config.settings import settings

# Captured before any test patches httpx.AsyncClient, so the mock factory below can still
# construct a real client (just wired to a MockTransport instead of the network).
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


def test_chat_stream_returns_503_when_openai_not_configured(
    authenticated_client: TestClient,
) -> None:
    response = authenticated_client.post(
        "/api/chat/stream",
        json={"conversation_id": "c1", "message": "hi", "image_ids": [], "provider": "openai"},
    )
    assert response.status_code == 503


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
    monkeypatch.setattr(settings, "openai_api_key", "sk-server-key")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer sk-server-key"
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
        },
    ) as response:
        body = "".join(response.iter_text())

    deltas = [str(data["text"]) for event, data in _parse_sse(body) if event == "delta"]
    assert "".join(deltas) == "Hi there"


def test_chat_stream_surfaces_upstream_error_without_leaking_the_key(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "openai_api_key", "sk-super-secret")

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
        },
    ) as response:
        body = "".join(response.iter_text())

    frames = _parse_sse(body)
    assert frames[0][0] == "error"
    assert "sk-super-secret" not in body


def _ollama_reply(text: str) -> httpx.Response:
    body = json.dumps({"message": {"content": text}, "done": True})
    return httpx.Response(200, text=body)


def _send(client: TestClient, conversation_id: str, message: str) -> str:
    with client.stream(
        "POST",
        "/api/chat/stream",
        json={"conversation_id": conversation_id, "message": message, "image_ids": []},
    ) as response:
        body = "".join(response.iter_text())
    assert response.status_code == 200, body
    return "".join(str(data["text"]) for event, data in _parse_sse(body) if event == "delta")


def test_chat_stream_sends_prior_turns_as_history(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    requests: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return _ollama_reply("ack")

    _mock_async_client(monkeypatch, handler)

    _send(authenticated_client, "c1", "first message")
    _send(authenticated_client, "c1", "second message")

    assert requests[0]["messages"] == [{"role": "user", "content": "first message"}]
    assert requests[1]["messages"] == [
        {"role": "user", "content": "first message"},
        {"role": "assistant", "content": "ack"},
        {"role": "user", "content": "second message"},
    ]


def test_chat_stream_history_is_windowed(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "chat_history_max_turns", 2)
    requests: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return _ollama_reply("ack")

    _mock_async_client(monkeypatch, handler)

    _send(authenticated_client, "c1", "one")
    _send(authenticated_client, "c1", "two")
    _send(authenticated_client, "c1", "three")

    # By the 3rd call, only the most recent 2 persisted messages (the 2nd user turn + its "ack"
    # reply) should be replayed as history - "one" has aged out of the window.
    assert requests[2]["messages"] == [
        {"role": "user", "content": "two"},
        {"role": "assistant", "content": "ack"},
        {"role": "user", "content": "three"},
    ]


def test_chat_stream_persists_user_and_assistant_messages(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _ollama_reply("Hello from Ollama")

    _mock_async_client(monkeypatch, handler)
    _send(authenticated_client, "c1", "hi there")

    detail = authenticated_client.get("/api/conversations/c1")
    assert detail.status_code == 200
    messages = detail.json()["messages"]
    assert [(m["role"], m["content"]) for m in messages] == [
        ("user", "hi there"),
        ("assistant", "Hello from Ollama"),
    ]


def test_chat_stream_error_persists_user_message_but_not_assistant_reply(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    _mock_async_client(monkeypatch, handler)

    with authenticated_client.stream(
        "POST", "/api/chat/stream", json={"conversation_id": "c1", "message": "hi", "image_ids": []}
    ) as response:
        body = "".join(response.iter_text())
    assert _parse_sse(body)[0][0] == "error"

    detail = authenticated_client.get("/api/conversations/c1")
    messages = detail.json()["messages"]
    assert [(m["role"], m["content"]) for m in messages] == [("user", "hi")]


def test_chat_stream_conversation_id_scoped_per_user(
    authenticated_client: TestClient,
    other_authenticated_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _ollama_reply("ack")

    _mock_async_client(monkeypatch, handler)

    _send(authenticated_client, "shared-id", "user A's secret")

    response = other_authenticated_client.post(
        "/api/chat/stream",
        json={"conversation_id": "shared-id", "message": "hi", "image_ids": []},
    )
    assert response.status_code == 404

    # user A's own history is untouched and not visible to user B.
    assert other_authenticated_client.get("/api/conversations/shared-id").status_code == 404
    assert authenticated_client.get("/api/conversations/shared-id").status_code == 200
