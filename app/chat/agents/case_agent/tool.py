"""The case agent's chat tools: read-only lookups of the Cases chat has saved - the same data the
Cases tab shows, so "what happened to CASE-000123?" and "has U7 on Board1 been corrected before?"
are answerable without leaving the conversation. Neither tool writes anything; a case is made and
corrected by the inspection and relabel agents (two steps, confirmed in a later turn).
"""

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from langchain_core.tools import tool

from app.chat.agents.toolkit import ChatTool, Runtime, ToolRefused, required, returns_json, text
from app.chat.services import case_views, inspection_log, run_samples
from app.shared.config.settings import settings
from app.shared.modelops import drift as drift_repo

MAX_CASES = 10

# What the model needs; the image/XML file names and the conversation are not for it to repeat.
_DETAIL_EXCLUDE = {"image_id", "inspection_xml_id", "conversation_id"}
_STATUSES = {"accepted", "review_required", "approved", "overridden"}
_MAX_DAYS = 90


@tool("get_case")
@returns_json
async def get_case(
    runtime: Runtime,
    case_number: Annotated[str, 'The case number, e.g. "CASE-000123".'],
) -> dict[str, Any]:
    """Looks up one saved case: what the model said and with what confidence, which model version,
    the scores, any correction and who made it and why, a relabel that is proposed but not yet
    confirmed, and the retraining ticket. Read-only. Use it when the user gives a case number or
    asks what happened to a case. A case number is not a sample id (S000001) - use get_sample for
    those."""

    detail = await case_views.get_case_detail(
        runtime.context.session,
        required(case_number, "case_number", 'e.g. "CASE-000123"'),
        viewer_id=runtime.context.user.id,
    )
    if detail is None:
        raise ToolRefused(f"no case {case_number.strip()!r} - case numbers look like CASE-000123")
    return detail.model_dump(mode="json", exclude=_DETAIL_EXCLUDE)


@tool("list_cases")
@returns_json
async def list_cases(
    runtime: Runtime,
    board_id: Annotated[str | None, "Only cases for this board, e.g. BOARD-1."] = None,
    component_ref: Annotated[str | None, "Only cases for this component, e.g. U7."] = None,
    model: Annotated[str | None, "Only cases the defect model answered, e.g. pcb_body_defect."] = None,
    status: Annotated[
        str | None, '"accepted", "review_required", "approved" or "overridden".'
    ] = None,
    corrected: Annotated[bool | None, "true: only corrected cases; false: only uncorrected."] = None,
    mine: Annotated[bool | None, "true: only cases the user created."] = None,
    days: Annotated[
        int | None, f"Only cases created in the last N days, 1 to {_MAX_DAYS}, e.g. 7 for 'this week'."
    ] = None,
    page: Annotated[int | None, "Which page of results, from 1, when more matched than were shown."] = None,
) -> dict[str, Any]:
    """Lists saved cases, newest first (10 per page), optionally for one board or component, one
    model, a status, corrected or not, the last N days, or just the user's own. Read-only. Use it for 'which cases do
    I have?', 'has U7 on BOARD-1 been corrected before?' or 'what did I correct today?'. It says how
    many matched in total and whether there is a next page, so tell the user when more exist than
    are shown and offer the next page."""

    wanted = text(status).lower()
    if wanted and wanted not in _STATUSES:
        raise ToolRefused(f"status must be one of {sorted(_STATUSES)}")
    listing = await case_views.list_cases(
        runtime.context.session,
        status=wanted or None,
        model=text(model) or None,
        corrected=corrected,
        board=text(board_id) or None,
        component=text(component_ref) or None,
        created_by_user_id=runtime.context.user.id if mine else None,
        since=(datetime.now(UTC) - timedelta(days=max(1, min(days, _MAX_DAYS)))) if days else None,
        limit=MAX_CASES,
        offset=(max(1, page or 1) - 1) * MAX_CASES,
    )
    shown_to = listing.offset + len(listing.cases)
    return {
        "total": listing.total,
        "shown": len(listing.cases),
        "page": listing.offset // MAX_CASES + 1,
        "next_page": listing.offset // MAX_CASES + 2 if shown_to < listing.total else None,
        "cases": [
            {
                "case_number": c.case_number,
                "status": c.status,
                "board_id": c.board_id,
                "component_ref": c.component_ref,
                "model": c.defect_model,
                "model_label": c.defect_label,
                "model_confidence": c.defect_confidence,
                "corrected_label": c.corrected_label,
                "corrected_model": c.corrected_model,
                "created_by": c.created_by,
                "created_at": c.created_at.isoformat(),
            }
            for c in listing.cases
        ],
    }


@tool("get_component_history")
@returns_json
async def get_component_history(
    runtime: Runtime,
    board_id: Annotated[str, "The board identifier, e.g. BOARD-1."],
    component_ref: Annotated[str, "The component reference designator, e.g. U7."],
) -> dict[str, Any]:
    """What chat has seen of one component on one board: how many times it was inspected, what the
    model said each time and how often a person confirmed it was right, plus the saved cases for it
    and whether any were corrected. Read-only. Use it for 'has U7 on BOARD-1 failed before?' or
    'what did we find on this part last time?'. Only inspections made in chat are covered - the
    Work tab's runs are not - and an inspection without a board and component given has no history."""

    board = required(board_id, "board_id", "e.g. BOARD-1")
    component = required(component_ref, "component_ref", "e.g. U7")
    history = await inspection_log.component_history(
        runtime.context.session, board_id=board, component_ref=component
    )
    cases = await case_views.list_cases(
        runtime.context.session, board=board, component=component, limit=MAX_CASES
    )
    return {
        "board_id": board,
        "component_ref": component,
        **history,
        "cases_total": cases.total,
        "cases": [
            {
                "case_number": c.case_number,
                "status": c.status,
                "model_label": c.defect_label,
                "corrected_label": c.corrected_label,
                "created_at": c.created_at.isoformat(),
            }
            for c in cases.cases
        ],
    }


@tool("get_activity_summary")
@returns_json
async def get_activity_summary(
    runtime: Runtime,
    days: Annotated[int | None, f"How many days back, 1 to {_MAX_DAYS}. Default 7."] = None,
) -> dict[str, Any]:
    """A short account of what happened over the last N days, for 'what happened this week?': how
    many images chat inspected (by verdict and model), how many a person confirmed right or
    corrected, cases created, drift reports opened, how many Work-tab runs were saved, and how
    replies were rated. Read-only counts - summarise them in a few lines and say the window. It
    covers chat and stored runs; model status and the retraining queue are the Admin tools'."""

    span = max(1, min(days or 7, _MAX_DAYS))
    since = datetime.now(UTC) - timedelta(days=span)
    session = runtime.context.session

    summary = await inspection_log.activity(session, since=since)
    reports = await drift_repo.list_drift_reports(session, since=since, limit=200)
    try:
        runs = await run_samples.ready_runs()
        runs_saved: int | None = sum(
            1 for r in runs if (r.saved_at_utc or "") >= since.isoformat()
        )
    except run_samples.RunStoreUnavailable:
        runs_saved = None  # the store could not be read - not the same as none
    return {
        "window_days": span,
        **summary,
        "drift_reports_opened": len(reports),
        "drift_reports_raised_automatically": sum(1 for r in reports if (r.stats or {}).get("auto")),
        "work_runs_saved": runs_saved,
    }


def _enabled() -> bool:
    return settings.case_lookup_agent_enabled


GET_CASE = ChatTool(get_case, label="Case lookup", enabled=_enabled)
LIST_CASES = ChatTool(list_cases, label="Case list", enabled=_enabled)
GET_ACTIVITY_SUMMARY = ChatTool(get_activity_summary, label="Activity summary", enabled=_enabled)
GET_COMPONENT_HISTORY = ChatTool(
    get_component_history, label="Component history", enabled=_enabled
)
