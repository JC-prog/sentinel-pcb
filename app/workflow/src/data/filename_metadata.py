"""Inspection-image filename parsing - `normalize_defect_label` and `parse_filename_metadata`.

Ported verbatim from pcb_agentic_inspector's main.py (the CLI, which is not part of this app). The
Work tab prepares its samples from the dataset CSV and inspection XML, so nothing in the web backend
calls these; they are kept because the teammate's unit tests (tests/test_metadata_and_telemetry.py)
and evals describe the filename convention through them.
"""

import re
from pathlib import Path
from typing import Any, Dict, Optional


def normalize_defect_label(label: Optional[str]) -> str:
    """Normalizes labels into standard IPC defect classes."""
    if not label:
        return "no defect"
    s = label.strip()
    s = re.sub(r'(?<!^)(?=[A-Z])', ' ', s).lower()
    s = s.replace("_", " ").replace("-", " ")
    s = " ".join(s.split())

    if "missing" in s:
        return "missing part"
    if "shift" in s:
        return "shifted"
    if "foreign" in s:
        return "foreign material"
    if "tomb" in s:
        return "tombstone"
    if "solder" in s or "insufficient" in s:
        return "solder insufficient"
    if "wrong" in s:
        return "wrong part"
    if "normal" in s or "no defect" in s or "pass" in s or "golden" in s:
        return "no defect"
    return s


def parse_filename_metadata(filename: str) -> Dict[str, Any]:
    """
    Parses component metadata and ground truth from inspection image filenames.
    Expected format:
    {Board}_{ComponentRef}_{Type}_{BoardID}_{Date}_{Time}_{DefectType}_{Index}.ext
    e.g. Board1_C636_Body_06-200036-02_20260824_193317036_MissingPart_3.jpg
    """
    path = Path(filename)
    tokens = path.stem.split("_")

    # Fallback for non-standard / malformed filenames
    if len(tokens) < 7:
        return {
            "component_ref": tokens[0] if tokens else "unknown",
            "board_id": "unknown",
            "ground_truth": "unknown",
            "raw_filename": filename,
        }

    raw_defect = tokens[6]
    ground_truth = normalize_defect_label(raw_defect)

    valid_classes = {
        "missing part",
        "shifted",
        "foreign material",
        "tombstone",
        "solder insufficient",
        "wrong part",
        "no defect",
    }
    if ground_truth not in valid_classes:
        ground_truth = "unknown"

    return {
        "component_ref": tokens[1],
        "board_id": tokens[3],
        "ground_truth": ground_truth,
        "raw_filename": filename,
    }
