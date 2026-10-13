"""The chat turn: the SSE reply generator for POST /api/chat/stream. It handles everything around
the model - /remember, guardrails, memory, persisting the turn - and hands the reply itself to the
supervisor agent (app/chat/agents/supervisor.py), relaying what it does as SSE frames:

- `event: delta` - reply text as it arrives
- `event: tool_call` - `{name, label}` just before a tool runs (a UI progress indicator)
- `event: tool_result` - `{name, result}` a tool's structured result, shown by the UI as a card
- `event: error` - the turn failed; `event: done` - always last, with the turn's `usage` (input and
  output tokens) when the provider reported it

Which tools exist and which a request may use are the agents' business (`tool_registry`); this
module names none of them. Kept separate from app/chat/api/chat.py (the thin route layer).
"""

import asyncio
import json
import logging
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import Any

import anyio

from app.chat.agents import tool_registry
from app.chat.agents.supervisor import (
    Answer,
    TextChunk,
    ToolFinished,
    ToolStarted,
    Usage,
    run_turn,
)
from app.chat.agents.toolkit import ChatTool
from app.chat.core.tools import ToolContext
from app.chat.db import Conversation
from app.chat.guardrails import get_guardrails_checker
from app.chat.memory import (
    build_memory_preamble,
    forget_reply,
    maybe_extract,
    memories_reply,
    remember_explicit,
)
from app.chat.services import history
from app.chat.services.messages import build_messages, compose_system_prompt
from app.chat.services.schemas import LlmProvider
from app.shared.auth.dependencies import SessionDep
from app.shared.config.settings import settings
from app.shared.db import User, UserRole

_REMEMBER_PREFIX = "/remember "
_FORGET_PREFIX = "/forget"
_MEMORIES_COMMAND = "/memories"

logger = logging.getLogger(__name__)


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def _available_tools(image_ids: list[str], role: UserRole) -> list[ChatTool]:
    """None of them when tool calling is switched off - the model is then given no tools at all."""

    if not settings.chat_tool_calling_enabled:
        return []
    return tool_registry.available(role=role, has_image=bool(image_ids))


async def _save_interrupted(
    session: SessionDep,
    conversation: Conversation,
    message: str,
    text: str,
    cards: list[dict[str, Any]],
) -> None:
    """Persists a reply that was stopped or failed part-way, flagged `interrupted`, so reopening the
    conversation (here or on another device) shows what the user saw and the model's next turn
    replays it. Nothing is written if nothing had streamed yet. Shielded: on a client disconnect
    this runs inside a cancelled scope, where every await would otherwise be cancelled again."""

    if not text and not cards:
        return
    with anyio.CancelScope(shield=True):
        try:
            await history.append_message(
                session,
                conversation.id,
                "assistant",
                text,
                [],
                tool_results=cards,
                interrupted=True,
            )
            await history.maybe_set_title(session, conversation, message)
        except Exception:
            logger.exception("could not save the interrupted reply for %s", conversation.id)


async def chat_sse(
    session: SessionDep,
    conversation: Conversation,
    is_new_conversation: bool,
    provider: LlmProvider,
    message: str,
    image_ids: list[str],
    xml_ids: list[str],
    user: User,
    model: str | None = None,
) -> AsyncGenerator[str, None]:
    """SSE body for POST /api/chat/stream (frames: see the module docstring).

    Persists the user's message before the model runs (durable even if the model fails partway) and
    the assistant's final answer after it succeeds - see app/chat/services/history.py - together with
    the cards it showed. A reply cut short (Stop, a dropped connection, a failure part-way) is kept
    as it was, flagged `interrupted`. Intermediate tool-call rounds and tool_call frames are never
    persisted. `conversation` is
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

    # `/memories` and `/forget` are the other half of `/remember`: see what is kept, and drop it.
    # Same shape - a pure memory command that never reaches the LLM.
    command = message.strip()
    is_forget = command == _FORGET_PREFIX or command.startswith(_FORGET_PREFIX + " ")
    if command == _MEMORIES_COMMAND or is_forget:
        await history.append_message(session, conversation.id, "user", message, image_ids)
        if is_forget:
            reply = await forget_reply(
                conversation.user_id, provider, command.removeprefix(_FORGET_PREFIX)
            )
        else:
            reply = await memories_reply(conversation.user_id, provider)
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
    streamed = ""  # the reply text sent so far - what survives if the turn is cut short
    cards: list[dict[str, Any]] = []
    usage: dict[str, int] = {}
    try:
        async for event in run_turn(
            provider=provider, messages=messages, ctx=ctx, tools=tools, model=model
        ):
            if isinstance(event, TextChunk):
                streamed += event.text
                yield _sse("delta", {"text": event.text})
            elif isinstance(event, ToolStarted):
                yield _sse("tool_call", {"name": event.name, "label": event.label})
            elif isinstance(event, ToolFinished):
                if event.card is not None:
                    cards.append({"name": event.name, "result": event.card})
                    yield _sse("tool_result", {"name": event.name, "result": event.card})
            elif isinstance(event, Usage):
                usage = {
                    "input_tokens": event.input_tokens,
                    "output_tokens": event.output_tokens,
                }
            elif isinstance(event, Answer):
                final_text = event.text
                if event.rounds_exhausted:
                    streamed += event.text
                    yield _sse("delta", {"text": event.text})
    except Exception:
        # The model call itself failed (unreachable, rejected, timed out). Logged in full; the
        # client gets a fixed message, never the upstream error (which can echo request details).
        logger.exception("chat turn failed for conversation %s", conversation.id)
        await _save_interrupted(session, conversation, message, streamed, cards)
        yield _sse("error", {"message": "The assistant is unavailable right now."})
        return
    except (asyncio.CancelledError, GeneratorExit):
        # The client went away (the user pressed Stop, closed the tab). Keep what was written.
        await _save_interrupted(session, conversation, message, streamed, cards)
        raise

    logger.debug("chat response: %r", final_text, extra={"final_text": final_text})
    saved = await history.append_message(
        session, conversation.id, "assistant", final_text, [], tool_results=cards
    )
    await history.maybe_set_title(session, conversation, message)
    await maybe_extract(session, conversation, provider)
    # The reply's id lets the UI rate it (PUT /api/messages/{id}/feedback) without a reload.
    done: dict[str, Any] = {"message_id": saved.id}
    if usage:
        done["usage"] = usage
    yield _sse("done", done)
