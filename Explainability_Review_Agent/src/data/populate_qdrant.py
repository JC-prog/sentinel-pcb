# src/data/populate_qdrant.py
from qdrant_client import QdrantClient
from qdrant_client.models import PointStruct, VectorParams, Distance
from sentence_transformers import SentenceTransformer
from PIL import Image

client = QdrantClient(url="http://localhost:6333")
encoder = SentenceTransformer("clip-ViT-B-32")  # Or your PCB vision encoder

# 1. Create Collection
client.recreate_collection(
    collection_name="pcb_defects",
    vectors_config=VectorParams(size=512, distance=Distance.COSINE),
)

# 2. Ingest historical defect records
def ingest_defect(point_id: int, image_path: str, component_ref: str, defect_type: str, resolution: str):
    image = Image.open(image_path)
    embedding = encoder.encode(image).tolist()

    client.upsert(
        collection_name="pcb_defects",
        points=[
            PointStruct(
                id=point_id,
                vector=embedding,
                payload={
                    "component_ref": component_ref,
                    "defect_type": defect_type,
                    "root_cause": "Insufficient solder paste volume",
                    "resolution": resolution,
                    "image_url": image_path
                }
            )
        ]
    )

# Example usage:
# ingest_defect(1, "data/samples/c12_tombstone.jpg", "C12", "Tombstoning", "Increase reflow zone time")
