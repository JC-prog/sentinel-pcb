"""ExplainabilityReviewTool wraps the ported LangGraph pipeline (graph.py) behind the app's Tool
protocol (app/core/tools.py), so it's invoked through app/agents/registry.py's
ToolRegistry/call_tool() the same way any future tool would be - even though today it's also
called directly by a route (POST /api/agents/explainability-review in app/main.py) and, in-process,
by app/agents/adc_inspection_agent/graph.py's escalate_review node.

InvestigateCaseTool is the same pipeline, entered from a case number instead of a raw image_id -
Flow 2's "investigate CASE-000123". It resolves the Case row itself (image_id, inspection_xml_id,
board_id/component_ref/package/feature), which is why it needs app.uploads.resolve_upload_path
directly rather than having main.py resolve an upload id in advance the way every other tool does -
the id it needs lives in a DB row this tool must query first, not in the request. Deliberate,
first-of-its-kind exception to that pattern, not one to copy elsewhere without the same reason.

`parameters` describes each tool's public, LLM-facing surface - the caller (app/main.py's
_run_tool_call) is responsible for resolving image ids to actual PIL.Image objects and for
supplying openai_api_key/session before calling run(), neither of which should ever be something
an LLM is prompted to supply itself.
"""

import asyncio
import json
from typing import Any

from langchain_core.runnables import RunnableConfig
from PIL import Image

from app.agents.explainability_review_agent.graph import PCBInspectionState, get_pipeline
from app.config.langfuse import get_langfuse_callbacks

_VALID_DEFECT_CATEGORIES = frozenset(
    {
        "missing part",
        "shifted",
        "foreign material",
        "tombstone",
        "solder insufficient",
        "wrong part",
        "no defect",
        "unknown",
    }
)


class ExplainabilityReviewTool:
    name = "explainability_review"
    description = (
        "Diagnoses a PCB component defect from an inspection image, combining visual evidence, "
        "historical defect precedents, IPC-A-610 standards, and AOI/ICT telemetry into a "
        "grounded root-cause explanation."
    )

    def __init__(self) -> None:
        # Set in __init__ rather than as a class attribute (matches CurrentTimeTool) - avoids a
        # mutable class-level default shared across instances.
        self.parameters: dict[str, Any] = {
            "type": "object",
            "properties": {
                "image_id": {
                    "type": "string",
                    "description": "Uploaded image id, from POST /api/uploads.",
                },
                "board_id": {"type": "string", "description": "PCB assembly/board identifier."},
                "component_ref": {
                    "type": "string",
                    "description": "Component reference designator, e.g. C978, R131.",
                },
                "issue_symptom": {
                    "type": "string",
                    "description": "Optional free-text symptom noted by the inspector.",
                },
            },
            "required": ["image_id", "board_id", "component_ref"],
        }

    async def run(self, **kwargs: Any) -> str:
        initial_state: PCBInspectionState = {
            "image": kwargs["image"],
            "image_name": kwargs.get("image_name"),
            "board_id": kwargs["board_id"],
            "component_ref": kwargs["component_ref"],
            "issue_symptom": kwargs.get("issue_symptom") or "AOI flagged anomaly",
            "inspection_xml_bytes": None,  # chat uploads have no XML for this tool - see graph.py
            "package": None,
            "feature": None,
            "historical_context": "",
            "reference_standards": "",
            "similar_cases": [],
            "visual_bounding_boxes": [],
            "visual_description": "",
            "measurements": {},
            "defect_location": None,
            "final_defect_category": "unknown",
            "final_diagnosis_text": "",
            "grounding_confidence": 0.0,
            "self_check_passed": False,
            "errors": [],
        }

        pipeline = get_pipeline(kwargs["openai_api_key"])
        # pipeline.invoke() and the OpenAI SDK calls inside it are blocking - run off the event
        # loop rather than stalling every other in-flight request.
        config: RunnableConfig = {"callbacks": get_langfuse_callbacks()}
        final_state = await asyncio.to_thread(pipeline.invoke, initial_state, config=config)
        return _format_result(final_state)


class InvestigateCaseTool:
    name = "investigate_case"
    description = (
        "Runs the explainability review pipeline against an existing Case, by case number, "
        "resolving its stored image, inspection XML, board, and component fields automatically - "
        "no need to re-attach the image."
    )

    def __init__(self) -> None:
        self.parameters: dict[str, Any] = {
            "type": "object",
            "properties": {
                "case_number": {
                    "type": "string",
                    "description": 'The case number, e.g. "CASE-000123".',
                },
                "issue_symptom": {
                    "type": "string",
                    "description": "Optional override of the case's recorded symptom.",
                },
            },
            "required": ["case_number"],
        }

    async def run(self, **kwargs: Any) -> str:
        # Deferred imports: this tool is the one place explainability_review_agent needs
        # adc_inspection_agent's case repository and app.uploads' path resolver - neither is a
        # dependency the rest of this package should carry at module import time.
        from app.agents.adc_inspection_agent.repository import (
            get_case_by_sequence_number,
            parse_case_number,
        )
        from app.uploads import resolve_upload_path

        session = kwargs["session"]
        sequence_number = parse_case_number(kwargs.get("case_number") or "")
        if sequence_number is None:
            return json.dumps({"error": f"invalid case number: {kwargs.get('case_number')!r}"})

        case = await get_case_by_sequence_number(session, sequence_number)
        if case is None:
            return json.dumps({"error": "case not found"})

        image_path = resolve_upload_path(case.image_id)
        if image_path is None:
            return json.dumps({"error": "case image not found on disk"})

        inspection_xml_bytes = None
        if case.inspection_xml_id:
            xml_path = resolve_upload_path(case.inspection_xml_id)
            if xml_path is not None:
                inspection_xml_bytes = xml_path.read_bytes()

        initial_state: PCBInspectionState = {
            "image": Image.open(image_path).convert("RGB"),
            "image_name": case.image_id,
            "board_id": case.board_id,
            "component_ref": case.component_ref,
            "issue_symptom": kwargs.get("issue_symptom") or case.issue_symptom or "AOI flagged anomaly",
            "inspection_xml_bytes": inspection_xml_bytes,
            "package": case.package,
            "feature": case.feature,
            "historical_context": "",
            "reference_standards": "",
            "similar_cases": [],
            "visual_bounding_boxes": [],
            "visual_description": "",
            "measurements": {},
            "defect_location": None,
            "final_defect_category": "unknown",
            "final_diagnosis_text": "",
            "grounding_confidence": 0.0,
            "self_check_passed": False,
            "errors": [],
        }

        pipeline = get_pipeline(kwargs["openai_api_key"])
        config: RunnableConfig = {"callbacks": get_langfuse_callbacks()}
        final_state = await asyncio.to_thread(pipeline.invoke, initial_state, config=config)
        result = json.loads(_format_result(final_state))
        result["case_number"] = case.case_number
        return json.dumps(result)


def _format_result(final_state: dict[str, Any]) -> str:
    category = final_state["final_defect_category"]
    if category not in _VALID_DEFECT_CATEGORIES:
        category = "unknown"

    return json.dumps(
        {
            "defect_category": category,
            "defect_location": final_state["defect_location"],
            "explanation": final_state["final_diagnosis_text"],
            "confidence_score": final_state["grounding_confidence"],
            "self_check_passed": final_state["self_check_passed"],
            "similar_cases": final_state.get("similar_cases", []),
            "errors": final_state["errors"],
        }
    )
