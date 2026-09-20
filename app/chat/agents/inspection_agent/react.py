"""The LLM-driven half of the inspection agent: a ReAct loop whose tools are the inspection steps
themselves - verify the image, look up a golden reference, validate an attached inspection XML,
align/quality-check against the golden image, run the two-stage classification through the
inference service, and list which models are live.

What the LLM does and does not control:

- It chooses which step to take next and reads each result (a ReAct loop), then writes a short
  plain-language summary for the QA engineer. It never sees image bytes or ids - the tools close
  over the run's state, the same way streaming.py injects uploads for every other tool.
- Every step it asks for is checked by the same PolicyEngine the deterministic planner uses, so an
  out-of-order call (classifying before the image is verified, running a defect model on an
  uncertain region) is refused with the reason instead of executing.
- It does NOT decide the verdict or persist anything. After this pass, graph.py's deterministic
  planner resumes from whatever state the LLM left behind: it runs any step the LLM skipped,
  applies the confidence/measurement/alignment rules to reach ACCEPTED or REVIEW_REQUIRED, and
  persists the Case. So a confused or failed LLM run can only cost time - the outcome is always one
  the deterministic pipeline would have reached, and with no LLM at all it simply runs unassisted.
"""

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool, StructuredTool
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.agents.inspection_agent import graph as steps
from app.chat.agents.inspection_agent.policy_engine import PolicyEngine
from app.chat.agents.inspection_agent.workflow_state import OrchestratorState
from app.shared import inference
from app.shared.config.langfuse import get_langfuse_callbacks
from app.shared.config.settings import settings

logger = logging.getLogger(__name__)

# At most this many tool calls in one pass. The longest legitimate path is six steps, so this is
# headroom for a retry or two, not a budget to tune; langgraph counts model turns as well as tool
# calls against recursion_limit, hence the factor of two.
_MAX_TOOL_CALLS = 10
_RECURSION_LIMIT = _MAX_TOOL_CALLS * 2 + 1
_LLM_TIMEOUT_SECONDS = 30

_SYSTEM_PROMPT = """You are the inspection agent for a PCB (printed circuit board) AOI defect \
inspection system. A QA engineer has attached an image of a flagged component and wants to know \
what defect it has.

Investigate it with your tools. A typical run is: verify_image, lookup_golden_image, then \
validate_measurements (only if an inspection XML is attached) and align_and_check_quality (only if a \
golden reference was found), then classify_region, and finally classify_defect. The tools enforce \
the order and will tell you if a step isn't allowed yet - read what they return. If a tool reports \
an error, stop investigating; do not retry it.

You do NOT decide the final verdict and you do not save anything - that happens automatically \
after you finish, using fixed confidence and measurement rules. When you are done, reply with a \
short plain-language summary (2-4 sentences) of what the tools found: the region, the defect and \
how confident the classifier was, and anything that looks off (low confidence, failed measurement \
validation, poor alignment). Report only what the tools returned; never invent a result."""


class _NoArguments(BaseModel):
    """None of the inspection tools take arguments - the image and context are the run's."""


def build_chat_model() -> BaseChatModel:
    """The agent's LLM, through the LiteLLM gateway like every other OpenAI-compatible call in
    the app. A seam for tests: they swap this for a scripted model."""

    return ChatOpenAI(
        model=settings.openai_model,
        api_key=SecretStr(settings.openai_api_key),
        base_url=settings.openai_base_url,
        temperature=0,
        timeout=_LLM_TIMEOUT_SECONDS,
    )


def _tool(
    name: str, description: str, run: Callable[[], Awaitable[str]]
) -> StructuredTool:
    return StructuredTool.from_function(
        coroutine=run, name=name, description=description, args_schema=_NoArguments
    )


def build_tools(session: AsyncSession, state: OrchestratorState) -> list[BaseTool]:
    """One tool per inspection step, all mutating `state` in place through the very same node
    functions the deterministic graph runs."""

    policy = PolicyEngine()

    async def step(action: str, execute: Callable[[], Awaitable[Any]]) -> str:
        allowed, reason = policy.validate_action(action, state)
        if not allowed:
            return json.dumps({"error": f"{action} is not allowed right now: {reason}"})

        seen = len(state["observations"])
        await execute()
        result: dict[str, Any] = {"result": state["observations"][seen:]}
        if state.get("error"):
            result["error"] = state["error"]
        return json.dumps(result)

    async def verify_image() -> str:
        return await step("verify_image", _sync(steps.verify_image_node))

    async def lookup_golden_image() -> str:
        return await step(
            "lookup_golden_image", lambda: steps.lookup_golden_image_node(session, state)
        )

    async def validate_measurements() -> str:
        return await step("validate_measurements", _sync(steps.validate_measurements_node))

    async def align_and_check_quality() -> str:
        return await step(
            "align_and_check_quality", lambda: steps.align_and_check_quality_node(session, state)
        )

    async def classify_region() -> str:
        return await step("classify_region", lambda: steps.classify_region_node(state))

    async def classify_defect() -> str:
        return await step("classify_defect", lambda: steps.classify_defect_node(state))

    async def list_models() -> str:
        try:
            models = await inference.list_models()
        except inference.InferenceError as exc:
            return json.dumps({"error": f"could not list models: {exc}"})
        except inference.InferenceNotConfigured as exc:
            return json.dumps({"error": str(exc)})
        return json.dumps(
            {
                "models": [
                    {"name": m.name, "version": m.version, "labels": m.labels} for m in models
                ]
            }
        )

    def _sync(node: Callable[[OrchestratorState], Any]) -> Callable[[], Awaitable[Any]]:
        async def run() -> Any:
            return node(state)

        return run

    return [
        _tool("verify_image", "Check that the attached file decodes as an image.", verify_image),
        _tool(
            "lookup_golden_image",
            "Look for a golden (known-good) reference image for this board/component/package/feature.",
            lookup_golden_image,
        ),
        _tool(
            "validate_measurements",
            "Validate the measurements in the attached inspection XML. Only when an XML is attached.",
            validate_measurements,
        ),
        _tool(
            "align_and_check_quality",
            "Compare the image against the golden reference for alignment and quality. Only when "
            "a golden reference was found.",
            align_and_check_quality,
        ),
        _tool(
            "classify_region",
            "Stage 1: classify which component region (Body, Lead or Text) the image shows.",
            classify_region,
        ),
        _tool(
            "classify_defect",
            "Stage 2: classify the defect using the model for the region found in stage 1. Only "
            "after a confident classify_region.",
            classify_defect,
        ),
        _tool(
            "list_models",
            "List the classification models currently live and their versions.",
            list_models,
        ),
    ]


def _briefing(state: OrchestratorState, question: str | None) -> str:
    known = {
        "board_id": state["board_id"],
        "component_ref": state["component_ref"],
        "package": state["package"],
        "feature": state["feature"],
        "reported symptom": state["issue_symptom"],
    }
    lines = [f"- {key}: {value}" for key, value in known.items() if value and value != "unknown"]
    xml = "attached" if state["inspection_xml_bytes"] is not None else "not attached"
    return (
        "Inspect the attached image.\n"
        f"Inspection XML: {xml}.\n"
        + ("Known context:\n" + "\n".join(lines) + "\n" if lines else "")
        + (f"The engineer asked: {question}\n" if question else "")
    )


async def run_react_pass(
    session: AsyncSession, state: OrchestratorState, *, question: str | None = None
) -> str | None:
    """Runs the LLM-driven pass, mutating `state` through its tool calls, and returns the agent's
    closing summary (None if it produced none). Never raises: any failure - no model, an upstream
    error, the tool-call budget exhausted - is recorded as an observation and returns None, and the
    deterministic planner then completes whatever the LLM did not."""

    try:
        agent = create_agent(
            build_chat_model(),
            tools=build_tools(session, state),
            system_prompt=_SYSTEM_PROMPT,
        )
        config: RunnableConfig = {
            "recursion_limit": _RECURSION_LIMIT,
            "callbacks": get_langfuse_callbacks(),
        }
        result = await agent.ainvoke(
            {"messages": [HumanMessage(content=_briefing(state, question))]}, config=config
        )
    except Exception as exc:  # noqa: BLE001 - the LLM pass is best-effort by design (see module docstring)
        logger.warning("LLM-driven inspection pass failed; continuing deterministically: %s", exc)
        state["observations"].append(
            "LLM-driven inspection pass failed; the remaining steps ran deterministically."
        )
        return None

    final = result["messages"][-1]
    if isinstance(final, AIMessage) and isinstance(final.content, str) and final.content.strip():
        return final.content.strip()
    return None
