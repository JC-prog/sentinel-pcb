from datetime import UTC, datetime

from app.agents.time_agent import CurrentTimeTool


async def test_current_time_tool_returns_iso_utc_now() -> None:
    result = await CurrentTimeTool().run()
    parsed = datetime.fromisoformat(result)
    assert parsed.tzinfo is not None
    assert abs((datetime.now(UTC) - parsed).total_seconds()) < 5
