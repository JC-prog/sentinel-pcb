"""Seed the local embedded Qdrant collection (settings.explainability_agent_data_dir/qdrant_db,
data/images/qdrant_db by default) with CLIP embeddings of historical PCB defect images, so
mcp_client.py's PCBMCPClient.search_historical() has real records to filter over for the "similar
historical cases" evidence node.

Ported from Kenny's Explainability_Review_Agent/src/data/populate_qdrant.py - same metadata
parsing and batched-embedding approach, adapted to resolve paths via settings (matching
graph.py's DATA_DIR and app.settings.chat_upload_dir's convention) instead of a hardcoded
Windows-relative path, and to use the project's own `qdrant-client`/`sentence-transformers`
dependencies directly (no separate requirements.txt / tqdm dependency - progress is just logged
every batch).

Usage (run as a module, so `app`/`scripts` resolve on sys.path):
    uv run python -m scripts.explainability_agent.populate_qdrant

Uses the same clip-ViT-B-32 encoder as mcp_client.py, so embeddings agree at query time.
"""

import uuid
from pathlib import Path
from typing import Any

from PIL import Image
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams
from sentence_transformers import SentenceTransformer

from app.settings import settings

DATA_DIR = Path(settings.explainability_agent_data_dir)
INPUT_DIR = DATA_DIR / "inputs"
QDRANT_DB_DIR = DATA_DIR / "qdrant_db"

COLLECTION_NAME = "pcb_defects"
VECTOR_SIZE = 512
BATCH_SIZE = 32

_VALID_IMAGE_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".bmp"})


def parse_metadata(file_path: Path) -> dict[str, Any]:
    """Extracts structured metadata from filename and directory tree, e.g.
    'Board1_R131_Body_18-010309-AAA-RV3_20260101_1200_Tombstone_01.jpg'."""

    parts = file_path.stem.split("_")

    if len(parts) >= 8:
        board_no, component_ref, part_zone, assembly_pn = parts[0], parts[1], parts[2], parts[3]
        date_str, time_str, defect_type, suffix = parts[4], parts[5], parts[6], parts[7]
    else:
        board_no = parts[0] if len(parts) > 0 else "Unknown"
        component_ref = parts[1] if len(parts) > 1 else "Unknown"
        part_zone = parts[2] if len(parts) > 2 else "Unknown"
        assembly_pn = file_path.parents[2].name if len(file_path.parents) > 2 else "Unknown"
        date_str, time_str, defect_type, suffix = "Unknown", "Unknown", "Unknown", "Unknown"

    return {
        "board_no": board_no,
        "component_ref": component_ref,
        "part_zone": part_zone,
        "assembly_pn": assembly_pn,
        "inspection_date": date_str,
        "inspection_time": time_str,
        "defect_type": defect_type,
        "index": suffix,
        "status": file_path.parent.name,
        "file_name": file_path.name,
        "image_path": str(file_path.resolve()),
    }


def _flush_batch(
    client: QdrantClient,
    encoder: SentenceTransformer,
    images: list[Image.Image],
    payloads: list[dict[str, Any]],
    ids: list[str],
) -> None:
    embeddings = encoder.encode(images, batch_size=len(images), show_progress_bar=False).tolist()
    points = [
        PointStruct(id=p_id, vector=vec, payload=payload)
        for p_id, vec, payload in zip(ids, embeddings, payloads, strict=True)
    ]
    client.upsert(collection_name=COLLECTION_NAME, points=points)


def ingest_all_images(input_dir: Path = INPUT_DIR, qdrant_db_dir: Path = QDRANT_DB_DIR) -> None:
    client = QdrantClient(path=str(qdrant_db_dir))
    encoder = SentenceTransformer("clip-ViT-B-32")

    if not client.collection_exists(collection_name=COLLECTION_NAME):
        client.create_collection(
            collection_name=COLLECTION_NAME,
            vectors_config=VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE),
        )
        print(f"Collection '{COLLECTION_NAME}' created.")
    else:
        print(f"Collection '{COLLECTION_NAME}' already exists.")

    all_files = [p for p in input_dir.rglob("*") if p.suffix.lower() in _VALID_IMAGE_EXTENSIONS]
    print(f"Found {len(all_files)} images to ingest.")

    batch_images: list[Image.Image] = []
    batch_payloads: list[dict[str, Any]] = []
    batch_ids: list[str] = []
    processed = 0

    for file_path in all_files:
        try:
            img = Image.open(file_path).convert("RGB")
            payload = parse_metadata(file_path)
            point_id = str(uuid.uuid5(uuid.NAMESPACE_URL, str(file_path.resolve())))

            batch_images.append(img)
            batch_payloads.append(payload)
            batch_ids.append(point_id)

            if len(batch_images) >= BATCH_SIZE:
                _flush_batch(client, encoder, batch_images, batch_payloads, batch_ids)
                processed += len(batch_images)
                print(f"Processed {processed}/{len(all_files)} images...")
                batch_images.clear()
                batch_payloads.clear()
                batch_ids.clear()
        except Exception as exc:  # noqa: BLE001 - skip unreadable/malformed images, keep ingesting
            print(f"Error processing {file_path}: {exc}")

    if batch_images:
        _flush_batch(client, encoder, batch_images, batch_payloads, batch_ids)
        processed += len(batch_images)

    print(f"Ingestion complete! Processed {processed}/{len(all_files)} images.")


if __name__ == "__main__":
    ingest_all_images()
