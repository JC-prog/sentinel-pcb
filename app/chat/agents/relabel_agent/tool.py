"""The relabel agent's chat tools: `relabel_case` (propose a correction) and `confirm_relabel`
(commit it). Two tools rather than one flag so the model cannot pass "confirmed=true" on the first
call - confirm_relabel takes no label at all, only commits what was proposed, and service.py refuses
it unless the user has had a turn to answer.

These are the boundary only: read the model's arguments, call service.py, and phrase the outcome -
including what the model should say next - as the JSON it reads and the card the UI shows.
"""

from typing import Annotated, Any, Literal

from langchain_core.tools import tool

from app.chat.agents.relabel_agent import service
from app.chat.agents.relabel_agent.errors import RelabelRefused
from app.chat.agents.relabel_agent.resolver import Stage, Target, stage_of
from app.chat.agents.toolkit import ChatTool, Runtime, ToolRefused, required, returns_json, text
from app.shared.config.settings import settings

_CASE_NUMBER = 'The case number, e.g. "CASE-000123". Omit for the latest case in this conversation.'
_IMAGE_NUMBER = (
    "When several images were inspected in the latest turn, which one (the number on its result). "
    "Omit for the last one inspected."
)


@tool("relabel_case")
@returns_json
async def relabel_case(
    runtime: Runtime,
    correct_label: Annotated[str, "What the label should have been, as the user said it."],
    reason: Annotated[str, "Why the model's label is wrong, in the user's words. Required."],
    case_number: Annotated[str | None, _CASE_NUMBER] = None,
    stage: Annotated[
        Literal["defect", "region"],
        "defect (default) = the defect label is wrong; region = the region (Body, Lead, Text) is "
        "wrong. Use region when the user says the part of the component is wrong, or when no defect "
        "model ran because the region was uncertain.",
    ] = "defect",
    image_number: Annotated[int | None, _IMAGE_NUMBER] = None,
) -> dict[str, Any]:
    """PROPOSES a correction when the user says one of the model's labels is wrong and tells you
    the right one: the defect label (default), or the region with stage='region'. Nothing is saved:
    it validates the label against that model's real classes and returns a proposal. Then ask the
    user to confirm, and only after they say yes call confirm_relabel; if they say no, call
    cancel_relabel. Needs the correct label and the user's reason. Without a case number it uses
    what the user is looking at: the image just inspected, or else the latest case in this
    conversation. The inspection does not need to be a case first - confirming creates it."""

    ctx = runtime.context
    try:
        proposal = await service.propose(
            ctx.session,
            case_number=text(case_number) or None,
            conversation_id=ctx.conversation_id,
            user_id=ctx.user.id,
            label=required(correct_label, "correct_label"),
            reason=required(reason, "reason", "ask the user why the label is wrong"),
            stage=stage,
            image_number=image_number,
        )
    except RelabelRefused as refusal:
        raise ToolRefused(refusal.message, **refusal.details) from refusal

    target = proposal.target
    saves = (
        "Nothing is saved yet."
        if target.case is not None
        else "Nothing is saved yet. Confirming will also save this inspection as a new case."
    )
    what = "region" if proposal.stage == "region" else "defect label"
    return {
        "status": "awaiting_confirmation",
        **_describe(target, proposal.stage),
        "proposed_label": proposal.label,
        "reason": proposal.reason,
        "instruction": (
            f"{saves} Tell the user the model said the {what} was {proposal.model_label!r} and you "
            f"would record {proposal.label!r} instead, queuing it for retraining, and ask them to "
            "confirm. Only after they reply yes, call confirm_relabel. If they say no, call "
            "cancel_relabel."
        ),
    }


@tool("confirm_relabel")
@returns_json
async def confirm_relabel(
    runtime: Runtime,
    case_number: Annotated[str | None, _CASE_NUMBER] = None,
    image_number: Annotated[int | None, _IMAGE_NUMBER] = None,
) -> dict[str, Any]:
    """CREATES A RECORD: commits the relabel proposed earlier with relabel_case - corrects the
    label and queues a retraining ticket for that model. Call it ONLY after the user has replied
    yes to your confirmation question; it is refused otherwise, and refused if the proposal is too
    old (then propose again). It takes no label - it commits exactly what was proposed. If the
    proposal was on an inspection that is not a case yet, this also creates the case. Without a
    case number it uses the proposal waiting in this conversation."""

    ctx = runtime.context
    try:
        correction = await service.confirm(
            ctx.session,
            case_number=text(case_number) or None,
            conversation_id=ctx.conversation_id,
            user_id=ctx.user.id,
            turn_started_at=ctx.turn_started_at,
            image_number=image_number,
        )
    except RelabelRefused as refusal:
        raise ToolRefused(refusal.message, **refusal.details) from refusal

    return {
        "status": "relabelled",
        **_describe(Target(case=correction.case), stage_of(correction.model)),
        "case_created": correction.created_case,
        "corrected_label": correction.label,
        "ticket_id": correction.ticket.id,
        "ticket_status": correction.ticket.status,
        "note": (
            "Recorded and queued for retraining. An Admin approves the retraining in the Models tab."
        ),
    }


@tool("cancel_relabel")
@returns_json
async def cancel_relabel(
    runtime: Runtime,
    case_number: Annotated[str | None, _CASE_NUMBER] = None,
    image_number: Annotated[int | None, _IMAGE_NUMBER] = None,
) -> dict[str, Any]:
    """Discards a relabel proposed with relabel_case that the user does not want ("no", "cancel
    that", "keep the model's label"). Nothing was saved by the proposal and nothing is saved now;
    the proposal simply can no longer be confirmed. Call it whenever the user declines a proposed
    relabel."""

    ctx = runtime.context
    try:
        cancelled = await service.cancel(
            ctx.session,
            case_number=text(case_number) or None,
            conversation_id=ctx.conversation_id,
            user_id=ctx.user.id,
            image_number=image_number,
        )
    except RelabelRefused as refusal:
        raise ToolRefused(refusal.message, **refusal.details) from refusal

    return {
        "status": "cancelled",
        "target": cancelled.target.what,
        "cancelled_label": cancelled.label,
        "note": f"The proposed relabel to {cancelled.label!r} was discarded. Nothing was saved.",
    }


@tool("list_my_corrections")
@returns_json
async def list_my_corrections(runtime: Runtime) -> dict[str, Any]:
    """Lists the corrections the user has confirmed (newest 10): what the model said, what it was
    corrected to, and where each stands - waiting for a retraining plan, in a plan, or retrained -
    and whether it can still be withdrawn. Read-only. Use it for 'what did I correct?' and 'what
    happened to my corrections?'. A retrained model may be simulated: say so if the plan status
    suggests training has run, since the trainer is a stub today."""

    corrections = await service.own_corrections(runtime.context.session, user_id=runtime.context.user.id)
    return {"total": len(corrections), "corrections": corrections}


@tool("withdraw_correction")
@returns_json
async def withdraw_correction(
    runtime: Runtime,
    case_number: Annotated[str | None, _CASE_NUMBER] = None,
) -> dict[str, Any]:
    """CHANGES A RECORD: takes back a correction the user confirmed - the case goes back to what
    the model said and its retraining ticket is removed. Only call it when the user has clearly asked
    to withdraw or undo a correction. It works only on the user's own correction, and only while it
    is not in a retraining plan; otherwise it says why. Without a case number it takes back the
    most recent correction that still can be."""

    ctx = runtime.context
    try:
        case = await service.withdraw(
            ctx.session,
            case_number=text(case_number) or None,
            conversation_id=ctx.conversation_id,
            user_id=ctx.user.id,
        )
    except RelabelRefused as refusal:
        raise ToolRefused(refusal.message, **refusal.details) from refusal

    return {
        "status": "withdrawn",
        "case_number": case.case_number,
        "model_label": case.defect_label,
        "note": (
            f"{case.case_number} is back to what the model said and its retraining ticket was "
            "removed."
        ),
    }


def _enabled() -> bool:
    return settings.relabel_agent_enabled


RELABEL_CASE = ChatTool(relabel_case, label="Relabel proposal", enabled=_enabled, shows_card=True)
CONFIRM_RELABEL = ChatTool(confirm_relabel, label="Relabel", enabled=_enabled, shows_card=True)
LIST_MY_CORRECTIONS = ChatTool(list_my_corrections, label="My corrections", enabled=_enabled)
WITHDRAW_CORRECTION = ChatTool(withdraw_correction, label="Withdraw correction", enabled=_enabled)
CANCEL_RELABEL = ChatTool(cancel_relabel, label="Cancel relabel", enabled=_enabled, shows_card=True)


def _describe(target: Target, stage: Stage = "defect") -> dict[str, Any]:
    """What the corrected model said: the defect model's call, or the region model's."""

    label, version, confidence = target.observed(stage)
    return {
        "case_number": target.case_number,
        "image_number": target.image_number,
        "stage": stage,
        "model": target.model_for(stage),
        "model_version": version,
        "model_label": label,
        "model_confidence": confidence,
    }
