"""A small LangGraph pipeline for the ADC (Automated Defect Classification) inspection agent:
classify the component region (Body/Lead/Text) -> route to the matching defect classifier for
that region. Ported from orchestrator-agent/adc_agentic_project's two-stage feature->defect
routing (services/multimodal_inference.py, inference/router.py), but calling out to the
inference/ microservice (app.inference.client) for both stages instead of loading ONNX
in-process - see inference/models.toml for the four registered PCBInspect-* models.

Node functions are async and call app.inference.client.classify() directly, unlike
time_agent/weather_agent's sync-node-run-via-asyncio.to_thread pattern - there's no blocking call
to hide behind a thread here, classify() is already async httpx.

Never raises: any inference failure becomes state["error"], surfaced by tool.py as a
{"error": ...} tool result - same graceful-degradation convention as every other agent.
"""

import logging
from typing import Any, Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.inference import InferenceError, InferenceNotConfigured, classify

logger = logging.getLogger(__name__)

_REGION_MODEL = "pcb_region"

# Region label (from pcb_region's own class order) -> the defect model that classifies it.
_DEFECT_MODEL_BY_REGION = {
    "Body": "pcb_body_defect",
    "Lead": "pcb_lead_defect",
    "Text": "pcb_text_defect",
}


class AdcInspectionState(TypedDict):
    image_bytes: bytes
    image_name: str
    username: str
    region: str
    region_confidence: float
    region_scores: dict[str, float]
    defect_model: str
    defect_label: str
    defect_confidence: float
    defect_scores: dict[str, float]
    error: str | None


async def _classify_region_node(state: AdcInspectionState) -> AdcInspectionState:
    """Stage 1: which part of the component is this image a crop of."""

    try:
        result = await classify(
            model=_REGION_MODEL,
            username=state["username"],
            image=state["image_bytes"],
            filename=state["image_name"],
        )
    except InferenceNotConfigured as exc:
        state["error"] = f"Inference service not configured: {exc}"
        return state
    except InferenceError as exc:
        state["error"] = f"Region classification failed: {exc}"
        return state

    state["region"] = result.label
    state["region_confidence"] = result.confidence
    state["region_scores"] = result.scores
    return state


def _route_after_region(state: AdcInspectionState) -> Literal["classify_defect", "end"]:
    if state.get("error"):
        return "end"
    return "classify_defect" if state["region"] in _DEFECT_MODEL_BY_REGION else "end"


async def _classify_defect_node(state: AdcInspectionState) -> AdcInspectionState:
    """Stage 2: the defect classifier matching the region picked in stage 1."""

    defect_model = _DEFECT_MODEL_BY_REGION.get(state["region"])
    if defect_model is None:
        state["error"] = f"No defect classifier routed for region {state['region']!r}."
        return state
    state["defect_model"] = defect_model

    try:
        result = await classify(
            model=defect_model,
            username=state["username"],
            image=state["image_bytes"],
            filename=state["image_name"],
        )
    except InferenceNotConfigured as exc:
        state["error"] = f"Inference service not configured: {exc}"
        return state
    except InferenceError as exc:
        state["error"] = f"Defect classification failed: {exc}"
        return state

    state["defect_label"] = result.label
    state["defect_confidence"] = result.confidence
    state["defect_scores"] = result.scores
    return state


def build_graph() -> CompiledStateGraph[AdcInspectionState, Any, Any, Any]:
    workflow = StateGraph(AdcInspectionState)
    workflow.add_node("classify_region", _classify_region_node)
    workflow.add_node("classify_defect", _classify_defect_node)

    workflow.add_edge(START, "classify_region")
    workflow.add_conditional_edges(
        "classify_region",
        _route_after_region,
        {"classify_defect": "classify_defect", "end": END},
    )
    workflow.add_edge("classify_defect", END)

    return workflow.compile()


_pipeline: CompiledStateGraph[AdcInspectionState, Any, Any, Any] | None = None


def get_pipeline() -> CompiledStateGraph[AdcInspectionState, Any, Any, Any]:
    """Lazy, process-wide singleton - cheap to build, but no reason to rebuild it per call."""

    global _pipeline
    if _pipeline is None:
        _pipeline = build_graph()
    return _pipeline
