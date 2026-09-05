"""WeatherTool - current weather for a named location, via Open-Meteo's free, keyless APIs
(https://open-meteo.com/en/docs and .../en/docs/geocoding-api). No API key/settings needed,
unlike ExplainabilityReviewTool's OpenAI dependency.

Never raises: a bad location or an upstream failure degrades to a {"error": ...} tool result
(the model sees it and can explain to the user) rather than killing the whole chat turn - same
pattern as app/agents/explainability_review_agent/mcp_client.py's search_historical().
"""

import json
from typing import Any

import httpx

_GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

# WMO weather interpretation codes, as used by Open-Meteo's API - see
# https://open-meteo.com/en/docs for the full table.
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


class WeatherTool:
    name = "get_weather"
    description = "Gets the current weather for a named location (city, place name)."

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
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                geocode_response = await client.get(
                    _GEOCODING_URL, params={"name": location, "count": 1}
                )
                geocode_response.raise_for_status()
                results = geocode_response.json().get("results") or []
                if not results:
                    return json.dumps({"error": f"No location found matching {location!r}."})
                place = results[0]

                forecast_response = await client.get(
                    _FORECAST_URL,
                    params={
                        "latitude": place["latitude"],
                        "longitude": place["longitude"],
                        "current": "temperature_2m,relative_humidity_2m,wind_speed_10m,weather_code",
                        "timezone": "auto",
                    },
                )
                forecast_response.raise_for_status()
                current = forecast_response.json()["current"]
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            return json.dumps({"error": f"Weather lookup failed: {exc}"})

        resolved_name = ", ".join(
            part for part in (place.get("name"), place.get("country")) if part
        )
        return json.dumps(
            {
                "location": resolved_name or location,
                "temperature_c": current["temperature_2m"],
                "condition": _WMO_CONDITIONS.get(current["weather_code"], "Unknown"),
                "wind_speed_kmh": current["wind_speed_10m"],
                "humidity_percent": current["relative_humidity_2m"],
            }
        )
