"""Executes a single agent tool call by name, within one chat turn. Deliberately minimal - no
multi-step planning/looping, no provider wiring yet.
"""

from collections.abc import Iterable
from typing import Any

from app.core.tools import Tool


class ToolNotFound(Exception):
    pass


class ToolRegistry:
    def __init__(self, tools: Iterable[Tool] = ()) -> None:
        self._tools: dict[str, Tool] = {tool.name: tool for tool in tools}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool:
        try:
            return self._tools[name]
        except KeyError:
            raise ToolNotFound(name) from None

    def specs(self) -> list[dict[str, Any]]:
        """Tool metadata in the shape a provider's function-calling API expects once one is
        wired up - see app/core/tools.py."""

        return [
            {"name": t.name, "description": t.description, "parameters": t.parameters}
            for t in self._tools.values()
        ]


async def call_tool(registry: ToolRegistry, name: str, arguments: dict[str, Any]) -> str:
    tool = registry.get(name)
    return await tool.run(**arguments)
