import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from app.agents.weather_agent import WeatherAgentTool
from app.agents.weather_agent import graph as weather_graph
from app.config.settings import settings

_RealClient = httpx.Client


def _mock_client(
    monkeypatch: pytest.MonkeyPatch, handler: Callable[[httpx.Request], httpx.Response]
) -> None:
    def factory(*args: object, **kwargs: object) -> httpx.Client:
        kwargs["transport"] = httpx.MockTransport(handler)
        return _RealClient(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(weather_graph.httpx, "Client", factory)


def _handler(
    geocode_body: dict[str, Any], forecast_body: dict[str, Any] | None = None
) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        if "geocoding-api" in request.url.host:
            return httpx.Response(200, json=geocode_body)
        assert forecast_body is not None
        return httpx.Response(200, json=forecast_body)

    return handler


_GEOCODE_BODY = {
    "results": [{"name": "Singapore", "country": "Singapore", "latitude": 1.3, "longitude": 103.8}]
}


def _forecast_body(current_code: int, current_wind: float = 12.5) -> dict[str, Any]:
    return {
        "current": {
            "temperature_2m": 31.2,
            "relative_humidity_2m": 70,
            "wind_speed_10m": current_wind,
            "weather_code": current_code,
        },
        "daily": {
            "time": ["2026-01-01", "2026-01-02", "2026-01-03"],
            "weather_code": [1, 1, 1],
            "temperature_2m_max": [32.0, 32.5, 31.0],
            "temperature_2m_min": [25.0, 25.5, 24.0],
            "precipitation_probability_max": [10, 5, 20],
        },
    }


@pytest.fixture(autouse=True)
def _no_llm_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Most tests exercise the deterministic fallback path (no OpenAI key configured), matching
    this tool's pre-existing "no key needed" behavior. Tests that want the LLM path opt in
    explicitly by monkeypatching settings.openai_api_key themselves."""

    monkeypatch.setattr(settings, "openai_api_key", "")


async def test_run_returns_weather_and_forecast_for_a_known_location(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_client(monkeypatch, _handler(_GEOCODE_BODY, _forecast_body(current_code=2)))

    result = json.loads(await WeatherAgentTool().run(location="Singapore"))

    assert result["location"] == "Singapore, Singapore"
    assert result["temperature_c"] == 31.2
    assert result["condition"] == "Partly cloudy"
    assert result["wind_speed_kmh"] == 12.5
    assert result["humidity_percent"] == 70
    assert len(result["forecast"]) == 3
    assert result["forecast"][0]["date"] == "2026-01-01"
    assert result["severe_weather_alert"] is False
    assert "no severe weather" in result["advisory"].lower()


async def test_run_flags_a_severe_weather_signal_from_the_current_conditions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_client(monkeypatch, _handler(_GEOCODE_BODY, _forecast_body(current_code=95)))

    result = json.loads(await WeatherAgentTool().run(location="Singapore"))

    assert result["severe_weather_alert"] is True
    assert "severe weather" in result["advisory"].lower()


async def test_run_flags_a_severe_weather_signal_from_high_wind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_client(
        monkeypatch, _handler(_GEOCODE_BODY, _forecast_body(current_code=1, current_wind=60.0))
    )

    result = json.loads(await WeatherAgentTool().run(location="Singapore"))

    assert result["severe_weather_alert"] is True


async def test_run_returns_error_for_unknown_location(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_client(monkeypatch, _handler({"results": []}))

    result = json.loads(await WeatherAgentTool().run(location="Nowhereville"))

    assert "error" in result


async def test_run_returns_error_on_upstream_geocode_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    _mock_client(monkeypatch, handler)

    result = json.loads(await WeatherAgentTool().run(location="Singapore"))

    assert "error" in result


async def test_run_uses_the_llm_advisory_when_a_key_is_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "openai_api_key", "sk-test")
    monkeypatch.setattr(settings, "weather_advisory_enabled", True)
    monkeypatch.setattr(
        weather_graph, "_query_advisory_llm", lambda state, *, severe: "Bring an umbrella."
    )
    _mock_client(monkeypatch, _handler(_GEOCODE_BODY, _forecast_body(current_code=2)))

    result = json.loads(await WeatherAgentTool().run(location="Singapore"))

    assert result["advisory"] == "Bring an umbrella."


async def test_run_falls_back_when_the_llm_advisory_call_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "openai_api_key", "sk-test")

    def _raise(state: object, *, severe: bool) -> str:
        raise RuntimeError("boom")

    monkeypatch.setattr(weather_graph, "_query_advisory_llm", _raise)
    _mock_client(monkeypatch, _handler(_GEOCODE_BODY, _forecast_body(current_code=2)))

    result = json.loads(await WeatherAgentTool().run(location="Singapore"))

    assert "no severe weather" in result["advisory"].lower()


async def test_run_skips_the_llm_when_the_kill_switch_is_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "openai_api_key", "sk-test")
    monkeypatch.setattr(settings, "weather_advisory_enabled", False)
    monkeypatch.setattr(
        weather_graph, "_query_advisory_llm", lambda state, *, severe: "should not be called"
    )
    _mock_client(monkeypatch, _handler(_GEOCODE_BODY, _forecast_body(current_code=2)))

    result = json.loads(await WeatherAgentTool().run(location="Singapore"))

    assert result["advisory"] != "should not be called"


def test_tool_metadata_shape() -> None:
    tool = WeatherAgentTool()
    assert tool.name == "get_weather"
    assert tool.parameters["required"] == ["location"]
    assert set(tool.parameters["properties"]) == {"location"}
