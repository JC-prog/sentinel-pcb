import os
import logging
from typing import List, Dict, Any, Optional
from qdrant_client import QdrantClient
from qdrant_client.http import models

logger = logging.getLogger(__name__)

# Default local storage folder (must match the folder used in populate_qdrant.py)
DEFAULT_STORAGE_PATH = os.path.join(os.path.dirname(__file__), "qdrant_local_db")

class DefectVectorStore:
    def __init__(
        self,
        collection_name: str = "pcb_defects",
        vector_size: int = 512,
        storage_path: Optional[str] = None
    ):
        """
        Connects to a local embedded Qdrant database stored on disk (no Docker required).
        """
        self.collection_name = collection_name
        self.vector_size = vector_size
        
        # Resolve storage directory
        self.storage_path = storage_path or os.getenv("QDRANT_STORAGE_PATH", DEFAULT_STORAGE_PATH)
        
        logger.info(f"Connecting to local Qdrant database at: {self.storage_path}")
        
        # Load local disk-based Qdrant
        self.client = QdrantClient(path=self.storage_path)

    def search_similar(
        self,
        embedding: List[float],
        top_k: int = 3,
        metadata_filter: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """
        Queries the 9,999+ populated defect records for visually similar items.
        """
        if not embedding or len(embedding) != self.vector_size:
            raise ValueError(f"Query embedding must have length {self.vector_size}, got {len(embedding) if embedding else 0}")

        query_filter = None
        if metadata_filter:
            must_conditions = [
                models.FieldCondition(
                    key=k,
                    match=models.MatchValue(value=v)
                )
                for k, v in metadata_filter.items() if v is not None
            ]
            if must_conditions:
                query_filter = models.Filter(must=must_conditions)

        # Vector search query against local database
        search_result = self.client.query_points(
            collection_name=self.collection_name,
            query=embedding,
            query_filter=query_filter,
            limit=top_k
        )

        results = []
        for hit in search_result.points:
            payload = hit.payload or {}
            payload["similarity_score"] = hit.score
            payload["point_id"] = hit.id
            results.append(payload)

        return results

    def close(self):
        """Cleanly closes local database storage locks."""
        if hasattr(self, 'client') and self.client:
            self.client.close()
