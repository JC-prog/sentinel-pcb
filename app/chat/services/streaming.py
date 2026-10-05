"""The chat turn: the SSE reply generator for POST /api/chat/stream. It handles everything around
the model - /remember, guardrails, memory, persisting the turn - and hands the reply itself to the
supervisor agent (app/chat/agents/supervisor.py), relaying what it does as SSE frames:

- `event: delta` - reply text as it arrives
- `event: tool_call` - `{name, label}` just before a tool runs (a UI progress indicator)
- `event: tool_result` - `{name, result}` a tool's structured result, shown by the UI as a card
- `event: error` - the turn failed; `event: done` - always last

Which tools exist and which a request may use are the agents' business (`tool_registry`); this
module names none of them. Kept separate from app/chat/api/chat.py (the thin route layer).
"""

import json
import logging
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import Any

from app.chat.agents import tool_registry
from app.chat.agents.supervisor import Answer, TextChunk, ToolFinished, ToolStarted, run_turn
from app.chat.agents.toolkit import ChatTool
from app.chat.core.tools import ToolContext
from app.chat.db import Conversation
from app.chat.guardrails import get_guardrails_checker
from app.chat.memory import build_memory_preamble, maybe_extract, remember_explicit
from app.chat.services import history
from app.chat.services.messages import build_messages, compose_system_prompt
from app.chat.services.schemas import LlmProvider
from app.shared.auth.dependencies import SessionDep
from app.shared.config.settings import settings
from app.shared.db import User, UserRole

_REMEMBER_PREFIX = "/remember "

logger = logging.getLogger(__name__)


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def _available_tools(image_ids: list[str], role: UserRole) -> list[ChatTool]:
    """None of them when tool calling is switched off - the model is then given no tools at all."""

    if not settings.chat_tool_calling_enabled:
        return []
    return tool_registry.available(role=role, has_image=bool(image_ids))


async def chat_sse(
    session: SessionDep,
    conversation: Conversation,
    is_new_conversation: bool,
    provider: LlmProvider,
    message: str,
    image_ids: list[str],
    xml_ids: list[str],
    user: User,
) -> AsyncGenerator[str, None]:
    """SSE body for POST /api/chat/stream (frames: see the module docstring).

    Persists the user's message before the model runs (durable even if the model fails partway) and
    the assistant's final answer after it succeeds - see app/chat/services/history.py. Intermediate
    tool-call rounds, tool_call and tool_result frames are never persisted. `conversation` is
    already resolved/ownership-checked by the caller (app/chat/api/chat.py's chat_stream), since a
    StreamingResponse commits its 200 status before this generator's first item is requested -
    anything that should be able to 404 has to happen before this is called.
    """

    # Taken first: a relabel proposed in one turn can only be confirmed in a later one
    # (app/chat/agents/relabel_agent/service.py), and this is what tells the turns apart.
    turn_started_at = datetime.now(UTC)

    # A pure memory-write command (app/chat/memory/service.py) - never reaches the LLM, so it can't
    # be derailed by (or accidentally leak into) the actual conversation. Checked before touching
    # history/persistence since it's a completely different code path from a normal chat turn.
    if message.strip().startswith(_REMEMBER_PREFIX):
        await history.append_message(session, conversation.id, "user", message, image_ids)
        fact = message.strip().removeprefix(_REMEMBER_PREFIX).strip()
        reply = (
            await remember_explicit(conversation.user_id, conversation.id, fact, provider)
            if fact
            else "Nothing to remember - add some text after /remember."
        )
        yield _sse("delta", {"text": reply})
        await history.append_message(session, conversation.id, "assistant", reply, [])
        await history.maybe_set_title(session, conversation, message)
        yield _sse("done", {})
        return

    turns = await history.load_history(
        session, conversation.id, max_turns=settings.chat_history_max_turns
    )
    await history.append_message(session, conversation.id, "user", message, image_ids)

    if settings.chat_guardrails_enabled:
        try:
            guardrail_result = await get_guardrails_checker().check_input(message)
        except Exception:
            # Fail open, same convention as every other kill-switchable agent in this repo (see
            # CLAUDE.md) - a broken guardrails check should never block the whole product.
            logger.exception("Guardrails input check failed - allowing the message through")
            guardrail_result = None
        if guardrail_result is not None and not guardrail_result.allowed:
            reply = guardrail_result.reason or "I can't help with that here."
            yield _sse("delta", {"text": reply})
            await history.append_message(session, conversation.id, "assistant", reply, [])
            await history.maybe_set_title(session, conversation, message)
            yield _sse("done", {})
            return

    memory_preamble = (
        await build_memory_preamble(conversation.user_id, message, provider)
        if is_new_conversation
        else None
    )
    system_prompt = compose_system_prompt(memory_preamble)

    messages = build_messages(system_prompt, turns, message, image_ids=image_ids, xml_ids=xml_ids)
    tools = _available_tools(image_ids, UserRole(user.role))
    ctx = ToolContext(
        session=session,
        user=user,
        conversation_id=conversation.id,
        turn_started_at=turn_started_at,
        image_ids=tuple(image_ids),
        xml_ids=tuple(xml_ids),
    )
    logger.debug(
        "chat request: provider=%s message=%r image_ids=%s tools=%s",
        provider,
        message,
        image_ids,
        [t.name for t in tools],
        extra={"provider": provider, "chat_message": message, "image_ids": image_ids},
    )

    final_text = ""
    try:
        async for event in run_turn(provider=provider, messages=messages, ctx=ctx, tools=tools):
            if isinstance(event, TextChunk):
                yield _sse("delta", {"text": event.text})
            elif isinstance(event, ToolStarted):
                yield _sse("tool_call", {"name": event.name, "label": event.label})
            elif isinstance(event, ToolFinished):
                if event.card is not None:
                    yield _sse("tool_result", {"name": event.name, "result": event.card})
            elif isinstance(event, Answer):
                final_text = event.text
                if event.rounds_exhausted:
                    yield _sse("delta", {"text": event.text})
    except Exception:
        # The model call itself failed (unreachable, rejected, timed out). Logged in full; the
        # client gets a fixed message, never the upstream error (which can echo request details).
        logger.exception("chat turn failed for conversation %s", conversation.id)
        yield _sse("error", {"message": "The assistant is unavailable right now."})
        return

    logger.debug("chat response: %r", final_text, extra={"final_text": final_text})
    await history.append_message(session, conversation.id, "assistant", final_text, [])
    await history.maybe_set_title(session, conversation, message)
    await maybe_extract(session, conversation, provider)
    yield _sse("done", {})
