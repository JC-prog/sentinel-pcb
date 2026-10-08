"""The sample agent's chat tools: read-only lookups of what the Work tab stored about a dataset
sample (`S000001`) - the boundary only: read the model's arguments, call service.py, and phrase the
result as the JSON the model reads. Neither tool writes anything, and neither creates a Case: a Case
comes from an image the user attached in chat (the inspection agent), a sample from a Work-tab run.
"""

from typing import Annotated, Any

from langchain_core.tools import tool

from app.chat.agents.sample_agent import service
from app.chat.agents.sample_agent.errors import SampleRefused
from app.chat.agents.toolkit import ChatTool, Runtime, ToolRefused, required, returns_json, text
from app.shared.config.settings import settings

_RUN_ID = (
    "The Work-tab run id, only if the user gave one. Omit it to use the latest run that has the sample."
)


@tool("get_sample")
@returns_json
async def get_sample(
    runtime: Runtime,
    sample_id: Annotated[str, 'The dataset sample id the user gave, e.g. "S000001".'],
    run_id: Annotated[str | None, _RUN_ID] = None,
) -> dict[str, Any]:
    """Looks up a sample from a Work-tab bulk run by its sample id and reports what was stored:
    the board and component, the machine's call and failed measurements, Agent 1's verdict and
    confidences, Agent 2's review and explanation, and the operator's final decision if there is
    one. Read-only. Use it whenever the user gives a sample id like S000001 - a sample id is not a
    case number (CASE-xxxxxx). If the sample appears in several runs it answers from the latest and
    lists the others in other_runs - tell the user and offer to look at another run."""

    try:
        return await service.describe_sample(
            required(sample_id, "sample_id", 'e.g. "S000001"'), text(run_id) or None
        )
    except SampleRefused as refusal:
        raise ToolRefused(refusal.message) from refusal


@tool("list_review_cases")
@returns_json
async def list_review_cases(
    runtime: Runtime,
    run_id: Annotated[str | None, _RUN_ID] = None,
    limit: Annotated[int | None, f"How many to list, at most {service.MAX_CASES}."] = None,
) -> dict[str, Any]:
    """Lists the samples of a Work-tab run that were flagged for review (REVIEW_REQUIRED), with
    Agent 1's and Agent 2's calls and whether the operator has decided each - for 'what is waiting
    for review?' questions. Read-only. Uses the latest run unless a run id is given."""

    try:
        return await service.review_cases(text(run_id) or None, limit or service.MAX_CASES)
    except SampleRefused as refusal:
        raise ToolRefused(refusal.message) from refusal


@tool("get_run_drift")
@returns_json
async def get_run_drift(
    runtime: Runtime,
    run_id: Annotated[str | None, _RUN_ID] = None,
) -> dict[str, Any]:
    """Shows how the models did in a Work-tab bulk run, per model: how many samples were reviewed,
    how often the operator corrected the model's label in the Review Console, how often Agent 2
    disagreed with it, and its confidence - plus the list of operator corrections and which already
    have a retraining ticket. Read-only. Use it for 'how is the model doing in the last run?' and
    'what did the operator correct?' (not get_drift_summary, which measures saved cases). Uses the
    latest run unless a run id is given."""

    try:
        return await service.run_drift_overview(runtime.context.session, text(run_id) or None)
    except SampleRefused as refusal:
        raise ToolRefused(refusal.message) from refusal


def _enabled() -> bool:
    return settings.sample_lookup_agent_enabled


GET_SAMPLE = ChatTool(get_sample, label="Sample lookup", enabled=_enabled)
LIST_REVIEW_CASES = ChatTool(list_review_cases, label="Review queue", enabled=_enabled)
GET_RUN_DRIFT = ChatTool(get_run_drift, label="Run drift", enabled=_enabled)
