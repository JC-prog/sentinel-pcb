"""The review agent's use cases and their rules: propose approving or overriding an inspection that
awaits review, confirm it, cancel it, and take a confirmed review back.

Only something flagged REVIEW_REQUIRED can be reviewed - the pipeline's verdict on an ACCEPTED case
is not up for a second opinion here, and a case someone already resolved stays resolved (unless the
person who resolved it withdraws their review). "Approve" confirms the flagged defect stands;
"override" reverses it as a false positive.

What is reviewed is a saved Case, or the inspection the user just ran (a draft, not a Case yet):
confirming a review of that inspection is its first human judgment and creates the Case already
resolved, the same way a confirmed relabel does.

The confirm step is enforced here, not left to the model's good manners - see
app/chat/services/confirmation.py: a proposal can only be committed in a LATER chat turn than the
one that proposed it, only by the user who proposed it, and only while it is fresh.
"""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.agents.review_agent import recorder
from app.chat.agents.review_agent.errors import ReviewRefused
from app.chat.db.models import Case, CaseDraft, CaseStatus
from app.chat.services.cases import resolve_case_ref
from app.chat.services.confirmation import (
    ConfirmationProblem,
    confirmation_problem,
    proposal_ttl,
)
from app.chat.services.current_inspection import current_inspection
from app.shared.config.langfuse import traced

_DECISIONS = (recorder.APPROVE, recorder.OVERRIDE)

_REFUSALS = {
    ConfirmationProblem.NOTHING_PENDING: (
        "there is no review waiting for confirmation on {what} - propose one with review_case first."
    ),
    ConfirmationProblem.PROPOSED_BY_SOMEONE_ELSE: (
        "that review was proposed by someone else; propose your own."
    ),
    ConfirmationProblem.SAME_TURN: (
        "the user has not confirmed yet - a review must be proposed in one message and confirmed by "
        "the user in a later one. Ask them, and wait for their answer."
    ),
    ConfirmationProblem.EXPIRED: (
        "the review proposed on {what} is too old to confirm now - nothing was saved. If the user "
        "still wants it, propose it again with review_case and ask them to confirm."
    ),
}


@dataclass(frozen=True)
class Subject:
    """What a review applies to: a saved Case, or an inspection that becomes one on confirmation."""

    case: Case | None = None
    draft: CaseDraft | None = None

    @property
    def what(self) -> str:
        if self.case is not None:
            return self.case.case_number
        assert self.draft is not None
        return f"image {self.draft.image_number}"

    @property
    def status(self) -> str:
        if self.case is not None:
            return self.case.status
        assert self.draft is not None
        return str(self.draft.payload.get("status") or "")

    def field(self, key: str) -> object:
        if self.case is not None:
            return getattr(self.case, key)
        assert self.draft is not None
        return self.draft.payload.get(key)


@dataclass(frozen=True)
class Proposal:
    subject: Subject
    decision: str
    note: str | None


@dataclass(frozen=True)
class Resolution:
    case: Case
    decision: str
    created_case: bool


async def _subject(
    session: AsyncSession,
    *,
    case_number: str | None,
    conversation_id: str,
    user_id: str,
    image_number: int | None,
    pending: bool = False,
) -> Subject:
    if (case_number or "").strip():
        case, error = await resolve_case_ref(session, case_number, conversation_id)
        if case is None:
            raise ReviewRefused(error or "case not found")
        return Subject(case=case)
    current = await current_inspection(
        session,
        conversation_id=conversation_id,
        user_id=user_id,
        image_number=image_number,
        pending="review" if pending else None,
    )
    if current.is_empty:
        raise ReviewRefused(
            current.empty_reason(
                "there is nothing to review in this conversation yet - inspect an image first, or "
                "give a case number."
            )
        )
    return Subject(draft=current.draft) if current.uses_draft else Subject(case=current.case)


def _require_awaiting_review(subject: Subject) -> None:
    status = subject.status
    if status == CaseStatus.REVIEW_REQUIRED:
        return
    if status == CaseStatus.ACCEPTED:
        raise ReviewRefused(
            f"{subject.what} was accepted by the pipeline - only inspections flagged for review can "
            "be approved or overridden."
        )
    raise ReviewRefused(f"{subject.what} was already {status} - it is not awaiting review.")


@traced("review-propose")
async def propose(
    session: AsyncSession,
    *,
    case_number: str | None,
    conversation_id: str,
    user_id: str,
    decision: str,
    note: str | None,
    image_number: int | None = None,
) -> Proposal:
    decision = decision.strip().lower()
    if decision not in _DECISIONS:
        raise ReviewRefused(f"decision must be one of {list(_DECISIONS)}, not {decision!r}.")

    subject = await _subject(
        session,
        case_number=case_number,
        conversation_id=conversation_id,
        user_id=user_id,
        image_number=image_number,
    )
    _require_awaiting_review(subject)

    if subject.case is not None:
        await recorder.save_proposal(session, subject.case, decision=decision, note=note, user_id=user_id)
    else:
        assert subject.draft is not None
        await recorder.save_draft_proposal(session, subject.draft, decision=decision, note=note)
    return Proposal(subject=subject, decision=decision, note=note)


@traced("review-confirm")
async def confirm(
    session: AsyncSession,
    *,
    case_number: str | None,
    conversation_id: str,
    user_id: str,
    turn_started_at: datetime,
    image_number: int | None = None,
) -> Resolution:
    subject = await _subject(
        session,
        case_number=case_number,
        conversation_id=conversation_id,
        user_id=user_id,
        image_number=image_number,
        pending=True,
    )

    if subject.case is not None:
        case = subject.case
        problem = confirmation_problem(
            pending_at=case.pending_resolution_at if case.pending_resolution else None,
            pending_by_user_id=case.pending_resolution_by_user_id,
            user_id=user_id,
            turn_started_at=turn_started_at,
            expires_after=proposal_ttl(),
        )
        if problem is not None:
            raise ReviewRefused(_REFUSALS[problem].format(what=subject.what))
        _require_awaiting_review(subject)  # it may have been resolved since the proposal
        decision = await recorder.apply_proposal(session, case, user_id=user_id)
        return Resolution(case=case, decision=decision, created_case=False)

    draft = subject.draft
    assert draft is not None
    problem = confirmation_problem(
        pending_at=draft.pending_resolution_at if draft.pending_resolution else None,
        pending_by_user_id=draft.user_id,  # a draft is per user, so the proposer is its owner
        user_id=user_id,
        turn_started_at=turn_started_at,
        expires_after=proposal_ttl(),
    )
    if problem is not None:
        raise ReviewRefused(_REFUSALS[problem].format(what=subject.what))
    new_case, decision = await recorder.apply_draft_proposal(session, draft, user_id=user_id)
    return Resolution(case=new_case, decision=decision, created_case=True)


@traced("review-cancel")
async def cancel(
    session: AsyncSession,
    *,
    case_number: str | None,
    conversation_id: str,
    user_id: str,
    image_number: int | None = None,
) -> tuple[Subject, str]:
    """Discards a proposed review the user declined; returns what it was about and the decision."""

    subject = await _subject(
        session,
        case_number=case_number,
        conversation_id=conversation_id,
        user_id=user_id,
        image_number=image_number,
        pending=True,
    )
    if subject.case is not None:
        case = subject.case
        if case.pending_resolution is None:
            raise ReviewRefused(f"there is no review waiting on {subject.what} to cancel.")
        if case.pending_resolution_by_user_id != user_id:
            raise ReviewRefused("that review was proposed by someone else, so it is not yours to cancel.")
        decision = case.pending_resolution
        await recorder.clear_proposal(session, case)
        return subject, decision

    draft = subject.draft
    assert draft is not None
    if draft.pending_resolution is None:
        raise ReviewRefused(f"there is no review waiting on {subject.what} to cancel.")
    decision = draft.pending_resolution
    await recorder.clear_draft_proposal(session, draft)
    return subject, decision


@traced("review-withdraw")
async def withdraw(
    session: AsyncSession, *, case_number: str | None, conversation_id: str, user_id: str
) -> Case:
    """Takes back a review the user confirmed: the case awaits review again. Only the person who
    resolved it can."""

    case, error = await resolve_case_ref(session, case_number, conversation_id)
    if case is None:
        raise ReviewRefused(error or "case not found")
    if case.status not in (CaseStatus.APPROVED, CaseStatus.OVERRIDDEN):
        raise ReviewRefused(f"{case.case_number} has no confirmed review to withdraw (it is {case.status}).")
    if case.resolved_by_user_id != user_id:
        raise ReviewRefused(
            f"{case.case_number} was reviewed by someone else, so that review is not yours to withdraw."
        )
    await recorder.reopen(session, case)
    return case
