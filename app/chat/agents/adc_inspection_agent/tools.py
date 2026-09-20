"""Three Tool implementations (app/chat/core/tools.py) wrapping the orchestrator workflow, invoked
through app/chat/agents/registry.py's ToolRegistry/call_tool() the same way any other tool is.
CreateCaseTool is now the sole image-upload entry point - it absorbs what used to be the separate,
non-persisting `adc_inspection` tool (deleted; see graph.py's docstring for why) as well as the
former case_agent's persisting behavior. Named tools.py (plural), unlike a single-tool agent's
tool.py, since this agent exposes three tools.

`parameters` on each describes the public, LLM-facing surface - the caller (app/chat/services/streaming.py's
_run_tool_call) is responsible for resolving image/xml ids to bytes, and for injecting `session`/
`username`/`user_id`/`conversation_id`, none of which should ever be something an LLM is prompted
to supply itself.
"""

import json
from typing import Any

from app.chat.agents.adc_inspection_agent.graph import build_graph
from app.chat.agents.adc_inspection_agent.repository import (
    get_case_by_sequence_number,
    list_cases,
    parse_case_number,
    resolve_case,
)
from app.chat.agents.adc_inspection_agent.workflow_state import OrchestratorState
from app.chat.db.models import CaseStatus
from app.shared.config.langfuse import get_langfuse_callbacks

# A generous ceiling on LangGraph super-steps for one run of the plan/policy loop (graph.py) -
# the longest real path is ~9 execution steps interleaved with ~9 plan steps (~18 total), so this
# is headroom against a planner/policy bug looping, not a tuned budget.
_RECURSION_LIMIT = 50


def _case_summary(case: Any) -> dict[str, Any]:
    return {
        "case_number": case.case_number,
        "status": case.status,
        "board_id": case.board_id,
        "component_ref": case.component_ref,
        "package": case.package,
        "feature": case.feature,
        "region": case.region,
        "defect_label": case.defect_label,
        "defect_confidence": case.defect_confidence,
        "created_at": case.created_at.isoformat(),
    }


class CreateCaseTool:
    name = "create_case"
    description = (
        "Runs the full PCB inspection workflow on an uploaded AOI image: looks up a matching "
        "golden reference image and checks alignment/quality against it, classifies region and "
        "defect, optionally validates an attached inspection XML's measurements, and persists "
        "the result as a reviewable Case with a case number. When the verdict is REVIEW_REQUIRED, "
        "automatically runs the explainability review agent and attaches its diagnosis to the "
        "case before persisting. This is the only tool for submitting an image for inspection."
    )

    def __init__(self) -> None:
        self.parameters: dict[str, Any] = {
            "type": "object",
            "properties": {
                "board_id": {"type": "string", "description": "The AOI board identifier."},
                "component_ref": {
                    "type": "string",
                    "description": "The component reference designator on the board.",
                },
                "package": {
                    "type": "string",
                    "description": "The component's package type, if known.",
                },
                "feature": {
                    "type": "string",
                    "description": "The specific feature/pad being flagged, if known.",
                },
                "issue_symptom": {
                    "type": "string",
                    "description": "What looked wrong, in the flagging user's own words.",
                },
            },
            "required": ["board_id", "component_ref"],
        }

    async def run(self, **kwargs: Any) -> str:
        initial_state: OrchestratorState = {
            "image_bytes": kwargs["image_bytes"],
            "image_name": kwargs.get("image_name") or "image",
            "inspection_xml_bytes": kwargs.get("inspection_xml_bytes"),
            "inspection_xml_id": kwargs.get("inspection_xml_id"),
            "username": kwargs["username"],
            "user_id": kwargs["user_id"],
            "conversation_id": kwargs["conversation_id"],
            "board_id": kwargs.get("board_id") or "",
            "component_ref": kwargs.get("component_ref") or "",
            "package": kwargs.get("package") or None,
            "feature": kwargs.get("feature") or None,
            "issue_symptom": kwargs.get("issue_symptom") or None,
            "image_readable": None,
            "golden_lookup_done": False,
            "golden_image_id": None,
            "measurement_validation": None,
            "alignment_done": False,
            "alignment_result": None,
            "quality_issues": [],
            "region": "",
            "region_confidence": 0.0,
            "region_scores": {},
            "region_uncertain": False,
            "defect_model": "",
            "defect_label": "",
            "defect_confidence": 0.0,
            "defect_scores": {},
            "final_decision": "",
            "escalation_done": False,
            "explainability_result": None,
            "case_persisted": False,
            "case_id": None,
            "case_number": None,
            "status": "RUNNING",
            "current_step": None,
            "plan_history": [],
            "tool_history": [],
            "replan_count": 0,
            "termination_reason": None,
            "observations": [],
            "error": None,
        }

        pipeline = build_graph(kwargs["session"])
        final_state = await pipeline.ainvoke(
            initial_state,
            config={
                "recursion_limit": _RECURSION_LIMIT,
                "callbacks": get_langfuse_callbacks(),
            },
        )

        if not final_state.get("case_persisted"):
            return json.dumps(
                {
                    "error": (
                        final_state.get("error")
                        or final_state.get("termination_reason")
                        or "workflow aborted"
                    ),
                    "observations": final_state["observations"],
                }
            )

        return json.dumps(
            {
                "case_number": final_state["case_number"],
                "final_decision": final_state["final_decision"],
                "review_required": final_state["final_decision"] == "REVIEW_REQUIRED",
                "region": final_state["region"],
                "region_confidence": final_state["region_confidence"],
                "defect_model": final_state["defect_model"],
                "defect_label": final_state["defect_label"],
                "defect_confidence": final_state["defect_confidence"],
                "golden_image_found": final_state["golden_image_id"] is not None,
                "measurement_validation": final_state["measurement_validation"],
                "quality_issues": final_state.get("quality_issues") or [],
                "explainability_review": final_state.get("explainability_result"),
                "observations": final_state["observations"],
            }
        )


class ListCasesTool:
    name = "list_cases"
    description = "Lists Cases, most recent first, optionally filtered by review status."

    def __init__(self) -> None:
        self.parameters: dict[str, Any] = {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": [s.value for s in CaseStatus],
                    "description": "Filter by status. Omit to list every status.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of cases to return.",
                    "default": 20,
                },
            },
            "required": [],
        }

    async def run(self, **kwargs: Any) -> str:
        session = kwargs["session"]
        status_arg = kwargs.get("status")
        status = CaseStatus(status_arg) if status_arg else None
        limit = int(kwargs.get("limit") or 20)

        cases = await list_cases(session, status=status, limit=limit)
        return json.dumps({"cases": [_case_summary(case) for case in cases]})


class ReviewCaseTool:
    name = "review_case"
    description = (
        "Resolves a REVIEW_REQUIRED case by approving the flagged defect or overriding it as a "
        "false positive."
    )

    def __init__(self) -> None:
        self.parameters: dict[str, Any] = {
            "type": "object",
            "properties": {
                "case_number": {
                    "type": "string",
                    "description": 'The case number, e.g. "CASE-000123".',
                },
                "decision": {
                    "type": "string",
                    "enum": ["approve", "override"],
                    "description": (
                        "'approve' confirms the flagged defect stands; 'override' reverses it as "
                        "a false positive."
                    ),
                },
                "note": {
                    "type": "string",
                    "description": "Optional reviewer note explaining the decision.",
                },
            },
            "required": ["case_number", "decision"],
        }

    async def run(self, **kwargs: Any) -> str:
        session = kwargs["session"]
        case_number = kwargs.get("case_number") or ""
        decision = kwargs.get("decision")

        sequence_number = parse_case_number(case_number)
        if sequence_number is None:
            return json.dumps({"error": f"invalid case number: {case_number!r}"})

        case = await get_case_by_sequence_number(session, sequence_number)
        if case is None:
            return json.dumps({"error": "case not found"})

        if case.status != CaseStatus.REVIEW_REQUIRED:
            return json.dumps({"error": "case is not pending review"})

        if decision == "approve":
            new_status = CaseStatus.APPROVED
        elif decision == "override":
            new_status = CaseStatus.OVERRIDDEN
        else:
            return json.dumps({"error": f"invalid decision: {decision!r}"})

        updated = await resolve_case(
            session,
            case,
            status=new_status,
            resolved_by_user_id=kwargs["user_id"],
            note=kwargs.get("note"),
        )
        return json.dumps(_case_summary(updated))
