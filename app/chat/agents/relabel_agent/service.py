"""The relabel agent's use cases and their rules: propose a correction, confirm it, cancel it, and
the user's own corrections (list, withdraw).

A correction applies either to a saved Case or to an inspection the user has just run, which is not
a Case yet: confirming a relabel on that inspection is the first human judgment of it, and is what
creates the Case (see recorder.apply_draft_proposal). It corrects one model's label: the defect
model's, or the region model's when the region itself was wrong.

The confirm step is enforced here, not left to the model's good manners - see
app/chat/services/confirmation.py: a proposal can only be committed in a LATER chat turn than the
one that proposed it, only by the user who proposed it, and only while it is fresh.
"""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.agents.relabel_agent import labels, recorder, resolver
from app.chat.agents.relabel_agent.errors import RelabelRefused
from app.chat.agents.relabel_agent.resolver import Stage, Target
from app.chat.db.models import Case
from app.chat.services.cases import resolve_case_ref
from app.chat.services.confirmation import (
    ConfirmationProblem,
    confirmation_problem,
    proposal_ttl,
)
from app.shared.config.langfuse import traced
from app.shared.db.models import RetrainingTicket, RetrainingTicketStatus
from app.shared.modelops import tickets as ticket_repo

_REFUSALS = {
    ConfirmationProblem.NOTHING_PENDING: (
        "there is no relabel waiting for confirmation on {what} - propose one with relabel_case first."
    ),
    ConfirmationProblem.PROPOSED_BY_SOMEONE_ELSE: (
        "that relabel was proposed by someone else; propose your own."
    ),
    ConfirmationProblem.SAME_TURN: (
        "the user has not confirmed yet - a relabel must be proposed in one message and confirmed by "
        "the user in a later one. Ask them, and wait for their answer."
    ),
    ConfirmationProblem.EXPIRED: (
        "the relabel proposed on {what} is too old to confirm now - nothing was saved. If the user "
        "still wants it, propose it again with relabel_case and ask them to confirm."
    ),
}


@dataclass(frozen=True)
class Proposal:
    target: Target
    stage: Stage
    model: str
    model_label: str
    label: str
    reason: str


@dataclass(frozen=True)
class Correction:
    case: Case
    model: str
    label: str
    ticket: RetrainingTicket
    created_case: bool


@dataclass(frozen=True)
class Cancelled:
    target: Target
    label: str


@traced("relabel-propose")
async def propose(
    session: AsyncSession,
    *,
    case_number: str | None,
    conversation_id: str,
    user_id: str,
    label: str,
    reason: str,
    stage: Stage = "defect",
    image_number: int | None = None,
) -> Proposal:
    target = await resolver.resolve_target(
        session, case_number, conversation_id, user_id, image_number=image_number
    )
    model, model_label = resolver.require_label(target, stage)
    canonical = await labels.canonical_label(model, label)

    if canonical == model_label:
        raise RelabelRefused(
            f"{target.what} was already classified as {canonical} - there is nothing to correct."
        )
    if canonical == target.corrected_label and target.corrected_model == model:
        raise RelabelRefused(f"{target.what} was already corrected to {canonical}.")

    if target.case is not None:
        await recorder.save_proposal(
            session, target.case, model=model, label=canonical, reason=reason, user_id=user_id
        )
    else:
        assert target.draft is not None
        await recorder.save_draft_proposal(
            session, target.draft, model=model, label=canonical, reason=reason
        )
    return Proposal(
        target=target, stage=stage, model=model, model_label=model_label, label=canonical, reason=reason
    )


@traced("relabel-confirm")
async def confirm(
    session: AsyncSession,
    *,
    case_number: str | None,
    conversation_id: str,
    user_id: str,
    turn_started_at: datetime,
    image_number: int | None = None,
) -> Correction:
    target = await resolver.resolve_target(
        session, case_number, conversation_id, user_id, image_number=image_number, pending=True
    )

    if target.case is not None:
        case = target.case
        problem = confirmation_problem(
            pending_at=case.pending_at if case.pending_label else None,
            pending_by_user_id=case.pending_by_user_id,
            user_id=user_id,
            turn_started_at=turn_started_at,
            expires_after=proposal_ttl(),
        )
        if problem is not None:
            raise RelabelRefused(_REFUSALS[problem].format(what=target.what))
        assert case.pending_label is not None  # confirmation_problem returns None only when pending
        label, model = case.pending_label, case.pending_model or case.defect_model or ""
        ticket = await recorder.apply_proposal(session, case, user_id=user_id)
        return Correction(case=case, model=model, label=label, ticket=ticket, created_case=False)

    draft = target.draft
    assert draft is not None
    problem = confirmation_problem(
        pending_at=draft.pending_at if draft.pending_label else None,
        pending_by_user_id=draft.user_id,  # a draft is per user, so the proposer is its owner
        user_id=user_id,
        turn_started_at=turn_started_at,
        expires_after=proposal_ttl(),
    )
    if problem is not None:
        raise RelabelRefused(_REFUSALS[problem].format(what=target.what))
    assert draft.pending_label is not None
    label = draft.pending_label
    model = draft.pending_model or str(draft.payload.get("defect_model") or "")
    new_case, ticket = await recorder.apply_draft_proposal(session, draft, user_id=user_id)
    return Correction(case=new_case, model=model, label=label, ticket=ticket, created_case=True)


@traced("relabel-cancel")
async def cancel(
    session: AsyncSession,
    *,
    case_number: str | None,
    conversation_id: str,
    user_id: str,
    image_number: int | None = None,
) -> Cancelled:
    """Discards a proposed relabel the user declined. Only the proposer can discard their own
    proposal; nothing that was confirmed is touched."""

    target = await resolver.resolve_target(
        session, case_number, conversation_id, user_id, image_number=image_number, pending=True
    )
    if target.case is not None:
        case = target.case
        if case.pending_label is None:
            raise RelabelRefused(f"there is no relabel waiting on {target.what} to cancel.")
        if case.pending_by_user_id != user_id:
            raise RelabelRefused("that relabel was proposed by someone else, so it is not yours to cancel.")
        label = case.pending_label
        await recorder.clear_proposal(session, case)
        return Cancelled(target=target, label=label)

    draft = target.draft
    assert draft is not None
    if draft.pending_label is None:
        raise RelabelRefused(f"there is no relabel waiting on {target.what} to cancel.")
    label = draft.pending_label
    await recorder.clear_draft_proposal(session, draft)
    return Cancelled(target=target, label=label)


@dataclass(frozen=True)
class OwnCorrection:
    case_number: str | None
    model: str | None
    model_label: str | None
    corrected_label: str | None
    ticket_status: str
    plan_status: str | None
    can_withdraw: bool
    made_at: datetime


def _own_ticket_state(status: str, plan_status: str | None) -> str:
    if status == RetrainingTicketStatus.RESOLVED:
        return "retrained"
    if plan_status is not None:
        return f"in a retraining plan ({plan_status.replace('_', ' ')})"
    return "waiting for a retraining plan"


@traced("relabel-list-own")
async def own_corrections(session: AsyncSession, *, user_id: str, limit: int = 10) -> list[dict[str, object]]:
    rows = await ticket_repo.list_flagged_by(session, user_id, limit=limit)
    out: list[dict[str, object]] = []
    for ticket, plan_status in rows:
        out.append(
            {
                "case_number": ticket.case_number,
                "sample": f"{ticket.run_id}:{ticket.sample_ref}" if ticket.sample_ref else None,
                "model": ticket.model_name,
                "model_said": ticket.observed_label,
                "corrected_to": ticket.correct_label,
                "reason": ticket.reason,
                "where_it_stands": _own_ticket_state(ticket.status, plan_status),
                # Only a chat correction that is not in any plan yet can be taken back here.
                "can_withdraw": bool(
                    ticket.case_id
                    and ticket.job_id is None
                    and ticket.status == RetrainingTicketStatus.OPEN
                ),
                "made_at": ticket.created_at.isoformat(),
            }
        )
    return out


@traced("relabel-withdraw")
async def withdraw(
    session: AsyncSession, *, case_number: str | None, conversation_id: str, user_id: str
) -> Case:
    """Takes back the user's own confirmed correction while its ticket is not in any plan."""

    if (case_number or "").strip():
        case, error = await resolve_case_ref(session, case_number, conversation_id)
        if case is None:
            raise RelabelRefused(error or "case not found")
    else:
        # "withdraw it": the user's most recent correction that can still be taken back.
        rows = await session.scalars(
            select(RetrainingTicket)
            .where(
                RetrainingTicket.flagged_by_user_id == user_id,
                RetrainingTicket.case_id.is_not(None),
                RetrainingTicket.job_id.is_(None),
                RetrainingTicket.status == RetrainingTicketStatus.OPEN,
            )
            .order_by(RetrainingTicket.created_at.desc())
            .limit(1)
        )
        found = rows.first()
        case = await session.get(Case, found.case_id) if found and found.case_id else None
        if case is None:
            raise RelabelRefused("you have no correction waiting that can be withdrawn.")

    ticket = await session.scalar(
        select(RetrainingTicket)
        .where(RetrainingTicket.case_id == case.id)
        .order_by(RetrainingTicket.created_at.desc())
        .limit(1)
    )
    if case.corrected_label is None or ticket is None:
        raise RelabelRefused(f"{case.case_number} has no correction to withdraw.")
    if ticket.flagged_by_user_id != user_id:
        raise RelabelRefused(
            f"the correction on {case.case_number} was made by someone else, so it is not yours "
            "to withdraw."
        )
    if ticket.job_id is not None or ticket.status != RetrainingTicketStatus.OPEN:
        raise RelabelRefused(
            f"the correction on {case.case_number} is already part of a retraining plan, so it "
            "can no longer be withdrawn here. An Admin can cancel the plan in the Models tab, "
            "which releases it."
        )
    await recorder.withdraw_correction(session, case, ticket)
    return case
