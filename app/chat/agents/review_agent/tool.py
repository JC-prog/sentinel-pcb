"""The review agent's chat tools: `review_case` (propose approving or overriding a case that awaits
review) and `confirm_review` (commit it). Two tools, like the relabel agent's, so the model cannot
decide and commit in one breath - confirm_review takes no decision at all, only commits what was
proposed, and service.py refuses it unless the user has had a turn to answer.

The boundary only: read the model's arguments, call service.py, and phrase the outcome - including
what the model should say next - as the JSON it reads and the card the UI shows.
"""

from typing import Annotated, Any, Literal

from langchain_core.tools import tool

from app.chat.agents.review_agent import service
from app.chat.agents.review_agent.errors import ReviewRefused
from app.chat.agents.toolkit import ChatTool, Runtime, ToolRefused, returns_json, text
from app.chat.db.models import Case
from app.shared.config.settings import settings

_CASE_NUMBER = 'The case number, e.g. "CASE-000123". Omit for the latest case in this conversation.'


@tool("review_case")
@returns_json
async def review_case(
    runtime: Runtime,
    decision: Annotated[
        Literal["approve", "override"],
        "approve = the flagged defect is real; override = it is a false positive.",
    ],
    note: Annotated[str | None, "Why, in the user's words. Optional but worth asking for."] = None,
    case_number: Annotated[str | None, _CASE_NUMBER] = None,
) -> dict[str, Any]:
    """PROPOSES approving or overriding a case flagged REVIEW_REQUIRED, when the user says they
    have reviewed it. Nothing is saved: it returns a proposal. Then ask the user to confirm, and
    only after they say yes call confirm_review. Only cases awaiting review qualify. Without a case
    number it uses the latest case in this conversation."""

    ctx = runtime.context
    try:
        proposal = await service.propose(
            ctx.session,
            case_number=text(case_number) or None,
            conversation_id=ctx.conversation_id,
            user_id=ctx.user.id,
            decision=decision,
            note=text(note) or None,
        )
    except ReviewRefused as refusal:
        raise ToolRefused(refusal.message) from refusal

    meaning = (
        "the flagged defect stands"
        if proposal.decision == "approve"
        else "it is a false positive and the flag is reversed"
    )
    return {
        "status": "awaiting_confirmation",
        **_describe(proposal.case),
        "decision": proposal.decision,
        "note": proposal.note,
        "instruction": (
            f"Nothing is saved yet. Tell the user you would {proposal.decision} "
            f"{proposal.case.case_number} ({meaning}) and ask them to confirm. Only after they "
            "reply yes, call confirm_review."
        ),
    }


@tool("confirm_review")
@returns_json
async def confirm_review(
    runtime: Runtime,
    case_number: Annotated[str | None, _CASE_NUMBER] = None,
) -> dict[str, Any]:
    """CREATES A RECORD: commits the review proposed earlier with review_case - the case becomes
    APPROVED or OVERRIDDEN. Call it ONLY after the user has replied yes to your confirmation
    question; it is refused otherwise. It takes no decision - it commits exactly what was proposed.
    Without a case number it uses the latest case in this conversation."""

    ctx = runtime.context
    try:
        resolution = await service.confirm(
            ctx.session,
            case_number=text(case_number) or None,
            conversation_id=ctx.conversation_id,
            user_id=ctx.user.id,
            turn_started_at=ctx.turn_started_at,
        )
    except ReviewRefused as refusal:
        raise ToolRefused(refusal.message) from refusal

    return {
        "status": "reviewed",
        **_describe(resolution.case),
        "decision": resolution.decision,
        "case_status": resolution.case.status,
        "note": resolution.case.resolution_note,
    }


def _enabled() -> bool:
    return settings.review_agent_enabled


REVIEW_CASE = ChatTool(review_case, label="Review proposal", enabled=_enabled, shows_card=True)
CONFIRM_REVIEW = ChatTool(confirm_review, label="Case review", enabled=_enabled, shows_card=True)


def _describe(case: Case) -> dict[str, Any]:
    return {
        "case_number": case.case_number,
        "model_label": case.defect_label,
        "model_confidence": case.defect_confidence,
    }
