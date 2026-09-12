"""A small LangGraph pipeline for the current-time agent: resolve an optional location to a
timezone -> compute the current time in it -> a deterministic business-hours branch. Mirrors
app/agents/weather_agent/graph.py's shape (state TypedDict, sync node functions run off the
event loop by tool.py, a lazy module-wide compiled-graph singleton, and the same Open-Meteo
geocoding endpoint to resolve a place name).

Deliberately no LLM step, unlike the Weather Agent's advisory - "what time is it" is fully
structured (no ambiguity an LLM would need to resolve), so spending a call on it would be pure
latency and cost with no value. The branch is still real, not cosmetic: business_hours/
after_hours produce a different `note`, decided by a plain weekday/hour check.
"""

import logging
from datetime import datetime
from typing import Any, Literal, TypedDict
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

logger = logging.getLogger(__name__)

_GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"

# Rough heuristic, not configurable per-location - good enough for "is someone likely at their
# desk right now", not a real business-hours calendar.
_BUSINESS_HOURS_START = 9
_BUSINESS_HOURS_END = 18


class TimeState(TypedDict):
    query_location: str | None
    resolved_name: str
    timezone_name: str
    iso_datetime: str
    utc_offset: str
    day_of_week: str
    is_business_hours: bool
    note: str
    error: str | None


def _resolve_timezone_node(state: TimeState) -> TimeState:
    """Node 1: UTC if no location was given, otherwise geocode it (same Open-Meteo endpoint the
    Weather Agent uses) - its geocoding results already carry an IANA timezone name per match,
    so no second lookup is needed."""

    location = state.get("query_location")
    if not location:
        state["resolved_name"] = "UTC"
        state["timezone_name"] = "UTC"
        return state

    logger.info("Time agent: resolving timezone for %r", location)
    try:
        with httpx.Client(timeout=10.0) as client:
            response = client.get(_GEOCODING_URL, params={"name": location, "count": 1})
            response.raise_for_status()
            results = response.json().get("results") or []
    except httpx.HTTPError as exc:
        state["error"] = f"Geocoding failed: {exc}"
        return state

    if not results:
        state["error"] = f"No location found matching {location!r}."
        return state

    place = results[0]
    state["resolved_name"] = (
        ", ".join(part for part in (place.get("name"), place.get("country")) if part) or location
    )
    state["timezone_name"] = place.get("timezone") or "UTC"
    return state


def _route_after_resolve(state: TimeState) -> Literal["compute_time", "end"]:
    return "end" if state.get("error") else "compute_time"


def _compute_time_node(state: TimeState) -> TimeState:
    """Node 2: the actual computation, plus the deterministic business-hours check that decides
    which branch runs next."""

    try:
        now = datetime.now(ZoneInfo(state["timezone_name"]))
    except ZoneInfoNotFoundError:
        state["error"] = f"Unknown timezone: {state['timezone_name']!r}."
        return state

    state["iso_datetime"] = now.isoformat()
    state["utc_offset"] = now.strftime("%z") or "+0000"
    state["day_of_week"] = now.strftime("%A")
    state["is_business_hours"] = (
        now.weekday() < 5 and _BUSINESS_HOURS_START <= now.hour < _BUSINESS_HOURS_END
    )
    return state


def _route_after_compute(state: TimeState) -> Literal["business_hours", "after_hours", "end"]:
    if state.get("error"):
        return "end"
    return "business_hours" if state["is_business_hours"] else "after_hours"


def _business_hours_node(state: TimeState) -> TimeState:
    state["note"] = (
        f"It's currently within typical business hours "
        f"({_BUSINESS_HOURS_START}:00-{_BUSINESS_HOURS_END}:00) in {state['resolved_name']}."
    )
    return state


def _after_hours_node(state: TimeState) -> TimeState:
    state["note"] = f"It's currently outside typical business hours in {state['resolved_name']}."
    return state


def build_graph() -> CompiledStateGraph[TimeState, Any, Any, Any]:
    workflow = StateGraph(TimeState)
    workflow.add_node("resolve_timezone", _resolve_timezone_node)
    workflow.add_node("compute_time", _compute_time_node)
    workflow.add_node("business_hours", _business_hours_node)
    workflow.add_node("after_hours", _after_hours_node)

    workflow.add_edge(START, "resolve_timezone")
    workflow.add_conditional_edges(
        "resolve_timezone", _route_after_resolve, {"compute_time": "compute_time", "end": END}
    )
    workflow.add_conditional_edges(
        "compute_time",
        _route_after_compute,
        {"business_hours": "business_hours", "after_hours": "after_hours", "end": END},
    )
    workflow.add_edge("business_hours", END)
    workflow.add_edge("after_hours", END)

    return workflow.compile()


_pipeline: CompiledStateGraph[TimeState, Any, Any, Any] | None = None


def get_pipeline() -> CompiledStateGraph[TimeState, Any, Any, Any]:
    """Lazy, process-wide singleton - cheap to build, but no reason to rebuild it per call."""

    global _pipeline
    if _pipeline is None:
        _pipeline = build_graph()
    return _pipeline
