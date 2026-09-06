"""Confirms the LLM provider request/response debug logging added alongside the API
request/response body logging (tests/test_request_logging_middleware.py) - both are gated behind
settings.log_level == "DEBUG", see app/config/logging_config.py.
"""

import json
import logging
from collections.abc import Callable

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


def test_ollama_provider_logs_request_and_response_payload_at_debug(
    authenticated_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG, logger="app.chat.providers.ollama")

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.dumps({"message": {"content": "Hello from Ollama"}, "done": True})
        return httpx.Response(200, text=body)

    _mock_async_client(monkeypatch, handler)

    with authenticated_client.stream(
        "POST",
        "/api/chat/stream",
        json={"conversation_id": "c1", "message": "hi", "image_ids": []},
    ) as response:
        response.read()
    assert response.status_code == 200

    records = [r for r in caplog.records if r.name == "app.chat.providers.ollama"]
    request_records = [r for r in records if hasattr(r, "payload")]
    response_records = [r for r in records if hasattr(r, "content")]

    assert request_records, "expected the outgoing Ollama payload to be logged"
    assert request_records[0].payload["messages"] == [{"role": "user", "content": "hi"}]

    assert response_records, "expected the Ollama response content to be logged"
    assert response_records[-1].content == "Hello from Ollama"


def test_no_llm_debug_logs_at_default_info_level(
    authenticated_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="app.chat.providers.ollama")

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.dumps({"message": {"content": "ack"}, "done": True})
        return httpx.Response(200, text=body)

    _mock_async_client(monkeypatch, handler)

    with authenticated_client.stream(
        "POST",
        "/api/chat/stream",
        json={"conversation_id": "c1", "message": "hi", "image_ids": []},
    ) as response:
        response.read()

    assert [r for r in caplog.records if r.name == "app.chat.providers.ollama"] == []
