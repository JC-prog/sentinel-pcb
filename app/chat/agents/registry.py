"""The set of chat tools, and the question the chat loop asks of it: which tools may this request
see. Only those are given to the supervisor agent, so a tool the request may not see cannot be run
at all - LangGraph only executes tools it was given. Everything the answer depends on comes from the
tools' own declarations (`enabled()`, `requires_image`) and the role table in access.py.
"""

from collections.abc import Iterable, Iterator

from app.chat.agents.access import allowed_tool_names
from app.chat.agents.toolkit import ChatTool
from app.shared.db import UserRole


class ToolNotFound(Exception):
    pass


class ToolRegistry:
    def __init__(self, tools: Iterable[ChatTool] = ()) -> None:
        self._tools: dict[str, ChatTool] = {tool.name: tool for tool in tools}

    def __iter__(self) -> Iterator[ChatTool]:
        return iter(self._tools.values())

    def get(self, name: str) -> ChatTool:
        try:
            return self._tools[name]
        except KeyError:
            raise ToolNotFound(name) from None

    def available(self, *, role: UserRole, has_image: bool) -> list[ChatTool]:
        """Switched on, permitted for the role, and - for a tool that inspects an upload - only
        when an image is actually attached."""

        allowed = allowed_tool_names(role)
        return [
            tool
            for tool in self
            if tool.enabled() and tool.name in allowed and (has_image or not tool.requires_image)
        ]
