"""Evaluation Script 3: IPC-A-610 Citation Fidelity & RAG Grounding Audit across inputs/.
Location: evals/eval_ipc_citation_fidelity.py
"""

from __future__ import annotations

import os
import sys
import re
import json
import logging
import argparse
from pathlib import Path
from typing import Dict, List, Any

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


KNOWN_IPC_PATTERNS = [
    r"IPC-A-610",
    r"IPC\s*J-STD-001",
    r"IPC-7711",
    r"IPC-7351",
    r"Class\s*[123]",
    r"Section\s*\d+\.\d+",
    r"overhang",
    r"wetting",
    r"fillet",
    r"coplanarity",
    r"alignment"
]


def audit_citations(diagnosis: str, ipc_citations: Any) -> Dict[str, Any]:
    text_to_audit = diagnosis + " " + str(ipc_citations)

    cited_clauses = []
    for pattern in KNOWN_IPC_PATTERNS:
        matches = re.findall(pattern, text_to_audit, re.IGNORECASE)
        if matches:
            cited_clauses.extend(list(set(matches)))

    has_ipc_mention = any("ipc" in s.lower() for s in cited_clauses) or bool(ipc_citations)
    has_clauses = len(cited_clauses) > 0

    return {
        "cited_clauses": list(set(cited_clauses)),
        "has_ipc_citation": has_ipc_mention,
        "is_faithful": has_ipc_mention and has_clauses
    }


def run_citation_audit(cases: List[Dict[str, Any]]):
    print("\n" + "=" * 74)
    print("AGENT 2: IPC-A-610 CITATION FIDELITY & RAG GROUNDING AUDIT")
    print("=" * 74)

    total_cases = len(cases)
    faithful_count = 0

    for idx, case in enumerate(cases, 1):
        print(f"\n[Case {idx}/{total_cases}] Evaluating {case.get('component_ref', 'Comp')} on {case.get('board_id', 'Board')}...")

        state: ReviewState = {
            "board_id": case.get("board_id", "Unknown"),
            "component_ref": case.get("component_ref", "Unknown"),
            "defect_image_path": str(Path(case["image_path"]).resolve()),
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

        result = agent2_graph.invoke(state)
        diagnosis = result.get("diagnosis", "")
        citations = result.get("ipc_citations", [])

        audit = audit_citations(diagnosis, citations)
        if audit["is_faithful"]:
            faithful_count += 1

        print(f"  IPC Reference Cited:   {'YES [✔]' if audit['has_ipc_citation'] else 'NO [✖]'}")
        print(f"  Explicit Citations:    {citations}")
        print(f"  Cited Clauses & Terms: {audit['cited_clauses']}")
        print(f"  Diagnosis Snippet:\n    {diagnosis[:160]}...")

    score = (faithful_count / total_cases * 100) if total_cases > 0 else 0
    print("\n" + "-" * 74)
    print(f"CITATION FIDELITY SCORE: {faithful_count}/{total_cases} ({score:.1f}%)")
    print("-" * 74 + "\n")


def main():
    parser = argparse.ArgumentParser(description="Audit IPC-A-610 citation fidelity on real images")
    parser.add_argument("--limit", type=int, default=5, help="Number of cases to audit (default: 5)")
    args = parser.parse_args()

    # Discover cases from inputs/06-200036-02/
    cases = []
    board_dir = INPUTS_DIR / "06-200036-02" / "Body" / "Passed"
    if board_dir.exists():
        for f in list(board_dir.glob("*.jpg"))[:args.limit]:
            tokens = f.stem.split("_")
            cases.append({
                "image_path": str(f.resolve()),
                "golden_image_path": None,
                "board_id": "06-200036-02",
                "component_ref": tokens[1] if len(tokens) > 1 else "Unknown",
                "preliminary_defect": "anomaly review"
            })

    if not cases:
        print(f"No images found in {board_dir}")
        return

    run_citation_audit(cases)


if __name__ == "__main__":
    main()