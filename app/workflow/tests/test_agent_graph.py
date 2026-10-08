"""Integration unit tests for LangGraph state transitions and contradiction handling."""

import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Mock environment key
os.environ["OPENAI_API_KEY"] = "sk-test-key-00000000000000000000"

from src.agent2_explainability.pipeline.review_graph import review_pipeline, ReviewState


class TestAgentGraphWorkflow(unittest.TestCase):

    @patch("src.agent2_explainability.pipeline.review_graph.ChatOpenAI")
    def test_full_graph_contradiction_resolution(self, mock_chat_openai):
        # Setup GPT-4o grounding node response:
        # Resolves optical discoloration vs physical telemetry contradiction
        gpt4o_output = {
            "predicted_defect": "missing part",
            "confidence": 0.98,
            "contradiction_detected": True,
            "self_check_passed": True,
            "diagnosis": "Contradiction resolved: Laser height is 0.4 um and open circuit confirms missing component, overriding optical discoloration.",
            "ipc_citations": ["IPC-A-610 Section 8.3.1"]
        }

        mock_llm_instance = MagicMock()
        mock_response_message = MagicMock()
        mock_response_message.content = json.dumps(gpt4o_output)
        mock_llm_instance.invoke.return_value = mock_response_message
        mock_chat_openai.return_value = mock_llm_instance

        # Construct initial state with conflicting signals:
        # Optical detector saw 'possible body', but physical telemetry indicates missing part
        initial_state: ReviewState = {
            "board_id": "06-200036-02",
            "component_ref": "C636",
            "defect_image_path": "sample_data/Board1_C636_Body.jpg",
            "golden_image_path": None,
            "feature_type": "Body",
            "preliminary_defect": "Shift",
            "baseline_confidence": 0.65,
            "aoi_measurements": {
                "laser_profile_height_um": 0.4,
                "measured_value": 0.0,
                "unit": "uF",
                "ict_status": "FAIL",
                "aoi_status": "FAIL",
                "side_overhang_percent": 0.0
            },
            "retrieved_precedents": [],
            "telemetry_data": {},
            # Pre-populated visual observation representing optical model output
            "visual_evidence": "Dark rectangle detected, possibly discolored component body or shadow.",
            "predicted_defect": "",
            "final_confidence": 0.0,
            "diagnosis": "",
            "contradiction_detected": False,
            "self_check_passed": False,
            "ipc_citations": [],
            "errors": []
        }

        # Execute compiled LangGraph end-to-end
        final_state = review_pipeline.invoke(initial_state)

        # Assertions on state transitions and final grounding
        self.assertEqual(final_state["predicted_defect"], "missing part")
        self.assertTrue(final_state["self_check_passed"])
        self.assertTrue(final_state["contradiction_detected"])
        self.assertAlmostEqual(final_state["final_confidence"], 0.98)
        self.assertIn("Contradiction resolved", final_state["diagnosis"])
        self.assertEqual(final_state["telemetry_data"]["laser_profile_height_um"], 0.4)
        self.assertEqual(final_state["telemetry_data"]["ict_status"], "FAIL")


if __name__ == "__main__":
    unittest.main()