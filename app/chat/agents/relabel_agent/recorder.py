"""Recorder sub-agent: the relabel's writes, and nothing else - the rules that decide whether a write
may happen are service.py's. A proposal waits on the Case in `pending_*`; applying it moves it to
`corrected_*` (the model's own `defect_label` is never touched) and queues the RetrainingTicket that
carries the correction to retraining - in one commit, so neither lands without the other."""

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.db.models import Case, CaseDraft
from app.chat.services import cases as case_service
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


async def save_draft_proposal(
    session: AsyncSession, draft: CaseDraft, *, label: str, reason: str
) -> None:
    draft.pending_label = label
    draft.pending_reason = reason
    draft.pending_at = datetime.now(UTC)
    await session.commit()


async def apply_draft_proposal(
    session: AsyncSession, draft: CaseDraft, *, user_id: str
) -> tuple[Case, RetrainingTicket]:
    """The first human judgment of an inspection: creates the Case with the correction already on
    it, drops the draft and queues the ticket - one commit, so a Case never exists half-corrected
    and the draft is consumed exactly once."""

    label, reason = draft.pending_label, draft.pending_reason or ""
    case = await case_service.create_case(
        session,
        created_by_user_id=draft.user_id,
        conversation_id=draft.conversation_id,
        commit=False,
        **draft.payload,
    )
    case.corrected_label = label
    case.corrected_by_user_id = user_id
    case.corrected_at = datetime.now(UTC)
    case.correction_reason = reason
    await session.delete(draft)

    # create_ticket commits the Case, its correction and the draft's deletion with the ticket.
    try:
        ticket = await ticket_repo.create_ticket(
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
    return case, ticket


async def withdraw_correction(session: AsyncSession, case: Case, ticket: RetrainingTicket) -> None:
    """Takes a confirmed correction back: the Case keeps what the model said (as it always did) and
    loses the correction, and its ticket goes - in one commit, so neither is left behind."""

    case.corrected_label = None
    case.corrected_by_user_id = None
    case.corrected_at = None
    case.correction_reason = None
    await session.delete(ticket)
    try:
        await session.commit()
    except Exception:
        await session.rollback()
        raise
