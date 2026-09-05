from qdrant_client import AsyncQdrantClient, models

from app.core.memory import MemoryMatch, MemoryRecord
from app.settings import settings

_PAYLOAD_USER_ID = "user_id"
_PAYLOAD_TEXT = "text"
_PAYLOAD_SOURCE_CONVERSATION_ID = "source_conversation_id"
_PAYLOAD_CREATED_AT = "created_at"
_PAYLOAD_CATEGORY = "category"


def _to_payload(record: MemoryRecord) -> dict[str, object]:
    return {
        _PAYLOAD_USER_ID: record.user_id,
        _PAYLOAD_TEXT: record.text,
        _PAYLOAD_SOURCE_CONVERSATION_ID: record.source_conversation_id,
        _PAYLOAD_CREATED_AT: record.created_at.isoformat(),
        _PAYLOAD_CATEGORY: record.category,
    }


def _from_scored_point(point: models.ScoredPoint) -> MemoryMatch:
    payload = point.payload or {}
    record = MemoryRecord(
        id=str(point.id),
        user_id=payload[_PAYLOAD_USER_ID],
        text=payload[_PAYLOAD_TEXT],
        source_conversation_id=payload.get(_PAYLOAD_SOURCE_CONVERSATION_ID),
        created_at=payload[_PAYLOAD_CREATED_AT],
        category=payload.get(_PAYLOAD_CATEGORY),
    )
    return MemoryMatch(record=record, score=point.score)


class QdrantMemoryStore:
    """MemoryStore backed by Qdrant (settings.qdrant_url) - see app/core/memory.py for why
    nothing outside this file should depend on Qdrant specifically."""

    def __init__(self, client: AsyncQdrantClient, collection_name: str) -> None:
        self._client = client
        self._collection_name = collection_name

    async def ensure_collection(self, *, vector_size: int) -> None:
        if await self._client.collection_exists(self._collection_name):
            return
        await self._client.create_collection(
            self._collection_name,
            vectors_config=models.VectorParams(size=vector_size, distance=models.Distance.COSINE),
        )
        # Indexed so search()'s per-user filter is efficient and, more importantly, always
        # applied - see this method's caller in app/memory/service.py for why an unindexed/
        # unfiltered search here would be a cross-user data leak, not just a slow query.
        await self._client.create_payload_index(
            self._collection_name,
            field_name=_PAYLOAD_USER_ID,
            field_schema=models.PayloadSchemaType.KEYWORD,
        )

    async def upsert(self, record: MemoryRecord, embedding: list[float]) -> None:
        await self.ensure_collection(vector_size=len(embedding))
        await self._client.upsert(
            self._collection_name,
            points=[
                models.PointStruct(id=record.id, vector=embedding, payload=_to_payload(record))
            ],
        )

    async def search(
        self, user_id: str, query_embedding: list[float], *, top_k: int
    ) -> list[MemoryMatch]:
        if not await self._client.collection_exists(self._collection_name):
            return []  # nothing has ever been written under this embedding model yet.
        response = await self._client.query_points(
            self._collection_name,
            query=query_embedding,
            query_filter=models.Filter(
                must=[
                    models.FieldCondition(
                        key=_PAYLOAD_USER_ID, match=models.MatchValue(value=user_id)
                    )
                ]
            ),
            limit=top_k,
        )
        return [_from_scored_point(point) for point in response.points]


_client: AsyncQdrantClient | None = None


def get_qdrant_client() -> AsyncQdrantClient:
    global _client
    if _client is None:
        _client = AsyncQdrantClient(url=settings.qdrant_url)
    return _client
