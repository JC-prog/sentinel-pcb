import os
import uuid
from pathlib import Path
from PIL import Image
from tqdm import tqdm
from qdrant_client import QdrantClient
from qdrant_client.models import PointStruct, VectorParams, Distance
from sentence_transformers import SentenceTransformer

# ----------------------------------------------------
# Configuration
# ----------------------------------------------------
INPUT_DIR = r"..\..\inputs"
COLLECTION_NAME = "pcb_defects"
BATCH_SIZE = 32

client = QdrantClient(url="http://localhost:6333")
encoder = SentenceTransformer("clip-ViT-B-32")

# ----------------------------------------------------
# 1. Initialize Collection
# ----------------------------------------------------
if not client.collection_exists(collection_name=COLLECTION_NAME):
    client.create_collection(
        collection_name=COLLECTION_NAME,
        vectors_config=VectorParams(size=512, distance=Distance.COSINE),
    )
    print(f"Collection '{COLLECTION_NAME}' created.")
else:
    print(f"Collection '{COLLECTION_NAME}' already exists.")


# ----------------------------------------------------
# 2. Metadata Extractor
# ----------------------------------------------------
def parse_metadata(file_path: Path) -> dict:
    """Extracts structured metadata from filename and directory tree."""
    stem = file_path.stem  # Filename without extension
    parts = stem.split("_")

    if len(parts) >= 8:
        board_no = parts[0]
        component_ref = parts[1]
        part_zone = parts[2]
        assembly_pn = parts[3]
        date_str = parts[4]
        time_str = parts[5]
        defect_type = parts[6]
        suffix = parts[7]
    else:
        # Fallback if filename structure differs
        board_no = parts[0] if len(parts) > 0 else "Unknown"
        component_ref = parts[1] if len(parts) > 1 else "Unknown"
        part_zone = parts[2] if len(parts) > 2 else "Unknown"
        assembly_pn = file_path.parents[2].name  # fallback from folder
        date_str = "Unknown"
        time_str = "Unknown"
        defect_type = "Unknown"
        suffix = "Unknown"

    folder_status = file_path.parent.name  # 'Passed', 'Golden', etc.

    return {
        "board_no": board_no,
        "component_ref": component_ref,
        "part_zone": part_zone,
        "assembly_pn": assembly_pn,
        "inspection_date": date_str,
        "inspection_time": time_str,
        "defect_type": defect_type,
        "index": suffix,
        "status": folder_status,
        "file_name": file_path.name,
        "image_path": str(file_path.resolve()),
    }


# ----------------------------------------------------
# 3. Ingest Images in Batches
# ----------------------------------------------------
def ingest_all_images(input_dir: str):
    root_path = Path(input_dir)
    image_extensions = {".jpg", ".jpeg", ".png", ".bmp"}
    
    # Collect all image paths recursively
    all_files = [p for p in root_path.rglob("*") if p.suffix.lower() in image_extensions]
    print(f"Found {len(all_files)} images to ingest.")

    batch_images = []
    batch_payloads = []
    batch_ids = []

    for file_path in tqdm(all_files, desc="Processing images"):
        try:
            # Read and verify image
            img = Image.open(file_path).convert("RGB")
            payload = parse_metadata(file_path)

            # Deterministic UUID based on path
            point_id = str(uuid.uuid5(uuid.NAMESPACE_URL, str(file_path.resolve())))

            batch_images.append(img)
            batch_payloads.append(payload)
            batch_ids.append(point_id)

            # Once batch size is reached, compute embeddings and upsert
            if len(batch_images) >= BATCH_SIZE:
                embeddings = encoder.encode(batch_images, batch_size=BATCH_SIZE, show_progress_bar=False).tolist()
                
                points = [
                    PointStruct(id=p_id, vector=vec, payload=p_load)
                    for p_id, vec, p_load in zip(batch_ids, embeddings, batch_payloads)
                ]
                client.upsert(collection_name=COLLECTION_NAME, points=points)

                batch_images.clear()
                batch_payloads.clear()
                batch_ids.clear()

        except Exception as e:
            print(f"Error processing {file_path}: {e}")

    # Process remaining images
    if batch_images:
        embeddings = encoder.encode(batch_images, batch_size=len(batch_images), show_progress_bar=False).tolist()
        points = [
            PointStruct(id=p_id, vector=vec, payload=p_load)
            for p_id, vec, p_load in zip(batch_ids, embeddings, batch_payloads)
        ]
        client.upsert(collection_name=COLLECTION_NAME, points=points)

    print("Ingestion complete!")


if __name__ == "__main__":
    ingest_all_images(INPUT_DIR)
