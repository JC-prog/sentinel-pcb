# agent.py
import logging
import json
from typing import Any, TypedDict, Literal
from PIL import Image

from langgraph.graph import StateGraph, START, END

logger = logging.getLogger(__name__)

from src.models.model_registry import registry 
from src.mcp.pcb_mcp_server import mcp_client

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
    board_id: str
    component_ref: str          
    issue_symptom: str          
    
    # MCP Tool Outputs
    historical_context: str
    reference_standards: str
    visual_bounding_boxes: list[dict]
    visual_description: str
    measurements: dict[str, Any] 
    
    # OpenAI Reasoning Outputs
    final_defect_category: DefectType
    final_diagnosis_text: str
    grounding_confidence: float
    self_check_passed: bool
    errors: list[str]


# ── Prompts ─────────────────────────────────────────────────────────────────

_VISUAL_QA_PROMPT = """
Examine this Printed Circuit Board (PCB) ROI carefully. 
Classify the visual anomaly strictly into one of the following defect types:
1. "missing part": The component is absent from its pads.
2. "shifted": The component is misaligned or off-center from pads.
3. "foreign material": Unexpected debris or conductive material nearby.
4. "tombstone": Component standing vertically on one end.
5. "solder insufficient": Thin, starved solder joint on leads.
6. "wrong part": Wrong component size, package, or markings.
7. "no defect": Normal, properly soldered component.

State which defect type is visible and describe its morphology.
"""

_OPENAI_REASONING_PROMPT = """
Review the evidence gathered via MCP tools for component {component_ref} on board {board_id}.

1. SYMPTOM: {issue_symptom}
2. HISTORICAL CONTEXT (via MCP Qdrant): 
{historical_context}
3. REFERENCE STANDARD (via MCP Standards DB):
{reference_standards}
4. VISUAL EVIDENCE (via VLM):
{visual_evidence}
5. MEASUREMENT EVIDENCE (via MCP ICT Telemetry): 
{measurement_evidence}

TASKS:
A. DIAGNOSIS CATEGORY: Select exactly one from: ["missing part", "shifted", "foreign material", "tombstone", "solder insufficient", "wrong part", "no defect"].
B. PHYSICAL EXPLANATION: Detail the root cause mechanism.
C. GROUNDING SELF-CHECK: Verify if visual observations match electrical measurements (e.g., missing parts must exhibit open circuits / 0 height).

Output strictly as a JSON object with keys:
"defect_category": string,
"explanation": string,
"contradictions_found": string,
"confidence_score": float (0.0 to 1.0),
"self_check_passed": boolean
"""

def _format_similar_cases(similar: list[dict]) -> str:
    if not similar:
        return "No visually similar historical cases found in Qdrant."
    lines = []
    for i, s in enumerate(similar[:3], 1):
        lines.append(f"CASE {i} (Score: {s.get('score', 0.0):.2f}): Category: {s.get('defect_category')} | Root Cause: {s.get('root_cause')}")
    return "\n".join(lines)


# ── Agent Nodes (Calling MCP Tools) ──────────────────────────────────────────

def tool1_context_retrieval_node(state: PCBInspectionState) -> PCBInspectionState:
    """Tool 1: Calls MCP Server for Qdrant Search & IPC Standards."""
    logger.info("Tool 1 [MCP]: Gathering Context for %s", state["component_ref"])
    errors = state.get("errors", [])
    try:
        # Call MCP tools
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
    """Tool 3: Calls MCP Server for ICT and 3D Laser height telemetry."""
    logger.info("Tool 3 [MCP]: Gathering Electrical & Height Telemetry")
    errors = state.get("errors", [])
    try:
        # Call MCP measurement tool
        measurements = mcp_client.get_measurements(state["board_id"], state["component_ref"])
        state["measurements"] = measurements
    except Exception as exc:
        logger.exception("Measurement extraction failed.")
        errors.append(f"MeasurementEvidence: {exc}")
    state["errors"] = errors
    return state

def tool4_reasoning_and_grounding_node(state: PCBInspectionState) -> PCBInspectionState:
    """Tool 4: Synthesizes MCP telemetry using OpenAI GPT-4o."""
    logger.info("Tool 4 [OpenAI]: Executing Reasoning & Self-Check")
    errors = state.get("errors", [])
    prompt = _OPENAI_REASONING_PROMPT.format(
        component_ref=state["component_ref"],
        board_id=state["board_id"],
        issue_symptom=state["issue_symptom"],
        historical_context=state["historical_context"], 
        reference_standards=state["reference_standards"],
        visual_evidence=state["visual_description"],
        measurement_evidence=json.dumps(state.get("measurements", {}))
    )
    
    try:
        # Calls OpenAI GPT-4o with guaranteed JSON output
        response_text = registry.reasoning_llm.query(prompt, require_json=True)
        response_data = json.loads(response_text)
        
        extracted_category = response_data.get("defect_category", "unknown").lower()
        valid_classes = ["missing part", "shifted", "foreign material", "tombstone", "solder insufficient", "wrong part", "no defect"]
        
        state["final_defect_category"] = extracted_category if extracted_category in valid_classes else "unknown"
        state["final_diagnosis_text"] = response_data.get("explanation", "")
        state["grounding_confidence"] = float(response_data.get("confidence_score", 0.0))
        state["self_check_passed"] = bool(response_data.get("self_check_passed", False))
    except Exception as exc:
        logger.exception("Reasoning failed.")
        errors.append(f"ReasoningGrounding: {exc}")
        state["final_defect_category"] = "unknown"
        state["self_check_passed"] = False
        
    state["errors"] = errors
    return state


# ── LangGraph Workflow ───────────────────────────────────────────────────────

workflow = StateGraph(PCBInspectionState)
workflow.add_node("context_retrieval", tool1_context_retrieval_node)
workflow.add_node("visual_evidence", tool2_visual_evidence_node)
workflow.add_node("measurement_evidence", tool3_measurement_evidence_node)
workflow.add_node("reasoning", tool4_reasoning_and_grounding_node)

workflow.add_edge(START, "context_retrieval")
workflow.add_edge("context_retrieval", "visual_evidence")
workflow.add_edge("visual_evidence", "measurement_evidence")
workflow.add_edge("measurement_evidence", "reasoning")
workflow.add_edge("reasoning", END)

pcb_graph = workflow.compile()
