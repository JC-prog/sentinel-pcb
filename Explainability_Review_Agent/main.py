import os
import sys
import json
import logging
import argparse
from pathlib import Path
from typing import Dict, Any, List, Optional
from PIL import Image

# 1. MUST load .env BEFORE importing agent (which initializes OpenAI)
from dotenv import load_dotenv
load_dotenv()

if not os.environ.get("OPENAI_API_KEY"):
    print("CRITICAL WARNING: OPENAI_API_KEY is not set in environment or .env file!")

# 2. Import compiled LangGraph workflow & State
from agent import pcb_graph, PCBInspectionState

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

DEFAULT_INPUT_DIR = r"C:\Users\kenny\Desktop\Semicon Agents\Prototype Code\inputs"
DEFAULT_OUTPUT_FILE = "inspection_results.json"


# ── Metadata Parser ──────────────────────────────────────────────────────────

def parse_filename_metadata(file_path: str) -> Dict[str, str]:
    """
    Extracts metadata from filenames like:
    'Board1_C978_Body_06-200036-02_20260824_154737922_WrongPart_13.jpg'
    or 'Board3_R131_Body_18-010309-AAA-RV3_20260824_100501995_Shift_4.jpg'
    """
    stem = Path(file_path).stem
    tokens = stem.split("_")

    meta = {
        "board_id": "Unknown",
        "component_ref": "Unknown",
        "ground_truth": "unknown",
        "issue_symptom": "AOI flagged anomaly"
    }

    if len(tokens) >= 7:
        meta["component_ref"] = tokens[1]    # e.g., C978, R131
        meta["board_id"] = tokens[3]         # e.g., 06-200036-02, 18-010309-AAA-RV3
        
        # Normalize ground truth defect name
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
        elif "golden" in raw_gt or "pass" in raw_gt:
            meta["ground_truth"] = "no defect"
        else:
            meta["ground_truth"] = raw_gt

        meta["issue_symptom"] = f"AOI flagged potential defect: {meta['ground_truth']}"
    elif len(tokens) >= 2:
        meta["board_id"] = tokens[0]
        meta["component_ref"] = tokens[1]

    return meta


# ── Core Agent Invocation ───────────────────────────────────────────────────

def run_agent_on_image(image_path: str, meta: Optional[Dict[str, str]] = None) -> PCBInspectionState:
    """Executes the full LangGraph agent workflow on a given image."""
    if not os.path.exists(image_path):
        raise FileNotFoundError(f"Image not found at {image_path}")

    if not meta:
        meta = parse_filename_metadata(image_path)

    initial_state: PCBInspectionState = {
        "image": Image.open(image_path).convert("RGB"),
        "board_id": meta.get("board_id", "Unknown"),
        "component_ref": meta.get("component_ref", "Unknown"),
        "issue_symptom": meta.get("issue_symptom", "AOI anomaly review"),
        "historical_context": "",
        "reference_standards": "",
        "visual_bounding_boxes": [],
        "visual_description": "",
        "measurements": {},
        "defect_location": None,
        "final_defect_category": "unknown",
        "final_diagnosis_text": "",
        "grounding_confidence": 0.0,
        "self_check_passed": False,
        "errors": []
    }

    return pcb_graph.invoke(initial_state)


# ── Display Formatter (from run_agent.py) ────────────────────────────────────

def print_engineering_report(state: PCBInspectionState, ground_truth: Optional[str] = None):
    """Prints a structured engineering report for human review."""
    print("\n" + "=" * 60)
    print("        PCB DEFECT EXPLAINABILITY REVIEW REPORT")
    print("=" * 60)
    print(f"Board Assembly ID:    {state['board_id']}")
    print(f"Component Reference:  {state['component_ref']}")
    if ground_truth:
        match_icon = "MATCH" if state['final_defect_category'] == ground_truth else "MISMATCH"
        print(f"Ground Truth:         {ground_truth.upper()}")
        print(f"Final Prediction:     {state['final_defect_category'].upper()} [{match_icon}]")
    else:
        print(f"Final Prediction:     {state['final_defect_category'].upper()}")
        
    print(f"Confidence Score:     {state['grounding_confidence'] * 100:.1f}%")
    print(f"Grounding Self-Check: {'PASSED' if state['self_check_passed'] else 'FAILED'}")

    # ── Defect Location Section ───────────────────────────────────────────────
    defect_loc = state.get("defect_location")
    if isinstance(defect_loc, dict):
        landmark = defect_loc.get("landmark", "Unspecified")
        bbox = defect_loc.get("bounding_box")
        print(f"Defect Landmark:      {landmark}")
        if bbox:
            print(f"Bounding Box [y,x]:   {bbox}")
    elif defect_loc:
        print(f"Defect Location:      {defect_loc}")
    elif state.get("visual_bounding_boxes"):
        print(f"Bounding Box [y,x]:   {state['visual_bounding_boxes']}")
    else:
        print("Defect Location:      None / Not localized")

    print("-" * 60)
    print("PHYSICAL ROOT-CAUSE & IPC COMPLIANCE EXPLANATION:")
    print(state["final_diagnosis_text"])
    
    if state.get("errors"):
        print("-" * 60)
        print(f"Warnings/Errors: {state['errors']}")
    print("=" * 60 + "\n")

# ── Image Finder ─────────────────────────────────────────────────────────────

def find_all_images(base_dir: str) -> List[str]:
    """Recursively finds all defect images inside Passed/ subfolders."""
    image_paths = []
    if not os.path.exists(base_dir):
        logger.error(f"Input directory does not exist: {base_dir}")
        return image_paths

    for root, _, files in os.walk(base_dir):
        if "passed" in root.lower():
            for f in files:
                if f.lower().endswith(('.jpg', '.jpeg', '.png')):
                    image_paths.append(os.path.join(root, f))
    return image_paths


# ── Execution Modes ──────────────────────────────────────────────────────────

def run_single_image_mode(image_path: str):
    """Evaluates a single image and prints detailed diagnosis."""
    logger.info(f"Running Single-Image Review on: {image_path}")
    meta = parse_filename_metadata(image_path)
    final_state = run_agent_on_image(image_path, meta)
    print_engineering_report(final_state, ground_truth=meta.get("ground_truth"))


def run_batch_mode(input_dir: str, limit: int = 10, output_file: str = DEFAULT_OUTPUT_FILE):
    """Evaluates a batch of images and saves evaluation metrics."""
    all_images = find_all_images(input_dir)
    logger.info(f"Found {len(all_images)} total images in: {input_dir}")

    if not all_images:
        logger.error("No images found! Check your input directory path.")
        return

    test_batch = all_images[:limit] if limit > 0 else all_images
    logger.info(f"Starting batch evaluation on {len(test_batch)} images...\n")

    results = []
    correct_count = 0

    for idx, img_path in enumerate(test_batch, 1):
        filename = os.path.basename(img_path)
        meta = parse_filename_metadata(img_path)

        logger.info(f"[{idx}/{len(test_batch)}] Processing: {meta['component_ref']} on Board {meta['board_id']}")
        logger.info(f"  Ground Truth: '{meta['ground_truth']}'")

        try:
            final_state = run_agent_on_image(img_path, meta)
            predicted_cat = final_state["final_defect_category"].lower()
            is_correct = (predicted_cat == meta["ground_truth"])

            if is_correct:
                correct_count += 1
                logger.info(f"  ✔ Prediction: '{predicted_cat}' [MATCH]\n")
            else:
                logger.warning(f"  ✖ Prediction: '{predicted_cat}' [MISMATCH]\n")

            results.append({
                "file_name": filename,
                "file_path": img_path,
                "board_id": final_state["board_id"],
                "component_ref": final_state["component_ref"],
                "ground_truth": meta["ground_truth"],
                "predicted_defect": predicted_cat,
                "defect_location": final_state.get("defect_location"),
                "is_correct": is_correct,
                "confidence": final_state["grounding_confidence"],
                "self_check_passed": final_state["self_check_passed"],
                "diagnosis": final_state["final_diagnosis_text"],
                "errors": final_state["errors"]
            })

        except Exception as exc:
            logger.error(f"Failed to process {filename}: {exc}")

    # Summary
    acc = (correct_count / len(test_batch)) * 100 if test_batch else 0
    print("\n" + "=" * 50)
    print(f"BATCH EVALUATION COMPLETE")
    print(f"Accuracy: {correct_count}/{len(test_batch)} ({acc:.1f}%)")
    print(f"Results saved to: {output_file}")
    print("=" * 50 + "\n")

    with open(output_file, "w") as f:
        json.dump(results, f, indent=4)


# ── Main Entrypoint ──────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="PCB Explainability Review Agent")
    parser.add_argument("--image", type=str, help="Path to a single PCB image to inspect.")
    parser.add_argument("--batch", action="store_true", help="Run batch evaluation over images directory.")
    parser.add_argument("--limit", type=int, default=10, help="Number of images to process in batch mode (default: 10).")
    parser.add_argument("--dir", type=str, default=DEFAULT_INPUT_DIR, help="Base input folder path.")
    parser.add_argument("--output", type=str, default=DEFAULT_OUTPUT_FILE, help="Output JSON results filename.")

    args = parser.parse_args()

    if args.image:
        run_single_image_mode(args.image)
    elif args.batch:
        run_batch_mode(input_dir=args.dir, limit=args.limit, output_file=args.output)
    else:
        # Default behavior if no flags passed: look for images and run single or first batch test
        all_images = find_all_images(args.dir)
        if all_images:
            print("No mode flag passed. Inspecting first found image as a sample test:")
            run_single_image_mode(all_images[0])
            print("\nTip: To run batch evaluation on 10 images, run:\n  python main.py --batch --limit 10")
        else:
            logger.warning(f"No images found in {args.dir}. Specify an image with --image <path>.")


if __name__ == "__main__":
    main()
