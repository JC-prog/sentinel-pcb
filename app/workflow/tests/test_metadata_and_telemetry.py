"""Unit tests for metadata filename parsing and physical telemetry lookup.

Ported from pcb_agentic_inspector. One change: `parse_filename_metadata` is imported from
src/data/filename_metadata.py (ported verbatim from upstream's main.py CLI, not part of this app).

Note on the two telemetry tests: they assert what the ported `measurement_evidence_tool` returns,
including the NOMINAL values it invents when it finds no reading (laser height 42.0, PASS/PASS).
That is kept as found, and documented here because app/workflow/services/agent2_mcp_service.py never
uses those values - it calls the tool only with an inspection XML and discards any `source`
starting with "fallback".
"""

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.data.filename_metadata import parse_filename_metadata
import src.agent2_explainability.mcp.agent2_mcp_server as mcp_server


class TestMetadataAndTelemetry(unittest.TestCase):

    def test_parse_standard_filename_missing_part(self):
        filename = "Board1_C636_Body_06-200036-02_20260824_193317036_MissingPart_3.jpg"
        meta = parse_filename_metadata(filename)
        self.assertEqual(meta["component_ref"], "C636")
        self.assertEqual(meta["board_id"], "06-200036-02")
        self.assertEqual(meta["ground_truth"], "missing part")

    def test_parse_shift_filename(self):
        filename = "Board3_R131_Body_18-010309-AAA-RV3_20260824_100501995_Shift_4.jpg"
        meta = parse_filename_metadata(filename)
        self.assertEqual(meta["component_ref"], "R131")
        self.assertEqual(meta["board_id"], "18-010309-AAA-RV3")
        self.assertEqual(meta["ground_truth"], "shifted")

    def test_parse_golden_no_defect(self):
        filename = "Board2_C101_Body_06-200036-02_20260824_112000000_Golden_1.png"
        meta = parse_filename_metadata(filename)
        self.assertEqual(meta["ground_truth"], "no defect")

    def test_parse_malformed_filename_fallback(self):
        filename = "arbitrary_image_without_tokens.jpg"
        meta = parse_filename_metadata(filename)
        self.assertEqual(meta["ground_truth"], "unknown")
        self.assertEqual(meta["component_ref"], "arbitrary")

    @patch.object(mcp_server, "_load_telemetry")
    def test_measurement_tool_cached_hit(self, mock_load):
        mock_load.return_value = {
            "Board1_C636.jpg": {
                "component_ref": "C636",
                "board_id": "06-200036-02",
                "laser_profile_height_um": 0.5,
                "ict_status": "FAIL",
                "side_overhang_percent": 0.0,
            }
        }
        res = mcp_server.measurement_evidence_tool(
            image_path="inputs/Board1_C636.jpg",
            component_ref="C636",
            board_id="06-200036-02"
        )
        self.assertEqual(res["laser_profile_height_um"], 0.5)
        self.assertEqual(res["ict_status"], "FAIL")

    @patch.object(mcp_server, "_load_telemetry")
    def test_measurement_tool_nominal_fallback(self, mock_load):
        mock_load.return_value = {}  # Empty cache
        res = mcp_server.measurement_evidence_tool(
            image_path="inputs/NonExistent.jpg",
            component_ref="C999",
            board_id="UnknownBoard"
        )
        self.assertEqual(res["aoi_status"], "PASS")
        self.assertEqual(res["laser_profile_height_um"], 42.0)
        self.assertEqual(res["side_overhang_percent"], 2.0)


if __name__ == "__main__":
    unittest.main()