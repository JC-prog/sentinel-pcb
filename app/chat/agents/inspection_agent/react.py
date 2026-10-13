"""The LLM-driven half of the inspect agent: a ReAct agent built with LangChain's `create_agent` (a
LangGraph graph underneath) over the tools in toolbox.py. The model comes from
app/shared/config/llm.py, so changing model or provider is a setting, and every run is traced in
Langfuse when that is on.

What the LLM does and does not control:

- It chooses which step to take next and reads each result, then writes a short plain-language
  summary for the QA engineer. It never sees image bytes or ids - the tools close over the run.
- Every step it asks for is checked by policy.py, the same rules the deterministic pipeline uses,
  so an out-of-order call is refused with the reason instead of executing.
- It does NOT decide the verdict or persist anything. After this pass, pipeline.py runs any step
  the LLM skipped and applies the fixed rules in verdict.py, so a confused or failed LLM run can
  only cost time - the outcome is always one the deterministic pipeline would have reached.
"""

import logging

from langchain.agents import create_agent
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.errors import GraphRecursionError

from app.chat.agents.inspection_agent.state import InspectionRun
from app.chat.agents.inspection_agent.toolbox import ToolBox, build_tools
from app.shared.config.langfuse import langfuse_callbacks, traced
from app.shared.config.llm import build_chat_model

logger = logging.getLogger(__name__)

# At most this many tool calls in one pass. The longest legitimate path is four steps, so this is
# headroom for read-only lookups and a retry, not a budget to tune. LangGraph counts model turns as
# well as tool calls against its recursion limit, hence the factor of two.
MAX_TOOL_CALLS = 10
_RECURSION_LIMIT = MAX_TOOL_CALLS * 2 + 1

SYSTEM_PROMPT = """You are the inspection agent for a PCB (printed circuit board) AOI defect \
inspection system. A QA engineer has attached an image of a flagged component and wants to know \
what defect it has.

Investigate it with your tools. A typical run is: verify_image, then validate_measurements (only if \
an inspection XML is attached), then classify_region, then classify_defect. The tools enforce the \
order and will tell you if a step is not allowed yet - read what they return. If a tool reports an \
error, stop investigating; do not retry it. Once the classifiers have run you may call get_scores \
to see the runner-up labels, and check_image_quality at any point to see whether the image itself \
is blurry, dark or low-contrast; both are read-only.

You do NOT decide the final verdict and you do not save anything - that happens automatically after \
you finish, using fixed confidence and measurement rules. When you are done, reply with a short \
plain-language summary (2-4 sentences) of what the tools found: the region, the defect and how \
confident the classifier was, and anything that looks off (low confidence, failed measurement \
validation, a blurry image). Report only what the tools returned; never invent a result."""


def _briefing(run: InspectionRun, question: str | None) -> str:
    request = run.request
    known = {
        "board_id": request.board_id,
        "component_ref": request.component_ref,
        "package": request.package,
        "feature": request.feature,
        "reported symptom": request.issue_symptom,
    }
    lines = [f"- {key}: {value}" for key, value in known.items() if value and value != "unknown"]
    xml = "attached" if request.inspection_xml_bytes is not None else "not attached"
    return (
        "Inspect the attached image.\n"
        f"Inspection XML: {xml}.\n"
        + ("Known context:\n" + "\n".join(lines) + "\n" if lines else "")
        + (f"The engineer asked: {question}\n" if question else "")
    )


@traced("inspect-react")
async def run_react_pass(run: InspectionRun, *, question: str | None = None) -> str | None:
    """Runs the LLM-driven pass, mutating `run` through its tool calls, and returns the agent's
    closing summary (None if it produced none). Never raises: any failure - no model, an upstream
    error, the tool-call budget exhausted - is recorded as an observation and returns None, and the
    deterministic pipeline then completes whatever the LLM did not."""

    try:
        agent = create_agent(
            build_chat_model(), tools=build_tools(ToolBox(run)), system_prompt=SYSTEM_PROMPT
        )
        config: RunnableConfig = {
            "recursion_limit": _RECURSION_LIMIT,
            "callbacks": langfuse_callbacks(),
        }
        result = await agent.ainvoke(
            {"messages": [HumanMessage(content=_briefing(run, question))]}, config=config
        )
    except GraphRecursionError:
        run.note("LLM-driven inspection stopped: tool-call budget exhausted.")
        return None
    except Exception as exc:  # noqa: BLE001 - the LLM pass is best-effort by design (see docstring)
        logger.warning("LLM-driven inspection pass failed; continuing deterministically: %s", exc)
        run.note("LLM-driven inspection pass failed; the remaining steps ran deterministically.")
        return None

    if run.error:
        return None  # a failed step ends the investigation - the pipeline reports the error
    final = result["messages"][-1]
    if not isinstance(final, AIMessage):
        return None
    return str(final.text).strip() or None
