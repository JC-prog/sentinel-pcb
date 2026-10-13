"""Evaluation Script 2: Contradiction Detection & Sensor Grounding Robustness.
Location: evals/eval_contradiction_grounding.py
"""

from __future__ import annotations

import os
import sys
import json
import logging
from pathlib import Path
from typing import Dict, List, Any
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# --- Adapted for this repo (everything else is the teammate's script as sent) --------------------
# app/workflow/ is on sys.path for the drop-in's bare `src....` imports, and the repo root for `app....`
# (the precedent reader uses the app's settings). The images are the teammate's dataset and are not in
# this repo: point AGENT2_EVAL_INPUTS at the folder that holds <board>/Body/Passed/*.jpg.
REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
INPUTS_DIR = Path(os.environ.get("AGENT2_EVAL_INPUTS", REPO_ROOT / "data" / "workflow" / "data" / "inputs"))
OUTPUTS_DIR = REPO_ROOT / "data" / "workflow" / "eval_outputs"
# --------------------------------------------------------------------------------------------------

from src.agent2_explainability.pipeline.review_graph import ReviewState, build_review_graph

agent2_graph = build_review_graph()

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

# Test scenarios testing physics grounding against misleading visual artifacts
CONTRADICTION_BENCHMARK = [
    {
        "test_name": "Solder Discoloration vs. Open-Circuit 0 uF (Missing Part)",
        "component_ref": "C636",
        "board_id": "06-200036-02",
        "sample_image": "inputs/06-200036-02/Body/Passed/Board1_C636_Body_06-200036-02_20260824_193317036_MissingPart_3.jpg",
        "preliminary_defect": "optical presence detected",
        "mock_visual": "Dark rectangular shape detected over solder land, looks like 0402 capacitor body.",
        "mock_telemetry": {
            "laser_profile_height_um": 0.4,
            "measured_value": 0.0,
            "unit": "uF",
            "ict_status": "FAIL",
            "aoi_status": "FAIL",
            "side_overhang_percent": 0.0
        },
        "expected_defect": "missing part",
        "must_pass_self_check": True
    },
    {
        "test_name": "Reflow Shadow vs. Flat Coplanar Seating (Nominal Part)",
        "component_ref": "R102",
        "board_id": "06-200036-02",
        "sample_image": "inputs/06-200036-02/Body/Passed/Board1_C978_Body_06-200036-02_20260824_154737922_WrongPart_13.jpg",
        "preliminary_defect": "tombstone suspected",
        "mock_visual": "Dark shadow band along terminal 1, visual cue indicates possible tombstone lifting.",
        "mock_telemetry": {
            "laser_profile_height_um": 39.0,
            "coplanarity_um": 1.2,
            "measured_value": 10.0,
            "unit": "kOhm",
            "ict_status": "PASS",
            "aoi_status": "PASS",
            "side_overhang_percent": 3.0
        },
        "expected_defect": "no defect",
        "must_pass_self_check": True
    },
    {
        "test_name": "Wetted Fillet Present but 58% Overhang (IPC Class 2 Shifted)",
        "component_ref": "C978",
        "board_id": "06-200036-02",
        "sample_image": "inputs/06-200036-02/Body/Passed/Board1_C978_Body_06-200036-02_20260824_200626908_Shift_4.jpg",
        "preliminary_defect": "misaligned",
        "mock_visual": "Capacitor is skewed diagonally across solder pads with wetting present.",
        "mock_telemetry": {
            "laser_profile_height_um": 42.0,
            "side_overhang_percent": 58.0,
            "measured_value": 0.1,
            "unit": "uF",
            "ict_status": "PASS",
            "aoi_status": "FAIL"
        },
        "expected_defect": "shifted",
        "must_pass_self_check": True
    }
]


def run_contradiction_evaluation():
    print("\n" + "=" * 76)
    print("AGENT 2: CONTRADICTION DETECTION & SENSOR GROUNDING STRESS TEST")
    print("=" * 76)

    passed_tests = 0
    pipeline_mod = "src.agent2_explainability.pipeline.review_graph"

    for idx, test in enumerate(CONTRADICTION_BENCHMARK, 1):
        print(f"\n[Test Case {idx}] {test['test_name']}")
        img_path = str((INPUTS_DIR / test["sample_image"].removeprefix("inputs/")).resolve())

        with patch(f"{pipeline_mod}.visual_evidence_tool", create=True) as mock_vlm, \
             patch(f"{pipeline_mod}.measurement_evidence_tool", create=True) as mock_sensors:

            mock_vlm.return_value = {
                "visual_description": test["mock_visual"],
                "bounding_boxes": [{"box_2d": [100, 100, 200, 200], "label": "component_site"}]
            }
            mock_sensors.return_value = test["mock_telemetry"]

            state: ReviewState = {
                "board_id": test["board_id"],
                "component_ref": test["component_ref"],
                "defect_image_path": img_path,
                "golden_image_path": None,
                "feature_type": "Body",
                "preliminary_defect": test["preliminary_defect"],
                "baseline_confidence": 0.5,
                "aoi_measurements": test["mock_telemetry"],
                "retrieved_precedents": [],
                "telemetry_data": test["mock_telemetry"],
                "visual_evidence": test["mock_visual"],
                "predicted_defect": "unknown",
                "final_confidence": 0.0,
                "diagnosis": "",
                "contradiction_detected": False,
                "self_check_passed": False,
                "ipc_citations": [],
                "errors": []
            }

            result = agent2_graph.invoke(state)

            predicted = result.get("predicted_defect", "unknown").lower()
            self_check = result.get("self_check_passed", False)
            explanation = result.get("diagnosis", "")

            expected = test["expected_defect"]
            defect_match = (predicted == expected)
            self_check_match = (self_check == test["must_pass_self_check"])

            test_success = defect_match and self_check_match
            if test_success:
                passed_tests += 1
                status = "PASSED [MATCH]"
            else:
                status = "FAILED [MISMATCH]"

            print(f"  Evaluation Status:  {status}")
            print(f"  Predicted Defect:   '{predicted}' (Expected: '{expected}')")
            print(f"  Self-Check Passed:  {self_check}")
            print(f"  Diagnosis Snippet:  {explanation[:160]}...")

    score_pct = (passed_tests / len(CONTRADICTION_BENCHMARK)) * 100
    print("\n" + "-" * 76)
    print(f"TOTAL CONTRADICTION PASS RATE: {passed_tests}/{len(CONTRADICTION_BENCHMARK)} ({score_pct:.1f}%)")
    print("-" * 76 + "\n")


if __name__ == "__main__":
    run_contradiction_evaluation()