"""Recorder: the review's writes, and nothing else - the rules that decide whether a write may happen
are service.py's. A proposal waits on the Case in `pending_resolution*`; applying it moves the Case
to APPROVED or OVERRIDDEN and records who decided, when and why."""

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.db.models import Case, CaseStatus

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


async def apply_proposal(session: AsyncSession, case: Case, *, user_id: str) -> str:
    """Commits the pending decision; returns it."""

    decision = case.pending_resolution
    assert decision in _STATUS  # service.py only applies a proposal it validated

    case.status = _STATUS[decision]
    case.resolved_by_user_id = user_id
    case.resolved_at = datetime.now(UTC)
    case.resolution_note = case.pending_resolution_note
    case.pending_resolution = None
    case.pending_resolution_note = None
    case.pending_resolution_by_user_id = None
    case.pending_resolution_at = None
    await session.commit()
    return decision
