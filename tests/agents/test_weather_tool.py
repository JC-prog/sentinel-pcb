import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from app.agents.weather_agent import WeatherTool

_RealAsyncClient = httpx.AsyncClient


def _mock_async_client(
    monkeypatch: pytest.MonkeyPatch, handler: Callable[[httpx.Request], httpx.Response]
) -> None:
    def factory(*args: object, **kwargs: object) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(handler)
        return _RealAsyncClient(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(httpx, "AsyncClient", factory)


def _handler(
    geocode_body: dict[str, Any], forecast_body: dict[str, Any] | None = None
) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        if "geocoding-api" in request.url.host:
            return httpx.Response(200, json=geocode_body)
        assert forecast_body is not None
        return httpx.Response(200, json=forecast_body)

    return handler


async def test_run_returns_weather_for_a_known_location(monkeypatch: pytest.MonkeyPatch) -> None:
    geocode_body = {
        "results": [
            {"name": "Singapore", "country": "Singapore", "latitude": 1.3, "longitude": 103.8}
        ]
    }
    forecast_body = {
        "current": {
            "temperature_2m": 31.2,
            "relative_humidity_2m": 70,
            "wind_speed_10m": 12.5,
            "weather_code": 2,
        }
    }
    _mock_async_client(monkeypatch, _handler(geocode_body, forecast_body))

    result = json.loads(await WeatherTool().run(location="Singapore"))

    assert result == {
        "location": "Singapore, Singapore",
        "temperature_c": 31.2,
        "condition": "Partly cloudy",
        "wind_speed_kmh": 12.5,
        "humidity_percent": 70,
    }


async def test_run_returns_error_for_unknown_location(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_async_client(monkeypatch, _handler({"results": []}))

    result = json.loads(await WeatherTool().run(location="Nowhereville"))

    assert "error" in result


async def test_run_returns_error_on_upstream_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    _mock_async_client(monkeypatch, handler)

    result = json.loads(await WeatherTool().run(location="Singapore"))

    assert "error" in result


def test_tool_metadata_shape() -> None:
    tool = WeatherTool()
    assert tool.name == "get_weather"
    assert tool.parameters["required"] == ["location"]
    assert set(tool.parameters["properties"]) == {"location"}
