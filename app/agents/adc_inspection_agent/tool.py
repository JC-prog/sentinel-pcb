"""AdcInspectionTool wraps the ADC LangGraph pipeline (graph.py) behind the app's Tool protocol
(app/core/tools.py), so it's invoked through app/agents/registry.py's ToolRegistry/call_tool()
the same way any other tool is. Same Tool protocol as ExplainabilityReviewTool and
WeatherAgentTool.

`parameters` describes the public, LLM-facing surface (image_id) - the caller (app/main.py) is
responsible for resolving image_id to raw image bytes and the calling user's username before
calling run(), neither of which should ever be something an LLM is prompted to supply itself.

Returns a raw two-stage classifier verdict (region, then the matching defect model's label and
confidence) - unlike explainability_review, there's no LLM-written narrative here, just the
model's own output.
"""

import json
from typing import Any

from app.agents.adc_inspection_agent.graph import AdcInspectionState, get_pipeline


class AdcInspectionTool:
    name = "adc_inspection"
    description = (
        "Classifies a PCB AOI inspection image using the two-stage ADC classifier: first the "
        "component region (Body/Lead/Text), then the matching defect model for that region. "
        "Returns a raw classifier verdict with confidence scores, not a narrative explanation."
    )

    def __init__(self) -> None:
        self.parameters: dict[str, Any] = {
            "type": "object",
            "properties": {
                "image_id": {
                    "type": "string",
                    "description": "Uploaded image id, from POST /api/uploads.",
                },
            },
            "required": ["image_id"],
        }

    async def run(self, **kwargs: Any) -> str:
        image_bytes: bytes = kwargs["image_bytes"]
        image_name: str = kwargs.get("image_name") or "image"
        username: str = kwargs["username"]

        initial_state: AdcInspectionState = {
            "image_bytes": image_bytes,
            "image_name": image_name,
            "username": username,
            "region": "",
            "region_confidence": 0.0,
            "region_scores": {},
            "defect_model": "",
            "defect_label": "",
            "defect_confidence": 0.0,
            "defect_scores": {},
            "error": None,
        }

        pipeline = get_pipeline()
        final_state = await pipeline.ainvoke(initial_state)

        if final_state.get("error"):
            return json.dumps({"error": final_state["error"]})

        return json.dumps(
            {
                "region": final_state["region"],
                "region_confidence": final_state["region_confidence"],
                "defect_model": final_state["defect_model"],
                "defect_label": final_state["defect_label"],
                "defect_confidence": final_state["defect_confidence"],
                "defect_scores": final_state["defect_scores"],
            }
        )
