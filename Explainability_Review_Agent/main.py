import os
import json
import logging
from PIL import Image
from typing import Dict, Any, Optional

# Import the compiled LangGraph workflow from agent.py
from agent import pcb_graph, PCBInspectionState

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

INPUT_DIR = r"C:\Users\kenny\Desktop\Semicon Agents\Prototype Code\inputs"
OUTPUT_FILE = "inspection_results.json"


def parse_filename_metadata(filename: str) -> Dict[str, str]:
    """
    Parses metadata directly from AOI filenames like:
    'Board1_C978_Body_06-200036-02_20260824_154737922_WrongPart_13.jpg'
    """
    stem = os.path.splitext(filename)[0]
    tokens = stem.split("_")
    
    # Defaults
    meta = {
        "board_id": "Unknown",
        "component_ref": "Unknown",
        "ground_truth": "unknown",
        "issue_symptom": "AOI flagged anomaly"
    }

    if len(tokens) >= 7:
        meta["component_ref"] = tokens[1]                          # e.g., C636, C977, C978
        meta["board_id"] = tokens[3]                               # e.g., 06-200036-02
        
        # Extract ground truth label (e.g., MissingPart -> missing part)
        raw_gt = tokens[6].lower()
        if "missing" in raw_gt:
            meta["ground_truth"] = "missing part"
        elif "shift" in raw_gt:
            meta["ground_truth"] = "shifted"
        elif "wrong" in raw_gt:
            meta["ground_truth"] = "wrong part"
        elif "foreign" in raw_gt or "debris" in raw_gt:
            meta["ground_truth"] = "foreign material"
        elif "tombstone" in raw_gt:
            meta["ground_truth"] = "tombstone"
        elif "solder" in raw_gt:
            meta["ground_truth"] = "solder insufficient"
        elif "golden" in raw_gt:
            meta["ground_truth"] = "no defect"
            
        meta["issue_symptom"] = f"AOI flagged potential defect: {meta['ground_truth']}"
        
    return meta


def process_single_image(image_path: str, meta: Dict[str, str]) -> Dict[str, Any]:
    """Executes the LangGraph agent pipeline on a single image."""
    
    initial_state: PCBInspectionState = {
        "image": Image.open(image_path).convert("RGB"),
        "board_id": meta["board_id"],
        "component_ref": meta["component_ref"],       
        "issue_symptom": meta["issue_symptom"],       
        "historical_context": "",
        "reference_standards": "",
        "visual_bounding_boxes": [],
        "visual_description": "",
        "measurements": {},
        "final_defect_category": "unknown",
        "final_diagnosis_text": "",
        "grounding_confidence": 0.0,
        "self_check_passed": False,
        "errors": []
    }

    final_state = pcb_graph.invoke(initial_state)

    # Check if prediction matched the filename ground truth
    predicted_cat = final_state["final_defect_category"].lower()
    is_correct = (predicted_cat == meta["ground_truth"])

    return {
        "file_name": os.path.basename(image_path),
        "board_id": final_state["board_id"],
        "component_ref": final_state["component_ref"],
        "ground_truth": meta["ground_truth"],
        "predicted_defect": predicted_cat,
        "is_correct": is_correct,
        "confidence": final_state["grounding_confidence"],
        "diagnosis": final_state["final_diagnosis_text"],
        "self_check_passed": final_state["self_check_passed"],
        "errors": final_state["errors"]
    }


def find_all_images(base_dir: str) -> list[str]:
    """Recursively finds all defect images inside Passed/ subfolders."""
    image_paths = []
    for root, _, files in os.walk(base_dir):
        # We target the 'Passed' folder containing defect instances
        if "passed" in root.lower():
            for f in files:
                if f.lower().endswith(('.jpg', '.jpeg', '.png')):
                    image_paths.append(os.path.join(root, f))
    return image_paths


def main():
    all_images = find_all_images(INPUT_DIR)
    logger.info(f"Found total of {len(all_images)} images across all board assemblies.")
    
    if not all_images:
        logger.error("No images found! Check that folders contain .jpg/.png files.")
        return

    # TEST WITH FIRST 5 IMAGES FIRST (Change to all_images when confident!)
    test_batch = all_images[:5]
    logger.info(f"Starting batch evaluation on {len(test_batch)} images...\n")

    results = []
    correct_count = 0

    for img_path in test_batch:
        filename = os.path.basename(img_path)
        meta = parse_filename_metadata(filename)
        
        logger.info(f"▶ Processing: {meta['component_ref']} on Board {meta['board_id']}")
        logger.info(f"  Ground Truth: '{meta['ground_truth']}'")

        try:
            res = process_single_image(img_path, meta)
            results.append(res)
            
            if res["is_correct"]:
                correct_count += 1
                logger.info(f"  ✔ Prediction: '{res['predicted_defect']}' [MATCH!]\n")
            else:
                logger.warning(f"  ✖ Prediction: '{res['predicted_defect']}' [MISMATCH]\n")
                
        except Exception as exc:
            logger.error(f"Failed to process {filename}: {exc}")

    # Summary
    acc = (correct_count / len(test_batch)) * 100 if test_batch else 0
    logger.info(f"==========================================")
    logger.info(f"BATCH COMPLETE! Accuracy: {correct_count}/{len(test_batch)} ({acc:.1f}%)")
    logger.info(f"Results saved to {OUTPUT_FILE}")
    logger.info(f"==========================================")

    with open(OUTPUT_FILE, "w") as f:
        json.dump(results, f, indent=4)


if __name__ == "__main__":
    main()
