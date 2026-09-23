"""InspectImageTool: the single chat tool of the inspection agent, invoked through
app/chat/agents/registry.py's ToolRegistry/call_tool() like any other tool. It is the only way an
uploaded AOI image gets inspected (it absorbs what used to be the separate, non-persisting
`adc_inspection` tool and the former case_agent's persisting behavior).

Two stages per call: an LLM-driven ReAct pass (react.py) that works the inspection steps through
tools and writes a summary, then the deterministic pipeline (graph.py), which completes anything
the LLM left undone, decides the verdict from fixed rules, and persists a Case. With no OpenAI key
(or INSPECTION_AGENT_LLM_ENABLED off) only the second stage runs. Either way the outcome, and the
Case, come from the deterministic rules - see react.py's docstring.

`parameters` is the public, LLM-facing surface - the caller (app/chat/services/streaming.py's
_run_tool_call) resolves image/xml ids to bytes and injects `session`/`username`/`user_id`/
`conversation_id`, none of which an LLM should ever be asked to supply itself.
"""

import json
from typing import Any

from app.chat.agents.inspection_agent import react
from app.chat.agents.inspection_agent.graph import build_graph
from app.chat.agents.inspection_agent.workflow_state import OrchestratorState
from app.shared.config.langfuse import get_langfuse_callbacks
from app.shared.config.settings import settings

# A generous ceiling on LangGraph super-steps for one run of the plan/policy loop (graph.py) -
# the longest real path is ~9 execution steps interleaved with ~9 plan steps (~18 total), so this
# is headroom against a planner/policy bug looping, not a tuned budget.
_RECURSION_LIMIT = 50

# What a Case records for a board/component the user didn't identify - a user asking "what defect
# is this?" shouldn't be blocked for lack of a reference designator. Never matches a golden image.
UNKNOWN = "unknown"


class InspectImageTool:
    name = "inspect_image"
    description = (
        "Inspects an uploaded AOI image of a PCB component: identifies which region it shows and "
        "what defect it has (two-stage classification through the inference service), checks it "
        "against a matching golden reference image for alignment/quality, optionally validates an "
        "attached inspection XML's measurements, and saves the result as a reviewable Case with a "
        "case number. Use it whenever the user attaches an image and asks what is wrong with it. "
        "This is the only tool for inspecting an image."
    )

    def __init__(self) -> None:
        self.parameters: dict[str, Any] = {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "What the user wants to know about the image, in their words.",
                },
                "board_id": {
                    "type": "string",
                    "description": "The AOI board identifier, if the user gave one.",
                },
                "component_ref": {
                    "type": "string",
                    "description": "The component reference designator (e.g. U7), if known.",
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
            "required": [],
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
            "board_id": kwargs.get("board_id") or UNKNOWN,
            "component_ref": kwargs.get("component_ref") or UNKNOWN,
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
            "region_model_version": "",
            "defect_model": "",
            "defect_model_version": "",
            "defect_label": "",
            "defect_confidence": 0.0,
            "defect_scores": {},
            "final_decision": "",
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

        session = kwargs["session"]
        summary: str | None = None
        if settings.inspection_agent_llm_enabled and settings.openai_api_key:
            summary = await react.run_react_pass(
                session, initial_state, question=kwargs.get("question")
            )

        pipeline = build_graph(session)
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
                "region_model_version": final_state["region_model_version"] or None,
                "defect_model": final_state["defect_model"],
                "defect_model_version": final_state["defect_model_version"] or None,
                "defect_label": final_state["defect_label"],
                "defect_confidence": final_state["defect_confidence"],
                "golden_image_found": final_state["golden_image_id"] is not None,
                "measurement_validation": final_state["measurement_validation"],
                "quality_issues": final_state.get("quality_issues") or [],
                "inspection_summary": summary,
                "observations": final_state["observations"],
            }
        )
