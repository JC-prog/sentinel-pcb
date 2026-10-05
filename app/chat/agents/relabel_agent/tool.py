"""The relabel agent's chat tools: `relabel_case` (propose a correction) and `confirm_relabel`
(commit it). Two tools rather than one flag so the model cannot pass "confirmed=true" on the first
call - confirm_relabel takes no label at all, only commits what was proposed, and service.py refuses
it unless the user has had a turn to answer.

These are the boundary only: read the model's arguments, call service.py, and phrase the outcome -
including what the model should say next - as the JSON it reads and the card the UI shows.
"""

from typing import Annotated, Any

from langchain_core.tools import tool

from app.chat.agents.relabel_agent import service
from app.chat.agents.relabel_agent.errors import RelabelRefused
from app.chat.agents.toolkit import ChatTool, Runtime, ToolRefused, required, returns_json, text
from app.chat.db.models import Case
from app.shared.config.settings import settings

_CASE_NUMBER = 'The case number, e.g. "CASE-000123". Omit for the latest case in this conversation.'


@tool("relabel_case")
@returns_json
async def relabel_case(
    runtime: Runtime,
    correct_label: Annotated[str, "What the defect label should have been, as the user said it."],
    reason: Annotated[str, "Why the model's label is wrong, in the user's words. Required."],
    case_number: Annotated[str | None, _CASE_NUMBER] = None,
) -> dict[str, Any]:
    """PROPOSES a correction when the user says the model's defect label on a Case is wrong and
    tells you the right one. Nothing is saved: it validates the label against the model's real
    classes and returns a proposal. Then ask the user to confirm, and only after they say yes call
    confirm_relabel. Needs the correct label and the user's reason. Without a case number it uses
    the latest case in this conversation."""

    ctx = runtime.context
    try:
        proposal = await service.propose(
            ctx.session,
            case_number=text(case_number) or None,
            conversation_id=ctx.conversation_id,
            user_id=ctx.user.id,
            label=required(correct_label, "correct_label"),
            reason=required(reason, "reason", "ask the user why the label is wrong"),
        )
    except RelabelRefused as refusal:
        raise ToolRefused(refusal.message, **refusal.details) from refusal

    case = proposal.case
    return {
        "status": "awaiting_confirmation",
        **_describe(case),
        "proposed_label": proposal.label,
        "reason": proposal.reason,
        "instruction": (
            f"Nothing is saved yet. Tell the user the model said {case.defect_label!r} and you "
            f"would record {proposal.label!r} instead, queuing it for retraining, and ask them to "
            "confirm. Only after they reply yes, call confirm_relabel."
        ),
    }


@tool("confirm_relabel")
@returns_json
async def confirm_relabel(
    runtime: Runtime,
    case_number: Annotated[str | None, _CASE_NUMBER] = None,
) -> dict[str, Any]:
    """CREATES A RECORD: commits the relabel proposed earlier with relabel_case - corrects the
    Case's label and queues a retraining ticket for it. Call it ONLY after the user has replied yes
    to your confirmation question; it is refused otherwise. It takes no label - it commits exactly
    what was proposed. Without a case number it uses the latest case in this conversation."""

    ctx = runtime.context
    try:
        correction = await service.confirm(
            ctx.session,
            case_number=text(case_number) or None,
            conversation_id=ctx.conversation_id,
            user_id=ctx.user.id,
            turn_started_at=ctx.turn_started_at,
        )
    except RelabelRefused as refusal:
        raise ToolRefused(refusal.message, **refusal.details) from refusal

    return {
        "status": "relabelled",
        **_describe(correction.case),
        "corrected_label": correction.label,
        "ticket_id": correction.ticket.id,
        "ticket_status": correction.ticket.status,
        "note": (
            "Recorded and queued for retraining. An Admin approves the retraining in the Models tab."
        ),
    }


def _enabled() -> bool:
    return settings.relabel_agent_enabled


RELABEL_CASE = ChatTool(relabel_case, label="Relabel proposal", enabled=_enabled, shows_card=True)
CONFIRM_RELABEL = ChatTool(confirm_relabel, label="Relabel", enabled=_enabled, shows_card=True)


def _describe(case: Case) -> dict[str, Any]:
    return {
        "case_number": case.case_number,
        "model": case.defect_model,
        "model_version": case.defect_model_version,
        "model_label": case.defect_label,
        "model_confidence": case.defect_confidence,
    }
