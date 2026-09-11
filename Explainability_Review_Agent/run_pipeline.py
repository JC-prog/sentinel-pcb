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
        meta["component_ref"] = tokens[1]  # e.g., C636, C978
        meta["board_id"] = tokens[3]       # e.g., 06-200036-02
        
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


def main():
    all_images = find_all_images(INPUT_DIR)
    logger.info(f"Discovered {len(all_images)} total images in {INPUT_DIR}")

    if not all_images:
        logger.error("No images found! Check your inputs directory.")
        return

    # TIP: Start with [:5] or [:10] to test before running all 100+
    batch_images = all_images[:5]  # Change to all_images to run every file
    logger.info(f"Running batch pipeline on {len(batch_images)} images...\n")

    results = []
    for idx, img_path in enumerate(batch_images, 1):
        filename = os.path.basename(img_path)
        meta = parse_filename_metadata(filename)

        logger.info(f"[{idx}/{len(batch_images)}] Processing: {meta['component_ref']} on Board {meta['board_id']}")
        logger.info(f"   Ground Truth: '{meta['ground_truth']}'")

        try:
            res = orchestrator_handle_event(
                board_id=meta["board_id"],
                component_ref=meta["component_ref"],
                image_path=img_path
            )
            results.append({
                "filename": filename,
                "metadata": meta,
                "orchestrator_output": res
            })
        except Exception as e:
            logger.error(f"Failed processing {filename}: {e}")

    # Ensure outputs directory exists
    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    with open(OUTPUT_FILE, "w") as f:
        json.dump(results, f, indent=4)

    logger.info(f"\n==========================================")
    logger.info(f"Batch completed! Processed {len(results)} images.")
    logger.info(f"Saved full audit results to: {OUTPUT_FILE}")
    logger.info(f"==========================================")


if __name__ == "__main__":
    main()
