"""Business logic for the chat tool-calling loop: the tool registry, which tools a role/request
may see, dispatching a model-requested tool call, and the SSE reply generator itself. Kept
separate from app/api/chat.py (the thin route layer) so that file stays limited to request
validation and response shaping - this is where the actual conversation/tool-orchestration
behavior lives.
"""

import json
import logging
from collections.abc import AsyncGenerator
from typing import Any

from PIL import Image

from app.agents import (
    CreateCaseTool,
    CurrentTimeAgentTool,
    FlagCaseForRetrainingTool,
    InvestigateCaseTool,
    ListCasesTool,
    MonitoringAgentTool,
    ReviewCaseTool,
    ToolRegistry,
    WeatherAgentTool,
    call_tool,
)
from app.agents.access import allowed_tool_names
from app.agents.explainability_review_agent import ExplainabilityReviewTool
from app.agents.router_agent import Clarify, route
from app.auth.dependencies import SessionDep
from app.chat import get_chat_service, history
from app.chat.messages import build_messages
from app.chat.schemas import LlmProvider
from app.config.settings import settings
from app.core.chat import ChatMessage, TextDelta, ToolCallRequest
from app.db import Conversation, User, UserRole
from app.memory import build_memory_preamble, maybe_extract, remember_explicit
from app.uploads import resolve_upload_path

_REMEMBER_PREFIX = "/remember "

logger = logging.getLogger(__name__)

# Constructing ExplainabilityReviewTool() here doesn't load anything heavy - it's a thin wrapper;
# the actual CLIP model load is deferred to first use of the agent (see
# app/agents/explainability_review_agent/graph.py's get_mcp_client()). Exposed to app/api/chat.py
# for its own direct-invocation explainability-review route.
tool_registry = ToolRegistry(
    [
        CreateCaseTool(),
        CurrentTimeAgentTool(),
        ExplainabilityReviewTool(),
        FlagCaseForRetrainingTool(),
        InvestigateCaseTool(),
        ListCasesTool(),
        MonitoringAgentTool(),
        ReviewCaseTool(),
        WeatherAgentTool(),
    ]
)


# Human-friendly names for the `event: tool_call` SSE frame emitted just before each tool actually
# runs (see chat_sse below) - purely cosmetic, for the UI to show "Calling <label>..." while a
# tool call is in flight. Falls back to a humanized version of the raw tool name for anything not
# listed here, so a future tool never goes unlabeled.
_TOOL_DISPLAY_LABELS: dict[str, str] = {
    "create_case": "ADC Inspection Agent",
    "explainability_review": "Explainability Agent",
    "investigate_case": "Explainability Agent",
    "list_cases": "Case Lookup",
    "review_case": "Case Review",
    "flag_case_for_retraining": "Monitoring Agent",
    "monitoring_status": "Monitoring Agent",
    "current_time": "Current Time",
    "get_weather": "Weather Agent",
}


def _tool_display_label(name: str) -> str:
    return _TOOL_DISPLAY_LABELS.get(name, name.replace("_", " ").title())


def _available_tool_specs(image_ids: list[str], role: UserRole) -> list[dict[str, Any]] | None:
    """None means "send no `tools` field at all" - both the kill switch and the empty-registry
    case fall back to this, so a disabled feature is byte-identical to the pre-tool-calling
    request shape. Every spec is first narrowed to what `role` is allowed to call at all
    (app/agents/access.py) - re-checked again at dispatch time in _run_tool_call, since hiding a
    spec from the LLM isn't itself an access control. explainability_review and create_case are
    only ever offered when an image is actually attached to this message - the model has no way to
    reference a real upload id itself (see _run_tool_call, which overrides whatever it supplies
    anyway). investigate_case and flag_case_for_retraining need no image attached - they resolve
    an existing Case by number instead."""

    if not settings.chat_tool_calling_enabled:
        return None
    specs = [s for s in tool_registry.specs() if s["name"] in allowed_tool_names(role)]

    if not settings.explainability_agent_enabled:
        specs = [s for s in specs if s["name"] not in ("explainability_review", "investigate_case")]
    if not image_ids:
        specs = [s for s in specs if s["name"] != "explainability_review"]

    if not image_ids or not settings.adc_inspection_agent_enabled:
        specs = [s for s in specs if s["name"] != "create_case"]

    if not settings.monitoring_agent_enabled:
        specs = [
            s for s in specs if s["name"] not in ("monitoring_status", "flag_case_for_retraining")
        ]

    return specs or None


async def _run_tool_call(
    call: ToolCallRequest,
    *,
    session: SessionDep,
    image_ids: list[str],
    xml_ids: list[str],
    conversation_id: str,
    user: User,
) -> str:
    """Executes one model-requested tool call. Never raises - any failure becomes a
    {"error": ...} tool result fed back to the model, so one bad call degrades gracefully
    instead of ending the whole SSE stream (mirrors app/agents/explainability_review_agent/
    mcp_client.py's own graceful-degradation pattern).

    Defense in depth: re-checks role access even though _available_tool_specs already filtered
    what the LLM was offered - a tool-call request naming something that wasn't offered should
    never actually dispatch.

    explainability_review, investigate_case, and the case tools need kwargs the model can't supply
    itself - a real image (and, for explainability_review/investigate_case, the server's OpenAI
    key) - injected here the same way app/api/chat.py's explainability-review route already does
    it by hand."""

    role = UserRole(user.role)
    if call.name not in allowed_tool_names(role):
        return json.dumps({"error": f"tool {call.name!r} is not permitted for role {user.role!r}"})

    arguments = dict(call.arguments)
    if call.name == "explainability_review":
        image_id = image_ids[0]  # only offered when non-empty - see _available_tool_specs
        image_path = resolve_upload_path(image_id)
        if image_path is None:
            return json.dumps({"error": "image not found"})
        if not settings.openai_api_key:
            return json.dumps({"error": "no OpenAI key configured on this server"})
        arguments = {
            "image": Image.open(image_path).convert("RGB"),
            "image_name": image_id,
            "board_id": arguments.get("board_id", ""),
            "component_ref": arguments.get("component_ref", ""),
            "issue_symptom": arguments.get("issue_symptom"),
            "openai_api_key": settings.openai_api_key,
        }
    elif call.name == "investigate_case":
        if not settings.openai_api_key:
            return json.dumps({"error": "no OpenAI key configured on this server"})
        arguments = {**arguments, "session": session, "openai_api_key": settings.openai_api_key}
    elif call.name == "flag_case_for_retraining":
        arguments = {**arguments, "session": session, "user_id": user.id}
    elif call.name == "create_case":
        image_id = image_ids[0]  # only offered when non-empty - see _available_tool_specs
        image_path = resolve_upload_path(image_id)
        if image_path is None:
            return json.dumps({"error": "image not found"})
        inspection_xml_bytes = None
        inspection_xml_id = None
        if xml_ids:
            xml_path = resolve_upload_path(xml_ids[0])
            if xml_path is not None:
                inspection_xml_bytes = xml_path.read_bytes()
                inspection_xml_id = xml_ids[0]
        arguments = {
            "session": session,
            "image_bytes": image_path.read_bytes(),
            "image_name": image_id,
            "inspection_xml_bytes": inspection_xml_bytes,
            "inspection_xml_id": inspection_xml_id,
            "username": user.username,
            "user_id": user.id,
            "conversation_id": conversation_id,
            "board_id": arguments.get("board_id", ""),
            "component_ref": arguments.get("component_ref", ""),
            "package": arguments.get("package"),
            "feature": arguments.get("feature"),
            "issue_symptom": arguments.get("issue_symptom"),
        }
    elif call.name in {"list_cases", "review_case"}:
        arguments = {**arguments, "session": session, "user_id": user.id, "username": user.username}

    try:
        return await call_tool(tool_registry, call.name, arguments)
    except Exception as exc:  # noqa: BLE001 - degrade to a tool-result error, not an SSE error
        return json.dumps({"error": str(exc)})


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
    """SSE body for POST /api/chat/stream: `event: delta` per chunk from the chat service,
    `event: tool_call` (`{name, label}`) just before a tool call actually dispatches, `event:
    error` if it raises, always ending in `event: done`. Same framing as the original app's
    `_trace_stream` (GET /workflows/{id}/trace).

    Persists the user's message before streaming starts (durable even if the LLM call fails
    partway) and the assistant's full reply after streaming succeeds - see app/chat/history.py.
    `conversation` is already resolved/ownership-checked by the caller (app/api/chat.py's
    chat_stream), since a StreamingResponse commits its 200 status before this generator's first
    item is even requested - anything that should be able to 404 instead has to happen before
    this is called.

    The reply itself may take several tool-call round trips (app/agents/registry.py) before the
    model produces a final answer - see the loop below. Only the final round's text is persisted
    as the assistant's message; intermediate tool-call rounds' text (usually empty) is discarded.
    `tool_call` events are purely a UI progress indicator ("Calling Explainability Agent...") -
    they're never persisted to history either.
    """

    # A pure memory-write command (app/memory/service.py) - never reaches the LLM, so it can't
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
        yield f"event: delta\ndata: {json.dumps({'text': reply})}\n\n"
        await history.append_message(session, conversation.id, "assistant", reply, [])
        await history.maybe_set_title(session, conversation, message)
        yield "event: done\ndata: {}\n\n"
        return

    turns = await history.load_history(
        session, conversation.id, max_turns=settings.chat_history_max_turns
    )
    await history.append_message(session, conversation.id, "user", message, image_ids)
    system_prompt = (
        await build_memory_preamble(conversation.user_id, message, provider)
        if is_new_conversation
        else None
    )

    service = get_chat_service(provider)
    messages = build_messages(system_prompt, turns, message, image_ids=image_ids, xml_ids=xml_ids)
    available_tools = _available_tool_specs(image_ids, UserRole(user.role))
    logger.debug(
        "chat request: provider=%s message=%r image_ids=%s",
        provider,
        message,
        image_ids,
        extra={"provider": provider, "chat_message": message, "image_ids": image_ids},
    )

    routing_outcome = await route(
        message, has_image=bool(image_ids), candidate_tools=available_tools
    )
    if isinstance(routing_outcome, Clarify):
        yield f"event: delta\ndata: {json.dumps({'text': routing_outcome.question})}\n\n"
        await history.append_message(
            session, conversation.id, "assistant", routing_outcome.question, []
        )
        await history.maybe_set_title(session, conversation, message)
        yield "event: done\ndata: {}\n\n"
        return
    available_tools = routing_outcome.available_tools

    final_text = ""
    try:
        for _round in range(settings.chat_tool_max_rounds):
            text_parts: list[str] = []
            pending_calls: list[ToolCallRequest] = []
            async for event in service.stream_with_tools(messages, available_tools):
                if isinstance(event, TextDelta):
                    text_parts.append(event.text)
                    yield f"event: delta\ndata: {json.dumps({'text': event.text})}\n\n"
                else:
                    pending_calls = event.calls
            final_text = "".join(text_parts)
            if not pending_calls:
                break

            messages.append(
                ChatMessage(role="assistant", content=final_text or None, tool_calls=pending_calls)
            )
            for call in pending_calls:
                yield (
                    "event: tool_call\n"
                    f"data: {json.dumps({'name': call.name, 'label': _tool_display_label(call.name)})}"
                    "\n\n"
                )
                result = await _run_tool_call(
                    call,
                    session=session,
                    image_ids=image_ids,
                    xml_ids=xml_ids,
                    conversation_id=conversation.id,
                    user=user,
                )
                messages.append(
                    ChatMessage(role="tool", tool_call_id=call.id, name=call.name, content=result)
                )
            final_text = ""
        else:
            final_text = (
                "I wasn't able to finish that after several tool calls - could you rephrase or "
                "simplify the request?"
            )
            yield f"event: delta\ndata: {json.dumps({'text': final_text})}\n\n"
    except Exception as exc:  # noqa: BLE001 - reported to the client as an SSE error event
        logger.debug("chat response error: %s", exc, extra={"error": str(exc)})
        yield f"event: error\ndata: {json.dumps({'message': str(exc)})}\n\n"
        return

    logger.debug("chat response: %r", final_text, extra={"final_text": final_text})
    await history.append_message(session, conversation.id, "assistant", final_text, [])
    await history.maybe_set_title(session, conversation, message)
    await maybe_extract(session, conversation, provider)
    yield "event: done\ndata: {}\n\n"
