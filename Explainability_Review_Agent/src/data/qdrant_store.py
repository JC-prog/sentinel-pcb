import logging
from typing import List, Dict, Any, Optional
from qdrant_client import QdrantClient
from qdrant_client.http import models

logger = logging.getLogger(__name__)

class DefectVectorStore:
    def __init__(self, collection_name: str = "pcb_defects"):
        # Run Qdrant locally in RAM for prototyping (no server needed)
        self.client = QdrantClient(":memory:")
        self.collection_name = collection_name
        self.vector_size = 512 
        self._ensure_collection()

    def _ensure_collection(self):
        """Checks if the collection exists, creates it, and seeds fake data."""
        if not self.client.collection_exists(self.collection_name):
            logger.info(f"Creating in-memory Qdrant collection '{self.collection_name}'...")
            
            self.client.create_collection(
                collection_name=self.collection_name,
                vectors_config=models.VectorParams(
                    size=self.vector_size, 
                    distance=models.Distance.COSINE
                )
            )
            self._seed_dummy_data()

    def _seed_dummy_data(self):
        """Injects fake historical cases so your agent has 'past context' to reason with."""
        dummy_points = [
            models.PointStruct(
                id=1,
                vector=[0.1] * self.vector_size,
                payload={
                    "component_ref": "U12",
                    "defect_category": "missing part",
                    "root_cause": "Pick & Place nozzle vacuum pressure failure.",
                    "historical_machine_state": "Nozzle pressure drop detected at Sector 4.",
                    "corrective_action": "Replaced P&P nozzle and cleaned pneumatic air filters."
                }
            ),
            models.PointStruct(
                id=2,
                vector=[0.2] * self.vector_size,
                payload={
                    "component_ref": "U12",
                    "defect_category": "shifted",
                    "root_cause": "Solder paste offset / Reflow profile mismatch.",
                    "historical_machine_state": "Zone 3 reflow oven temperature drop by 5 degrees.",
                    "corrective_action": "Adjusted reflow profile and wiped printing stencil."
                }
            )
        ]
        
        self.client.upsert(
            collection_name=self.collection_name, 
            points=dummy_points
        )
        logger.info("Seeded Qdrant memory database with dummy historical cases.")

    def search_similar(self, embedding: List[float], top_k: int = 3, metadata_filter: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """
        Searches the vector database for historically visually similar defects using query_points.
        """
        query_filter = None
        
        if metadata_filter:
            must_conditions = []
            for key, value in metadata_filter.items():
                must_conditions.append(
                    models.FieldCondition(
                        key=key,
                        match=models.MatchValue(value=value)
                    )
                )
            query_filter = models.Filter(must=must_conditions)

        # Fallback if embedding is missing or wrong size
        if not embedding or len(embedding) != self.vector_size:
            embedding = [0.0] * self.vector_size

        # Perform the Vector Search using the updated API
        search_result = self.client.query_points(
            collection_name=self.collection_name,
            query=embedding,
            query_filter=query_filter,
            limit=top_k
        )

        # Format results (query_points returns an object with a .points list)
        results = []
        for hit in search_result.points:
            payload = hit.payload or {}
            payload["score"] = hit.score
            results.append(payload)

        return results
