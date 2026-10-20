"""The inspections waiting for a human judgment: parked by the inspection pipeline, turned into a
Case by `create_case`, a confirmed relabel or a confirmed review (see
app/chat/db/models/case_draft.py). One per image inspected in the latest turn that inspected
anything. Only reads and writes - the rules for when a draft may be committed are in each agent.
Lives in services/ because several agents use it and agents do not import each other."""

from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.db.models import CaseDraft

Pending = Literal["relabel", "review"]


async def list_drafts(session: AsyncSession, *, conversation_id: str, user_id: str) -> list[CaseDraft]:
    """This user's drafts in the conversation, newest first (the last image inspected leads)."""

    result = await session.scalars(
        select(CaseDraft)
        .where(CaseDraft.conversation_id == conversation_id, CaseDraft.user_id == user_id)
        .order_by(CaseDraft.created_at.desc(), CaseDraft.image_number.desc())
    )
    return list(result.all())


def _has_pending(draft: CaseDraft, pending: Pending) -> bool:
    if pending == "relabel":
        return draft.pending_label is not None
    return draft.pending_resolution is not None


async def get_draft(
    session: AsyncSession,
    *,
    conversation_id: str,
    user_id: str,
    image_number: int | None = None,
    pending: Pending | None = None,
) -> CaseDraft | None:
    """With `image_number`, that image's draft. Without it, the newest draft carrying a `pending`
    proposal of that kind if asked for and there is one, else the newest draft - the last image
    inspected, which is what "it" means."""

    found = await list_drafts(session, conversation_id=conversation_id, user_id=user_id)
    if image_number is not None:
        return next((d for d in found if d.image_number == image_number), None)
    if pending is not None:
        with_pending = next((d for d in found if _has_pending(d, pending)), None)
        if with_pending is not None:
            return with_pending
    return found[0] if found else None


def clear_pending(draft: CaseDraft) -> None:
    draft.pending_label = draft.pending_reason = draft.pending_at = draft.pending_model = None
    draft.pending_resolution = draft.pending_resolution_note = draft.pending_resolution_at = None


async def save_draft(
    session: AsyncSession,
    *,
    conversation_id: str,
    user_id: str,
    payload: dict[str, Any],
    image_number: int = 1,
    turn_started_at: datetime | None = None,
) -> CaseDraft:
    """Parks `payload` as this user's pending inspection of `image_number`. Drafts an earlier turn
    left are dropped (a new batch of images replaces the last), while the other images inspected in
    this same turn stay. Without `turn_started_at` every other draft is dropped, as when there was
    one per conversation. Its `created_at` is when this turn parked it - the moment the
    confirmation rule counts a later turn from."""

    stale = delete(CaseDraft).where(
        CaseDraft.conversation_id == conversation_id,
        CaseDraft.user_id == user_id,
        CaseDraft.image_number != image_number,
    )
    if turn_started_at is not None:
        stale = stale.where(CaseDraft.created_at < turn_started_at)
    await session.execute(stale)

    draft = await get_draft(
        session, conversation_id=conversation_id, user_id=user_id, image_number=image_number
    )
    if draft is None:
        draft = CaseDraft(
            conversation_id=conversation_id,
            user_id=user_id,
            image_number=image_number,
            payload=payload,
        )
        session.add(draft)
    else:
        draft.payload = payload
        draft.created_at = datetime.now(UTC)
        # A proposal was about the previous image; it must not carry over to this one.
        clear_pending(draft)
    await session.commit()
    await session.refresh(draft)
    return draft
