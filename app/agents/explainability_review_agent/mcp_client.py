"""Ported from Kenny's Explainability_Review_Agent/src/mcp/pcb_mcp_server.py. Despite the name
("MCP server"/"MCP client"), this doesn't use the Model Context Protocol package at all - it's a
plain Python class Kenny's agent.py calls directly. Kept as PCBMCPClient (not renamed) to match
the rest of the ported code and any external references.

Known gaps, faithfully preserved rather than fixed here (see DEVELOPMENT.md):
- get_standards() and get_measurements() are hardcoded placeholder returns - they don't read the
  IPC JSON file (settings.explainability_agent_data_dir/ipc_standards/ipc_a_610_chip_components.json,
  data/images/ipc_standards/... by default) or the telemetry JSON that
  scripts/explainability_agent/generate_telemetry.py produces, despite both existing for that
  purpose.
- search_historical() does a Qdrant metadata filter (scroll + FieldCondition), not embedding
  similarity search - the SentenceTransformer loaded below is never actually queried by it. It's
  still required here because scripts/explainability_agent/populate_qdrant.py uses an identical
  model to embed images when seeding the collection, and the two need to agree.
"""

from pathlib import Path
from typing import Any

from qdrant_client import QdrantClient
from qdrant_client.models import FieldCondition, Filter, MatchValue
from sentence_transformers import SentenceTransformer


class PCBMCPClient:
    def __init__(self, qdrant_path: str) -> None:
        # Connect to local embedded Qdrant - path is resolved by the caller (see graph.py),
        # relative to this package's data directory rather than cwd or a hardcoded path.
        self.client = QdrantClient(path=qdrant_path)
        self.encoder = SentenceTransformer("clip-ViT-B-32")
        self.standards_file = Path("data/ipc_standards.json")

    def search_historical(self, component_ref: str, limit: int = 3) -> list[dict[str, Any]]:
        """Queries Qdrant for similar defect cases."""
        try:
            results = self.client.scroll(
                collection_name="pcb_defects",
                scroll_filter=Filter(
                    must=[
                        FieldCondition(key="component_ref", match=MatchValue(value=component_ref))
                    ]
                ),
                limit=limit,
            )[0]

            return [
                {
                    "score": 0.92,
                    "defect_category": p.payload.get("defect_type", "unknown")
                    if p.payload
                    else "unknown",
                    "root_cause": p.payload.get("status", "inspected")
                    if p.payload
                    else "inspected",
                }
                for p in results
            ]
        except Exception:  # noqa: BLE001 - degrade to "no history found" rather than fail the pipeline
            return []

    def get_standards(self, component_ref: str) -> dict[str, str]:
        """Retrieves IPC-A-610 acceptance criteria."""
        if component_ref.startswith(("R", "C")):
            return {
                "standard_id": "IPC-A-610 Section 9.3 (Chip Components)",
                "class_2_rule": "Side overhang (A) <= 50% Component Width. Minimum End Joint Width (C) >= 50% Width.",
            }
        return {
            "standard_id": "IPC-A-610 General Workmanship Criteria",
            "class_2_rule": "Solder joint must exhibit positive wetting and no bridging.",
        }

    def get_measurements(self, board_id: str, component_ref: str) -> dict[str, Any]:
        """Mock telemetry from 3D AOI / ICT In-Circuit Tester."""
        return {
            "board_id": board_id,
            "component_ref": component_ref,
            "measured_resistance_ohms": 99.8,
            "nominal_resistance_ohms": 100.0,
            "laser_profile_height_um": 42.5,
            "side_overhang_percent": 32.0,
            "ict_status": "PASS",
        }
