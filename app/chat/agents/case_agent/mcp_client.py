"""Ported from Kenny's Explainability_Review_Agent/src/mcp/pcb_mcp_server.py. Despite the name
("MCP server"/"MCP client"), this doesn't use the Model Context Protocol package at all - it's a
plain Python class Kenny's agent.py calls directly. Kept as PCBMCPClient (not renamed) to match
the rest of the ported code and any external references.

get_standards() remains the hardcoded placeholder it always was - it doesn't read the IPC JSON
file (settings.explainability_agent_data_dir/ipc_standards/ipc_a_610_chip_components.json,
data/images/ipc_standards/... by default) despite it existing for that purpose. Unlike
get_measurements() and search_historical() below, pcb_agentic_inspector's Agent 2 has no better
version of this to port from either - its own precedent retrieval is equally hardcoded.

get_measurements() now reads a case's real inspection-XML measurements when they're available
(reusing app.chat.services.xml_measurements' find_failed_feature/
extract_all_failed_measurements - non-trivial XML-walking logic, not the kind of small duplication
this repo's "three similar lines" convention condones), flattening them into the same telemetry
shape pcb_agentic_inspector's Agent 2 extract_telemetry_node produces via key-aliasing. Falls back
to the original hardcoded mock only when no XML is available at all.

search_historical() now does a real CLIP embedding similarity search against Qdrant (the encoder
below was already loaded but never queried) instead of a metadata-only scroll+filter -
scripts/explainability_agent/populate_qdrant.py already seeds the `pcb_defects` collection with
real embeddings using the same clip-ViT-B-32 model, so the two agree at query time.
"""

from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from PIL import Image
from qdrant_client import QdrantClient
from qdrant_client.models import FieldCondition, Filter, MatchValue
from sentence_transformers import SentenceTransformer

from app.chat.services.xml_measurements import (
    extract_all_failed_measurements,
    find_failed_feature,
    validate_measurements,
)

# "Nominal" defaults - the same values the original hardcoded mock always returned - used when a
# telemetry-worthy measurement can't be found by name in the attached XML either.
_DEFAULT_LASER_PROFILE_HEIGHT_UM = 42.5
_DEFAULT_SIDE_OVERHANG_PERCENT = 32.0


def _find_measurement_value(
    measurements_by_inspection: dict[str, dict[str, dict[str, str]]], *, name_contains: str
) -> float | None:
    """Best-effort key-aliasing over heterogeneous AOI machine measurement names, mirroring
    pcb_agentic_inspector's extract_telemetry_node - looks for any measurement whose name contains
    the given substring (case-insensitive) and returns its first parseable numeric attribute."""

    needle = name_contains.lower()
    for measurements in measurements_by_inspection.values():
        for measurement_name, attrs in measurements.items():
            if needle not in measurement_name.lower():
                continue
            for key in ("Value", "HeightAboveLeadPlane"):
                raw = attrs.get(key)
                if raw is None:
                    continue
                try:
                    return float(raw)
                except (TypeError, ValueError):
                    continue
    return None


def _flatten_xml_measurements_to_telemetry(
    board_id: str,
    component_ref: str,
    measurements_by_inspection: dict[str, dict[str, dict[str, str]]],
) -> dict[str, Any]:
    validation = validate_measurements(measurements_by_inspection)
    laser_height = _find_measurement_value(measurements_by_inspection, name_contains="height")
    overhang = _find_measurement_value(measurements_by_inspection, name_contains="overhang")
    return {
        "board_id": board_id,
        "component_ref": component_ref,
        "laser_profile_height_um": (
            laser_height if laser_height is not None else _DEFAULT_LASER_PROFILE_HEIGHT_UM
        ),
        "side_overhang_percent": (
            overhang if overhang is not None else _DEFAULT_SIDE_OVERHANG_PERCENT
        ),
        "ict_status": "PASS" if validation["valid"] else "FAIL",
    }


class PCBMCPClient:
    def __init__(self, qdrant_path: str) -> None:
        # Connect to local embedded Qdrant - path is resolved by the caller (see graph.py),
        # relative to this package's data directory rather than cwd or a hardcoded path.
        self.client = QdrantClient(path=qdrant_path)
        self.encoder = SentenceTransformer("clip-ViT-B-32")
        self.standards_file = Path("data/ipc_standards.json")

    def search_historical(
        self, image: Image.Image, component_ref: str | None = None, limit: int = 3
    ) -> list[dict[str, Any]]:
        """Embeds the case image and queries Qdrant's `pcb_defects` collection for visually
        similar historical defects, optionally narrowed to the same component_ref. Degrades to "no
        history found" on any failure (collection missing, Qdrant unavailable) rather than failing
        the pipeline - same convention as the rest of this class."""

        try:
            vector = self.encoder.encode(image).tolist()
            query_filter = (
                Filter(
                    must=[FieldCondition(key="component_ref", match=MatchValue(value=component_ref))]
                )
                if component_ref
                else None
            )
            results = self.client.query_points(
                collection_name="pcb_defects",
                query=vector,
                query_filter=query_filter,
                limit=limit,
            ).points

            return [
                {
                    "score": point.score,
                    "defect_category": (point.payload or {}).get("defect_type", "unknown"),
                    "root_cause": (point.payload or {}).get("status", "inspected"),
                }
                for point in results
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

    def get_measurements(
        self,
        board_id: str,
        component_ref: str,
        *,
        inspection_xml_bytes: bytes | None = None,
        package: str | None = None,
        feature: str | None = None,
    ) -> dict[str, Any]:
        """Real AOI/ICT telemetry from an attached inspection XML when available; otherwise the
        original hardcoded mock ("Mock telemetry from 3D AOI / ICT In-Circuit Tester")."""

        if inspection_xml_bytes is not None:
            try:
                root = ET.fromstring(inspection_xml_bytes)
                feature_xml = find_failed_feature(
                    root,
                    board_id=board_id,
                    component_ref=component_ref,
                    package=package,
                    feature=feature,
                )
                if feature_xml is not None:
                    measurements = extract_all_failed_measurements(feature_xml)
                    return _flatten_xml_measurements_to_telemetry(
                        board_id, component_ref, measurements
                    )
            except ET.ParseError:
                pass

        return {
            "board_id": board_id,
            "component_ref": component_ref,
            "measured_resistance_ohms": 99.8,
            "nominal_resistance_ohms": 100.0,
            "laser_profile_height_um": _DEFAULT_LASER_PROFILE_HEIGHT_UM,
            "side_overhang_percent": _DEFAULT_SIDE_OVERHANG_PERCENT,
            "ict_status": "PASS",
        }
