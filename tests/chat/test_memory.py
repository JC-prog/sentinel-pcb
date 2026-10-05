"""Long-term (cross-conversation) memory. DB-backed tests target the real local Postgres (same
as tests/conftest.py's db_session); memory-store tests additionally target the real local Qdrant
(`docker compose -f infra/development/docker-compose.yml up -d qdrant`) and skip cleanly if it's
unreachable, same reasoning as db_session.
"""

import json
import uuid
from collections.abc import AsyncGenerator, Callable
from datetime import UTC, datetime

import httpx
import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, BaseMessage
from qdrant_client import AsyncQdrantClient

from app.chat.core.memory import MemoryRecord
from app.chat.memory import service as memory_service
from app.shared.config.settings import settings
from tests.chat._llm import ScriptedModel, ai, install

_TEST_COLLECTION_PREFIX = "test_chat_memories"

# Captured before any test patches httpx.AsyncClient, same reasoning as tests/test_chat.py.
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


def _send(client: TestClient, conversation_id: str, message: str) -> str:
    with client.stream(
        "POST",
        "/api/chat/stream",
        json={"conversation_id": conversation_id, "message": message, "image_ids": []},
    ) as response:
        body = "".join(response.iter_text())
    assert response.status_code == 200, body
    return "".join(str(data["text"]) for event, data in _parse_sse(body) if event == "delta")


@pytest_asyncio.fixture
async def qdrant_ready(monkeypatch: pytest.MonkeyPatch) -> AsyncGenerator[None, None]:
    """Points settings at a test-only collection prefix (so this never touches real dev-time
    memories) and skips cleanly if Qdrant isn't reachable - mirrors db_session's Postgres skip."""

    monkeypatch.setattr(settings, "memory_enabled", True)
    monkeypatch.setattr(settings, "qdrant_collection_name", _TEST_COLLECTION_PREFIX)

    client = AsyncQdrantClient(url=settings.qdrant_url)
    try:
        await client.get_collections()
    except Exception as exc:  # noqa: BLE001 - any failure here means "skip", not "fail"
        await client.close()
        pytest.skip(f"Qdrant not reachable at settings.qdrant_url ({exc!r})")

    yield

    collections = await client.get_collections()
    for collection in collections.collections:
        if collection.name.startswith(_TEST_COLLECTION_PREFIX):
            await client.delete_collection(collection.name)
    await client.close()


def _ollama_embed_response(input_texts: list[str]) -> httpx.Response:
    return httpx.Response(200, json={"embeddings": [[1.0, 0.0, 0.0] for _ in input_texts]})


def _embed_only(request: httpx.Request) -> httpx.Response:
    """Embeddings are the one thing in this feature still reached over HTTP (Ollama's /api/embed)."""

    assert request.url.path == "/api/embed", request.url.path
    return _ollama_embed_response(json.loads(request.content)["input"])


def _memory_model(fact_json: str, reply_text: str) -> ScriptedModel:
    """One scripted model for both jobs the chat model does here: app/chat/memory/service.py's
    fact-extraction pass (told apart by its system prompt) and the ordinary reply."""

    def reply(messages: list[BaseMessage]) -> AIMessage:
        is_extraction = any(m.type == "system" and "durable" in str(m.content) for m in messages)
        return ai(fact_json if is_extraction else reply_text)

    return ScriptedModel(reply_fn=reply)


class TestQdrantMemoryStoreIsolation:
    """The most safety-critical property of this whole feature: one user's long-term memory must
    never be visible to another, however many layers of caching/mocking sit above it in the other
    tests in this file. Exercised directly against the store, no LLM/HTTP involved."""

    async def test_search_never_crosses_users(self, qdrant_ready: None) -> None:
        store = memory_service.get_memory_store("isolation-test-model")
        now = datetime.now(UTC)

        await store.upsert(
            MemoryRecord(
                id=str(uuid.uuid4()),
                user_id="user-a",
                text="user A prefers metric units",
                source_conversation_id=None,
                created_at=now,
            ),
            [1.0, 0.0, 0.0],
        )
        await store.upsert(
            MemoryRecord(
                id=str(uuid.uuid4()),
                user_id="user-b",
                text="user B prefers imperial units",
                source_conversation_id=None,
                created_at=now,
            ),
            [1.0, 0.0, 0.0],
        )

        matches_a = await store.search("user-a", [1.0, 0.0, 0.0], top_k=5)
        assert [m.record.text for m in matches_a] == ["user A prefers metric units"]

        matches_b = await store.search("user-b", [1.0, 0.0, 0.0], top_k=5)
        assert [m.record.text for m in matches_b] == ["user B prefers imperial units"]

    async def test_search_on_a_never_written_model_is_empty_not_an_error(
        self, qdrant_ready: None
    ) -> None:
        store = memory_service.get_memory_store("a-model-nothing-has-used-yet")
        assert await store.search("anyone", [1.0, 0.0, 0.0], top_k=5) == []


async def test_extraction_writes_a_memory_after_the_configured_number_of_turns(
    authenticated_client: TestClient, qdrant_ready: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "memory_extraction_interval_turns", 1)
    _mock_async_client(monkeypatch, _embed_only)
    install(monkeypatch, _memory_model('["prefers dark roast coffee"]', "Sure thing!"))

    reply = _send(authenticated_client, "c1", "For the record, I prefer dark roast coffee.")
    assert reply == "Sure thing!"

    user_id = authenticated_client.get("/api/auth/me").json()["id"]
    store = memory_service.get_memory_store(settings.ollama_embedding_model)
    matches = await store.search(user_id, [1.0, 0.0, 0.0], top_k=5)
    assert [m.record.text for m in matches] == ["prefers dark roast coffee"]


async def test_retrieval_injects_memory_preamble_into_a_new_conversation(
    authenticated_client: TestClient, qdrant_ready: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    user_id = authenticated_client.get("/api/auth/me").json()["id"]
    store = memory_service.get_memory_store(settings.ollama_embedding_model)
    await store.upsert(
        MemoryRecord(
            id=str(uuid.uuid4()),
            user_id=user_id,
            text="works the night shift on line 3",
            source_conversation_id=None,
            created_at=datetime.now(UTC),
        ),
        [1.0, 0.0, 0.0],
    )

    _mock_async_client(monkeypatch, _embed_only)
    model = _memory_model("[]", "ack")
    install(monkeypatch, model)

    _send(authenticated_client, "new-conversation", "hi there")

    system_messages = [str(m.content) for m in model.seen[0] if m.type == "system"]
    assert any("works the night shift on line 3" in text for text in system_messages)


async def test_remember_command_stores_a_fact_without_calling_the_chat_llm(
    authenticated_client: TestClient, qdrant_ready: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    _mock_async_client(monkeypatch, _embed_only)
    model = _memory_model("[]", "should not be called")
    install(monkeypatch, model)

    reply = _send(authenticated_client, "c1", "/remember I only drink oat milk")
    assert "I only drink oat milk" in reply
    assert model.seen == []  # a /remember command never reaches the chat LLM.

    user_id = authenticated_client.get("/api/auth/me").json()["id"]
    store = memory_service.get_memory_store(settings.ollama_embedding_model)
    matches = await store.search(user_id, [1.0, 0.0, 0.0], top_k=5)
    assert [m.record.text for m in matches] == ["I only drink oat milk"]


def test_memory_disabled_kill_switch_makes_zero_memory_calls(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Deliberately does NOT use qdrant_ready - with memory disabled, chat must work even if
    # Qdrant were completely unreachable.
    monkeypatch.setattr(settings, "memory_enabled", False)
    embed_calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        embed_calls.append(request)
        return _embed_only(request)

    _mock_async_client(monkeypatch, handler)
    install(monkeypatch, _memory_model("[]", "ack"))

    reply = _send(authenticated_client, "c1", "hello")
    assert reply == "ack"
    assert embed_calls == []
