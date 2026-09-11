# agent.py
import json
import logging
from typing import Any, TypedDict, Literal
from langgraph.graph import StateGraph, START, END

# Import Agent 2's MCP Tools directly
from src.mcp.agent2_mcp_server import (
    case_context_retrieval_tool,
    visual_evidence_tool,
    measurement_evidence_tool,
    grounding_and_self_check_tool
)

logger = logging.getLogger(__name__)

DefectType = Literal[
    "missing part", "shifted", "foreign material", "tombstone", 
    "solder insufficient", "wrong part", "no defect", "unknown"
]

class PCBInspectionState(TypedDict):
    image_path: str
    board_id: str
    component_ref: str          
    issue_symptom: str          
    historical_context: str
    reference_standards: str
    visual_bounding_boxes: list[dict]
    visual_description: str
    measurements: dict[str, Any] 
    final_defect_category: DefectType
    final_diagnosis_text: str
    grounding_confidence: float
    self_check_passed: bool
    errors: list[str]

_VISUAL_QA_PROMPT = """
Examine this PCB ROI carefully. Classify the defect strictly into one of:
["missing part", "shifted", "foreign material", "tombstone", "solder insufficient", "wrong part", "no defect"].
Describe morphology and location.
"""

_REASONING_PROMPT = """
Review evidence for component {component_ref} on board {board_id}.
1. SYMPTOM: {issue_symptom}
2. HISTORICAL (MCP): {historical_context}
3. VISUAL (MCP): {visual_evidence}
4. MEASUREMENTS (MCP): {measurement_evidence}

Diagnose one of: ["missing part", "shifted", "foreign material", "tombstone", "solder insufficient", "wrong part", "no defect"].
Output JSON with: "defect_category", "explanation", "confidence_score" (0.0-1.0), "self_check_passed" (bool).
"""

# Node 1: Context via MCP
def tool1_context_node(state: PCBInspectionState) -> PCBInspectionState:
    logger.info("Agent 2 Node 1: Calling Case Context Retrieval MCP Tool")
    res = case_context_retrieval_tool(state["component_ref"])
    state["historical_context"] = str(res["similar_cases"])
    state["reference_standards"] = res["ipc_standard"]
    return state

# Node 2: Visual via MCP
def tool2_visual_node(state: PCBInspectionState) -> PCBInspectionState:
    logger.info("Agent 2 Node 2: Calling Visual Evidence MCP Tool")
    res = visual_evidence_tool(state["image_path"], _VISUAL_QA_PROMPT)
    state["visual_description"] = res["visual_description"]
    state["visual_bounding_boxes"] = res["bounding_boxes"]
    return state

# Node 3: Measurements via MCP
def tool3_measurements_node(state: PCBInspectionState) -> PCBInspectionState:
    logger.info("Agent 2 Node 3: Calling Measurement Evidence MCP Tool")
    res = measurement_evidence_tool(state["board_id"], state["component_ref"])
    state["measurements"] = res
    return state

# Node 4: Grounding via MCP
def tool4_grounding_node(state: PCBInspectionState) -> PCBInspectionState:
    logger.info("Agent 2 Node 4: Calling Grounding & Self-Check MCP Tool")
    prompt = _REASONING_PROMPT.format(
        component_ref=state["component_ref"],
        board_id=state["board_id"],
        issue_symptom=state["issue_symptom"],
        historical_context=state["historical_context"],
        visual_evidence=state["visual_description"],
        measurement_evidence=json.dumps(state["measurements"])
    )
    res = grounding_and_self_check_tool(prompt)
    state["final_defect_category"] = res.get("defect_category", "unknown").lower()
    state["final_diagnosis_text"] = res.get("explanation", "")
    state["grounding_confidence"] = float(res.get("confidence_score", 0.0))
    state["self_check_passed"] = bool(res.get("self_check_passed", False))
    return state

# Assemble Agent 2's LangGraph
workflow = StateGraph(PCBInspectionState)
workflow.add_node("node1", tool1_context_node)
workflow.add_node("node2", tool2_visual_node)
workflow.add_node("node3", tool3_measurements_node)
workflow.add_node("node4", tool4_grounding_node)

workflow.add_edge(START, "node1")
workflow.add_edge("node1", "node2")
workflow.add_edge("node2", "node3")
workflow.add_edge("node3", "node4")
workflow.add_edge("node4", END)

pcb_graph = workflow.compile()
