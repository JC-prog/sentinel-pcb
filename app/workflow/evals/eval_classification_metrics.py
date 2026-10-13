"""Evaluation Script 1: Multi-Class Accuracy & Confusion Matrix across inputs/.
Location: evals/eval_classification_metrics.py
"""

from __future__ import annotations

import os
import sys
import json
import logging
import argparse
from pathlib import Path
from collections import defaultdict
from typing import Dict, List, Any, Optional

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

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("eval_metrics")


TAXONOMY = [
    "missing part",
    "shifted",
    "foreign material",
    "tombstone",
    "solder insufficient",
    "wrong part",
    "no defect"
]


def parse_filename_meta(defect_path: Path) -> Dict[str, Any]:
    """Extracts board, ref, ground truth, and pairs with golden image."""
    stem = defect_path.stem
    tokens = stem.split("_")
    
    meta = {
        "board_id": defect_path.parents[2].name if len(defect_path.parents) >= 3 else "Unknown",
        "component_ref": tokens[1] if len(tokens) > 1 else "Unknown",
        "ground_truth": "unknown",
        "feature_type": "Body"
    }

    raw = stem.lower()
    if "missing" in raw:
        meta["ground_truth"] = "missing part"
    elif "shift" in raw:
        meta["ground_truth"] = "shifted"
    elif "wrong" in raw:
        meta["ground_truth"] = "wrong part"
    elif "foreign" in raw or "debris" in raw:
        meta["ground_truth"] = "foreign material"
    elif "tombstone" in raw:
        meta["ground_truth"] = "tombstone"
    elif "solder" in raw:
        meta["ground_truth"] = "solder insufficient"
    elif "golden" in raw or "pass" in raw:
        meta["ground_truth"] = "no defect"

    # Search for matching Golden reference image
    golden_dir = defect_path.parents[1] / "Golden"
    golden_path = None
    if golden_dir.exists():
        # Match by component prefix: Board1_C636_Body_..._Golden.jpg
        prefix = f"{tokens[0]}_{tokens[1]}" if len(tokens) >= 2 else tokens[0]
        for g in golden_dir.glob("*.jpg"):
            if prefix.lower() in g.name.lower():
                golden_path = str(g.resolve())
                break

    meta["golden_image_path"] = golden_path
    return meta


def discover_input_cases(inputs_root: Path, limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """Scans all */Body/Passed/*.jpg files across all board subfolders."""
    cases = []
    if not inputs_root.exists():
        logger.error(f"Inputs directory not found: {inputs_root}")
        return cases

    for defect_file in inputs_root.glob("**/Passed/*.*"):
        if defect_file.suffix.lower() in {".jpg", ".jpeg", ".png"}:
            meta = parse_filename_meta(defect_file)
            cases.append({
                "image_path": str(defect_file.resolve()),
                "golden_image_path": meta["golden_image_path"],
                "board_id": meta["board_id"],
                "component_ref": meta["component_ref"],
                "ground_truth": meta["ground_truth"],
                "preliminary_defect": meta["ground_truth"]
            })

    logger.info(f"Discovered {len(cases)} total defect image cases in {inputs_root}")
    if limit and limit > 0:
        cases = cases[:limit]
        logger.info(f"Subsetting to first {limit} cases.")
    return cases


def run_classification_eval(test_cases: List[Dict[str, Any]]) -> Dict[str, Any]:
    confusion_matrix = defaultdict(lambda: defaultdict(int))
    total_samples = len(test_cases)
    correct_samples = 0
    predictions = []

    logger.info(f"Evaluating {total_samples} cases with Agent 2...")

    for idx, case in enumerate(test_cases, 1):
        image_path = case["image_path"]
        gt_category = case["ground_truth"].strip().lower()

        logger.info(f"[{idx}/{total_samples}] Evaluating: {case['component_ref']} on Board {case['board_id']} (GT: {gt_category})")

        state: ReviewState = {
            "board_id": case.get("board_id", "Unknown"),
            "component_ref": case.get("component_ref", "Unknown"),
            "defect_image_path": image_path,
            "golden_image_path": case.get("golden_image_path", None),
            "feature_type": "Body",
            "preliminary_defect": case.get("preliminary_defect", "unknown"),
            "baseline_confidence": 0.5,
            "aoi_measurements": {},
            "retrieved_precedents": [],
            "telemetry_data": {},
            "visual_evidence": "",
            "predicted_defect": "unknown",
            "final_confidence": 0.0,
            "diagnosis": "",
            "contradiction_detected": False,
            "self_check_passed": False,
            "ipc_citations": [],
            "errors": []
        }

        try:
            result = agent2_graph.invoke(state)
            pred_category = result.get("predicted_defect", "unknown").strip().lower()
        except Exception as e:
            logger.error(f"Inference failed on {Path(image_path).name}: {e}")
            pred_category = "unknown"
            result = {}

        confusion_matrix[gt_category][pred_category] += 1
        is_correct = (pred_category == gt_category)
        if is_correct:
            correct_samples += 1

        status = "MATCH [✔]" if is_correct else "MISMATCH [✖]"
        logger.info(f"       Prediction: '{pred_category}' | {status}")

        predictions.append({
            "image": Path(image_path).name,
            "board_id": case["board_id"],
            "component_ref": case["component_ref"],
            "ground_truth": gt_category,
            "predicted_defect": pred_category,
            "is_correct": is_correct,
            "confidence": result.get("final_confidence", 0.0),
            "diagnosis": result.get("diagnosis", "")
        })

    accuracy = (correct_samples / total_samples * 100) if total_samples > 0 else 0.0

    print("\n" + "=" * 68)
    print(f"AGENT 2 CLASSIFICATION EVALUATION REPORT (Accuracy: {accuracy:.2f}%)")
    print("=" * 68)
    print(f"{'Class Name':<22} | {'Precision':<10} | {'Recall':<10} | {'F1-Score':<10}")
    print("-" * 68)

    per_class_metrics = {}
    for cls in TAXONOMY:
        tp = confusion_matrix[cls][cls]
        fp = sum(confusion_matrix[other][cls] for other in TAXONOMY if other != cls)
        fn = sum(confusion_matrix[cls][other] for other in TAXONOMY if other != cls)

        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = (2 * precision * recall) / (precision + recall) if (precision + recall) > 0 else 0.0

        per_class_metrics[cls] = {"precision": precision, "recall": recall, "f1": f1}
        print(f"{cls:<22} | {precision * 100:>8.1f}% | {recall * 100:>8.1f}% | {f1:>8.3f}")

    print("=" * 68 + "\n")

    return {
        "accuracy": accuracy,
        "per_class_metrics": per_class_metrics,
        "confusion_matrix": {k: dict(v) for k, v in confusion_matrix.items()},
        "predictions": predictions
    }


def main():
    parser = argparse.ArgumentParser(description="Evaluate Agent 2 classification metrics")
    parser.add_argument("--limit", type=int, default=10, help="Number of cases to evaluate (default: 10)")
    parser.add_argument("--board", type=str, default=None, help="Filter to specific board (e.g. 06-200036-02)")
    args = parser.parse_args()

    target_dir = INPUTS_DIR / args.board if args.board else INPUTS_DIR
    cases = discover_input_cases(target_dir, limit=args.limit)

    if not cases:
        logger.warning(f"No defect images found in {target_dir}")
        return

    results = run_classification_eval(cases)

    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    out_file = OUTPUTS_DIR / "classification_metrics_results.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)
    logger.info(f"Report saved to: {out_file}")


if __name__ == "__main__":
    main()