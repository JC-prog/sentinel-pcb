"""The review agent's chat tools: `review_case` (propose approving or overriding an inspection that
awaits review), `confirm_review` (commit it), `cancel_review` (drop a proposal the user declined)
and `withdraw_review` (take a confirmed review back). Propose and confirm are two tools, like the
relabel agent's, so the model cannot decide and commit in one breath - confirm_review takes no
decision at all, only commits what was proposed, and service.py refuses it unless the user has had a
turn to answer.

The boundary only: read the model's arguments, call service.py, and phrase the outcome - including
what the model should say next - as the JSON it reads and the card the UI shows.
"""

from typing import Annotated, Any, Literal

from langchain_core.tools import tool

from app.chat.agents.review_agent import service
from app.chat.agents.review_agent.errors import ReviewRefused
from app.chat.agents.review_agent.service import Subject
from app.chat.agents.toolkit import ChatTool, Runtime, ToolRefused, returns_json, text
from app.chat.db.models import Case
from app.shared.config.settings import settings

_CASE_NUMBER = (
    'The case number, e.g. "CASE-000123". Omit for what the user is looking at: the image just '
    "inspected, or else the latest case in this conversation."
)
_IMAGE_NUMBER = (
    "When several images were inspected in the latest turn, which one (the number on its result). "
    "Omit for the last one inspected."
)


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
    image_number: Annotated[int | None, _IMAGE_NUMBER] = None,
) -> dict[str, Any]:
    """PROPOSES approving or overriding an inspection flagged REVIEW_REQUIRED, when the user says
    they have reviewed it. Nothing is saved: it returns a proposal. Then ask the user to confirm,
    and only after they say yes call confirm_review; if they say no, call cancel_review. Works on a
    saved case or on the image just inspected - that does not need to be a case first; confirming
    saves it as one, already reviewed."""

    ctx = runtime.context
    try:
        proposal = await service.propose(
            ctx.session,
            case_number=text(case_number) or None,
            conversation_id=ctx.conversation_id,
            user_id=ctx.user.id,
            decision=decision,
            note=text(note) or None,
            image_number=image_number,
        )
    except ReviewRefused as refusal:
        raise ToolRefused(refusal.message) from refusal

    subject = proposal.subject
    meaning = (
        "the flagged defect stands"
        if proposal.decision == "approve"
        else "it is a false positive and the flag is reversed"
    )
    saves = (
        "Nothing is saved yet."
        if subject.case is not None
        else "Nothing is saved yet. Confirming will also save this inspection as a new case."
    )
    return {
        "status": "awaiting_confirmation",
        **_describe(subject),
        "decision": proposal.decision,
        "note": proposal.note,
        "instruction": (
            f"{saves} Tell the user you would {proposal.decision} {subject.what} ({meaning}) and "
            "ask them to confirm. Only after they reply yes, call confirm_review. If they say no, "
            "call cancel_review."
        ),
    }


@tool("confirm_review")
@returns_json
async def confirm_review(
    runtime: Runtime,
    case_number: Annotated[str | None, _CASE_NUMBER] = None,
    image_number: Annotated[int | None, _IMAGE_NUMBER] = None,
) -> dict[str, Any]:
    """CREATES A RECORD: commits the review proposed earlier with review_case - the case becomes
    APPROVED or OVERRIDDEN (created first if it was the image just inspected). Call it ONLY after
    the user has replied yes to your confirmation question; it is refused otherwise, and refused if
    the proposal is too old (then propose again). It takes no decision - it commits exactly what was
    proposed."""

    ctx = runtime.context
    try:
        resolution = await service.confirm(
            ctx.session,
            case_number=text(case_number) or None,
            conversation_id=ctx.conversation_id,
            user_id=ctx.user.id,
            turn_started_at=ctx.turn_started_at,
            image_number=image_number,
        )
    except ReviewRefused as refusal:
        raise ToolRefused(refusal.message) from refusal

    return {
        "status": "reviewed",
        **_describe(Subject(case=resolution.case)),
        "case_created": resolution.created_case,
        "decision": resolution.decision,
        "case_status": resolution.case.status,
        "note": resolution.case.resolution_note,
    }


@tool("cancel_review")
@returns_json
async def cancel_review(
    runtime: Runtime,
    case_number: Annotated[str | None, _CASE_NUMBER] = None,
    image_number: Annotated[int | None, _IMAGE_NUMBER] = None,
) -> dict[str, Any]:
    """Discards a review proposed with review_case that the user does not want ("no", "cancel
    that"). Nothing was saved by the proposal and nothing is saved now; it simply can no longer be
    confirmed. Call it whenever the user declines a proposed review."""

    ctx = runtime.context
    try:
        subject, decision = await service.cancel(
            ctx.session,
            case_number=text(case_number) or None,
            conversation_id=ctx.conversation_id,
            user_id=ctx.user.id,
            image_number=image_number,
        )
    except ReviewRefused as refusal:
        raise ToolRefused(refusal.message) from refusal

    return {
        "status": "cancelled",
        "target": subject.what,
        "cancelled_decision": decision,
        "note": f"The proposed {decision} of {subject.what} was discarded. Nothing was saved.",
    }


@tool("withdraw_review")
@returns_json
async def withdraw_review(
    runtime: Runtime,
    case_number: Annotated[str | None, _CASE_NUMBER] = None,
) -> dict[str, Any]:
    """CHANGES A RECORD: takes back a review the user confirmed - the case goes back to awaiting
    review. Only call it when the user has clearly asked to undo or withdraw their review. It works
    only on a review the user made themselves; otherwise it says why."""

    ctx = runtime.context
    try:
        case = await service.withdraw(
            ctx.session,
            case_number=text(case_number) or None,
            conversation_id=ctx.conversation_id,
            user_id=ctx.user.id,
        )
    except ReviewRefused as refusal:
        raise ToolRefused(refusal.message) from refusal

    return {
        "status": "withdrawn",
        "case_number": case.case_number,
        "case_status": case.status,
        "note": f"{case.case_number} is awaiting review again.",
    }


def _enabled() -> bool:
    return settings.review_agent_enabled


REVIEW_CASE = ChatTool(review_case, label="Review proposal", enabled=_enabled, shows_card=True)
CONFIRM_REVIEW = ChatTool(confirm_review, label="Case review", enabled=_enabled, shows_card=True)
CANCEL_REVIEW = ChatTool(cancel_review, label="Cancel review", enabled=_enabled, shows_card=True)
WITHDRAW_REVIEW = ChatTool(withdraw_review, label="Withdraw review", enabled=_enabled)


def _describe(subject: Subject) -> dict[str, Any]:
    case: Case | None = subject.case
    return {
        "case_number": case.case_number if case else None,
        "image_number": subject.draft.image_number if subject.draft else None,
        "model_label": subject.field("defect_label"),
        "model_confidence": subject.field("defect_confidence"),
    }
