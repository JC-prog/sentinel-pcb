"""ExplainabilityReviewTool wraps the ported LangGraph pipeline (graph.py) behind the app's Tool
protocol (app/core/tools.py), so it's invoked through app/agents/registry.py's
ToolRegistry/call_tool() the same way any future tool would be - even though today it's called
directly by a route (POST /api/agents/explainability-review in app/main.py) rather than by an LLM
deciding to call it. See DEVELOPMENT.md for why the full LLM tool-calling loop isn't built yet.

`parameters` describes the public, LLM-facing surface (image_id, not a raw image) - the route is
responsible for resolving image_id to an actual PIL.Image and for supplying openai_api_key before
calling run(), neither of which should ever be something an LLM is prompted to supply itself.
"""

import asyncio
import json
from typing import Any

from PIL import Image

from app.agents.explainability_review_agent.graph import PCBInspectionState, get_pipeline

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
        image: Image.Image = kwargs["image"]
        board_id: str = kwargs["board_id"]
        component_ref: str = kwargs["component_ref"]
        issue_symptom: str = kwargs.get("issue_symptom") or "AOI flagged anomaly"
        image_name: str | None = kwargs.get("image_name")
        openai_api_key: str = kwargs["openai_api_key"]

        initial_state: PCBInspectionState = {
            "image": image,
            "image_name": image_name,
            "board_id": board_id,
            "component_ref": component_ref,
            "issue_symptom": issue_symptom,
            "historical_context": "",
            "reference_standards": "",
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

        pipeline = get_pipeline(openai_api_key)
        # pcb_graph.invoke() and the OpenAI SDK calls inside it are blocking - run off the event
        # loop rather than stalling every other in-flight request.
        final_state = await asyncio.to_thread(pipeline.invoke, initial_state)

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
                "errors": final_state["errors"],
            }
        )
