"""A small LangGraph pipeline: geocode -> fetch current conditions + a short forecast -> an
LLM-synthesized advisory, branching into a more cautious tone when the data carries a
severe-weather signal (thunderstorm, heavy precipitation, or high wind). Mirrors the shape of
app/agents/explainability_review_agent/graph.py (state TypedDict, sync node functions run off
the event loop by tool.py, a lazy module-wide compiled-graph singleton) at a much smaller scale -
no per-request credentials to thread through, so nodes are plain functions, not closures.

The advisory step is best-effort: no OpenAI key is required for the core lookup (Open-Meteo is
keyless, as before this existed), and the LLM call degrades to a templated summary when
WEATHER_ADVISORY_ENABLED is off, no key is configured, or the call itself fails - so this
pipeline still never raises.
"""

import logging
from typing import Any, Literal, TypedDict

import httpx
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from openai import OpenAI

from app.config.settings import settings

logger = logging.getLogger(__name__)

_GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
_FORECAST_DAYS = 3

# WMO weather interpretation codes Open-Meteo uses - see https://open-meteo.com/en/docs.
_WMO_CONDITIONS: dict[int, str] = {
    0: "Clear sky",
    1: "Mainly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Fog",
    48: "Depositing rime fog",
    51: "Light drizzle",
    53: "Moderate drizzle",
    55: "Dense drizzle",
    56: "Light freezing drizzle",
    57: "Dense freezing drizzle",
    61: "Slight rain",
    63: "Moderate rain",
    65: "Heavy rain",
    66: "Light freezing rain",
    67: "Heavy freezing rain",
    71: "Slight snow fall",
    73: "Moderate snow fall",
    75: "Heavy snow fall",
    77: "Snow grains",
    80: "Slight rain showers",
    81: "Moderate rain showers",
    82: "Violent rain showers",
    85: "Slight snow showers",
    86: "Heavy snow showers",
    95: "Thunderstorm",
    96: "Thunderstorm with slight hail",
    99: "Thunderstorm with heavy hail",
}

# Rough heuristic, not a meteorological standard: any of these WMO codes, or a 10m wind speed at
# or above this threshold, flags severe_weather_alert and routes to the more cautious advisory.
_SEVERE_WMO_CODES = {65, 66, 67, 75, 82, 86, 95, 96, 99}
_HIGH_WIND_KMH = 50.0

_ADVISORY_PROMPT = """You are a concise weather advisor. Based on the data below, write a short
(2-3 sentence) practical recommendation for someone deciding whether to go outside or travel
today. Summarize the numbers, don't just repeat them verbatim.

Location: {location}
Current conditions: {current_summary}
Upcoming forecast: {forecast_summary}
{severity_note}
"""

_SEVERITY_NOTE = (
    "Note: the forecast includes a severe-weather signal (thunderstorm, heavy precipitation, "
    "or high wind) - lead with a clear caution."
)


class WeatherAdvisoryState(TypedDict):
    query_location: str
    resolved_name: str
    latitude: float
    longitude: float
    current: dict[str, Any]
    forecast: list[dict[str, Any]]
    severe_weather_alert: bool
    advisory: str
    error: str | None


def _is_severe(entry: dict[str, Any]) -> bool:
    if entry.get("weather_code") in _SEVERE_WMO_CODES:
        return True
    wind = entry.get("wind_speed_kmh")
    return wind is not None and wind >= _HIGH_WIND_KMH


def _fallback_advisory(current: dict[str, Any], severe: bool) -> str:
    """Used when the LLM step is disabled, unconfigured, or fails - keeps this tool's older
    "never needs a key" behavior as the floor, not just an error."""

    base = (
        f"{current['condition']}, {current['temperature_c']}°C, "
        f"wind {current['wind_speed_kmh']} km/h."
    )
    if severe:
        return base + " Severe weather signal in the forecast - check local advisories."
    return base + " No severe weather signals in the forecast."


def _query_advisory_llm(state: WeatherAdvisoryState, *, severe: bool) -> str:
    # Routed through the LiteLLM gateway like every other OpenAI-compatible call in the app
    # (app/agents/explainability_review_agent/models.py, app/chat/providers/openai.py) - never
    # api.openai.com directly. settings.openai_api_key is then a LiteLLM key, not an sk- key.
    client = OpenAI(api_key=settings.openai_api_key, base_url=settings.openai_base_url)
    current = state["current"]
    forecast_summary = (
        "; ".join(
            f"{day['date']}: {day['condition']}, "
            f"{day['temperature_min_c']}-{day['temperature_max_c']}°C"
            for day in state["forecast"]
        )
        or "not available"
    )
    prompt = _ADVISORY_PROMPT.format(
        location=state["resolved_name"],
        current_summary=(
            f"{current['condition']}, {current['temperature_c']}°C, "
            f"wind {current['wind_speed_kmh']} km/h, humidity {current['humidity_percent']}%"
        ),
        forecast_summary=forecast_summary,
        severity_note=_SEVERITY_NOTE if severe else "",
    )
    response = client.chat.completions.create(
        model=settings.openai_model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
    )
    return (response.choices[0].message.content or "").strip()


def _geocode_node(state: WeatherAdvisoryState) -> WeatherAdvisoryState:
    """Node 1: resolves a free-text place name to coordinates."""

    logger.info("Weather agent: geocoding %r", state["query_location"])
    try:
        with httpx.Client(timeout=10.0) as client:
            response = client.get(
                _GEOCODING_URL, params={"name": state["query_location"], "count": 1}
            )
            response.raise_for_status()
            results = response.json().get("results") or []
    except httpx.HTTPError as exc:
        state["error"] = f"Geocoding failed: {exc}"
        return state

    if not results:
        state["error"] = f"No location found matching {state['query_location']!r}."
        return state

    place = results[0]
    state["resolved_name"] = (
        ", ".join(part for part in (place.get("name"), place.get("country")) if part)
        or state["query_location"]
    )
    state["latitude"] = place["latitude"]
    state["longitude"] = place["longitude"]
    return state


def _route_after_geocode(state: WeatherAdvisoryState) -> Literal["fetch_forecast", "end"]:
    return "end" if state.get("error") else "fetch_forecast"


def _fetch_forecast_node(state: WeatherAdvisoryState) -> WeatherAdvisoryState:
    """Node 2: current conditions plus a short daily forecast, and the deterministic
    severe-weather check that decides which advisory node runs next."""

    logger.info("Weather agent: fetching forecast for %s", state["resolved_name"])
    try:
        with httpx.Client(timeout=10.0) as client:
            response = client.get(
                _FORECAST_URL,
                params={
                    "latitude": state["latitude"],
                    "longitude": state["longitude"],
                    "current": "temperature_2m,relative_humidity_2m,wind_speed_10m,weather_code",
                    "daily": (
                        "weather_code,temperature_2m_max,temperature_2m_min,"
                        "precipitation_probability_max"
                    ),
                    "forecast_days": _FORECAST_DAYS,
                    "timezone": "auto",
                },
            )
            response.raise_for_status()
            data = response.json()
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        state["error"] = f"Forecast lookup failed: {exc}"
        return state

    current = data["current"]
    state["current"] = {
        "temperature_c": current["temperature_2m"],
        "condition": _WMO_CONDITIONS.get(current["weather_code"], "Unknown"),
        "wind_speed_kmh": current["wind_speed_10m"],
        "humidity_percent": current["relative_humidity_2m"],
        "weather_code": current["weather_code"],
    }

    daily = data.get("daily", {})
    dates = daily.get("time", [])
    forecast = [
        {
            "date": date,
            "condition": _WMO_CONDITIONS.get(daily["weather_code"][i], "Unknown"),
            "temperature_max_c": daily["temperature_2m_max"][i],
            "temperature_min_c": daily["temperature_2m_min"][i],
            "precipitation_probability_percent": daily.get(
                "precipitation_probability_max", [None] * len(dates)
            )[i],
            "weather_code": daily["weather_code"][i],
        }
        for i, date in enumerate(dates)
    ]
    state["forecast"] = forecast
    state["severe_weather_alert"] = _is_severe(state["current"]) or any(
        _is_severe(day) for day in forecast
    )
    return state


def _route_after_forecast(
    state: WeatherAdvisoryState,
) -> Literal["severe_advisory", "normal_advisory", "end"]:
    if state.get("error"):
        return "end"
    return "severe_advisory" if state["severe_weather_alert"] else "normal_advisory"


def _advisory_node(state: WeatherAdvisoryState, *, severe: bool) -> WeatherAdvisoryState:
    """Nodes 3a/3b: the real branch. Same data either way, different tone/emphasis - a genuine
    disposition, not just a cosmetic label, since the prompt (and the fallback text) actually
    changes on the severe path."""

    if settings.weather_advisory_enabled and settings.openai_api_key:
        try:
            state["advisory"] = _query_advisory_llm(state, severe=severe)
            return state
        except Exception:
            logger.exception("Weather advisory LLM call failed - using a templated summary.")
    state["advisory"] = _fallback_advisory(state["current"], severe)
    return state


def _severe_advisory_node(state: WeatherAdvisoryState) -> WeatherAdvisoryState:
    logger.info("Weather agent: severe-weather signal detected, using the cautious advisory")
    return _advisory_node(state, severe=True)


def _normal_advisory_node(state: WeatherAdvisoryState) -> WeatherAdvisoryState:
    return _advisory_node(state, severe=False)


def build_graph() -> CompiledStateGraph[WeatherAdvisoryState, Any, Any, Any]:
    workflow = StateGraph(WeatherAdvisoryState)
    workflow.add_node("geocode", _geocode_node)
    workflow.add_node("fetch_forecast", _fetch_forecast_node)
    workflow.add_node("severe_advisory", _severe_advisory_node)
    workflow.add_node("normal_advisory", _normal_advisory_node)

    workflow.add_edge(START, "geocode")
    workflow.add_conditional_edges(
        "geocode", _route_after_geocode, {"fetch_forecast": "fetch_forecast", "end": END}
    )
    workflow.add_conditional_edges(
        "fetch_forecast",
        _route_after_forecast,
        {"severe_advisory": "severe_advisory", "normal_advisory": "normal_advisory", "end": END},
    )
    workflow.add_edge("severe_advisory", END)
    workflow.add_edge("normal_advisory", END)

    return workflow.compile()


_pipeline: CompiledStateGraph[WeatherAdvisoryState, Any, Any, Any] | None = None


def get_pipeline() -> CompiledStateGraph[WeatherAdvisoryState, Any, Any, Any]:
    """Lazy, process-wide singleton - cheap to build (no model loading, unlike the
    Explainability Agent's mcp_client), but there's no reason to rebuild it per call either."""

    global _pipeline
    if _pipeline is None:
        _pipeline = build_graph()
    return _pipeline
