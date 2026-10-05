"""The monitoring agent's chat tools - the boundary only: read the model's arguments, call
service.py, and phrase the result (and what the model should suggest next) as the JSON it reads.
None needs LLM reasoning of its own; the chat model orchestrates them.
"""

from typing import Annotated, Any

from langchain_core.tools import tool

from app.chat.agents.monitoring_agent import service
from app.chat.agents.toolkit import ChatTool, Runtime, ToolRefused, required, returns_json, text
from app.shared.config.settings import settings

_MAX_WINDOW_DAYS = 90

_MODEL = "The model's name, e.g. pcb_region, pcb_body_defect, pcb_lead_defect or pcb_text_defect."
_WINDOW = (
    "Days in the recent window, compared against the same number of days before it. "
    f"Default {service.DEFAULT_WINDOW_DAYS}."
)


def _window_days(window_days: int | None) -> int:
    return max(1, min(window_days or service.DEFAULT_WINDOW_DAYS, _MAX_WINDOW_DAYS))


@tool("get_drift_summary")
@returns_json
async def get_drift_summary(
    runtime: Runtime,
    model: Annotated[str, _MODEL],
    window_days: Annotated[int | None, _WINDOW] = None,
) -> dict[str, Any]:
    """Shows whether a model looks like it is drifting: its recent review rate, the share of its
    calls reviewers overrode or corrected, and its classifier confidence, compared with the period
    before and broken down by model version - with plain-language signals for whatever moved.
    Read-only, and the first thing to call for any 'is the model getting worse?' question - use it
    before reporting drift or planning a retrain."""

    return await service.drift_overview(
        runtime.context.session, required(model, "model"), days=_window_days(window_days)
    )


@tool("report_model_drift")
@returns_json
async def report_model_drift(
    runtime: Runtime,
    model: Annotated[str, _MODEL],
    description: Annotated[str, "What the user is seeing that suggests the model drifted."],
    case_numbers: Annotated[
        list[str] | None, "Case numbers that show the problem, if the user named any."
    ] = None,
    window_days: Annotated[int | None, _WINDOW] = None,
) -> dict[str, Any]:
    """CREATES A RECORD: files a drift report because the user believes a model has drifted (its
    verdicts have become less reliable). Only call it when the user wants that reported - check
    get_drift_summary first. Records their description, the cases they point to as evidence, and a
    snapshot of the model's current drift numbers, so the Models tab and engineers can see it. This
    only records the report - it does not retrain anything."""

    model_name, summary_text = text(model), text(description)
    if not model_name or not summary_text:
        raise ToolRefused("both model and description are required")

    filed = await service.file_drift_report(
        runtime.context.session,
        model=model_name,
        description=summary_text,
        case_numbers=[str(n) for n in case_numbers or []],
        days=_window_days(window_days),
        user_id=runtime.context.user.id,
    )
    return {
        "report_id": filed.report.id,
        "model": model_name,
        "model_version": filed.report.model_version,
        "evidence_cases": filed.evidence_cases,
        "unknown_case_numbers": filed.unknown_case_numbers,
        "signals": filed.summary["signals"],
        "enough_data": filed.summary["enough_data"],
        "open_drift_reports_for_model": filed.open_reports_for_model,
        "next_step": (
            "To retrain, correct the cases the model got wrong (relabel_case, then "
            "confirm_relabel) and ask for a retraining plan."
        ),
    }


@tool("draft_retraining_plan")
@returns_json
async def draft_retraining_plan(
    runtime: Runtime,
    model: Annotated[str, _MODEL],
    rationale: Annotated[
        str | None,
        "Why retraining is warranted, in the user's words. Optional - a summary of the flagged "
        "cases and drift signals is written if omitted.",
    ] = None,
) -> dict[str, Any]:
    """CREATES A RECORD: drafts a retraining plan for a model from every open retraining ticket
    flagged against it, linking any open drift reports. The plan is queued as pending approval - an
    Admin must approve it in the Models tab before anything is sent for retraining, and promoting a
    retrained model is a separate Admin step. Fails if no cases have been corrected for that model
    yet."""

    model_name = required(model, "model")
    try:
        plan = await service.draft_plan(
            runtime.context.session,
            model=model_name,
            rationale=text(rationale),
            user_id=runtime.context.user.id,
        )
    except service.PlanRefused as refusal:
        raise ToolRefused(str(refusal)) from refusal

    return {
        "job_id": plan.job.id,
        "model": model_name,
        "status": plan.job.status,
        "base_version": plan.job.base_version,
        "flagged_cases": len(plan.job.samples),
        "drift_reports_linked": plan.drift_reports_linked,
        "next_step": (
            "An Admin must approve this plan in the Models tab before it is sent for retraining."
        ),
    }


@tool("monitoring_status")
@returns_json
async def monitoring_status(runtime: Runtime) -> dict[str, Any]:
    """Admin overview of model health: which version of each model is live, open drift reports,
    open retraining tickets, and the retraining queue. Read-only."""

    return await service.status_overview(runtime.context.session)


def _enabled() -> bool:
    # Drift reports, plans and the overview belong to the model-operations feature too.
    return settings.monitoring_agent_enabled and settings.modelops_enabled


GET_DRIFT_SUMMARY = ChatTool(get_drift_summary, label="Drift summary", enabled=_enabled)
REPORT_MODEL_DRIFT = ChatTool(report_model_drift, label="Drift report", enabled=_enabled)
DRAFT_RETRAINING_PLAN = ChatTool(draft_retraining_plan, label="Retraining plan", enabled=_enabled)
MONITORING_STATUS = ChatTool(monitoring_status, label="Model status", enabled=_enabled)
