import pytest

from app.agents.registry import ToolNotFound, ToolRegistry, call_tool
from app.agents.time_agent import CurrentTimeTool


def test_registry_get_returns_registered_tool() -> None:
    registry = ToolRegistry([CurrentTimeTool()])
    assert registry.get("current_time").name == "current_time"


def test_registry_get_raises_for_unknown_tool() -> None:
    registry = ToolRegistry()
    with pytest.raises(ToolNotFound):
        registry.get("does_not_exist")


async def test_call_tool_executes_by_name() -> None:
    registry = ToolRegistry([CurrentTimeTool()])
    result = await call_tool(registry, "current_time", {})
    assert result


def test_registry_specs_shape_is_provider_ready() -> None:
    registry = ToolRegistry([CurrentTimeTool()])
    [spec] = registry.specs()
    assert spec.keys() == {"name", "description", "parameters"}
