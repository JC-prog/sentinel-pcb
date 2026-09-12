"""Public entrypoint for the intent router: classify_intent() runs the one-node LangGraph
pipeline (graph.py) off the event loop (its OpenAI call is blocking), and turns any failure -
including no OpenAI key configured or nothing to route among - into a RouterDecision that tells
the caller (app/main.py) to fall back to "no restriction, no clarification" rather than raise.
"""

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from app.agents.router_agent.graph import RouterState, get_pipeline
from app.config.settings import settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RouterDecision:
    target_tool: str | None
    confidence: float
    clarifying_question: str | None


_NOT_ROUTED = RouterDecision(target_tool=None, confidence=1.0, clarifying_question=None)


async def classify_intent(
    message: str, *, has_image: bool, candidate_tools: list[dict[str, Any]]
) -> RouterDecision:
    """High confidence + no clarifying_question means "proceed as if routing were off" - used
    both when routing genuinely isn't needed (nothing to route among) and when it couldn't run at
    all (no OpenAI key, upstream failure)."""

    if not candidate_tools or not settings.openai_api_key:
        return _NOT_ROUTED

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
    final_state = await asyncio.to_thread(pipeline.invoke, initial_state)

    if final_state.get("error"):
        logger.warning("Intent router failed, offering every tool: %s", final_state["error"])
        return _NOT_ROUTED

    return RouterDecision(
        target_tool=final_state["target_tool"],
        confidence=final_state["confidence"],
        clarifying_question=final_state["clarifying_question"],
    )


__all__ = ["RouterDecision", "classify_intent"]
