"""What every chat tool is built from.

A chat tool is a LangChain `@tool` - its docstring is the description the model reads, its
annotated parameters are the arguments the model may supply - plus one injected parameter,
`runtime: ToolRuntime[ToolContext]`, that LangGraph fills in and the model never sees: who is asking,
in which conversation, with which attachments, on which DB session. So nothing the model writes can
stand in for the user, the session or an upload.

Under `@tool`, `@returns_json` lets the tool just return a dict: it encodes the JSON the model reads,
turns a `ToolRefused` (anything the model should be told and can act on) into `{"error": ...}`, and
turns a bug into a logged, generic error instead of a crashed chat turn.

`ChatTool` wraps the LangChain tool with what the chat loop needs to know about it - its UI label,
whether it needs an attached image, whether its result is shown as a card, and its kill switch - so
the loop itself never names a tool.

    @tool("get_drift_summary")
    @returns_json
    async def get_drift_summary(runtime: Runtime, model: Annotated[str, "..."]) -> dict[str, Any]:
        \"\"\"What the model reads about this tool.\"\"\"
"""

import functools
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from langchain.tools import ToolRuntime
from langchain_core.tools import BaseTool

from app.chat.core.tools import ToolContext

logger = logging.getLogger(__name__)

# The injected parameter every chat tool declares: `runtime: Runtime`.
Runtime = ToolRuntime[ToolContext]


def _always() -> bool:
    return True


@dataclass(frozen=True)
class ChatTool:
    tool: BaseTool
    # Shown in the UI while the tool runs: "Calling <label>...".
    label: str
    # Its feature's kill switch, read per request.
    enabled: Callable[[], bool] = field(default=_always)
    # Only offered when the message has an image attached - the model cannot name an upload.
    requires_image: bool = False
    # Its result is also sent to the UI as an `event: tool_result` card.
    shows_card: bool = False

    @property
    def name(self) -> str:
        return self.tool.name

    def model_schema(self) -> dict[str, Any]:
        """The JSON Schema of what the model may supply - the injected `runtime` is not in it."""

        schema: Any = self.tool.tool_call_schema
        return schema if isinstance(schema, dict) else schema.model_json_schema()


class ToolRefused(Exception):
    """An expected refusal the model should read and act on (a missing argument, an unknown case,
    a label the model cannot output) - as opposed to a bug. `details` travel with the message, e.g.
    the valid labels the model can offer the user instead."""

    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(message)
        self.message = message
        self.details = details

    def payload(self) -> dict[str, Any]:
        return {"error": self.message, **self.details}


def returns_json[**P](fn: Callable[P, Awaitable[dict[str, Any]]]) -> Callable[P, Awaitable[str]]:
    @functools.wraps(fn)
    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> str:
        try:
            payload = await fn(*args, **kwargs)
        except ToolRefused as refusal:
            payload = refusal.payload()
        except Exception:
            # A bug, not a refusal - log it in full, tell the model only that it failed.
            logger.exception("tool %s failed", fn.__name__)
            payload = {"error": f"{fn.__name__} failed unexpectedly; tell the user to try again later"}
        return json.dumps(payload)

    return wrapper


def text(value: object) -> str:
    """A string argument, stripped; "" when the model omitted it or sent null."""

    return str(value).strip() if value is not None else ""


def required(value: object, name: str, hint: str = "") -> str:
    stripped = text(value)
    if not stripped:
        raise ToolRefused(f"{name} is required" + (f" - {hint}" if hint else ""))
    return stripped
