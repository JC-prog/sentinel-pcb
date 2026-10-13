"""POST /api/chat/stream: the SSE frames, history, persistence and who may talk to which
conversation. The chat model is a scripted stand-in (tests/chat/_llm.py)."""

import json

import pytest
from fastapi.testclient import TestClient

from app.shared.config.settings import settings
from tests.chat._llm import ScriptedModel, ai, conversation, install, says


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


def _stream(client: TestClient, conversation_id: str, message: str, **extra: object) -> str:
    with client.stream(
        "POST",
        "/api/chat/stream",
        json={"conversation_id": conversation_id, "message": message, "image_ids": [], **extra},
    ) as response:
        body = "".join(response.iter_text())
    assert response.status_code == 200, body
    return body


def _reply(body: str) -> str:
    return "".join(str(data["text"]) for event, data in _parse_sse(body) if event == "delta")


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
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A developer's .env normally has a real OPENAI_API_KEY, which would otherwise defeat this
    # test - force the "not configured" case explicitly rather than relying on the default.
    monkeypatch.setattr(settings, "openai_api_key", "")
    response = authenticated_client.post(
        "/api/chat/stream",
        json={"conversation_id": "c1", "message": "hi", "image_ids": [], "provider": "openai"},
    )
    assert response.status_code == 503


def test_chat_stream_uses_openai_by_default(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    providers = install(monkeypatch, says("Hello from OpenAI"))

    body = _stream(authenticated_client, "c1", "hi")

    assert providers == ["openai"]
    assert _reply(body) == "Hello from OpenAI"
    event, data = _parse_sse(body)[-1]
    assert event == "done" and set(data) == {"message_id"}


def _record_chat_models(monkeypatch: pytest.MonkeyPatch, reply: str) -> list[str | None]:
    """Replaces the supervisor's model factory; returns the `model` it was asked for, per turn."""

    asked: list[str | None] = []
    scripted = says(reply)

    def build(provider: str | None = None, **kwargs: object) -> object:
        asked.append(kwargs.get("model"))  # type: ignore[arg-type]
        return scripted

    monkeypatch.setattr("app.chat.agents.supervisor.build_chat_model", build)
    return asked


def test_chat_models_lists_the_default_first(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "openai_model", "gpt-4o-mini")
    monkeypatch.setattr(settings, "chat_models", ["gpt-4o", "gpt-4o-mini"])

    response = authenticated_client.get("/api/chat/models")

    assert response.status_code == 200
    assert response.json() == {"default": "gpt-4o-mini", "models": ["gpt-4o-mini", "gpt-4o"]}


def test_chat_models_requires_login(client: TestClient) -> None:
    assert client.get("/api/chat/models").status_code == 401


def test_the_chat_reply_uses_the_model_picked_in_settings(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "chat_models", ["gpt-4o-mini", "gpt-4o"])
    asked = _record_chat_models(monkeypatch, "ok")

    _stream(authenticated_client, "c1", "hi", model="gpt-4o")

    assert asked == ["gpt-4o"]


def test_a_model_the_server_does_not_allow_falls_back_to_the_default(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "chat_models", ["gpt-4o-mini"])
    asked = _record_chat_models(monkeypatch, "ok")

    _stream(authenticated_client, "c1", "hi", model="some-expensive-model")
    _stream(authenticated_client, "c2", "hi")

    assert asked == [None, None]


def test_chat_stream_uses_openai_when_selected(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "openai_api_key", "sk-server-key")
    providers = install(monkeypatch, says("Hi there"))

    body = _stream(authenticated_client, "c1", "hi", provider="openai")

    assert providers == ["openai"]
    assert _reply(body) == "Hi there"


def test_chat_stream_reports_a_model_failure_without_leaking_its_details(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The upstream error can echo request details (a key, a prompt) - the client gets a fixed
    message instead; the real one is logged."""

    monkeypatch.setattr(settings, "openai_api_key", "sk-super-secret")
    install(monkeypatch, ScriptedModel(replies=[RuntimeError("401 invalid key sk-super-secret")]))

    body = _stream(authenticated_client, "c1", "hi", provider="openai")

    frames = _parse_sse(body)
    assert frames[0][0] == "error"
    assert "sk-super-secret" not in body
    assert not any(event == "done" for event, _ in frames)


def test_chat_stream_sends_prior_turns_as_history(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    model = says("ack")
    install(monkeypatch, model)

    _stream(authenticated_client, "c1", "first message")
    _stream(authenticated_client, "c1", "second message")

    assert conversation(model.seen[0]) == [("human", "first message")]
    assert conversation(model.seen[1]) == [
        ("human", "first message"),
        ("ai", "ack"),
        ("human", "second message"),
    ]


def test_chat_stream_history_is_windowed(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "chat_history_max_turns", 2)
    model = says("ack")
    install(monkeypatch, model)

    for message in ("one", "two", "three"):
        _stream(authenticated_client, "c1", message)

    # By the 3rd call, only the most recent 2 persisted messages (the 2nd user turn + its "ack"
    # reply) should be replayed as history - "one" has aged out of the window.
    assert conversation(model.seen[2]) == [("human", "two"), ("ai", "ack"), ("human", "three")]


def test_every_prompt_starts_with_the_system_prompt(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    model = says("ack")
    install(monkeypatch, model)

    _stream(authenticated_client, "c1", "hi")

    system = model.seen[0][0]
    assert system.type == "system"
    assert "SentinelChat" in str(system.content)


def test_chat_stream_persists_user_and_assistant_messages(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    install(monkeypatch, says("Hello from the model"))
    _stream(authenticated_client, "c1", "hi there")

    detail = authenticated_client.get("/api/conversations/c1")
    assert detail.status_code == 200
    messages = detail.json()["messages"]
    assert [(m["role"], m["content"]) for m in messages] == [
        ("user", "hi there"),
        ("assistant", "Hello from the model"),
    ]


def test_chat_stream_error_persists_user_message_but_not_assistant_reply(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    install(monkeypatch, ScriptedModel(replies=[RuntimeError("boom")]))

    body = _stream(authenticated_client, "c1", "hi")
    assert _parse_sse(body)[0][0] == "error"

    messages = authenticated_client.get("/api/conversations/c1").json()["messages"]
    assert [(m["role"], m["content"]) for m in messages] == [("user", "hi")]


def test_a_tool_calling_turn_persists_only_the_final_answer(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Intermediate rounds (the tool call and its result) are never part of the history."""

    install(
        monkeypatch,
        ScriptedModel(replies=[ai("", ("get_drift_summary", {"model": "pcb_body_defect"})), ai("No drift.")]),
    )

    _stream(authenticated_client, "c1", "is the model drifting?")

    messages = authenticated_client.get("/api/conversations/c1").json()["messages"]
    assert [(m["role"], m["content"]) for m in messages] == [
        ("user", "is the model drifting?"),
        ("assistant", "No drift."),
    ]


def test_chat_stream_conversation_id_scoped_per_user(
    authenticated_client: TestClient,
    other_authenticated_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install(monkeypatch, says("ack"))

    _stream(authenticated_client, "shared-id", "user A's secret")

    response = other_authenticated_client.post(
        "/api/chat/stream",
        json={"conversation_id": "shared-id", "message": "hi", "image_ids": []},
    )
    assert response.status_code == 404

    # user A's own history is untouched and not visible to user B.
    assert other_authenticated_client.get("/api/conversations/shared-id").status_code == 404
    assert authenticated_client.get("/api/conversations/shared-id").status_code == 200
