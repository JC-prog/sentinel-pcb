# src/data/qdrant_store.py
#
# Ported from pcb_agentic_inspector and ADAPTED (see app/workflow/INTEGRATION_NOTES.md):
#   - it used a local embedded Qdrant (`qdrant_db/`); it now reads this app's Docker Qdrant
#     (settings.qdrant_url), the same container chat's memory and the run store use;
#   - it embedded with FastEmbed (BAAI/bge-small-en-v1.5, a ~130 MB model download and a dependency
#     that was missing from the upstream requirements); it now embeds with the app's own OpenAI
#     embedding model, the same call chat's memory makes;
#   - it never invents a vector: if an embedding cannot be made the search returns nothing, where
#     data/qdrant_indexer.py's hash-vector fallback would have produced meaningless "matches";
#   - hits under a minimum score are dropped, so an unrelated chunk is never cited as a precedent.
# The function names and the shape of a hit (score, text, source, page) are upstream's.
#
# What it reads is `pcb_standards_precedents`: chunks of IPC standard documents. Nothing upstream
# ever filled it; app/workflow/services/ipc_ingest.py does, from files you supply.
import logging
from typing import Any, Dict, List

import httpx
from qdrant_client import QdrantClient

from app.shared.config.settings import settings

logger = logging.getLogger(__name__)

COLLECTION_NAME = "pcb_standards_precedents"
VECTOR_DIM = 1536  # settings.openai_embedding_model = text-embedding-3-small

# Cached instance
_client = None


def get_qdrant_client() -> QdrantClient:
    global _client
    if _client is None:
        _client = QdrantClient(url=settings.qdrant_url, timeout=10)
    return _client


def embed_texts(texts: List[str]) -> List[List[float]]:
    """Embeddings for `texts` from the app's OpenAI embedding model. Raises on any failure - a caller
    must never store or search with a made-up vector."""
    api_key = settings.orchestrator_api_key or settings.openai_api_key
    if not api_key:
        raise RuntimeError("no OpenAI API key is configured")
    response = httpx.post(
        f"{settings.openai_base_url.rstrip('/')}/embeddings",
        json={"model": settings.openai_embedding_model, "input": texts},
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=60.0,
    )
    if response.status_code != 200:
        raise RuntimeError(f"embeddings request failed ({response.status_code})")
    return [item["embedding"] for item in response.json()["data"]]


def query_defect_precedents(query_text: str, top_k: int = 3) -> List[Dict[str, Any]]:
    """Runs a semantic vector search over IPC standards and defect documents."""
    min_score = settings.ipc_precedent_min_score
    try:
        query_vector = embed_texts([query_text])[0]
    except Exception as exc:
        logger.warning(f"Precedent search skipped, no embedding: {exc}")
        return []

    client = get_qdrant_client()
    if not client.collection_exists(COLLECTION_NAME):
        logger.warning(f"Collection {COLLECTION_NAME} does not exist.")
        return []

    # Use query_points (modern qdrant-client API)
    response = client.query_points(
        collection_name=COLLECTION_NAME,
        query=query_vector,
        limit=top_k,
        with_payload=True
    )

    results = []
    for hit in response.points:
        if float(hit.score) < min_score:
            continue
        payload = hit.payload or {}
        results.append({
            "score": round(float(hit.score), 4),
            "text": payload.get("text", ""),
            "source": payload.get("source_file", "unknown"),
            "page": payload.get("page", None)
        })

    return results
