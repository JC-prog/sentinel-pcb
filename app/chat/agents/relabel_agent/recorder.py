"""Recorder sub-agent: the relabel's writes, and nothing else - the rules that decide whether a write
may happen are service.py's. A proposal waits on the Case in `pending_*`; applying it moves it to
`corrected_*` (the model's own `defect_label` is never touched) and queues the RetrainingTicket that
carries the correction to retraining - in one commit, so neither lands without the other."""

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.db.models import Case
from app.shared.db.models import RetrainingTicket
from app.shared.modelops import tickets as ticket_repo


async def save_proposal(
    session: AsyncSession, case: Case, *, label: str, reason: str, user_id: str
) -> None:
    case.pending_label = label
    case.pending_reason = reason
    case.pending_by_user_id = user_id
    case.pending_at = datetime.now(UTC)
    await session.commit()


async def apply_proposal(session: AsyncSession, case: Case, *, user_id: str) -> RetrainingTicket:
    label, reason = case.pending_label, case.pending_reason or ""
    case.corrected_label = label
    case.corrected_by_user_id = user_id
    case.corrected_at = datetime.now(UTC)
    case.correction_reason = reason
    case.pending_label = case.pending_reason = case.pending_by_user_id = case.pending_at = None

    # create_ticket commits the Case changes above along with the ticket.
    try:
        return await ticket_repo.create_ticket(
            session,
            case_id=case.id,
            case_number=case.case_number,
            flagged_by_user_id=user_id,
            reason=reason,
            model_name=case.defect_model,
            model_version=case.defect_model_version,
            observed_label=case.defect_label,
            correct_label=label,
        )
    except Exception:
        await session.rollback()
        raise
