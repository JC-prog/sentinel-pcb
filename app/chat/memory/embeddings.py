import httpx

from app.chat.core.memory import EmbeddingService
from app.chat.services.schemas import LlmProvider
from app.shared.config.settings import settings


class OpenAiEmbeddingService:
    """Embeds text via OpenAI's embeddings API using the server-side settings.openai_api_key and
    settings.openai_base_url (OpenAI direct unless overridden) - see app/shared/config/settings.py."""

    async def embed(self, texts: list[str]) -> list[list[float]]:
        payload = {"model": settings.openai_embedding_model, "input": texts}
        headers = {"Authorization": f"Bearer {settings.openai_api_key}"}
        url = f"{settings.openai_base_url.rstrip('/')}/embeddings"
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(url, json=payload, headers=headers)
            if response.status_code != 200:
                raise RuntimeError(
                    f"OpenAI embeddings request failed ({response.status_code}): {response.text}"
                )
            data = response.json()
            return [item["embedding"] for item in data["data"]]


def get_embedding_service(provider: LlmProvider) -> EmbeddingService:
    return OpenAiEmbeddingService()


def embedding_model_name(provider: LlmProvider) -> str:
    """Used to name the Qdrant collection a given provider's embeddings live in - see
    app/chat/memory/service.py's get_memory_store()."""

    return settings.openai_embedding_model
