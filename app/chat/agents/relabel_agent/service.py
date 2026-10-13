"""The relabel agent's two use cases and their rules: propose a correction, then confirm it.

A correction applies either to a saved Case or to the inspection the user has just run, which is not
a Case yet: confirming a relabel on that inspection is the first human judgment of it, and is what
creates the Case (see recorder.apply_draft_proposal).

The confirm step is enforced here, not left to the model's good manners - see
app/chat/services/confirmation.py: a proposal can only be committed in a LATER chat turn than the
one that proposed it, and only by the user who proposed it.
"""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.agents.relabel_agent import labels, recorder, resolver
from app.chat.agents.relabel_agent.errors import RelabelRefused
from app.chat.agents.relabel_agent.resolver import Target
from app.chat.db.models import Case
from app.chat.services.confirmation import ConfirmationProblem, confirmation_problem
from app.shared.config.langfuse import traced
from app.shared.db.models import RetrainingTicket

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
}


@dataclass(frozen=True)
class Proposal:
    target: Target
    label: str
    reason: str


@dataclass(frozen=True)
class Correction:
    case: Case
    label: str
    ticket: RetrainingTicket
    created_case: bool


@traced("relabel-propose")
async def propose(
    session: AsyncSession,
    *,
    case_number: str | None,
    conversation_id: str,
    user_id: str,
    label: str,
    reason: str,
) -> Proposal:
    target = await resolver.resolve_target(session, case_number, conversation_id, user_id)
    assert target.defect_model is not None  # the resolver only returns classified targets
    canonical = await labels.canonical_label(target.defect_model, label)
    what = target.case_number or "this inspection"

    if canonical == target.defect_label:
        raise RelabelRefused(f"{what} was already classified as {canonical} - there is nothing to correct.")
    if canonical == target.corrected_label:
        raise RelabelRefused(f"{what} was already corrected to {canonical}.")

    if target.case is not None:
        await recorder.save_proposal(session, target.case, label=canonical, reason=reason, user_id=user_id)
    else:
        assert target.draft is not None
        await recorder.save_draft_proposal(session, target.draft, label=canonical, reason=reason)
    return Proposal(target=target, label=canonical, reason=reason)


@traced("relabel-confirm")
async def confirm(
    session: AsyncSession,
    *,
    case_number: str | None,
    conversation_id: str,
    user_id: str,
    turn_started_at: datetime,
) -> Correction:
    target = await resolver.resolve_target(session, case_number, conversation_id, user_id)
    what = target.case_number or "this inspection"

    if target.case is not None:
        case = target.case
        problem = confirmation_problem(
            pending_at=case.pending_at if case.pending_label else None,
            pending_by_user_id=case.pending_by_user_id,
            user_id=user_id,
            turn_started_at=turn_started_at,
        )
        if problem is not None:
            raise RelabelRefused(_REFUSALS[problem].format(what=what))
        assert case.pending_label is not None  # confirmation_problem returns None only when pending
        label = case.pending_label
        ticket = await recorder.apply_proposal(session, case, user_id=user_id)
        return Correction(case=case, label=label, ticket=ticket, created_case=False)

    draft = target.draft
    assert draft is not None
    problem = confirmation_problem(
        pending_at=draft.pending_at if draft.pending_label else None,
        pending_by_user_id=draft.user_id,  # a draft is per user, so the proposer is its owner
        user_id=user_id,
        turn_started_at=turn_started_at,
    )
    if problem is not None:
        raise RelabelRefused(_REFUSALS[problem].format(what=what))
    assert draft.pending_label is not None
    label = draft.pending_label
    new_case, ticket = await recorder.apply_draft_proposal(session, draft, user_id=user_id)
    return Correction(case=new_case, label=label, ticket=ticket, created_case=True)
