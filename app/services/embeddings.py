"""Embedding provider abstraction for Case Context RAG retrieval
(app.agents.explainability_review.case_context).

Only a local provider is implemented today (settings.embedding_provider == "local") - the
Protocol exists so a hosted provider (Voyage/OpenAI/etc.), and eventually an admin panel to pick
between them, is a config change later, not a rewrite of every call site.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Protocol

from app.settings import settings


class EmbeddingProvider(Protocol):
    def embed(self, text: str) -> list[float]: ...


class LocalEmbeddingProvider:
    """Wraps a local sentence-transformers model - no API key, no external call. First use
    downloads the model (~90MB for the default all-MiniLM-L6-v2) the same way get_detector()
    needed models/*.onnx present; after that it's cached locally.
    """

    def __init__(self, model_name: str) -> None:
        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer(model_name)

    def embed(self, text: str) -> list[float]:
        vector = self._model.encode(text, convert_to_numpy=True)
        return [float(x) for x in vector]


@lru_cache(maxsize=1)
def get_embedding_provider() -> EmbeddingProvider:
    """Process-wide singleton - loading the model is expensive, do it once."""

    if settings.embedding_provider == "local":
        return LocalEmbeddingProvider(settings.embedding_model_name)
    raise ValueError(f"Unknown embedding_provider: {settings.embedding_provider!r}")
