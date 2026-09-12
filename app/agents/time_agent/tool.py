"""CurrentTimeAgentTool wraps a small LangGraph pipeline (graph.py): resolve an optional
location to a timezone -> compute the current time in it -> a deterministic business-hours
branch. Same Tool protocol (app/core/tools.py) as WeatherAgentTool/ExplainabilityReviewTool,
invoked through app/agents/registry.py's ToolRegistry/call_tool() the same way.

No LLM step, unlike the other two agents - "what time is it" is fully structured, so there's
nothing worth spending a call on. Never raises: a bad location degrades to an {"error": ...}
result rather than killing the whole chat turn, same pattern as the other agents.
"""

import asyncio
import json
from typing import Any

from app.agents.time_agent.graph import TimeState, get_pipeline


class CurrentTimeAgentTool:
    name = "current_time"
    description = (
        "Returns the current date and time, in UTC or for a named location (city, place "
        "name), including the day of week, UTC offset, and whether it's within typical "
        "business hours there."
    )

    def __init__(self) -> None:
        self.parameters: dict[str, Any] = {
            "type": "object",
            "properties": {
                "location": {
                    "type": "string",
                    "description": (
                        "Optional city or place name, e.g. 'Tokyo' or 'Paris, France'. "
                        "Omit for UTC."
                    ),
                }
            },
            "required": [],
        }

    async def run(self, **kwargs: Any) -> str:
        location: str | None = kwargs.get("location") or None
        initial_state: TimeState = {
            "query_location": location,
            "resolved_name": "",
            "timezone_name": "",
            "iso_datetime": "",
            "utc_offset": "",
            "day_of_week": "",
            "is_business_hours": False,
            "note": "",
            "error": None,
        }

        pipeline = get_pipeline()
        # The geocode node does a blocking HTTP call (only when a location is given) - run off
        # the event loop rather than stalling every other in-flight request, same reasoning as
        # the other agents.
        final_state = await asyncio.to_thread(pipeline.invoke, initial_state)

        if final_state.get("error"):
            return json.dumps({"error": final_state["error"]})

        return json.dumps(
            {
                "location": final_state["resolved_name"],
                "timezone": final_state["timezone_name"],
                "datetime": final_state["iso_datetime"],
                "utc_offset": final_state["utc_offset"],
                "day_of_week": final_state["day_of_week"],
                "is_business_hours": final_state["is_business_hours"],
                "note": final_state["note"],
            }
        )
