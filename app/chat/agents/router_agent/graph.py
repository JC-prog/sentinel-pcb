"""A one-node LangGraph pipeline for the intent router: given the user's message and the tools
currently on offer (app/chat/agents/registry.py's ToolRegistry.specs(), already filtered by
app/chat/services/streaming.py's _available_tool_specs), ask the LLM to either pick the single best-matching tool
(or `null` for "no tool needed, this is just conversation") with a confidence score, or supply a
clarifying question when nothing is a confident fit.

Same sync-OpenAI-client-call shape as app/chat/agents/weather_agent/graph.py's _query_advisory_llm -
routed through the LiteLLM gateway (settings.openai_base_url), run off the event loop by
router_agent/__init__.py's classify_intent() via asyncio.to_thread. Kept as a one-node graph
(rather than a plain function) to match every other agent in app/chat/agents/ - state TypedDict, a
node function, build_graph(), a lazy module-wide pipeline singleton - even though there's only one
real step here.

Never raises: any failure (bad JSON, no key, upstream error) becomes state["error"], which the
caller treats as "couldn't route - fall back to offering every tool with no clarification" (see
app/chat/services/streaming.py), same fail-open stance as every other kill-switchable agent.
"""

import json
import logging
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from openai import OpenAI

from app.shared.config.settings import settings

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You route user messages in a chat app to the right tool, or ask a clarifying \
question when the request is too vague to route confidently.

You will be given the user's message and a list of available tools (name + description). Decide:
- If exactly one tool clearly matches what the user is asking for, set "target_tool" to its name.
- If the message is just conversation and doesn't need any tool (a greeting, small talk, a \
question you can answer directly), set "target_tool" to null and use a high confidence.
- If the message plausibly matches more than one tool, or doesn't give enough detail to tell \
which capability is needed, set "target_tool" to null, use a low confidence, and write a short \
"clarifying_question" asking the user for the detail that would disambiguate it.

Respond with a single JSON object: {"target_tool": "<name-or-null>", "confidence": <0.0-1.0>, \
"clarifying_question": "<string-or-null>"}. "clarifying_question" must be null whenever \
confidence is high. Never invent a tool name that wasn't listed."""


class RouterState(TypedDict):
    message: str
    has_image: bool
    candidate_tools: list[dict[str, Any]]
    target_tool: str | None
    confidence: float
    clarifying_question: str | None
    error: str | None


def _query_router_llm(user_prompt: str) -> dict[str, Any]:
    # Routed through the LiteLLM gateway like every other OpenAI-compatible call in the app
    # (app/chat/agents/weather_agent/graph.py, app/chat/agents/case_review_agent/models.py) -
    # never api.openai.com directly.
    client = OpenAI(api_key=settings.openai_api_key, base_url=settings.openai_base_url)
    response = client.chat.completions.create(
        model=settings.openai_model,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.0,
        response_format={"type": "json_object"},
    )
    payload: dict[str, Any] = json.loads(response.choices[0].message.content or "{}")
    return payload


def _classify_intent_node(state: RouterState) -> RouterState:
    if not state["candidate_tools"]:
        # Nothing to route among - let the caller fall back to "no restriction" directly.
        state["target_tool"] = None
        state["confidence"] = 1.0
        state["clarifying_question"] = None
        return state

    tool_list = "\n".join(
        f"- {t['name']}: {t['description']}" for t in state["candidate_tools"]
    )
    user_prompt = (
        f"Available tools:\n{tool_list}\n\n"
        f"Image attached to this message: {state['has_image']}\n"
        f"User message: {state['message']!r}"
    )

    try:
        payload = _query_router_llm(user_prompt)
    except Exception as exc:  # noqa: BLE001 - degrades to state["error"], never raises
        state["error"] = f"Intent routing failed: {exc}"
        return state

    target_tool = payload.get("target_tool")
    valid_names = {t["name"] for t in state["candidate_tools"]}
    if target_tool is not None and target_tool not in valid_names:
        target_tool = None

    try:
        confidence = float(payload.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0

    state["target_tool"] = target_tool
    state["confidence"] = max(0.0, min(1.0, confidence))
    state["clarifying_question"] = payload.get("clarifying_question") or None
    return state


def build_graph() -> CompiledStateGraph[RouterState, Any, Any, Any]:
    workflow = StateGraph(RouterState)
    workflow.add_node("classify_intent", _classify_intent_node)
    workflow.add_edge(START, "classify_intent")
    workflow.add_edge("classify_intent", END)
    return workflow.compile()


_pipeline: CompiledStateGraph[RouterState, Any, Any, Any] | None = None


def get_pipeline() -> CompiledStateGraph[RouterState, Any, Any, Any]:
    """Lazy, process-wide singleton - cheap to build, but no reason to rebuild it per call."""

    global _pipeline
    if _pipeline is None:
        _pipeline = build_graph()
    return _pipeline
