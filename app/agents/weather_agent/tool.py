"""WeatherAgentTool wraps a small LangGraph pipeline (graph.py): geocode -> fetch current
conditions and a short forecast -> an LLM-synthesized advisory, branching into a more cautious
tone when the forecast carries a severe-weather signal. Same Tool protocol
(app/core/tools.py) as ExplainabilityReviewTool, invoked through
app/agents/registry.py's ToolRegistry/call_tool() the same way.

Never raises: a bad location, an upstream failure, or a failed advisory call all degrade to a
usable result (an {"error": ...} tool result, or a templated advisory) rather than killing the
whole chat turn - same pattern as before this was a graph, and as
app/agents/explainability_review_agent/mcp_client.py's own graceful-degradation.
"""

import asyncio
import json
from typing import Any

from app.agents.weather_agent.graph import WeatherAdvisoryState, get_pipeline


class WeatherAgentTool:
    name = "get_weather"
    description = (
        "Gets the current weather and a short forecast for a named location (city, place "
        "name), with a brief practical recommendation and a flag for any severe-weather signal."
    )

    def __init__(self) -> None:
        self.parameters: dict[str, Any] = {
            "type": "object",
            "properties": {
                "location": {
                    "type": "string",
                    "description": "City or place name, e.g. 'Singapore' or 'Paris, France'.",
                }
            },
            "required": ["location"],
        }

    async def run(self, **kwargs: Any) -> str:
        location: str = kwargs["location"]
        initial_state: WeatherAdvisoryState = {
            "query_location": location,
            "resolved_name": "",
            "latitude": 0.0,
            "longitude": 0.0,
            "current": {},
            "forecast": [],
            "severe_weather_alert": False,
            "advisory": "",
            "error": None,
        }

        pipeline = get_pipeline()
        # Node bodies do blocking HTTP/OpenAI calls - run off the event loop rather than
        # stalling every other in-flight request, same reasoning as
        # app/agents/explainability_review_agent/tool.py.
        final_state = await asyncio.to_thread(pipeline.invoke, initial_state)

        if final_state.get("error"):
            return json.dumps({"error": final_state["error"]})

        current = final_state["current"]
        return json.dumps(
            {
                "location": final_state["resolved_name"],
                "temperature_c": current["temperature_c"],
                "condition": current["condition"],
                "wind_speed_kmh": current["wind_speed_kmh"],
                "humidity_percent": current["humidity_percent"],
                "forecast": final_state["forecast"],
                "severe_weather_alert": final_state["severe_weather_alert"],
                "advisory": final_state["advisory"],
            }
        )
