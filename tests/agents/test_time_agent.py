import json
from collections.abc import Callable
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import httpx
import pytest

from app.agents.time_agent import CurrentTimeAgentTool
from app.agents.time_agent import graph as time_graph

_RealClient = httpx.Client


def _mock_client(
    monkeypatch: pytest.MonkeyPatch, handler: Callable[[httpx.Request], httpx.Response]
) -> None:
    def factory(*args: object, **kwargs: object) -> httpx.Client:
        kwargs["transport"] = httpx.MockTransport(handler)
        return _RealClient(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(time_graph.httpx, "Client", factory)


def _geocode_handler(
    results: list[dict[str, object]],
) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": results})

    return handler


async def test_run_defaults_to_utc_when_no_location_given() -> None:
    result = json.loads(await CurrentTimeAgentTool().run())

    assert result["location"] == "UTC"
    assert result["timezone"] == "UTC"
    parsed = datetime.fromisoformat(result["datetime"])
    assert abs((datetime.now(UTC) - parsed).total_seconds()) < 5


async def test_run_resolves_a_location_to_its_timezone(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_client(
        monkeypatch,
        _geocode_handler(
            [{"name": "Tokyo", "country": "Japan", "timezone": "Asia/Tokyo"}],
        ),
    )

    result = json.loads(await CurrentTimeAgentTool().run(location="Tokyo"))

    assert result["location"] == "Tokyo, Japan"
    assert result["timezone"] == "Asia/Tokyo"
    parsed = datetime.fromisoformat(result["datetime"])
    assert abs((datetime.now(ZoneInfo("Asia/Tokyo")) - parsed).total_seconds()) < 5


async def test_run_flags_business_hours(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        time_graph,
        "datetime",
        type(
            "_FixedDatetime",
            (),
            {"now": staticmethod(lambda tz=None: datetime(2026, 1, 5, 14, 0, tzinfo=tz))},
        ),
    )

    result = json.loads(await CurrentTimeAgentTool().run())

    assert result["is_business_hours"] is True
    assert result["day_of_week"] == "Monday"
    assert "business hours" in result["note"].lower()


async def test_run_flags_after_hours(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        time_graph,
        "datetime",
        type(
            "_FixedDatetime",
            (),
            {"now": staticmethod(lambda tz=None: datetime(2026, 1, 5, 22, 0, tzinfo=tz))},
        ),
    )

    result = json.loads(await CurrentTimeAgentTool().run())

    assert result["is_business_hours"] is False
    assert "outside typical business hours" in result["note"].lower()


async def test_run_returns_error_for_unknown_location(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_client(monkeypatch, _geocode_handler([]))

    result = json.loads(await CurrentTimeAgentTool().run(location="Nowhereville"))

    assert "error" in result


async def test_run_returns_error_on_upstream_geocode_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    _mock_client(monkeypatch, handler)

    result = json.loads(await CurrentTimeAgentTool().run(location="Tokyo"))

    assert "error" in result


def test_tool_metadata_shape() -> None:
    tool = CurrentTimeAgentTool()
    assert tool.name == "current_time"
    assert tool.parameters["required"] == []
    assert set(tool.parameters["properties"]) == {"location"}
