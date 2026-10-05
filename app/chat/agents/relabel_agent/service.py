"""The relabel agent's two use cases and their rules: propose a correction, then confirm it.

The confirm step is enforced here, not left to the model's good manners - see
app/chat/services/confirmation.py: a proposal can only be committed in a LATER chat turn than the
one that proposed it, and only by the user who proposed it.
"""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.agents.relabel_agent import labels, recorder, resolver
from app.chat.agents.relabel_agent.errors import RelabelRefused
from app.chat.db.models import Case
from app.chat.services.confirmation import ConfirmationProblem, confirmation_problem
from app.shared.config.langfuse import traced
from app.shared.db.models import RetrainingTicket

_REFUSALS = {
    ConfirmationProblem.NOTHING_PENDING: (
        "there is no relabel waiting for confirmation on {case} - propose one with relabel_case first."
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
    case: Case
    label: str
    reason: str


@dataclass(frozen=True)
class Correction:
    case: Case
    label: str
    ticket: RetrainingTicket


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
    case = await resolver.resolve_case(session, case_number, conversation_id)
    assert case.defect_model is not None  # the resolver only returns classified cases
    canonical = await labels.canonical_label(case.defect_model, label)

    if canonical == case.defect_label:
        raise RelabelRefused(
            f"{case.case_number} was already classified as {canonical} - there is nothing to correct."
        )
    if canonical == case.corrected_label:
        raise RelabelRefused(f"{case.case_number} was already corrected to {canonical}.")

    await recorder.save_proposal(session, case, label=canonical, reason=reason, user_id=user_id)
    return Proposal(case=case, label=canonical, reason=reason)


@traced("relabel-confirm")
async def confirm(
    session: AsyncSession,
    *,
    case_number: str | None,
    conversation_id: str,
    user_id: str,
    turn_started_at: datetime,
) -> Correction:
    case = await resolver.resolve_case(session, case_number, conversation_id)

    problem = confirmation_problem(
        pending_at=case.pending_at if case.pending_label else None,
        pending_by_user_id=case.pending_by_user_id,
        user_id=user_id,
        turn_started_at=turn_started_at,
    )
    if problem is not None:
        raise RelabelRefused(_REFUSALS[problem].format(case=case.case_number))

    assert case.pending_label is not None  # confirmation_problem returns None only when pending
    label = case.pending_label
    ticket = await recorder.apply_proposal(session, case, user_id=user_id)
    return Correction(case=case, label=label, ticket=ticket)
