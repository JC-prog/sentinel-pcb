# agent.py
import logging
import json
import re
from pathlib import Path
from typing import TypedDict, Literal, Optional, Any, List, Dict, Union
from PIL import Image

from langgraph.graph import StateGraph, START, END

logger = logging.getLogger(__name__)

from src.models.model_registry import registry 
from src.mcp.pcb_mcp_server import mcp_client

# ── Telemetry Cache Helper ──────────────────────────────────────────────────

_TELEMETRY_CACHE: Optional[Dict[str, Any]] = None

def _get_telemetry_database() -> Dict[str, Any]:
    """Loads outputs/telemetry_by_image.json with in-memory caching."""
    global _TELEMETRY_CACHE
    if _TELEMETRY_CACHE is not None:
        return _TELEMETRY_CACHE

    # Check both current working directory and relative to this file
    potential_paths = [
        Path("outputs") / "telemetry_by_image.json",
        Path(__file__).resolve().parent / "outputs" / "telemetry_by_image.json",
        Path(__file__).resolve().parent.parent / "outputs" / "telemetry_by_image.json",
    ]

    for p in potential_paths:
        if p.is_file():
            try:
                with open(p, "r", encoding="utf-8") as f:
                    _TELEMETRY_CACHE = json.load(f)
                logger.info("Loaded telemetry lookup table from: %s", p)
                return _TELEMETRY_CACHE
            except Exception as e:
                logger.warning("Failed loading telemetry file %s: %s", p, e)

    logger.warning("outputs/telemetry_by_image.json not found in search paths.")
    return {}


# ── State Definition ────────────────────────────────────────────────────────

DefectType = Literal[
    "missing part", 
    "shifted", 
    "foreign material", 
    "tombstone", 
    "solder insufficient", 
    "wrong part", 
    "no defect", 
    "unknown"
]

class PCBInspectionState(TypedDict):
    image: Image.Image
    image_name: Optional[str]   # Filename for lookup (e.g. 'Board1_R131_Body_...jpg')
    board_id: str
    component_ref: str          
    issue_symptom: str          
    
    # MCP Tool Outputs
    historical_context: str
    reference_standards: str
    visual_bounding_boxes: List[Dict[str, Any]]
    visual_description: str
    measurements: Dict[str, Any]
    defect_location: Optional[Any]     
    
    # OpenAI Reasoning Outputs
    final_defect_category: DefectType
    final_diagnosis_text: str
    grounding_confidence: float
    self_check_passed: bool
    errors: List[str]


# ── Prompts ─────────────────────────────────────────────────────────────────

_VISUAL_QA_PROMPT = """
Examine this Printed Circuit Board (PCB) Region of Interest (ROI) carefully. 
Perform a strict, step-by-step visual inspection:

1. COMPONENT PRESENCE: Is the main electronic component (chip, resistor, capacitor) physically present on its pads? (Yes / No).
2. VISUAL OBSERVATION: 
   - If No (missing), describe the bare pads or remaining solder paste, and DO NOT invent solder defects.
   - If Yes (present), objectively describe any visual anomalies (e.g., component body overhang, solder void, lifted termination, unexpected debris).
3. SPATIAL LOCALIZATION: Provide the bounding box of the specific observation in [ymin, xmin, ymax, xmax] normalized format (scale 0-1000). Pinpoint the exact location relative to features on the board (e.g., "entire land pattern", "left terminal").

Do NOT classify the defect into a predefined category. Stick strictly to physical visual evidence.
"""

_OPENAI_REASONING_PROMPT = """
Review the evidence gathered via MCP tools for component {component_ref} on board {board_id}.

1. SYMPTOM: {issue_symptom}
2. HISTORICAL CONTEXT: {historical_context}
3. REFERENCE STANDARD: {reference_standards}
4. VISUAL EVIDENCE (via VLM): {visual_evidence}
5. MEASUREMENT EVIDENCE (via MCP ICT Telemetry): {measurement_evidence}

CRITICAL REASONING RULES:
- If MEASUREMENT EVIDENCE shows an OPEN circuit, infinite resistance, or ~0 capacitance, the part is either completely detached or MISSING. This overrides minor visual anomalies.
- If VISUAL EVIDENCE explicitly states the component is not present, classify as "missing part".

TASKS:
A. DIAGNOSIS CATEGORY: Select exactly one from: ["missing part", "shifted", "foreign material", "tombstone", "solder insufficient", "wrong part", "no defect"].
B. PHYSICAL EXPLANATION: Detail the root cause mechanism based on standards and physics.
C. GROUNDING SELF-CHECK: Verify if visual observations match electrical measurements. If the VLM claims a solder defect but ICT shows an open circuit, flag the contradiction and fail the self-check.
D. LOCATION: Report the exact defect location on the PCB, citing specific pad/lead landmarks and bounding coordinates [ymin, xmin, ymax, xmax].

Output strictly as a valid JSON object:
{{
  "defect_category": string,
  "defect_location": {{
    "landmark": string,
    "bounding_box": [ymin, xmin, ymax, xmax] or null
  }},
  "explanation": string,
  "contradictions_found": string,
  "confidence_score": float,
  "self_check_passed": boolean
}}
"""

def _format_similar_cases(similar: List[Dict[str, Any]]) -> str:
    if not similar:
        return "No visually similar historical cases found in Qdrant."
    lines = []
    for i, s in enumerate(similar[:3], 1):
        lines.append(f"CASE {i} (Score: {s.get('score', 0.0):.2f}): Category: {s.get('defect_category')} | Root Cause: {s.get('root_cause')}")
    return "\n".join(lines)


# ── Agent Nodes ──────────────────────────────────────────────────────────────

def tool1_context_retrieval_node(state: PCBInspectionState) -> PCBInspectionState:
    """Tool 1: Calls MCP Server for Qdrant Search & IPC Standards."""
    logger.info("Tool 1 [MCP]: Gathering Context for %s", state["component_ref"])
    errors = state.get("errors", [])
    try:
        similar_cases = mcp_client.search_historical(state["component_ref"])
        standards_data = mcp_client.get_standards(state["component_ref"])
        
        state["historical_context"] = _format_similar_cases(similar_cases)
        state["reference_standards"] = standards_data.get("standard_id", "Standard not defined.")
    except Exception as exc:
        logger.exception("Context Retrieval failed.")
        errors.append(f"ContextRetrieval: {exc}")
    state["errors"] = errors
    return state

def tool2_visual_evidence_node(state: PCBInspectionState) -> PCBInspectionState:
    """Tool 2: Extracts visual evidence via Object Detector + LLaVA."""
    logger.info("Tool 2: Gathering Visual Evidence")
    errors = state.get("errors", [])
    try:
        det_result = registry.pcb_detector.detect(state["image"])
        visual_desc = registry.llava.query(state["image"], _VISUAL_QA_PROMPT)
        state["visual_bounding_boxes"] = det_result.get("defects", [])
        state["visual_description"] = visual_desc
    except Exception as exc:
        logger.exception("Visual Evidence failed.")
        errors.append(f"VisualEvidence: {exc}")
    state["errors"] = errors
    return state

def tool3_measurement_evidence_node(state: PCBInspectionState) -> PCBInspectionState:
    """
    Tool 3: Retrieves ICT & 3D AOI telemetry from outputs/telemetry_by_image.json.
    Falls back to mcp_client.get_measurements if the file or key is unavailable.
    """
    logger.info("Tool 3: Gathering Electrical & Laser Height Telemetry")
    errors = state.get("errors", [])
    telemetry = None

    try:
        db = _get_telemetry_database()
        image_name = state.get("image_name")
        board_id = state.get("board_id")
        comp_ref = state.get("component_ref")

        # 1. Primary lookup by exact image filename
        if image_name and image_name in db:
            telemetry = db[image_name]
            logger.info("Matched telemetry via image_name: %s", image_name)

        # 2. Secondary fallback: Search matching board_id & component_ref
        elif db:
            for item in db.values():
                if item.get("board_id") == board_id and item.get("component_ref") == comp_ref:
                    telemetry = item
                    logger.info("Matched telemetry via board_id (%s) & comp_ref (%s)", board_id, comp_ref)
                    break

        # 3. Tertiary fallback: Call MCP server client
        if not telemetry:
            logger.warning("No pre-generated record found in outputs. Falling back to mcp_client.")
            telemetry = mcp_client.get_measurements(board_id, comp_ref)

        state["measurements"] = telemetry

    except Exception as exc:
        logger.exception("Measurement extraction failed.")
        errors.append(f"MeasurementEvidence: {exc}")
        state["measurements"] = {}

    state["errors"] = errors
    return state

def tool4_reasoning_and_grounding_node(state: PCBInspectionState) -> PCBInspectionState:
    """Tool 4: Synthesizes MCP telemetry using OpenAI GPT-4o."""
    logger.info("Tool 4 [OpenAI]: Executing Reasoning & Self-Check")
    errors = state.get("errors", [])
    prompt = _OPENAI_REASONING_PROMPT.format(
        component_ref=state.get("component_ref", "Unknown"),
        board_id=state.get("board_id", "Unknown"),
        issue_symptom=state.get("issue_symptom", "AOI anomaly review"),
        historical_context=state.get("historical_context", ""), 
        reference_standards=state.get("reference_standards", ""),
        visual_evidence=state.get("visual_description", ""),
        measurement_evidence=json.dumps(state.get("measurements", {}))
    )
    
    try:
        response_text = registry.reasoning_llm.query(prompt, require_json=True)
        
        clean_text = response_text.strip()
        if clean_text.startswith("```"):
            clean_text = clean_text.split("```")[1]
            if clean_text.startswith("json"):
                clean_text = clean_text[4:]
            clean_text = clean_text.strip()
            
        response_data = json.loads(clean_text)
        
        extracted_category = response_data.get("defect_category", "unknown").lower()
        valid_classes = [
            "missing part", "shifted", "foreign material", 
            "tombstone", "solder insufficient", "wrong part", "no defect"
        ]
        
        state["final_defect_category"] = extracted_category if extracted_category in valid_classes else "unknown"
        state["defect_location"] = response_data.get("defect_location")
        state["final_diagnosis_text"] = response_data.get("explanation", "")
        state["grounding_confidence"] = float(response_data.get("confidence_score", 0.0))
        state["self_check_passed"] = bool(response_data.get("self_check_passed", False))
        
    except Exception as exc:
        logger.exception("Reasoning failed.")
        errors.append(f"ReasoningGrounding: {exc}")
        state["final_defect_category"] = "unknown"
        state["defect_location"] = None
        state["self_check_passed"] = False
        
    state["errors"] = errors
    return state

# Domain terminology that a legitimate PCB inspection explanation MUST reference
_PCB_DOMAIN_TERMS = {
    "solder", "pad", "terminal", "lead", "pin", "component", "resistor", 
    "capacitor", "ic", "trace", "land", "overhang", "fillet", "wetting", 
    "tombstone", "short", "open", "shift", "resistance", "capacitance", 
    "height", "ipc", "meniscus", "void", "coplanarity", "reflow", "flux"
}

# Forbidden conversational filler, prompt leaks, or meta-assistant language
_FORBIDDEN_PHRASES = [
    r"as an ai\b",
    r"as a language model\b",
    r"here is the (analysis|json|response)",
    r"i hope this helps",
    r"feel free to ask",
    r"system prompt",
    r"my instructions",
    r"i apologize",
    r"certainly!",
    r"sure thing"
]

def tool5_guardrail_node(state: PCBInspectionState) -> PCBInspectionState:
    """
    Guardrail Node: Audits reasoning text and JSON payload.
    Rejects or sanitizes irrelevant content, meta-dialogue, and hallucinated data.
    """
    logger.info("Tool 5 [Guardrail]: Running content relevance and schema verification")
    
    errors = state.get("errors", [])
    flags: List[str] = []
    
    explanation = state.get("final_diagnosis_text", "")
    category = state.get("final_defect_category", "unknown")
    location = state.get("defect_location")
    
    clean_explanation = explanation.strip()

    # ── Check 1: Disallowed Meta / Conversational Phrases ─────────────────
    for pattern in _FORBIDDEN_PHRASES:
        if re.search(pattern, clean_explanation, re.IGNORECASE):
            flags.append(f"Forbidden conversational/meta phrase matched: '{pattern}'")
            # Strip simple preambles if found at start
            clean_explanation = re.sub(f"^.*{pattern}[:,]?\\s*", "", clean_explanation, flags=re.IGNORECASE).strip()

    # ── Check 2: Domain Relevance Anchor ─────────────────────────────────
    # If the text has no mention of any standard PCB hardware term, it's off-topic
    explanation_words = set(re.findall(r"\b[a-z]+\b", clean_explanation.lower()))
    domain_matches = explanation_words.intersection(_PCB_DOMAIN_TERMS)
    
    if len(domain_matches) < 2 and category != "no defect":
        flags.append(
            f"Irrelevant content: explanation lacks sufficient PCB terminology (found: {list(domain_matches)})"
        )

    # ── Check 3: Coordinate Bounds Audit [0, 1000] ───────────────────────
    if location and isinstance(location, dict):
        bbox = location.get("bounding_box")
        if bbox:
            if not (isinstance(bbox, list) and len(bbox) == 4):
                flags.append("Malformed bounding_box: must be [ymin, xmin, ymax, xmax] of length 4")
            else:
                out_of_bounds = [v for v in bbox if not (isinstance(v, (int, float)) and 0 <= v <= 1000)]
                if out_of_bounds:
                    flags.append(f"Bounding box values out of normalized bounds [0-1000]: {out_of_bounds}")
                    location["bounding_box"] = None

    # ── Check 4: Enforce Valid Category ──────────────────────────────────
    valid_categories = {
        "missing part", "shifted", "foreign material", 
        "tombstone", "solder insufficient", "wrong part", "no defect"
    }
    if category not in valid_categories:
        flags.append(f"Invalid defect category provided: '{category}'")
        category = "unknown"

    # ── Decision & Action ────────────────────────────────────────────────
    guardrail_passed = len(flags) == 0

    if not guardrail_passed:
        logger.warning("Guardrail violations detected: %s", flags)
        errors.extend([f"GuardrailViolation: {f}" for f in flags])
        
        # If text is deemed irrelevant, replace with safe sanitized fallback
        if any("Irrelevant content" in f for f in flags):
            clean_explanation = (
                f"Inspection rejected by guardrail: generated text contained unverified or off-topic narrative. "
                f"Flagged terms: {flags}"
            )
            category = "unknown"
            state["self_check_passed"] = False

    state["final_diagnosis_text"] = clean_explanation
    state["final_defect_category"] = category
    state["guardrail_passed"] = guardrail_passed
    state["guardrail_flags"] = flags
    state["errors"] = errors

    return state
    
# ── LangGraph Workflow ───────────────────────────────────────────────────────

workflow = StateGraph(PCBInspectionState)

# Add Nodes
workflow.add_node("context_retrieval", tool1_context_retrieval_node)
workflow.add_node("visual_evidence", tool2_visual_evidence_node)
workflow.add_node("measurement_evidence", tool3_measurement_evidence_node)
workflow.add_node("reasoning", tool4_reasoning_and_grounding_node)
workflow.add_node("guardrail", tool5_guardrail_node)  # <-- Added Guardrail

# Add Edges
workflow.add_edge(START, "context_retrieval")
workflow.add_edge("context_retrieval", "visual_evidence")
workflow.add_edge("visual_evidence", "measurement_evidence")
workflow.add_edge("measurement_evidence", "reasoning")
workflow.add_edge("reasoning", "guardrail")           
workflow.add_edge("guardrail", END)                   

pcb_graph = workflow.compile()
