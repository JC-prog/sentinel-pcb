"""Ported from Kenny's Explainability_Review_Agent/agent.py - node logic, prompts, and graph
structure (context_retrieval -> visual_evidence -> measurement_evidence -> reasoning) are
unchanged. Restructured so nodes close over a `registry`/`mcp_client` pair instead of importing
module-level globals, since `registry` now carries a per-request OpenAI key (see tool.py) rather
than reading a single server-wide OPENAI_API_KEY at import time.
"""

import json
import logging
from pathlib import Path
from typing import Any, Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from PIL import Image

from app.agents.explainability_review_agent.mcp_client import PCBMCPClient
from app.agents.explainability_review_agent.models import ModelRegistry
from app.settings import settings

logger = logging.getLogger(__name__)

DATA_DIR = Path(settings.explainability_agent_data_dir)
_QDRANT_DB_DIR = DATA_DIR / "qdrant_db"
_TELEMETRY_FILE = DATA_DIR / "outputs" / "telemetry_by_image.json"

_mcp_client: PCBMCPClient | None = None


def get_mcp_client() -> PCBMCPClient:
    """Lazy, process-wide singleton - PCBMCPClient.__init__ loads a real CLIP model, so this
    must not run at import time (mirrors app/memory/qdrant_store.py's get_qdrant_client())."""

    global _mcp_client
    if _mcp_client is None:
        _mcp_client = PCBMCPClient(qdrant_path=str(_QDRANT_DB_DIR))
    return _mcp_client


_telemetry_cache: dict[str, Any] | None = None


def _get_telemetry_database() -> dict[str, Any]:
    """Loads data/outputs/telemetry_by_image.json (produced by
    scripts/explainability_agent/generate_telemetry.py), cached in-memory after first load."""

    global _telemetry_cache
    if _telemetry_cache is not None:
        return _telemetry_cache

    if _TELEMETRY_FILE.is_file():
        try:
            with open(_TELEMETRY_FILE, encoding="utf-8") as f:
                _telemetry_cache = json.load(f)
            logger.info("Loaded telemetry lookup table from: %s", _TELEMETRY_FILE)
            return _telemetry_cache
        except Exception:
            logger.exception("Failed loading telemetry file %s", _TELEMETRY_FILE)

    logger.warning("%s not found - falling back to mcp_client.get_measurements.", _TELEMETRY_FILE)
    _telemetry_cache = {}
    return _telemetry_cache


DefectType = Literal[
    "missing part",
    "shifted",
    "foreign material",
    "tombstone",
    "solder insufficient",
    "wrong part",
    "no defect",
    "unknown",
]


class PCBInspectionState(TypedDict):
    image: Image.Image
    image_name: str | None
    board_id: str
    component_ref: str
    issue_symptom: str
    historical_context: str
    reference_standards: str
    visual_bounding_boxes: list[dict[str, Any]]
    visual_description: str
    measurements: dict[str, Any]
    defect_location: Any | None
    final_defect_category: DefectType
    final_diagnosis_text: str
    grounding_confidence: float
    self_check_passed: bool
    errors: list[str]


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


def _format_similar_cases(similar: list[dict[str, Any]]) -> str:
    if not similar:
        return "No visually similar historical cases found in Qdrant."
    lines = []
    for i, s in enumerate(similar[:3], 1):
        lines.append(
            f"CASE {i} (Score: {s.get('score', 0.0):.2f}): "
            f"Category: {s.get('defect_category')} | Root Cause: {s.get('root_cause')}"
        )
    return "\n".join(lines)


def build_graph(
    registry: ModelRegistry, mcp_client: PCBMCPClient
) -> CompiledStateGraph[PCBInspectionState, Any, Any, Any]:
    """Compiles a fresh graph whose nodes close over the given registry/mcp_client. Cheap (no
    model loading happens here - that's already done by the time registry/mcp_client exist) -
    called once per pipeline invocation since `registry` carries a request-scoped OpenAI key."""

    def tool1_context_retrieval_node(state: PCBInspectionState) -> PCBInspectionState:
        """Tool 1: Calls MCP client for Qdrant search & IPC standards."""
        logger.info("Tool 1 [MCP]: Gathering Context for %s", state["component_ref"])
        errors = state.get("errors", [])
        try:
            similar_cases = mcp_client.search_historical(state["component_ref"])
            standards_data = mcp_client.get_standards(state["component_ref"])
            state["historical_context"] = _format_similar_cases(similar_cases)
            state["reference_standards"] = standards_data.get(
                "standard_id", "Standard not defined."
            )
        except Exception as exc:
            logger.exception("Context Retrieval failed.")
            errors.append(f"ContextRetrieval: {exc}")
        state["errors"] = errors
        return state

    def tool2_visual_evidence_node(state: PCBInspectionState) -> PCBInspectionState:
        """Tool 2: Extracts visual evidence via the (stub) detector + GPT-4o vision."""
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
        """Tool 3: Retrieves ICT & 3D AOI telemetry, falling back to mcp_client.get_measurements."""
        logger.info("Tool 3: Gathering Electrical & Laser Height Telemetry")
        errors = state.get("errors", [])
        telemetry = None

        try:
            db = _get_telemetry_database()
            image_name = state.get("image_name")
            board_id = state.get("board_id")
            comp_ref = state.get("component_ref")

            if image_name and image_name in db:
                telemetry = db[image_name]
                logger.info("Matched telemetry via image_name: %s", image_name)
            elif db:
                for item in db.values():
                    if item.get("board_id") == board_id and item.get("component_ref") == comp_ref:
                        telemetry = item
                        logger.info(
                            "Matched telemetry via board_id (%s) & comp_ref (%s)",
                            board_id,
                            comp_ref,
                        )
                        break

            if not telemetry:
                logger.warning("No pre-generated record found. Falling back to mcp_client.")
                telemetry = mcp_client.get_measurements(board_id, comp_ref)

            state["measurements"] = telemetry
        except Exception as exc:
            logger.exception("Measurement extraction failed.")
            errors.append(f"MeasurementEvidence: {exc}")
            state["measurements"] = {}

        state["errors"] = errors
        return state

    def tool4_reasoning_and_grounding_node(state: PCBInspectionState) -> PCBInspectionState:
        """Tool 4: Synthesizes evidence using GPT-4o with a self-check."""
        logger.info("Tool 4 [OpenAI]: Executing Reasoning & Self-Check")
        errors = state.get("errors", [])
        prompt = _OPENAI_REASONING_PROMPT.format(
            component_ref=state.get("component_ref", "Unknown"),
            board_id=state.get("board_id", "Unknown"),
            issue_symptom=state.get("issue_symptom", "AOI anomaly review"),
            historical_context=state.get("historical_context", ""),
            reference_standards=state.get("reference_standards", ""),
            visual_evidence=state.get("visual_description", ""),
            measurement_evidence=json.dumps(state.get("measurements", {})),
        )

        try:
            response_text = registry.reasoning_llm.query(prompt, require_json=True)

            clean_text = response_text.strip()
            if clean_text.startswith("```"):
                clean_text = clean_text.split("```")[1]
                clean_text = clean_text.removeprefix("json")
                clean_text = clean_text.strip()

            response_data = json.loads(clean_text)

            extracted_category = response_data.get("defect_category", "unknown").lower()
            valid_classes = [
                "missing part",
                "shifted",
                "foreign material",
                "tombstone",
                "solder insufficient",
                "wrong part",
                "no defect",
            ]

            state["final_defect_category"] = (
                extracted_category if extracted_category in valid_classes else "unknown"
            )
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

    return workflow.compile()


def get_pipeline(api_key: str) -> CompiledStateGraph[PCBInspectionState, Any, Any, Any]:
    """Entry point used by tool.py. `registry` is built fresh per call (cheap - just wraps an
    OpenAI client) since it carries the caller's API key; `mcp_client` is the lazy process-wide
    singleton (expensive - loads a CLIP model) shared across calls."""

    registry = ModelRegistry(api_key)
    mcp_client = get_mcp_client()
    return build_graph(registry, mcp_client)
