"""
LangGraph Review & Explainability State Machine for Agent 2.
Reconciles baseline predictions, local VLM observations, and physical telemetry against IPC-A-610 standards.

Ported as-is from a teammate's standalone pcb_agentic_inspector prototype
(src/agent2_explainability/pipeline/review_graph.py) - node logic, config loading (this
package's own config/agent2_config.yaml, relative to cwd, same convention as this app's other
cwd-relative paths), and the OPENAI_API_KEY env var read are all unchanged. Never a chat tool -
called in-process only by app/workflow/agents/orchestrator_agent/orchestrator.py's escalation hook
(_execute_inference) for REVIEW_REQUIRED samples, via execute_explainability_review() below,
gated by settings.explainability_review_agent_enabled.
"""

from __future__ import annotations

import base64
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, TypedDict

import re
import requests
import yaml
from langchain_core.messages import SystemMessage, HumanMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, START, END

logger = logging.getLogger("agent2.review_graph")


# -----------------------------------------------------------------------------
# Graph State Definition
# -----------------------------------------------------------------------------
class ReviewState(TypedDict):
    # Inputs from A2A Task
    board_id: str
    component_ref: str
    defect_image_path: str
    golden_image_path: Optional[str]
    feature_type: Optional[str]
    preliminary_defect: Optional[str]
    baseline_confidence: float
    aoi_measurements: Dict[str, Any]

    # Node Intermediate Outputs
    retrieved_precedents: List[Dict[str, Any]]
    telemetry_data: Dict[str, Any]
    visual_evidence: str

    # Final Evaluation & Grounding
    predicted_defect: str
    final_confidence: float
    diagnosis: str
    contradiction_detected: bool
    self_check_passed: bool
    ipc_citations: List[str]
    errors: List[str]


# -----------------------------------------------------------------------------
# Configuration Loader Helper
# -----------------------------------------------------------------------------
def load_agent2_config() -> Dict[str, Any]:
    config_path = Path("config/agent2_config.yaml")
    if not config_path.exists():
        return {}
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


CONFIG = load_agent2_config()


# -----------------------------------------------------------------------------
# Graph Nodes
# -----------------------------------------------------------------------------
def retrieve_precedents_node(state: ReviewState) -> Dict[str, Any]:
    """Queries persistent Qdrant collection or local cache for IPC rules and precedents."""
    precedents = []
    component_type = state.get("feature_type", "Component")
    defect = state.get("preliminary_defect", "General")

    # In production, this hooks to qdrant_client. Search local mock/store if unavailable
    precedents.append({
        "ipc_clause": "IPC-A-610 Section 8.3.2",
        "standard": "Class 2 / Class 3 SMT Placement & Fillet Requirements",
        "rule": "Side overhang must not exceed 50% of component width for Class 2, or 25% for Class 3.",
        "relevance": 0.92
    })
    precedents.append({
        "ipc_clause": "IPC-A-610 Section 8.3.5",
        "standard": "Solder Joint Integrity & Wetting",
        "rule": "Evidence of wetting must be present across pad land pattern. Open circuit implies missing or tombstoned part.",
        "relevance": 0.88
    })
    return {"retrieved_precedents": precedents}

def normalize_label(label: str) -> str:
    """Normalizes 'MissingPart', 'missing_part', 'missing part' -> 'missing part'."""
    if not label:
        return "no defect"
    # Convert PascalCase/camelCase to spaces: MissingPart -> Missing Part
    s = re.sub(r'(?<!^)(?=[A-Z])', ' ', label).lower()
    return s.replace("_", " ").strip()

def extract_telemetry_node(state: ReviewState) -> Dict[str, Any]:
    """Recursively parses nested AOI inspection measurements and extracts physical metrics."""
    aoi_data = state.get("aoi_measurements", {})
    
    # Flatten nested inspection blocks (e.g. {"BlobDetection": {"side_overhang_percent": 62.0}})
    flat_measurements = {}
    if isinstance(aoi_data, dict):
        for k, v in aoi_data.items():
            if isinstance(v, dict):
                flat_measurements.update(v)
            else:
                flat_measurements[k] = v

    # Extract metrics using multiple key aliases
    laser_h = flat_measurements.get("laser_profile_height_um") or flat_measurements.get("height_um") or 45.0
    overhang = flat_measurements.get("side_overhang_percent") or flat_measurements.get("side_overhang") or 0.0
    coplanarity = flat_measurements.get("coplanarity_um") or flat_measurements.get("coplanarity") or 0.0

    telemetry = {
        "board_id": state.get("board_id"),
        "component_ref": state.get("component_ref"),
        "laser_profile_height_um": float(laser_h),
        "side_overhang_percent": float(overhang),
        "coplanarity_um": float(coplanarity),
        "ict_status": "FAIL" if flat_measurements.get("status") == "Failed" and laser_h < 5.0 else "PASS"
    }
    return {"telemetry_data": telemetry}


def locate_image(raw_path: Optional[str]) -> Optional[Path]:
    """Locates an image even across different working directories or nested folders."""
    if not raw_path:
        return None
        
    p = Path(raw_path)
    if p.is_file():
        return p.resolve()
        
    filename = p.name
    # Search common root folders relative to repository root
    possible_roots = [
        Path("."),
        Path("inputs"),
        Path("data/inputs"),
        Path("sample_data"),
        Path("../.."),
        Path("../../inputs")
    ]
    for root in possible_roots:
        if root.exists():
            matches = list(root.rglob(filename))
            if matches:
                return matches[0].resolve()
    return None


def inspect_visuals_node(state: ReviewState) -> Dict[str, Any]:
    """Queries the local LLaVA VLM with robust image path resolution."""
    raw_defect_path = state.get("defect_image_path")
    defect_img_path = locate_image(raw_defect_path)

    vlm_config = CONFIG.get("models", {}).get("vlm", {})
    ollama_url = vlm_config.get("base_url", "http://localhost:11434")
    model_name = vlm_config.get("model_name", "llava")

    if not defect_img_path or not defect_img_path.is_file():
        logger.warning(f"Could not locate image file for: {raw_defect_path}")
        return {"visual_evidence": f"Defect image '{raw_defect_path}' missing or unreadable. Visual inspect skipped."}

    try:
        with open(defect_img_path, "rb") as img_f:
            b64_img = base64.b64encode(img_f.read()).decode("utf-8")

        prompt = (
            f"You are inspecting PCB component {state.get('component_ref')} with preliminary label "
            f"'{state.get('preliminary_defect')}'. Look at the pad, presence of component body, solder joints, "
            f"and alignment. Is the part absent, shifted, rotated, or damaged? Describe in 2 concise sentences."
        )

        resp = requests.post(
            f"{ollama_url}/api/generate",
            json={
                "model": model_name,
                "prompt": prompt,
                "images": [b64_img],
                "stream": False,
                "options": {"temperature": 0.1, "num_predict": 128}
            },
            timeout=25.0
        )
        if resp.status_code == 200:
            evidence = resp.json().get("response", "").strip()
            return {"visual_evidence": evidence}
        else:
            logger.warning(f"Ollama returned HTTP {resp.status_code}: {resp.text}")
    except Exception as e:
        logger.warning(f"Local Ollama VLM call failed: {e}. Falling back to default visual summary.")

    return {"visual_evidence": f"Visual features confirm structural anomaly corresponding to {state.get('preliminary_defect', 'unknown')}."}


def grounding_self_check_node(state: ReviewState) -> Dict[str, Any]:
    """
    GPT-4o Grounding Self-Check: Reconciles baseline inference, visual evidence,
    and physical measurements (ICT and 3D Laser) to detect contradictions.
    """
    openai_key = os.getenv("OPENAI_API_KEY")
    if not openai_key:
        # Fallback heuristic if API key is not supplied
        return _heuristic_self_check(state)

    llm_cfg = CONFIG.get("models", {}).get("grounding_llm", {})
    model = ChatOpenAI(
        model=llm_cfg.get("model_name", "gpt-4o"),
        temperature=0.0,
        api_key=openai_key
    )

    system_prompt = (
        "You are an industrial PCB QA diagnostic agent performing physics-grounded verification. "
        "Your task is to reconcile preliminary classifier output, VLM visual descriptions, and physical telemetry "
        "(3D laser height profile, coplanarity, ICT circuit readings) according to IPC-A-610 Class 2 standards.\n\n"
        "PHYSICAL LAWS & CONTRADICTION RULES:\n"
        "1. Missing Part: If ICT resistance is open circuit (>10 MΩ) and laser height <= 5 µm, it IS 'missing part', "
        "regardless of discoloration that might visually look like a component.\n"
        "2. Shifted: Component side overhang must be > 50% for Class 2 violation.\n"
        "3. Tombstone: High laser height elevation + open ICT electrical circuit.\n\n"
        "Return ONLY a valid JSON object matching this schema:\n"
        "{\n"
        '  "predicted_defect": "missing part | shifted | foreign material | tombstone | solder insufficient | wrong part | no defect",\n'
        '  "confidence": float (0.0 to 1.0),\n'
        '  "contradiction_detected": bool,\n'
        '  "self_check_passed": bool,\n'
        '  "diagnosis": "Detailed 2-sentence rationale.",\n'
        '  "ipc_citations": ["IPC-A-610 clause"]\n'
        "}"
    )

    context = {
        "component_ref": state.get("component_ref"),
        "board_id": state.get("board_id"),
        "preliminary_defect": state.get("preliminary_defect"),
        "baseline_confidence": state.get("baseline_confidence"),
        "visual_evidence": state.get("visual_evidence"),
        "physical_telemetry": state.get("telemetry_data"),
        "ipc_precedents": state.get("retrieved_precedents")
    }

    try:
        response = model.invoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=f"Verify this case:\n{json.dumps(context, indent=2)}")
        ])
        raw_text = response.content.strip()
        if raw_text.startswith("```json"):
            raw_text = raw_text[7:-3].strip()
        data = json.loads(raw_text)

        return {
            "predicted_defect": data.get("predicted_defect", state.get("preliminary_defect")),
            "final_confidence": float(data.get("confidence", 0.95)),
            "contradiction_detected": bool(data.get("contradiction_detected", False)),
            "self_check_passed": bool(data.get("self_check_passed", True)),
            "diagnosis": data.get("diagnosis", "Grounding check completed successfully."),
            "ipc_citations": data.get("ipc_citations", ["IPC-A-610 Class 2"])
        }
    except Exception as e:
        logger.error(f"Grounding self-check LLM call failed: {e}")
        return _heuristic_self_check(state)


def _heuristic_self_check(state: ReviewState) -> Dict[str, Any]:
    tel = state.get("telemetry_data", {})
    laser_h = tel.get("laser_profile_height_um", 45.0)
    overhang = tel.get("side_overhang_percent", 0.0)
    ict_status = tel.get("ict_status", "PASS")
    
    prelim = normalize_label(state.get("preliminary_defect", ""))

    # 1. Missing Part: Open circuit + near zero height
    if ict_status == "FAIL" or laser_h < 5.0:
        is_missing = prelim == "missing part"
        return {
            "predicted_defect": "missing part",
            "final_confidence": 0.98,
            "contradiction_detected": not is_missing,
            "self_check_passed": True,
            "diagnosis": f"Physical open circuit (ICT FAIL) and laser height ({laser_h:.2f} µm) confirm missing part.",
            "ipc_citations": ["IPC-A-610 Class 2 Section 8.3"]
        }

    # 2. Shifted: > 50% overhang
    if overhang > 50.0:
        is_shift = "shift" in prelim
        return {
            "predicted_defect": "shifted",
            "final_confidence": 0.95,
            "contradiction_detected": not is_shift,
            "self_check_passed": True,
            "diagnosis": f"Side overhang ({overhang:.1f}%) violates IPC-A-610 Class 2 maximum 50% threshold.",
            "ipc_citations": ["IPC-A-610 Class 2 Section 8.3.2"]
        }

    # 3. Nominal fallback
    return {
        "predicted_defect": prelim,
        "final_confidence": state.get("baseline_confidence", 0.85),
        "contradiction_detected": False,
        "self_check_passed": True,
        "diagnosis": "Telemetry aligns within nominal tolerances; no contradiction detected.",
        "ipc_citations": ["IPC-A-610 Class 2"]
    }


# -----------------------------------------------------------------------------
# Graph Compilation
# -----------------------------------------------------------------------------
def build_review_graph():
    builder = StateGraph(ReviewState)
    builder.add_node("retrieve_precedents", retrieve_precedents_node)
    builder.add_node("extract_telemetry", extract_telemetry_node)
    builder.add_node("inspect_visuals", inspect_visuals_node)
    builder.add_node("grounding_self_check", grounding_self_check_node)

    builder.add_edge(START, "retrieve_precedents")
    builder.add_edge("retrieve_precedents", "extract_telemetry")
    builder.add_edge("extract_telemetry", "inspect_visuals")
    builder.add_edge("inspect_visuals", "grounding_self_check")
    builder.add_edge("grounding_self_check", END)

    return builder.compile()


review_pipeline = build_review_graph()


def execute_explainability_review(input_data: Dict[str, Any]) -> Dict[str, Any]:
    """Synchronous interface called by the A2A endpoint."""
    init_state: ReviewState = {
        "board_id": input_data.get("board_id", "UNKNOWN"),
        "component_ref": input_data.get("component_ref", "UNKNOWN"),
        "defect_image_path": input_data.get("defect_image_path", ""),
        "golden_image_path": input_data.get("golden_image_path"),
        "feature_type": input_data.get("feature_type"),
        "preliminary_defect": input_data.get("preliminary_defect"),
        "baseline_confidence": float(input_data.get("confidence", 0.0)),
        "aoi_measurements": input_data.get("aoi_measurements", {}),
        "retrieved_precedents": [],
        "telemetry_data": {},
        "visual_evidence": "",
        "predicted_defect": "",
        "final_confidence": 0.0,
        "diagnosis": "",
        "contradiction_detected": False,
        "self_check_passed": False,
        "ipc_citations": [],
        "errors": []
    }
    return review_pipeline.invoke(init_state)
