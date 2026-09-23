"""
Qdrant Vector Database Indexer for Agent 1.
Ingests prepared PCB inspection records (XML telemetry, CSV metadata, and image pairs),
generates vector embeddings, and stores them in local persistent storage (qdrant_db).
"""

from __future__ import annotations

import hashlib
import logging
import os
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels

load_dotenv()
logger = logging.getLogger("QdrantIndexer")

COLLECTION_NAME = "ipc_defect_precedents"
VECTOR_DIM = 1536  # Matches OpenAI text-embedding-3-small


def get_embeddings(texts: List[str], api_key: Optional[str] = None) -> List[List[float]]:
    """Generates embeddings via OpenAI, or falls back to deterministic vectors if offline."""
    key = api_key or os.getenv("OPENAI_API_KEY")
    if key and not key.startswith("sk-proj-your"):
        try:
            from openai import OpenAI
            client = OpenAI(api_key=key)
            resp = client.embeddings.create(
                model="text-embedding-3-small",
                input=texts
            )
            return [item.embedding for item in resp.data]
        except Exception as e:
            logger.warning(f"OpenAI embedding generation failed ({e}). Using deterministic offline vector fallback.")

    # Offline deterministic fallback (pseudo-embeddings based on sha256)
    logger.info("Generating deterministic fallback vectors for local offline storage.")
    fallback_vectors = []
    for text in texts:
        seed = int(hashlib.sha256(text.encode("utf-8")).hexdigest()[:8], 16)
        import random
        rng = random.Random(seed)
        vec = [rng.uniform(-1.0, 1.0) for _ in range(VECTOR_DIM)]
        # Normalize
        norm = sum(x * x for x in vec) ** 0.5
        fallback_vectors.append([x / norm for x in vec])
    return fallback_vectors


def build_sample_document(sample: Dict[str, Any]) -> str:
    """Creates a rich textual summary of the component, telemetry, and defect for embedding."""
    board_id = sample.get("board_id", "UnknownBoard")
    comp_id = sample.get("component_id", "UnknownComponent")
    feat_type = sample.get("feature_type", "Body")
    defect_hint = sample.get("defect_hint") or sample.get("preliminary_defect") or "NoDefect"

    # Extract nested telemetry
    telemetry = sample.get("failed_inspections", {})
    flat_metrics = []
    if isinstance(telemetry, dict):
        for insp_name, data in telemetry.items():
            if isinstance(data, dict):
                for k, v in data.items():
                    flat_metrics.append(f"{k}: {v}")
            else:
                flat_metrics.append(f"{insp_name}: {data}")
    telemetry_str = ", ".join(flat_metrics) if flat_metrics else "Nominal tolerances satisfied"

    doc = (
        f"PCB Inspection Precedent - Board: {board_id}, Component: {comp_id}, Feature: {feat_type}. "
        f"Defect Classification: {defect_hint}. "
        f"Sensor Telemetry & Measurements: {telemetry_str}. "
        f"Visual Image Source: {Path(sample.get('defect_image_path', '')).name}."
    )
    return doc


def populate_qdrant_db(
    samples: List[Dict[str, Any]],
    db_path: str = "qdrant_db",
    collection_name: str = COLLECTION_NAME
) -> int:
    """
    Persists prepared samples into local embedded Qdrant vector database.
    """
    if not samples:
        logger.warning("No samples provided to populate Qdrant.")
        return 0

    target_dir = Path(db_path)
    target_dir.mkdir(parents=True, exist_ok=True)

    # Initialize local persistent client
    client = QdrantClient(path=str(target_dir))

    # Create collection if it does not exist
    existing = [c.name for c in client.get_collections().collections]
    if collection_name not in existing:
        logger.info(f"Creating Qdrant collection '{collection_name}' (dim={VECTOR_DIM}, metric=Cosine)...")
        client.create_collection(
            collection_name=collection_name,
            vectors_config=qmodels.VectorParams(
                size=VECTOR_DIM,
                distance=qmodels.Distance.COSINE
            )
        )

    # Prepare batches
    texts = [build_sample_document(s) for s in samples]
    vectors = get_embeddings(texts)

    points = []
    for i, (sample, doc_text, vec) in enumerate(zip(samples, texts, vectors)):
        # Generate stable UUID for point ID
        sample_key = sample.get("sample_id") or f"{sample.get('board_id')}_{sample.get('component_id')}_{i}"
        point_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, sample_key))

        payload = {
            "sample_id": sample_key,
            "board_id": sample.get("board_id"),
            "component_id": sample.get("component_id"),
            "feature_type": sample.get("feature_type", "Body"),
            "defect_label": sample.get("defect_hint") or sample.get("preliminary_defect"),
            "defect_image_path": sample.get("defect_image_path"),
            "golden_image_path": sample.get("golden_image_path"),
            "measurements": sample.get("failed_inspections", {}),
            "document_text": doc_text
        }

        points.append(
            qmodels.PointStruct(
                id=point_id,
                vector=vec,
                payload=payload
            )
        )

    # Upsert in chunks of 50
    chunk_size = 50
    for idx in range(0, len(points), chunk_size):
        batch = points[idx:idx + chunk_size]
        client.upsert(collection_name=collection_name, points=batch)

    count = client.count(collection_name=collection_name).count
    logger.info(f"Successfully populated Qdrant at '{db_path}'. Total indexed records: {count}")
    client.close()
    return len(points)


if __name__ == "__main__":
    # Test execution on auto-discovered samples
    from main import load_samples
    discovered = load_samples("data/sample_data/dataset.csv", "data/sample_data/inspection.xml", "data/inputs")
    populate_qdrant_db(discovered)
