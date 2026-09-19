"""Public entrypoint for the intent router, in two layers:

- classify_intent() is the low-level primitive: it runs the one-node LangGraph pipeline
  (graph.py) off the event loop (its OpenAI call is blocking) and turns any failure - no OpenAI
  key, nothing to route among, an upstream error - into None rather than raising, so route() can
  tell "couldn't route, proceed unrestricted" apart from a genuine, confident "no tool needed"
  decision (which is a real RouterDecision with target_tool=None) - the two used to collapse into
  the same shape, which meant a broken/unconfigured router silently stripped every tool from the
  request instead of falling back to offering them all. It knows nothing about confidence
  thresholds or what a caller should *do* with a decision.
- route() is the policy layer: it calls classify_intent() and applies
  settings.intent_router_confidence_threshold - either Clarify (stop the turn, show this
  question) or Proceed (continue with the tool list narrowed to the router's pick, or
  unrestricted if routing didn't run or picked no tool). This is the piece app/main.py's
  _chat_sse used to do inline; pulling it in here means the policy is unit-testable on its own
  and main.py is just "call route(), branch on the result."
"""

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from langchain_core.runnables import RunnableConfig

from app.agents.router_agent.graph import RouterState, get_pipeline
from app.config.langfuse import get_langfuse_callbacks
from app.config.settings import settings

logger = logging.getLogger(__name__)

_DEFAULT_CLARIFYING_QUESTION = "Could you share a bit more detail about what you'd like help with?"


@dataclass(frozen=True)
class RouterDecision:
    target_tool: str | None
    confidence: float
    clarifying_question: str | None


async def classify_intent(
    message: str, *, has_image: bool, candidate_tools: list[dict[str, Any]]
) -> RouterDecision | None:
    """None means "couldn't classify - proceed as if routing were off" - routing genuinely isn't
    needed (nothing to route among), couldn't run at all (no OpenAI key), or the call itself
    failed (upstream error). Distinct from a real RouterDecision with target_tool=None, which
    means the LLM was actually asked and confidently decided no tool applies - only that case
    should clear the tool list; these should leave it untouched."""

    if not candidate_tools or not settings.openai_api_key:
        return None

    initial_state: RouterState = {
        "message": message,
        "has_image": has_image,
        "candidate_tools": candidate_tools,
        "target_tool": None,
        "confidence": 0.0,
        "clarifying_question": None,
        "error": None,
    }

    pipeline = get_pipeline()
    config: RunnableConfig = {"callbacks": get_langfuse_callbacks()}
    final_state = await asyncio.to_thread(pipeline.invoke, initial_state, config=config)

    if final_state.get("error"):
        logger.warning("Intent router failed, offering every tool: %s", final_state["error"])
        return None

    return RouterDecision(
        target_tool=final_state["target_tool"],
        confidence=final_state["confidence"],
        clarifying_question=final_state["clarifying_question"],
    )


@dataclass(frozen=True)
class Clarify:
    """Stop the chat turn here and show `question` instead of calling the model."""

    question: str


@dataclass(frozen=True)
class Proceed:
    """Continue the chat turn with `available_tools` (None means no restriction - every
    candidate stays on offer, same as if the router were off)."""

    available_tools: list[dict[str, Any]] | None


RoutingOutcome = Clarify | Proceed


async def route(
    message: str, *, has_image: bool, candidate_tools: list[dict[str, Any]] | None
) -> RoutingOutcome:
    """The router's full policy for one chat turn: skip entirely (kill switch off, or nothing to
    route among) and Proceed unrestricted; otherwise ask classify_intent() and apply
    settings.intent_router_confidence_threshold - below it, Clarify; at or above it, Proceed with
    the tools narrowed to the router's single pick (or cleared, if it decided no tool is needed).
    """

    if not settings.intent_router_enabled or not candidate_tools:
        return Proceed(candidate_tools)

    decision = await classify_intent(
        message, has_image=has_image, candidate_tools=candidate_tools
    )
    if decision is None:
        return Proceed(candidate_tools)

    if decision.confidence < settings.intent_router_confidence_threshold:
        return Clarify(decision.clarifying_question or _DEFAULT_CLARIFYING_QUESTION)

    if decision.target_tool is None:
        return Proceed(None)
    return Proceed([t for t in candidate_tools if t["name"] == decision.target_tool])


__all__ = [
    "Clarify",
    "Proceed",
    "RouterDecision",
    "RoutingOutcome",
    "classify_intent",
    "route",
]
