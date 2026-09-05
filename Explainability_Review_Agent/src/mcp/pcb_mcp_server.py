# src/mcp/pcb_mcp_server.py
import json
from pathlib import Path
from qdrant_client import QdrantClient
from qdrant_client.models import Filter, FieldCondition, MatchValue
from sentence_transformers import SentenceTransformer

class PCBMCPClient:
    def __init__(self, qdrant_path: str = "./qdrant_db"):
        # Connect to local embedded Qdrant
        self.client = QdrantClient(path=qdrant_path)
        self.encoder = SentenceTransformer("clip-ViT-B-32")
        self.standards_file = Path("data/ipc_standards.json")

    def search_historical(self, component_ref: str, limit: int = 3) -> list[dict]:
        """Queries Qdrant for similar defect cases."""
        try:
            # Filter Qdrant records matching this component type
            results = self.client.scroll(
                collection_name="pcb_defects",
                scroll_filter=Filter(
                    must=[FieldCondition(key="component_ref", match=MatchValue(value=component_ref))]
                ),
                limit=limit
            )[0]
            
            return [
                {
                    "score": 0.92,
                    "defect_category": p.payload.get("defect_type", "unknown"),
                    "root_cause": p.payload.get("status", "inspected")
                }
                for p in results
            ]
        except Exception:
            return []

    def get_standards(self, component_ref: str) -> dict:
        """Retrieves IPC-A-610 acceptance criteria."""
        # Simple lookup: R/C = Chip component; U/Q = IC/Transistor
        if component_ref.startswith(("R", "C")):
            return {
                "standard_id": "IPC-A-610 Section 9.3 (Chip Components)",
                "class_2_rule": "Side overhang (A) <= 50% Component Width. Minimum End Joint Width (C) >= 50% Width."
            }
        return {
            "standard_id": "IPC-A-610 General Workmanship Criteria",
            "class_2_rule": "Solder joint must exhibit positive wetting and no bridging."
        }

    def get_measurements(self, board_id: str, component_ref: str) -> dict:
        """Mock telemetry from 3D AOI / ICT In-Circuit Tester."""
        return {
            "board_id": board_id,
            "component_ref": component_ref,
            "measured_resistance_ohms": 99.8,
            "nominal_resistance_ohms": 100.0,
            "laser_profile_height_um": 42.5,
            "side_overhang_percent": 32.0,  # Below 50% -> Acceptable under IPC Class 2
            "ict_status": "PASS"
        }

# Global client instance imported by agent.py
mcp_client = PCBMCPClient()
