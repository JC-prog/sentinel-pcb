# run_pipeline.py
import os
import json
import logging
from orchestrator_agent import orchestrator_handle_event

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

INPUT_DIR = r"\inputs"
OUTPUT_FILE = r"\outputs\batch_inspection_results.json"


def parse_filename_metadata(filename: str):
    """Extracts component_ref, board_id, and ground truth from the AOI filename."""
    stem = os.path.splitext(filename)[0]
    tokens = stem.split("_")
    
    meta = {
        "board_id": "Unknown",
        "component_ref": "Unknown",
        "ground_truth": "unknown"
    }

    if len(tokens) >= 7:
        meta["component_ref"] = tokens[1]
        meta["board_id"] = tokens[3]
        
        raw_gt = tokens[6].lower()
        if "missing" in raw_gt:
            meta["ground_truth"] = "missing part"
        elif "shift" in raw_gt:
            meta["ground_truth"] = "shifted"
        elif "wrong" in raw_gt:
            meta["ground_truth"] = "wrong part"
        elif "foreign" in raw_gt:
            meta["ground_truth"] = "foreign material"
        elif "tombstone" in raw_gt:
            meta["ground_truth"] = "tombstone"
        elif "solder" in raw_gt:
            meta["ground_truth"] = "solder insufficient"
        elif "golden" in raw_gt:
            meta["ground_truth"] = "no defect"

    return meta


def find_all_images(base_dir: str):
    """Recursively finds all defect images inside 'Passed' subfolders."""
    image_paths = []
    for root, _, files in os.walk(base_dir):
        if "passed" in root.lower():
            for f in files:
                if f.lower().endswith(('.jpg', '.jpeg', '.png')):
                    image_paths.append(os.path.join(root, f))
    return image_paths


def extract_agent2_reasoning(agent2_res: dict) -> str:
    """Extracts the reasoning / diagnosis produced by Agent 2."""
    if not isinstance(agent2_res, dict):
        return "No reasoning returned by Agent 2."
    
    # Priority order for Agent 2 explanation fields:
    # 1. diagnosis_text (Agent 2 standard field)
    # 2. reasoning / explanation
    for key in ["diagnosis_text", "reasoning", "explanation", "rationale"]:
        val = agent2_res.get(key)
        if val:
            return str(val)
            
    return "No diagnosis text found."


def main():
    all_images = find_all_images(INPUT_DIR)
    logger.info(f"Discovered {len(all_images)} total images in {INPUT_DIR}")

    if not all_images:
        logger.error("No images found! Check your inputs directory.")
        return

    # Run on first 5 images for test
    batch_images = all_images[:5]
    logger.info(f"Running batch pipeline on {len(batch_images)} images...\n")

    results = []
    for idx, img_path in enumerate(batch_images, 1):
        filename = os.path.basename(img_path)
        meta = parse_filename_metadata(filename)

        logger.info(f"[{idx}/{len(batch_images)}] Processing: {meta['component_ref']} on Board {meta['board_id']}")
        logger.info(f"   Ground Truth: '{meta['ground_truth']}'")

        try:
            agent2_output = orchestrator_handle_event(
                board_id=meta["board_id"],
                component_ref=meta["component_ref"],
                image_path=img_path
            )

            reasoning = extract_agent2_reasoning(agent2_output)
            logger.info(f"   Agent 2 Reasoning: {reasoning[:120]}...\n")

            results.append({
                "filename": filename,
                "metadata": meta,
                "agent_2_reasoning": reasoning,
                "agent_2_output": agent2_output
            })
        except Exception as e:
            logger.error(f"Failed processing {filename}: {e}", exc_info=True)

    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=4, default=str)

    logger.info(f"==========================================")
    logger.info(f"Batch completed! Processed {len(results)} images.")
    logger.info(f"Saved results to: {OUTPUT_FILE}")
    logger.info(f"==========================================")


if __name__ == "__main__":
    main()
