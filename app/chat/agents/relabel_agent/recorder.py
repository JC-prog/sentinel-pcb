"""Recorder sub-agent: the relabel's writes, and nothing else - the rules that decide whether a write
may happen are service.py's. A proposal waits on the Case (or the draft) in `pending_*`; applying it
moves it to `corrected_*` (the model's own labels are never touched) and queues the RetrainingTicket
that carries the correction to retraining - in one commit, so neither lands without the other.

A correction names the model it corrects (`pending_model` -> `corrected_model`): the defect model,
or the region model when the region was wrong. The ticket is filed against that model, with the
label and version that model gave."""

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.agents.relabel_agent.resolver import Target, stage_of
from app.chat.db.models import Case, CaseDraft
from app.chat.services import cases as case_service
from app.shared.db.models import RetrainingTicket
from app.shared.modelops import tickets as ticket_repo


async def save_proposal(
    session: AsyncSession, case: Case, *, model: str, label: str, reason: str, user_id: str
) -> None:
    case.pending_model = model
    case.pending_label = label
    case.pending_reason = reason
    case.pending_by_user_id = user_id
    case.pending_at = datetime.now(UTC)
    await session.commit()


async def clear_proposal(session: AsyncSession, case: Case) -> None:
    case.pending_label = case.pending_reason = case.pending_by_user_id = None
    case.pending_at = case.pending_model = None
    await session.commit()


async def _file_ticket(
    session: AsyncSession, case: Case, *, model: str, label: str, reason: str, user_id: str
) -> RetrainingTicket:
    """Commits whatever is staged on the session together with the ticket."""

    target = Target(case=case)
    observed, version, _ = target.observed(stage_of(model))
    try:
        return await ticket_repo.create_ticket(
            session,
            case_id=case.id,
            case_number=case.case_number,
            flagged_by_user_id=user_id,
            reason=reason,
            model_name=model,
            model_version=version,
            observed_label=observed,
            correct_label=label,
        )
    except Exception:
        await session.rollback()
        raise


def _correct(case: Case, *, model: str, label: str, reason: str, user_id: str) -> None:
    case.corrected_model = model
    case.corrected_label = label
    case.corrected_by_user_id = user_id
    case.corrected_at = datetime.now(UTC)
    case.correction_reason = reason


async def apply_proposal(session: AsyncSession, case: Case, *, user_id: str) -> RetrainingTicket:
    assert case.pending_label is not None  # service.py only applies a proposal it validated
    label, reason = case.pending_label, case.pending_reason or ""
    model = case.pending_model or case.defect_model or ""
    _correct(case, model=model, label=label, reason=reason, user_id=user_id)
    case.pending_label = case.pending_reason = case.pending_by_user_id = None
    case.pending_at = case.pending_model = None
    return await _file_ticket(session, case, model=model, label=label, reason=reason, user_id=user_id)


async def save_draft_proposal(
    session: AsyncSession, draft: CaseDraft, *, model: str, label: str, reason: str
) -> None:
    draft.pending_model = model
    draft.pending_label = label
    draft.pending_reason = reason
    draft.pending_at = datetime.now(UTC)
    await session.commit()


async def clear_draft_proposal(session: AsyncSession, draft: CaseDraft) -> None:
    draft.pending_label = draft.pending_reason = draft.pending_at = draft.pending_model = None
    await session.commit()


async def apply_draft_proposal(
    session: AsyncSession, draft: CaseDraft, *, user_id: str
) -> tuple[Case, RetrainingTicket]:
    """The first human judgment of an inspection: creates the Case with the correction already on
    it, drops the draft and queues the ticket - one commit, so a Case never exists half-corrected
    and the draft is consumed exactly once. A review proposed on the same draft stays proposed, now
    on the Case."""

    assert draft.pending_label is not None
    label, reason = draft.pending_label, draft.pending_reason or ""
    model = draft.pending_model or str(draft.payload.get("defect_model") or "")
    case = await case_service.create_case(
        session,
        created_by_user_id=draft.user_id,
        conversation_id=draft.conversation_id,
        commit=False,
        **draft.payload,
    )
    _correct(case, model=model, label=label, reason=reason, user_id=user_id)
    carry_review(case, draft)
    await session.delete(draft)
    # create_ticket commits the Case, its correction and the draft's deletion with the ticket.
    ticket = await _file_ticket(session, case, model=model, label=label, reason=reason, user_id=user_id)
    return case, ticket


def carry_review(case: Case, draft: CaseDraft) -> None:
    """Moves a review proposed on the draft onto the new Case, stamped now: the "yes" that
    committed the relabel must not also commit the review."""

    if draft.pending_resolution is None:
        return
    case.pending_resolution = draft.pending_resolution
    case.pending_resolution_note = draft.pending_resolution_note
    case.pending_resolution_by_user_id = draft.user_id
    case.pending_resolution_at = datetime.now(UTC)


async def withdraw_correction(session: AsyncSession, case: Case, ticket: RetrainingTicket) -> None:
    """Takes a confirmed correction back: the Case keeps what the model said (as it always did) and
    loses the correction, and its ticket goes - in one commit, so neither is left behind."""

    case.corrected_label = None
    case.corrected_model = None
    case.corrected_by_user_id = None
    case.corrected_at = None
    case.correction_reason = None
    await session.delete(ticket)
    try:
        await session.commit()
    except Exception:
        await session.rollback()
        raise


