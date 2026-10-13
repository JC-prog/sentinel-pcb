"""The review agent's two use cases and their rules: propose approving or overriding a case that
awaits review, then confirm it.

Only a REVIEW_REQUIRED case can be reviewed - the pipeline's verdict on an ACCEPTED case is not up
for a second opinion here, and a case someone already resolved stays resolved. "Approve" confirms
the flagged defect stands; "override" reverses it as a false positive (the signal drift watches).

The confirm step is enforced here, not left to the model's good manners - see
app/chat/services/confirmation.py: a proposal can only be committed in a LATER chat turn than the
one that proposed it, and only by the user who proposed it.
"""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.agents.review_agent import recorder
from app.chat.agents.review_agent.errors import ReviewRefused
from app.chat.db.models import Case, CaseStatus
from app.chat.services.cases import resolve_case_ref
from app.chat.services.confirmation import ConfirmationProblem, confirmation_problem
from app.shared.config.langfuse import traced

_DECISIONS = (recorder.APPROVE, recorder.OVERRIDE)

_REFUSALS = {
    ConfirmationProblem.NOTHING_PENDING: (
        "there is no review waiting for confirmation on {case} - propose one with review_case first."
    ),
    ConfirmationProblem.PROPOSED_BY_SOMEONE_ELSE: (
        "that review was proposed by someone else; propose your own."
    ),
    ConfirmationProblem.SAME_TURN: (
        "the user has not confirmed yet - a review must be proposed in one message and confirmed by "
        "the user in a later one. Ask them, and wait for their answer."
    ),
}


@dataclass(frozen=True)
class Proposal:
    case: Case
    decision: str
    note: str | None


@dataclass(frozen=True)
class Resolution:
    case: Case
    decision: str


async def _resolve(session: AsyncSession, case_number: str | None, conversation_id: str) -> Case:
    case, error = await resolve_case_ref(session, case_number, conversation_id)
    if case is None:
        raise ReviewRefused(error or "case not found")
    return case


def _require_awaiting_review(case: Case) -> None:
    if case.status == CaseStatus.REVIEW_REQUIRED:
        return
    if case.status == CaseStatus.ACCEPTED:
        raise ReviewRefused(
            f"{case.case_number} was accepted by the pipeline - only cases flagged for review can be "
            "approved or overridden."
        )
    raise ReviewRefused(f"{case.case_number} was already {case.status} - it is not awaiting review.")


@traced("review-propose")
async def propose(
    session: AsyncSession,
    *,
    case_number: str | None,
    conversation_id: str,
    user_id: str,
    decision: str,
    note: str | None,
) -> Proposal:
    decision = decision.strip().lower()
    if decision not in _DECISIONS:
        raise ReviewRefused(f"decision must be one of {list(_DECISIONS)}, not {decision!r}.")

    case = await _resolve(session, case_number, conversation_id)
    _require_awaiting_review(case)

    await recorder.save_proposal(session, case, decision=decision, note=note, user_id=user_id)
    return Proposal(case=case, decision=decision, note=note)


@traced("review-confirm")
async def confirm(
    session: AsyncSession,
    *,
    case_number: str | None,
    conversation_id: str,
    user_id: str,
    turn_started_at: datetime,
) -> Resolution:
    case = await _resolve(session, case_number, conversation_id)

    problem = confirmation_problem(
        pending_at=case.pending_resolution_at if case.pending_resolution else None,
        pending_by_user_id=case.pending_resolution_by_user_id,
        user_id=user_id,
        turn_started_at=turn_started_at,
    )
    if problem is not None:
        raise ReviewRefused(_REFUSALS[problem].format(case=case.case_number))

    _require_awaiting_review(case)  # it may have been resolved since the proposal
    decision = await recorder.apply_proposal(session, case, user_id=user_id)
    return Resolution(case=case, decision=decision)
