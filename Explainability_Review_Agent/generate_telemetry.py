import os
import json
import re
from pathlib import Path
from dataclasses import dataclass
from typing import Dict, Any, Optional
import numpy as np
import random


@dataclass
class ComponentSpec:
    comp_type: str
    nominal_val: float
    tolerance_pct: float
    nominal_height_um: float


class SyntheticTelemetryGenerator:
    def __init__(self, seed: Optional[int] = 42):
        if seed is not None:
            np.random.seed(seed)
            random.seed(seed)

        self.default_specs = {
            "R": ComponentSpec("R", nominal_val=100.0, tolerance_pct=5.0, nominal_height_um=45.0),
            "C": ComponentSpec("C", nominal_val=10e-6, tolerance_pct=10.0, nominal_height_um=50.0),
            "U": ComponentSpec("IC", nominal_val=0.0, tolerance_pct=0.0, nominal_height_um=120.0),
            "D": ComponentSpec("Diode", nominal_val=0.7, tolerance_pct=5.0, nominal_height_um=60.0),
            "L": ComponentSpec("Inductor", nominal_val=4.7e-6, tolerance_pct=10.0, nominal_height_um=80.0),
        }

    def _infer_type(self, ref: str) -> str:
        prefix = ''.join([c for c in ref if c.isalpha()])
        return prefix if prefix in self.default_specs else "R"

    def generate_measurement(
        self, 
        board_id: str, 
        component_ref: str, 
        condition: str = "NORMAL"
    ) -> Dict[str, Any]:
        comp_type = self._infer_type(component_ref)
        spec = self.default_specs[comp_type]

        # 1. Simulate Electrical (ICT)
        cond_upper = condition.upper()
        if "MISSING" in cond_upper or "TOMBSTONE" in cond_upper:
            measured_val = 1e7
            ict_status = "FAIL_OPEN"
        elif "SHORT" in cond_upper:
            measured_val = float(np.clip(np.random.normal(0.05, 0.02), 0.01, 0.1))
            ict_status = "FAIL_SHORT"
        elif "FAILED" in cond_upper or "FAIL" in cond_upper:
            # Generic fail: out of tolerance
            offset = spec.nominal_val * (spec.tolerance_pct / 100.0) * np.random.choice([1.8, -1.8])
            measured_val = float(spec.nominal_val + offset)
            ict_status = "FAIL_OUT_OF_TOLERANCE"
        else:
            sigma = (spec.nominal_val * (spec.tolerance_pct / 100.0)) / 3.0
            measured_val = float(np.random.normal(spec.nominal_val, sigma))
            ict_status = "PASS"

        # 2. Simulate 3D AOI
        if "MISSING" in cond_upper:
            laser_height = float(np.clip(np.random.normal(2.0, 1.0), 0.0, 5.0))
            overhang_pct = 0.0
            aoi_status = "FAIL_MISSING"
        elif "TOMBSTONE" in cond_upper:
            laser_height = float(spec.nominal_height_um * np.random.uniform(2.5, 4.0))
            overhang_pct = float(np.random.uniform(60.0, 95.0))
            aoi_status = "FAIL_TOMBSTONE"
        elif "MISALIGNED" in cond_upper or "SHIFT" in cond_upper and "FAILED" in cond_upper:
            laser_height = float(np.random.normal(spec.nominal_height_um, 3.0))
            overhang_pct = float(np.random.uniform(52.0, 85.0))  # Class 2 fail (> 50%)
            aoi_status = "FAIL_ALIGNMENT"
        elif "FAILED" in cond_upper or "FAIL" in cond_upper:
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
            "overall_status": overall_status
        }


def parse_filename_metadata(file_path: Path):
    """
    Extracts board_id, ref_des, and condition from folder tree & filename:
    e.g.: 'Board1_R131_Body_18-010309-AAA-RV3_...jpg'
    """
    filename = file_path.name
    
    # 1. Parse component ref designator (e.g., R131, C42, U1)
    match_ref = re.search(r'([A-Z]+[0-9]+)', filename)
    component_ref = match_ref.group(1) if match_ref else "R1"

    # 2. Parse Board ID from parent folders (e.g., '18-010309-AAA-RV3')
    # If the file path contains inputs/<board_id>/...
    parts = file_path.parts
    board_id = "UNKNOWN"
    if "inputs" in parts:
        idx = parts.index("inputs")
        if len(parts) > idx + 1:
            board_id = parts[idx + 1]

    # 3. Parse condition from folder names (Passed vs Failed)
    path_str_upper = str(file_path).upper()
    if "PASSED" in path_str_upper:
        condition = "NORMAL"
    elif "TOMBSTONE" in path_str_upper:
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


def run_pipeline(
    input_dir: str = "inputs", 
    output_dir: str = "outputs"
):
    input_path = Path(input_dir).resolve()
    out_path = Path(output_dir).resolve()
    out_path.mkdir(parents=True, exist_ok=True)

    generator = SyntheticTelemetryGenerator(seed=42)
    valid_exts = {".jpg", ".jpeg", ".png", ".bmp"}

    print(f"Scanning images in: {input_path}")
    image_files = [p for p in input_path.glob("**/*") if p.suffix.lower() in valid_exts]
    print(f"Found {len(image_files)} image files.")

    all_telemetry = []
    lookup_table = {}

    for img in image_files:
        board_id, comp_ref, condition = parse_filename_metadata(img)
        
        telemetry = generator.generate_measurement(
            board_id=board_id,
            component_ref=comp_ref,
            condition=condition
        )

        # Store the relative path to make it portable across machines
        rel_path = str(img.relative_to(input_path.parent))
        telemetry["image_path"] = rel_path
        telemetry["filename"] = img.name

        all_telemetry.append(telemetry)
        # Allows instant O(1) query by filename in your agent
        lookup_table[img.name] = telemetry

    # 1. Save complete array of telemetry records
    full_output = out_path / "synthetic_telemetry.json"
    with open(full_output, "w", encoding="utf-8") as f:
        json.dump(all_telemetry, f, indent=2)

    # 2. Save image-to-telemetry lookup dict for the Review Agent
    lookup_output = out_path / "telemetry_by_image.json"
    with open(lookup_output, "w", encoding="utf-8") as f:
        json.dump(lookup_table, f, indent=2)

    print(f"\n[DONE] Generated synthetic telemetry for {len(all_telemetry)} images.")
    print(f" Saved full records -> {full_output}")
    print(f" Saved image lookup -> {lookup_output}")


if __name__ == "__main__":
    run_pipeline()
