"""Evaluation Script 4: LLM-as-a-Judge Quality & Engineering Rubric Scoring.
Location: evals/eval_explainability_rubric.py
"""

from __future__ import annotations

import os
import sys
import json
import logging
import argparse
from pathlib import Path
from typing import Dict, Any, List
from openai import OpenAI

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


_JUDGE_PROMPT = """
You are an expert SMT Quality Assurance Lead auditing an automated AI review explanation.
Score the explanation on a 1-5 scale across 4 criteria:

1. Physical Telemetry Grounding (1-5): Does it cite quantitative sensor metrics (3D laser height, coplanarity, resistance, capacitance)?
2. Standard Adherence (1-5): Does it correctly reference IPC-A-610 criteria or SMT manufacturing tolerance limits?
3. Contradiction Resolution (1-5): Does it clearly explain why optical and electrical sensor modalities agree or conflict?
4. Engineering Actionability (1-5): Is the root-cause diagnosis clear enough for an SMT process engineer to address on the reflow/placement line?

Return ONLY a valid JSON object matching:
{
  "physical_grounding": int,
  "standard_adherence": int,
  "contradiction_resolution": int,
  "engineering_actionability": int,
  "overall_score": float,
  "critique": "concise explanation of score"
}
"""


def evaluate_with_judge(
    diagnosis: str,
    component_ref: str,
    telemetry: Dict[str, Any],
    defect: str
) -> Dict[str, Any]:
    client = OpenAI()
    prompt = f"""
INSPECTION CASE:
Component: {component_ref}
Predicted Defect: {defect}
Available Telemetry: {json.dumps(telemetry)}

AGENT EXPLANATION:
\"\"\"{diagnosis}\"\"\"
"""
    resp = client.chat.completions.create(
        model="gpt-4o",
        temperature=0.0,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": _JUDGE_PROMPT},
            {"role": "user", "content": prompt}
        ]
    )
    return json.loads(resp.choices[0].message.content)


def run_explainability_rubric(cases: List[Dict[str, str]]):
    print("\n" + "=" * 74)
    print("AGENT 2: LLM-AS-A-JUDGE EXPLAINABILITY QUALITY RUBRIC AUDIT")
    print("=" * 74)

    scores = []

    for idx, case in enumerate(cases, 1):
        print(f"\n[Case {idx}/{len(cases)}] Evaluating: {case.get('component_ref', 'Comp')} on {case.get('board_id', 'Board')}...")

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

        judge_eval = evaluate_with_judge(
            diagnosis=result.get("diagnosis", ""),
            component_ref=result.get("component_ref", "Unknown"),
            telemetry=result.get("telemetry_data", result.get("aoi_measurements", {})),
            defect=result.get("predicted_defect", "unknown")
        )

        scores.append(judge_eval["overall_score"])

        print(f"  Physical Grounding:        {judge_eval['physical_grounding']}/5")
        print(f"  IPC Standard Adherence:    {judge_eval['standard_adherence']}/5")
        print(f"  Contradiction Resolution:  {judge_eval['contradiction_resolution']}/5")
        print(f"  Engineering Actionability: {judge_eval['engineering_actionability']}/5")
        print(f"  Overall Score:             {judge_eval['overall_score']:.1f}/5.0")
        print(f"  Judge Critique:            {judge_eval['critique']}")

    avg_score = sum(scores) / len(scores) if scores else 0.0
    print("\n" + "-" * 74)
    print(f"AVERAGE EXPLAINABILITY RUBRIC SCORE: {avg_score:.2f} / 5.0")
    print("-" * 74 + "\n")


def main():
    parser = argparse.ArgumentParser(description="Run LLM-as-a-Judge explainability rubric on inputs/")
    parser.add_argument("--limit", type=int, default=3, help="Number of cases to judge (default: 3)")
    args = parser.parse_args()

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
                "preliminary_defect": "missing part" if "missing" in f.name.lower() else "anomaly review"
            })

    if not cases:
        print(f"No images found in {board_dir}")
        return

    run_explainability_rubric(cases)


if __name__ == "__main__":
    main()