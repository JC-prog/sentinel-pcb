"""The supervisor: the chat model the user talks to, as a LangGraph agent (LangChain `create_agent`)
over the three agents' tools. It decides which tool to call (if any), reads the results and writes
the reply; the tools do the work.

One turn = one `run_turn`. It is given only the tools this request may use (registry.available), so
anything else simply does not exist for the model, and it streams back plain events - text as it
arrives, a tool starting, a tool's result, the final answer - that app/chat/services/streaming.py
turns into SSE frames. Nothing here knows about HTTP or persistence.

The model is the conversation's provider choice (app/shared/config/llm.py). `ToolContext` is passed
as the agent's runtime context, so every tool gets the user, session and uploads without the model
ever seeing them. Traced in Langfuse when that is on: the callback records every model call and tool
call, and the metadata attributes the trace to the user and conversation.
"""

import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Literal, cast

from langchain.agents import create_agent
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.errors import GraphRecursionError

from app.chat.agents.toolkit import ChatTool
from app.chat.core.tools import ToolContext
from app.shared.config.langfuse import langfuse_callbacks
from app.shared.config.llm import build_chat_model
from app.shared.config.settings import settings

logger = logging.getLogger(__name__)

ROUNDS_EXHAUSTED = (
    "I wasn't able to finish that after several tool calls - could you rephrase or simplify the "
    "request?"
)


@dataclass(frozen=True)
class TextChunk:
    text: str


@dataclass(frozen=True)
class ToolStarted:
    name: str
    label: str


@dataclass(frozen=True)
class ToolFinished:
    name: str
    result: str
    # The tool's result as a card for the UI; None for a tool without one, or a failed call (the
    # model explains an error in words).
    card: dict[str, Any] | None


@dataclass(frozen=True)
class Answer:
    text: str
    # True when the model kept calling tools past CHAT_TOOL_MAX_ROUNDS; `text` is then a fallback.
    rounds_exhausted: bool = False


SupervisorEvent = TextChunk | ToolStarted | ToolFinished | Answer

# "messages" streams reply text as the model produces it; "updates" reports each finished step -
# which tool calls the model asked for, and each tool's result.
_STREAM_MODES: list[Literal["messages", "updates"]] = ["messages", "updates"]


async def run_turn(
    *,
    provider: str,
    messages: list[BaseMessage],
    ctx: ToolContext,
    tools: list[ChatTool],
) -> AsyncIterator[SupervisorEvent]:
    """`messages` is the whole prompt: system message, history, the new user message. Errors from
    the model itself propagate - the caller reports them."""

    by_name = {t.name: t for t in tools}
    agent = create_agent(
        build_chat_model(provider, temperature=None, timeout=settings.chat_llm_timeout_seconds),
        tools=[t.tool for t in tools],
        context_schema=ToolContext,
    )
    config: RunnableConfig = {
        # A model turn and a tools turn per round, so this allows exactly CHAT_TOOL_MAX_ROUNDS model
        # calls that ask for tools before GraphRecursionError.
        "recursion_limit": 2 * settings.chat_tool_max_rounds,
        "callbacks": langfuse_callbacks(),
        "metadata": {
            "langfuse_user_id": ctx.user.id,
            "langfuse_session_id": ctx.conversation_id,
            "langfuse_tags": ["chat"],
        },
    }

    answer = ""
    try:
        # InputAgentState wants list[AnyMessage]; our list[BaseMessage] is the same messages.
        agent_input: Any = {"messages": messages}
        async for part in agent.astream(
            agent_input,
            config=config,
            context=ctx,
            stream_mode=_STREAM_MODES,
        ):
            # With several stream modes, each part is a (mode, data) pair.
            mode, data = cast(tuple[str, Any], part)
            if mode == "messages":
                chunk, metadata = data
                from_model = metadata.get("langgraph_node") == "model"
                if from_model and isinstance(chunk, AIMessageChunk | AIMessage) and chunk.text:
                    yield TextChunk(str(chunk.text))
                continue

            for node, update in data.items():
                for message in (update or {}).get("messages", []):
                    if node == "model" and isinstance(message, AIMessage):
                        if message.tool_calls:
                            for call in message.tool_calls:
                                yield ToolStarted(call["name"], _label(by_name, call["name"]))
                        else:
                            answer = str(message.text)
                    elif node == "tools" and isinstance(message, ToolMessage):
                        name = message.name or ""
                        result = str(message.content)
                        yield ToolFinished(name, result, _card(by_name.get(name), result))
    except GraphRecursionError:
        yield Answer(ROUNDS_EXHAUSTED, rounds_exhausted=True)
        return

    yield Answer(answer)


def _label(tools: dict[str, ChatTool], name: str) -> str:
    tool = tools.get(name)
    return tool.label if tool else name.replace("_", " ").title()


def _card(tool: ChatTool | None, result: str) -> dict[str, Any] | None:
    if tool is None or not tool.shows_card:
        return None
    try:
        payload = json.loads(result)
    except ValueError:
        return None
    if not isinstance(payload, dict) or "error" in payload:
        return None
    return payload
