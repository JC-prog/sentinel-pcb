"""Pure interfaces for long-term (cross-conversation) memory - no framework/IO imports, same
reasoning as app/core/chat.py. Concrete implementations live in app/memory/; that package's
service.py holds the get_memory_store()/get_embedding_service() factories, mirroring
app/chat/service.py's get_chat_service().

The production vector-store choice is still open (see the project's vector-store-choice note) -
everything outside app/memory/qdrant_store.py depends only on MemoryStore, so swapping the
backing store later touches one implementation file, not this interface or any caller.
"""

from datetime import datetime
from typing import Protocol

from pydantic import BaseModel


class MemoryRecord(BaseModel):
    id: str
    user_id: str
    text: str
    source_conversation_id: str | None
    created_at: datetime
    category: str | None = None


class MemoryMatch(BaseModel):
    record: MemoryRecord
    score: float


class MemoryStore(Protocol):
    async def upsert(self, record: MemoryRecord, embedding: list[float]) -> None: ...

    async def search(
        self, user_id: str, query_embedding: list[float], *, top_k: int
    ) -> list[MemoryMatch]: ...


class EmbeddingService(Protocol):
    async def embed(self, texts: list[str]) -> list[list[float]]: ...
