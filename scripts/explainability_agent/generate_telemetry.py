"""Generate synthetic AOI/ICT telemetry for the PCB images under
settings.explainability_agent_data_dir/inputs/ (data/images/inputs/ by default), so the
Explainability & Review Agent's measurement_evidence node has real-looking numbers to reason over
instead of always falling back to mcp_client.get_measurements()'s fixed stub values.

Ported from Kenny's Explainability_Review_Agent/generate_telemetry.py - same synthetic-measurement
logic and filename/folder parsing conventions, adapted to resolve paths via settings (matching
app.settings.chat_upload_dir's convention) instead of the current working directory.

Usage (run as a module, so `app`/`scripts` resolve on sys.path):
    uv run python -m scripts.explainability_agent.generate_telemetry

Expects images under <data_dir>/inputs/<board_id>/... where the filename or an ancestor folder
name encodes the component ref designator (e.g. "R131") and condition (e.g. "Passed", "Tombstone",
"Missing", "Short", "Failed") - see parse_filename_metadata() below. Writes
<data_dir>/outputs/synthetic_telemetry.json (full records) and
<data_dir>/outputs/telemetry_by_image.json (filename -> record lookup, which is what graph.py's
_get_telemetry_database() reads).
"""

import json
import random
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from app.settings import settings

DATA_DIR = Path(settings.explainability_agent_data_dir)
INPUT_DIR = DATA_DIR / "inputs"
OUTPUT_DIR = DATA_DIR / "outputs"

_VALID_IMAGE_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".bmp"})


@dataclass
class ComponentSpec:
    comp_type: str
    nominal_val: float
    tolerance_pct: float
    nominal_height_um: float


_DEFAULT_SPECS: dict[str, ComponentSpec] = {
    "R": ComponentSpec("R", nominal_val=100.0, tolerance_pct=5.0, nominal_height_um=45.0),
    "C": ComponentSpec("C", nominal_val=10e-6, tolerance_pct=10.0, nominal_height_um=50.0),
    "U": ComponentSpec("IC", nominal_val=0.0, tolerance_pct=0.0, nominal_height_um=120.0),
    "D": ComponentSpec("Diode", nominal_val=0.7, tolerance_pct=5.0, nominal_height_um=60.0),
    "L": ComponentSpec("Inductor", nominal_val=4.7e-6, tolerance_pct=10.0, nominal_height_um=80.0),
}


def _infer_type(ref: str) -> str:
    prefix = "".join(c for c in ref if c.isalpha())
    return prefix if prefix in _DEFAULT_SPECS else "R"


def generate_measurement(
    board_id: str, component_ref: str, condition: str = "NORMAL"
) -> dict[str, Any]:
    """Simulates ICT (electrical) + 3D AOI (laser height/overhang) measurements matching the
    given defect condition, so the numbers are consistent with what that defect would actually
    look like on real test equipment."""

    comp_type = _infer_type(component_ref)
    spec = _DEFAULT_SPECS[comp_type]
    cond_upper = condition.upper()

    if "MISSING" in cond_upper or "TOMBSTONE" in cond_upper:
        measured_val = 1e7
        ict_status = "FAIL_OPEN"
    elif "SHORT" in cond_upper:
        measured_val = float(np.clip(np.random.normal(0.05, 0.02), 0.01, 0.1))
        ict_status = "FAIL_SHORT"
    elif "FAIL" in cond_upper:
        offset = spec.nominal_val * (spec.tolerance_pct / 100.0) * random.choice([1.8, -1.8])
        measured_val = float(spec.nominal_val + offset)
        ict_status = "FAIL_OUT_OF_TOLERANCE"
    else:
        sigma = (spec.nominal_val * (spec.tolerance_pct / 100.0)) / 3.0
        measured_val = float(np.random.normal(spec.nominal_val, sigma))
        ict_status = "PASS"

    if "MISSING" in cond_upper:
        laser_height = float(np.clip(np.random.normal(2.0, 1.0), 0.0, 5.0))
        overhang_pct = 0.0
        aoi_status = "FAIL_MISSING"
    elif "TOMBSTONE" in cond_upper:
        laser_height = float(spec.nominal_height_um * np.random.uniform(2.5, 4.0))
        overhang_pct = float(np.random.uniform(60.0, 95.0))
        aoi_status = "FAIL_TOMBSTONE"
    elif "MISALIGNED" in cond_upper or ("SHIFT" in cond_upper and "FAIL" in cond_upper):
        laser_height = float(np.random.normal(spec.nominal_height_um, 3.0))
        overhang_pct = float(np.random.uniform(52.0, 85.0))  # Class 2 fail (> 50%)
        aoi_status = "FAIL_ALIGNMENT"
    elif "FAIL" in cond_upper:
        laser_height = float(np.random.normal(spec.nominal_height_um * 1.5, 5.0))
        overhang_pct = float(np.random.uniform(51.0, 70.0))
        aoi_status = "FAIL"
    else:
        laser_height = float(np.random.normal(spec.nominal_height_um, 2.5))
        overhang_pct = float(np.clip(np.random.normal(15.0, 8.0), 0.0, 45.0))
        aoi_status = "PASS"

    overall_status = "PASS" if (ict_status == "PASS" and aoi_status == "PASS") else "FAIL"

    return {
        "board_id": board_id,
        "component_ref": component_ref,
        "condition_label": condition,
        "nominal_value": spec.nominal_val,
        "measured_value": round(measured_val, 3),
        "unit": "Ohms" if comp_type == "R" else ("Farads" if comp_type == "C" else "V"),
        "ict_status": ict_status,
        "laser_profile_height_um": round(laser_height, 2),
        "side_overhang_percent": round(overhang_pct, 1),
        "coplanarity_um": round(float(np.random.exponential(1.5)), 2),
        "aoi_status": aoi_status,
        "overall_status": overall_status,
    }


def parse_filename_metadata(file_path: Path, input_root: Path) -> tuple[str, str, str]:
    """Extracts (board_id, component_ref, condition) from the folder tree and filename, e.g.
    'Board1_R131_Body_18-010309-AAA-RV3_...jpg' under inputs/<board_id>/Passed/... ."""

    filename = file_path.name

    match_ref = re.search(r"([A-Z]+[0-9]+)", filename)
    component_ref = match_ref.group(1) if match_ref else "R1"

    board_id = "UNKNOWN"
    try:
        rel_parts = file_path.relative_to(input_root).parts
        if rel_parts:
            board_id = rel_parts[0]
    except ValueError:
        pass

    path_str_upper = str(file_path).upper()
    if "TOMBSTONE" in path_str_upper:
        condition = "TOMBSTONE"
    elif "SHORT" in path_str_upper:
        condition = "SHORT"
    elif "MISSING" in path_str_upper:
        condition = "MISSING"
    elif "FAILED" in path_str_upper:
        condition = "FAILED"
    else:
        condition = "NORMAL"

    return board_id, component_ref, condition


def run_pipeline(input_dir: Path = INPUT_DIR, output_dir: Path = OUTPUT_DIR) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    image_files = [p for p in input_dir.glob("**/*") if p.suffix.lower() in _VALID_IMAGE_EXTENSIONS]
    print(f"Scanning images in: {input_dir}")
    print(f"Found {len(image_files)} image files.")

    all_telemetry: list[dict[str, Any]] = []
    lookup_table: dict[str, dict[str, Any]] = {}

    for img in image_files:
        board_id, comp_ref, condition = parse_filename_metadata(img, input_dir)
        telemetry = generate_measurement(
            board_id=board_id, component_ref=comp_ref, condition=condition
        )
        telemetry["image_path"] = str(img.relative_to(input_dir.parent))
        telemetry["filename"] = img.name

        all_telemetry.append(telemetry)
        lookup_table[img.name] = telemetry

    full_output = output_dir / "synthetic_telemetry.json"
    with open(full_output, "w", encoding="utf-8") as f:
        json.dump(all_telemetry, f, indent=2)

    lookup_output = output_dir / "telemetry_by_image.json"
    with open(lookup_output, "w", encoding="utf-8") as f:
        json.dump(lookup_table, f, indent=2)

    print(f"\n[DONE] Generated synthetic telemetry for {len(all_telemetry)} images.")
    print(f" Saved full records -> {full_output}")
    print(f" Saved image lookup -> {lookup_output}")


if __name__ == "__main__":
    random.seed(42)
    np.random.seed(42)
    run_pipeline()
