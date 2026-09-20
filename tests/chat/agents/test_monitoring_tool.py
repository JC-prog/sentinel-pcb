import json

from app.chat.agents.monitoring_agent import MonitoringAgentTool


async def test_run_returns_not_implemented_placeholder() -> None:
    result = json.loads(await MonitoringAgentTool().run())

    assert result["status"] == "not_implemented"
    assert "message" in result


def test_tool_metadata_shape() -> None:
    tool = MonitoringAgentTool()
    assert tool.name == "monitoring_status"
    assert tool.parameters == {"type": "object", "properties": {}, "required": []}
