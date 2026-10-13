"""The case agent's chat tools: read-only lookups of the Cases chat has saved - the same data the
Cases tab shows, so "what happened to CASE-000123?" and "has U7 on Board1 been corrected before?"
are answerable without leaving the conversation. Neither tool writes anything; a case is made and
corrected by the inspection and relabel agents (two steps, confirmed in a later turn).
"""

from typing import Annotated, Any

from langchain_core.tools import tool

from app.chat.agents.toolkit import ChatTool, Runtime, ToolRefused, required, returns_json, text
from app.chat.services import case_views
from app.shared.config.settings import settings

MAX_CASES = 10

# What the model needs; the image/XML file names and the conversation are not for it to repeat.
_DETAIL_EXCLUDE = {"image_id", "inspection_xml_id", "conversation_id"}
_STATUSES = {"accepted", "review_required", "approved", "overridden"}


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
) -> dict[str, Any]:
    """Lists saved cases, newest first (at most 10), optionally for one board or component, one
    model, a status, corrected or not, or just the user's own. Read-only. Use it for 'which cases do
    I have?', 'has U7 on BOARD-1 been corrected before?' or 'what did I correct today?'. It says how
    many matched in total, so tell the user when more exist than are shown."""

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
        limit=MAX_CASES,
    )
    return {
        "total": listing.total,
        "shown": len(listing.cases),
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
                "created_by": c.created_by,
                "created_at": c.created_at.isoformat(),
            }
            for c in listing.cases
        ],
    }


def _enabled() -> bool:
    return settings.case_lookup_agent_enabled


GET_CASE = ChatTool(get_case, label="Case lookup", enabled=_enabled)
LIST_CASES = ChatTool(list_cases, label="Case list", enabled=_enabled)
