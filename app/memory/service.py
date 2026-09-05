"""Orchestrates long-term memory: extracting durable facts from a conversation and retrieving
them to seed a new conversation's system prompt. See app/core/memory.py for the MemoryStore/
EmbeddingService interfaces this depends on - app/memory/qdrant_store.py is the only file that
knows it's Qdrant underneath.
"""

import json
import logging
import uuid
from datetime import UTC, datetime

from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from app.chat import repository as chat_repository
from app.chat.schemas import LlmProvider
from app.chat.service import get_chat_service
from app.core.memory import MemoryRecord, MemoryStore
from app.db.models import Conversation
from app.memory.embeddings import embedding_model_name, get_embedding_service
from app.memory.qdrant_store import QdrantMemoryStore, get_qdrant_client
from app.settings import settings

logger = logging.getLogger(__name__)

_MAX_FACTS_PER_EXTRACTION = 5
_EXTRACTION_SYSTEM_PROMPT = (
    "You extract durable, user-specific facts worth remembering across future conversations - "
    "stated preferences, ongoing projects, recurring context. Reply with ONLY a JSON array of "
    "short fact strings (at most 5), or an empty array [] if nothing here is worth remembering "
    "beyond this conversation. Do not include facts that only matter to this single exchange."
)


def get_memory_store(embedding_model: str) -> MemoryStore:
    """One Qdrant collection per embedding model, not one collection overall - vectors from
    different models aren't comparable and don't even share a dimension, so mixing them in a
    single collection would corrupt search results (or fail outright on a size mismatch).
    Trade-off: switching providers mid-use means retrieval only sees memories written under the
    provider currently selected, not ones written under a different one - acceptable for now
    since most deployments will stick to one provider."""

    collection_name = f"{settings.qdrant_collection_name}__{embedding_model}"
    return QdrantMemoryStore(get_qdrant_client(), collection_name)


async def maybe_extract(
    session: AsyncSession,
    conversation: Conversation,
    provider: LlmProvider,
    openai_api_key: SecretStr | None,
) -> None:
    """Runs a small LLM pass over the conversation's recent turns to pull out durable facts,
    throttled to every settings.memory_extraction_interval_turns assistant replies so it isn't an
    extra LLM call on every single message. Never raises - a broken extraction should never take
    down a chat response that's already been sent to the client."""

    if not settings.memory_enabled:
        return
    try:
        assistant_turns = await chat_repository.count_messages_by_role(
            session, conversation.id, "assistant"
        )
        if assistant_turns == 0 or assistant_turns % settings.memory_extraction_interval_turns != 0:
            return

        window = settings.memory_extraction_interval_turns * 2
        recent = await chat_repository.get_recent_messages(session, conversation.id, limit=window)
        transcript = "\n".join(f"{m.role}: {m.content}" for m in recent)

        facts = await _extract_facts(transcript, provider, openai_api_key)
        if not facts:
            return

        embedding_service = get_embedding_service(provider, openai_api_key)
        embeddings = await embedding_service.embed(facts)
        store = get_memory_store(embedding_model_name(provider))
        now = datetime.now(UTC)
        for fact, embedding in zip(facts, embeddings, strict=True):
            record = MemoryRecord(
                id=str(uuid.uuid4()),
                user_id=conversation.user_id,
                text=fact,
                source_conversation_id=conversation.id,
                created_at=now,
                category="extracted",
            )
            await store.upsert(record, embedding)
    except Exception:
        logger.exception("Long-term memory extraction failed for conversation %s", conversation.id)


async def _extract_facts(
    transcript: str, provider: LlmProvider, openai_api_key: SecretStr | None
) -> list[str]:
    service = get_chat_service(provider, openai_api_key)
    chunks: list[str] = []
    async for chunk in service.stream_reply(
        [], transcript, [], system_prompt=_EXTRACTION_SYSTEM_PROMPT
    ):
        chunks.append(chunk)
    try:
        facts = json.loads("".join(chunks))
    except json.JSONDecodeError:
        return []
    if not isinstance(facts, list):
        return []
    return [str(fact).strip() for fact in facts if str(fact).strip()][:_MAX_FACTS_PER_EXTRACTION]


async def remember_explicit(
    user_id: str,
    conversation_id: str,
    fact: str,
    provider: LlmProvider,
    openai_api_key: SecretStr | None,
) -> str:
    """Backs the `/remember <text>` escape hatch (app/main.py) - stores fact verbatim, bypassing
    the LLM-extraction heuristic, for when a user wants a guaranteed memory rather than hoping
    the implicit extraction picks it up."""

    if not settings.memory_enabled:
        return "Long-term memory is currently disabled."
    try:
        embedding_service = get_embedding_service(provider, openai_api_key)
        [embedding] = await embedding_service.embed([fact])
        store = get_memory_store(embedding_model_name(provider))
        record = MemoryRecord(
            id=str(uuid.uuid4()),
            user_id=user_id,
            text=fact,
            source_conversation_id=conversation_id,
            created_at=datetime.now(UTC),
            category="explicit",
        )
        await store.upsert(record, embedding)
    except Exception:
        logger.exception("Explicit /remember failed for user %s", user_id)
        return "Sorry, I couldn't save that just now."
    return f'Got it, I\'ll remember: "{fact}"'


async def build_memory_preamble(
    user_id: str, query_text: str, provider: LlmProvider, openai_api_key: SecretStr | None
) -> str | None:
    """Only called for a brand-new conversation (app/main.py) - retrieval is scoped solely by
    user_id, never by conversation, since surfacing facts from OTHER conversations is the entire
    point of long-term memory. Never raises - a broken retrieval should degrade to "no memory
    this time", not break the chat request."""

    if not settings.memory_enabled:
        return None
    try:
        embedding_service = get_embedding_service(provider, openai_api_key)
        [query_embedding] = await embedding_service.embed([query_text])
        store = get_memory_store(embedding_model_name(provider))
        matches = await store.search(
            user_id, query_embedding, top_k=settings.memory_retrieval_top_k
        )
    except Exception:
        logger.exception("Long-term memory retrieval failed for user %s", user_id)
        return None

    if not matches:
        return None
    facts = "\n".join(f"- {match.record.text}" for match in matches)
    return (
        "Here are some things you remember about this user from past conversations. Use them "
        f"only if relevant, and don't mention that you're recalling stored memories:\n{facts}"
    )
