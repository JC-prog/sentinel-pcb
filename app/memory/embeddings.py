import httpx
from pydantic import SecretStr

from app.chat.schemas import LlmProvider
from app.config.settings import settings
from app.core.memory import EmbeddingService

_OPENAI_EMBEDDINGS_URL = "https://api.openai.com/v1/embeddings"


class OllamaEmbeddingService:
    """Embeds text via a local Ollama server (settings.ollama_base_url) - see
    https://github.com/ollama/ollama/blob/main/docs/api.md#generate-embeddings. Requires
    settings.ollama_embedding_model to be pulled locally (`ollama pull nomic-embed-text` by
    default) - not auto-pulled, see DEVELOPMENT.md for setup."""

    async def embed(self, texts: list[str]) -> list[list[float]]:
        payload = {"model": settings.ollama_embedding_model, "input": texts}
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(f"{settings.ollama_base_url}/api/embed", json=payload)
            if response.status_code != 200:
                raise RuntimeError(
                    f"Ollama embeddings request failed ({response.status_code}): {response.text}"
                )
            data = response.json()
            return list(data["embeddings"])


class OpenAiEmbeddingService:
    """Embeds text via OpenAI's embeddings API using the same bring-your-own key already used
    for chat (never persisted server-side - see ChatStreamRequest.openai_api_key)."""

    def __init__(self, api_key: SecretStr) -> None:
        self._api_key = api_key

    async def embed(self, texts: list[str]) -> list[list[float]]:
        payload = {"model": settings.openai_embedding_model, "input": texts}
        headers = {"Authorization": f"Bearer {self._api_key.get_secret_value()}"}
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(_OPENAI_EMBEDDINGS_URL, json=payload, headers=headers)
            if response.status_code != 200:
                raise RuntimeError(
                    f"OpenAI embeddings request failed ({response.status_code}): {response.text}"
                )
            data = response.json()
            return [item["embedding"] for item in data["data"]]


def get_embedding_service(
    provider: LlmProvider, openai_api_key: SecretStr | None
) -> EmbeddingService:
    if provider == "openai":
        if openai_api_key is None:
            raise ValueError("openai_api_key is required when provider is 'openai'")
        return OpenAiEmbeddingService(api_key=openai_api_key)
    return OllamaEmbeddingService()


def embedding_model_name(provider: LlmProvider) -> str:
    """Used to name the Qdrant collection a given provider's embeddings live in - see
    app/memory/service.py's get_memory_store()."""

    return (
        settings.openai_embedding_model if provider == "openai" else settings.ollama_embedding_model
    )
