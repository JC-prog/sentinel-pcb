"""The orchestrator agent: a cyclic LangGraph pipeline that reproduces
orchestrator-agent/adc_agentic_project's Planner -> PolicyEngine -> execute -> replan loop
(agents/orchestrator.py) for exactly one case (one image, optionally one inspection XML), rather
than a batch of dataset rows.

A `plan` node calls Planner.plan() (planner.py) to propose the next action, then
PolicyEngine.validate_action() (policy_engine.py) to check it's actually legal given the current
state; a conditional edge routes to the corresponding execution node, which always edges back to
`plan` (except `abort`/`persist_case`, which end the run) - the graph keeps cycling through `plan`
until the plan/policy layer says the run is either done or must abort. This folds in what used to
be app/chat/agents/case_agent/graph.py (always persists a Case, whether ACCEPTED or REVIEW_REQUIRED)
plus two things neither case_agent nor the old, now-deleted, non-persisting inspection_agent
had: golden-image alignment/quality checks (align_and_check_quality, ported from
orchestrator-agent's verification/{image_alignment,image_quality}.py via verification.py).
REVIEW_REQUIRED is a terminal verdict here, as in orchestrator-agent: this agent no longer hands
off to any other agent (agents are mutually independent - tests/chat/test_agent_boundaries.py);
a deeper diagnosis is something the user asks the case agent for by case number.

One deliberate divergence from a naive port: Planner.plan() here is a pure function of state (see
planner.py's docstring for why no LLM planner), so a policy rejection cannot loop back to `plan`
the way it could when the proposer was a fallible LLM - asking a pure function the same question
against unchanged state would propose the same rejected action forever. A rejection here routes
straight to `abort` instead; PolicyEngine is a safety net against planner bugs, not a retry
mechanism.

Never raises: any inference/DB failure becomes state["error"], surfaced by tools.py - same
graceful-degradation convention as every other agent. An "error" state never reaches persist_case (routes to abort instead).
"""

import logging
from io import BytesIO
from typing import Any
from xml.etree import ElementTree as ET

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from PIL import Image, UnidentifiedImageError
from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.agents.inspection_agent import golden_images, verification
from app.chat.agents.inspection_agent.planner import Planner
from app.chat.agents.inspection_agent.policy_engine import PolicyEngine
from app.chat.agents.inspection_agent.workflow_state import OrchestratorState
from app.chat.db.models import CaseStatus, GoldenImage
from app.chat.services import cases, xml_measurements
from app.chat.services.cases import REGION_MODEL
from app.shared.config.settings import settings
from app.shared.inference import InferenceError, InferenceNotConfigured, classify

logger = logging.getLogger(__name__)

_REGION_MODEL = REGION_MODEL

# Region label (from pcb_region's own class order) -> the defect model that classifies it.
# Mirrors adc_agentic_project/inference/router.py's ROUTES.
_DEFECT_MODEL_BY_REGION = {
    "Body": "pcb_body_defect",
    "Lead": "pcb_lead_defect",
    "Text": "pcb_text_defect",
}

# Mirrors orchestrator-agent's max_replans safety net - a run here has at most ~9 concrete steps,
# so this is a generous ceiling against a planner/policy bug looping, not a tuned budget.
_MAX_REPLANS = 20

_EXEC_NODES = (
    "verify_image",
    "lookup_golden_image",
    "validate_measurements",
    "align_and_check_quality",
    "classify_region",
    "classify_defect",
    "finalize",
    "persist_case",
    "abort",
)


def verify_image_node(state: OrchestratorState) -> OrchestratorState:
    """Same PIL readability check as the old inspection_agent/case_agent graphs' verify_image."""

    try:
        with Image.open(BytesIO(state["image_bytes"])) as probe:
            probe.verify()
        width, height = Image.open(BytesIO(state["image_bytes"])).size
    except (UnidentifiedImageError, OSError, ValueError):
        state["image_readable"] = False
        state["error"] = "IMAGE_UNREADABLE: uploaded file is not a decodable image."
        state["observations"].append("Image verification: unreadable, aborting before inference.")
        return state

    state["image_readable"] = True
    state["observations"].append(f"Image verification: readable ({width}x{height}).")
    return state


def validate_measurements_node(state: OrchestratorState) -> OrchestratorState:
    """Only reached when an inspection XML was attached. Never raises: an unparseable XML or a
    feature that can't be matched becomes a recorded validation issue, not a pipeline error - a
    case can still be created from just the image."""

    xml_bytes = state["inspection_xml_bytes"]
    assert xml_bytes is not None  # only reachable when inspection_xml_bytes is not None

    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        state["measurement_validation"] = {
            "valid": False,
            "issues": ["XML_UNPARSEABLE"],
            "inspection_results": {},
        }
        state["observations"].append("Measurement validation: inspection XML was unparseable.")
        return state

    feature_xml = xml_measurements.find_failed_feature(
        root,
        board_id=state["board_id"],
        component_ref=state["component_ref"],
        package=state["package"],
        feature=state["feature"],
    )
    if feature_xml is None:
        state["measurement_validation"] = {
            "valid": False,
            "issues": ["FAILED_FEATURE_NOT_FOUND"],
            "inspection_results": {},
        }
        state["observations"].append(
            "Measurement validation: no matching failed feature found in the inspection XML."
        )
        return state

    measurements = xml_measurements.extract_all_failed_measurements(feature_xml)
    validation = xml_measurements.validate_measurements(measurements)
    state["measurement_validation"] = validation
    state["observations"].append(
        f"Measurement validation: {'passed' if validation['valid'] else 'failed'} "
        f"({len(validation['issues'])} issue(s))."
    )
    return state


async def classify_region_node(state: OrchestratorState) -> OrchestratorState:
    """Stage 1, same threshold gate as before."""

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
    state["region_model_version"] = result.model_version
    state["region_confidence"] = result.confidence
    state["region_scores"] = result.scores
    state["observations"].append(
        f"Region classification: {result.label} (confidence={result.confidence:.2f})."
    )

    if result.label not in _DEFECT_MODEL_BY_REGION:
        state["error"] = f"No defect classifier routed for region {result.label!r}."
        return state

    if result.confidence < settings.adc_region_confidence_threshold:
        state["region_uncertain"] = True
        state["observations"].append(
            "Region confidence below threshold "
            f"({result.confidence:.2f} < {settings.adc_region_confidence_threshold:.2f}); "
            "skipping defect classification."
        )

    return state


async def classify_defect_node(state: OrchestratorState) -> OrchestratorState:
    """Stage 2, the defect classifier matching the region picked in stage 1."""

    defect_model = _DEFECT_MODEL_BY_REGION[state["region"]]
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
    state["defect_model_version"] = result.model_version
    state["defect_confidence"] = result.confidence
    state["defect_scores"] = result.scores
    state["observations"].append(
        f"Defect classification: {result.label} (confidence={result.confidence:.2f})."
    )
    return state


def finalize_node(state: OrchestratorState) -> OrchestratorState:
    """Combines the defect-confidence gate with measurement-validation and alignment/quality
    override checks. Does not persist anything - that's persist_case's job."""

    region_or_defect_uncertain = state.get("region_uncertain") or (
        state["defect_confidence"] < settings.adc_defect_confidence_threshold
    )
    final_decision = "REVIEW_REQUIRED" if region_or_defect_uncertain else "ACCEPTED"

    measurement_validation = state.get("measurement_validation")
    if measurement_validation is not None and not measurement_validation["valid"]:
        if final_decision != "REVIEW_REQUIRED":
            state["observations"].append(
                "Measurement validation failed; downgrading to REVIEW_REQUIRED despite a "
                "confident classifier verdict."
            )
        final_decision = "REVIEW_REQUIRED"

    if any(issue.endswith("_FAILED") for issue in state.get("quality_issues") or []):
        if final_decision != "REVIEW_REQUIRED":
            state["observations"].append(
                "Golden-image alignment/quality check failed; downgrading to REVIEW_REQUIRED "
                "despite a confident classifier verdict."
            )
        final_decision = "REVIEW_REQUIRED"

    state["final_decision"] = final_decision
    state["observations"].append(f"Workflow finalized as {final_decision}.")
    return state


async def lookup_golden_image_node(
    session: AsyncSession, state: OrchestratorState
) -> OrchestratorState:
    """Never fails the pipeline - a missing golden reference is recorded, not an error."""

    golden = await golden_images.find_golden_image(
        session,
        board_id=state["board_id"],
        component_ref=state["component_ref"],
        package=state["package"],
        feature=state["feature"],
    )
    state["golden_lookup_done"] = True
    if golden is None:
        state["golden_image_id"] = None
        state["observations"].append(
            f"No golden reference on file for board_id={state['board_id']!r}, "
            f"component_ref={state['component_ref']!r}, package={state['package']!r}, "
            f"feature={state['feature']!r} - proceeding without one."
        )
    else:
        state["golden_image_id"] = golden.id
        state["observations"].append(f"Golden reference found: {golden.id}.")
    return state

async def align_and_check_quality_node(
    session: AsyncSession, state: OrchestratorState
) -> OrchestratorState:
    """Only reached when lookup_golden_image found a reference. Ported from
    orchestrator-agent's verification/{image_alignment,image_quality}.py via verification.py -
    never fails the pipeline; any issue found is recorded in quality_issues for finalize to
    weigh, same graceful-degradation convention as measurement validation."""

    golden = await session.get(GoldenImage, state["golden_image_id"])
    if golden is None:
        # Shouldn't happen (lookup_golden_image just found this row) - degrade, don't crash.
        state["alignment_result"] = None
        state["quality_issues"] = ["GOLDEN_IMAGE_ROW_MISSING"]
        state["alignment_done"] = True
        state["observations"].append("Alignment check skipped: golden reference row vanished.")
        return state

    try:
        golden_bytes = golden_images.resolve_golden_image_path(golden).read_bytes()
    except OSError:
        state["alignment_result"] = None
        state["quality_issues"] = ["GOLDEN_IMAGE_UNREADABLE"]
        state["alignment_done"] = True
        state["observations"].append(
            "Alignment check skipped: golden reference file missing on disk."
        )
        return state

    quality_issues: list[str] = []
    case_quality = verification.image_quality(state["image_bytes"])
    if not case_quality.get("readable", False):
        quality_issues.append("CASE_IMAGE_UNREADABLE")

    alignment = verification.estimate_translation(golden_bytes, state["image_bytes"])
    if "error" in alignment:
        quality_issues.append(alignment["error"])
    else:
        shift = alignment["shift_pixels"]
        if shift > settings.adc_alignment_fail_shift_px:
            quality_issues.append("IMAGE_PAIR_ALIGNMENT_FAILED")
        elif shift > settings.adc_alignment_warning_shift_px:
            quality_issues.append("IMAGE_PAIR_ALIGNMENT_WARNING")

    state["alignment_result"] = alignment
    state["quality_issues"] = quality_issues
    state["alignment_done"] = True
    state["observations"].append(
        f"Alignment/quality check: {len(quality_issues)} issue(s) - {quality_issues}."
        if quality_issues
        else "Alignment/quality check: passed."
    )
    return state


def build_graph(session: AsyncSession) -> CompiledStateGraph[OrchestratorState, Any, Any, Any]:
    """Compiles a fresh graph whose session-dependent nodes close over the given request-scoped
    AsyncSession. Cheap - called once per case-creation call, mirrors every other agent's
    build_graph(session)/build_graph(registry, ...) pattern here."""

    def _plan_node(state: OrchestratorState) -> OrchestratorState:
        """Records the outcome of whatever step just ran (tool_history - skipped on the very
        first call, when current_step is still None), then proposes and policy-checks the next
        one. Mutates and returns state rather than a no-op node, so the conditional-edge routing
        function that follows (_route_from_plan) can stay a pure reader of state["current_step"],
        exactly the same contract every other conditional edge in this codebase already relies on."""

        if state["current_step"] is not None:
            state["tool_history"].append(
                {"step": state["current_step"], "success": state.get("error") is None}
            )

        decision = Planner().plan(state)
        state["replan_count"] += 1
        state["plan_history"].append(
            {
                "step": state["replan_count"],
                "observation": decision.observation,
                "decision": decision.decision,
                "reason": decision.reason,
            }
        )

        if decision.decision == "done":
            state["current_step"] = "done"
            return state

        if state["replan_count"] > _MAX_REPLANS:
            state["current_step"] = "abort"
            state["termination_reason"] = "MAX_REPLAN_LIMIT_REACHED"
            state["observations"].append("Replan limit reached; aborting.")
            return state

        allowed, reason = PolicyEngine().validate_action(decision.decision, state)
        if not allowed:
            state["current_step"] = "abort"
            state["termination_reason"] = "POLICY_REJECTED_DETERMINISTIC_PLAN"
            state["observations"].append(f"Policy rejected {decision.decision!r}: {reason}")
            return state

        state["current_step"] = decision.decision
        return state

    def _route_from_plan(state: OrchestratorState) -> str:
        step = state["current_step"]
        assert step is not None  # _plan_node always sets this before returning
        return step

    async def _lookup_golden_image(state: OrchestratorState) -> OrchestratorState:
        return await lookup_golden_image_node(session, state)

    async def _align_and_check_quality(state: OrchestratorState) -> OrchestratorState:
        return await align_and_check_quality_node(session, state)

    async def _persist_case_node(state: OrchestratorState) -> OrchestratorState:
        case = await cases.create_case(
            session,
            created_by_user_id=state["user_id"],
            conversation_id=state["conversation_id"],
            board_id=state["board_id"],
            component_ref=state["component_ref"],
            package=state["package"],
            feature=state["feature"],
            issue_symptom=state["issue_symptom"],
            image_id=state["image_name"],
            inspection_xml_id=state.get("inspection_xml_id"),
            golden_image_id=state.get("golden_image_id"),
            region=state.get("region") or None,
            region_confidence=state.get("region_confidence"),
            region_model_version=state.get("region_model_version") or None,
            defect_model=state.get("defect_model") or None,
            defect_model_version=state.get("defect_model_version") or None,
            defect_label=state.get("defect_label") or None,
            defect_confidence=state.get("defect_confidence"),
            defect_scores=state.get("defect_scores") or {},
            measurement_validation=state.get("measurement_validation"),
            observations=state["observations"],
            status=(
                CaseStatus.ACCEPTED
                if state["final_decision"] == "ACCEPTED"
                else CaseStatus.REVIEW_REQUIRED
            ),
        )
        state["case_id"] = case.id
        state["case_number"] = case.case_number
        state["case_persisted"] = True
        state["status"] = (
            "COMPLETED" if state["final_decision"] == "ACCEPTED" else "REVIEW_REQUIRED"
        )
        return state

    def _abort_node(state: OrchestratorState) -> OrchestratorState:
        state["status"] = "ABORTED"
        return state

    workflow = StateGraph(OrchestratorState)
    workflow.add_node("plan", _plan_node)
    workflow.add_node("verify_image", verify_image_node)
    workflow.add_node("lookup_golden_image", _lookup_golden_image)
    workflow.add_node("validate_measurements", validate_measurements_node)
    workflow.add_node("align_and_check_quality", _align_and_check_quality)
    workflow.add_node("classify_region", classify_region_node)
    workflow.add_node("classify_defect", classify_defect_node)
    workflow.add_node("finalize", finalize_node)
    workflow.add_node("persist_case", _persist_case_node)
    workflow.add_node("abort", _abort_node)

    workflow.add_edge(START, "plan")
    workflow.add_conditional_edges(
        "plan",
        _route_from_plan,
        {**{name: name for name in _EXEC_NODES}, "done": END},
    )
    for name in _EXEC_NODES:
        workflow.add_edge(name, END if name in ("abort", "persist_case") else "plan")

    return workflow.compile()
