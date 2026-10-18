"""Recorder: the review's writes, and nothing else - the rules that decide whether a write may happen
are service.py's. A proposal waits on the Case (or on the draft of an inspection that is not a Case
yet) in `pending_resolution*`; applying it moves the Case to APPROVED or OVERRIDDEN and records who
decided, when and why. On a draft, applying it creates the Case already resolved, in one commit."""

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.db.models import Case, CaseDraft, CaseStatus
from app.chat.services import cases as case_service

APPROVE = "approve"
OVERRIDE = "override"

_STATUS = {APPROVE: CaseStatus.APPROVED, OVERRIDE: CaseStatus.OVERRIDDEN}


async def save_proposal(
    session: AsyncSession, case: Case, *, decision: str, note: str | None, user_id: str
) -> None:
    case.pending_resolution = decision
    case.pending_resolution_note = note
    case.pending_resolution_by_user_id = user_id
    case.pending_resolution_at = datetime.now(UTC)
    await session.commit()


async def save_draft_proposal(
    session: AsyncSession, draft: CaseDraft, *, decision: str, note: str | None
) -> None:
    draft.pending_resolution = decision
    draft.pending_resolution_note = note
    draft.pending_resolution_at = datetime.now(UTC)
    await session.commit()


def _clear_case(case: Case) -> None:
    case.pending_resolution = None
    case.pending_resolution_note = None
    case.pending_resolution_by_user_id = None
    case.pending_resolution_at = None


async def clear_proposal(session: AsyncSession, case: Case) -> None:
    _clear_case(case)
    await session.commit()


async def clear_draft_proposal(session: AsyncSession, draft: CaseDraft) -> None:
    draft.pending_resolution = draft.pending_resolution_note = draft.pending_resolution_at = None
    await session.commit()


def _resolve(case: Case, *, decision: str, note: str | None, user_id: str) -> None:
    case.status = _STATUS[decision]
    case.resolved_by_user_id = user_id
    case.resolved_at = datetime.now(UTC)
    case.resolution_note = note


async def apply_proposal(session: AsyncSession, case: Case, *, user_id: str) -> str:
    """Commits the pending decision; returns it."""

    decision = case.pending_resolution
    assert decision in _STATUS  # service.py only applies a proposal it validated
    _resolve(case, decision=decision, note=case.pending_resolution_note, user_id=user_id)
    _clear_case(case)
    await session.commit()
    return decision


async def apply_draft_proposal(
    session: AsyncSession, draft: CaseDraft, *, user_id: str
) -> tuple[Case, str]:
    """The first human judgment of an inspection: creates the Case already resolved and drops the
    draft - one commit. A relabel proposed on the same draft stays proposed, now on the Case."""

    decision = draft.pending_resolution
    assert decision in _STATUS
    case = await case_service.create_case(
        session,
        created_by_user_id=draft.user_id,
        conversation_id=draft.conversation_id,
        commit=False,
        **draft.payload,
    )
    _resolve(case, decision=decision, note=draft.pending_resolution_note, user_id=user_id)
    if draft.pending_label is not None:
        # Stamped now: the "yes" that committed the review must not also commit the relabel.
        case.pending_model = draft.pending_model
        case.pending_label = draft.pending_label
        case.pending_reason = draft.pending_reason
        case.pending_by_user_id = draft.user_id
        case.pending_at = datetime.now(UTC)
    await session.delete(draft)
    try:
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    await session.refresh(case)
    return case, decision


async def reopen(session: AsyncSession, case: Case) -> None:
    """Takes a review back: the case awaits review again, as the pipeline flagged it."""

    case.status = CaseStatus.REVIEW_REQUIRED
    case.resolved_by_user_id = None
    case.resolved_at = None
    case.resolution_note = None
    await session.commit()
